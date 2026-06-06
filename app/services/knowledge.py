"""KnowledgeMapService:会话、消息、节点的核心业务流。

所有方法都拿 AsyncSession 操作 ORM。DeepSeek 失败时回退到本地规则引擎。

消息路径按 intent 分流:
- explain:对当前节点做深度讲解,不新增子节点
- subdivide:把当前节点拆成 N 个子节点,reply 只是过渡句
- auto:根据用户消息内的关键词推断走 explain 还是 subdivide
"""

from __future__ import annotations

import asyncio
import collections
import difflib
import json
import re
from typing import Any, Iterable

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    NODE_STATUSES,
    KnowledgeNode,
    LearningSession,
    Message,
    NodeEvent,
    new_id,
    now_utc,
)
from app.services.ai import (
    DeepSeekClient,
    calibrate_relevance_distribution,
    child_limit_for_mode,
    clamp_metric,
    thinking_mode_profile,
)
from app.services.prompt_store import get_prompt_store
from app.services.topics import child_topics, default_topics

SUBDIVIDE_KEYWORDS = ("细分", "拆", "展开", "子地图", "拆开", "全貌", "里面")
COMPLETE_KEYWORDS = ("我懂了", "已懂", "完成", "学完")
SKIP_KEYWORDS = ("跳过",)
FUZZY_DEDUP_RATIO = 0.85
# 优化E:语义搜索粗筛上限。节点数超过它就先本地打分截断,避免 prompt 随树膨胀。
RANK_PREFILTER_MAX = 40
# 优化C:主讲解保留的"最近对话"窗口;更早的消息折叠进 context_summary。
EXPLAIN_HISTORY_WINDOW = 10
# 窗口外消息每攒满这么多条,才触发一次摘要更新(避免每轮都调一次 LLM)。
CONTEXT_SUMMARY_BATCH = 6
CONTEXT_SUMMARY_MAX_CHARS = 600
PROMOTE_BRANCH_RE = re.compile(
    r"(?:请)?把[「“\"](?P<title>[^」”\"]{1,80})[」”\"]展开(?:成)?(?:一个)?(?:真正的)?学习分支"
)
QUOTED_TITLE_RE = re.compile(r"[「“\"](?P<title>[^」”\"]{1,80})[」”\"]")
UNQUOTED_PROMOTE_RE = re.compile(
    r"(?:请)?把(?P<title>[^，。,.!?！？\n]{1,80}?)(?:展开|拆开|拆分|细分|拆成|展开成)"
)


# 优化A:这些键在同一节点/模式下逐轮基本不变,抽到前置 system 消息里
# 可命中 LLM 的 prompt 前缀缓存(DeepSeek prompt_cache_*),大幅降低重复 input token 费用。
# 顺序 = 最稳定的在前(缓存按前缀逐字匹配,稳定内容越靠前命中越多):
#   task/instructions/json_schema 是任务级固定;
#   field/current_problem/learning_background 是会话级固定(创建后不变)——优化C/前缀扩展。
# 注意:current_node / sibling_titles 这类会随导航和节点状态变化,故仍留在每轮的 user 块里。
_CACHEABLE_PROMPT_KEYS = (
    "task",
    "instructions",
    "json_schema",
    "field",
    "current_problem",
    "learning_background",
)


def _cached_chat_messages(system_rule: str, prompt: dict[str, Any]) -> list[dict[str, str]]:
    """构造命中前缀缓存的消息序列。

    顺序 = [固定输出规则] + [固定 task/instructions/json_schema] + [每轮会变的数据]。
    前两条 system 消息在同节点同模式的连续追问里逐字相同,DeepSeek 会按前缀命中缓存,
    只有末尾 user 消息(用户问题、最近对话、节点状态)每轮变化。
    """
    volatile = {k: v for k, v in prompt.items() if k not in _CACHEABLE_PROMPT_KEYS}
    static = {k: prompt[k] for k in _CACHEABLE_PROMPT_KEYS if k in prompt}
    messages: list[dict[str, str]] = [{"role": "system", "content": system_rule}]
    if static:
        messages.append(
            {
                "role": "system",
                "content": (
                    "本次任务的固定规则与输出格式(请严格遵守约定的 json_schema):\n"
                    + json.dumps(static, ensure_ascii=False)
                ),
            }
        )
    messages.append({"role": "user", "content": json.dumps(volatile, ensure_ascii=False)})
    return messages


def _prefilter_nodes_by_query(
    candidates: list[KnowledgeNode], query: str, limit: int
) -> list[KnowledgeNode]:
    """零 LLM 成本的本地粗筛:按 query 与 title/summary 的子串命中 + 模糊比相似度打分,取 top-N。

    只在节点数超过 RANK_PREFILTER_MAX 时调用;目的是把交给 LLM 精排的候选集封顶,
    让搜索的 prompt 体积不随知识树增长而膨胀。粗筛宁可多放进(召回优先),精排交给 LLM。
    """
    q = query.lower()
    q_terms = [t for t in re.split(r"\s+", q) if t]

    def score(node: KnowledgeNode) -> float:
        title = (node.title or "").lower()
        summary = (node.summary or "").lower()
        hay = f"{title} {summary}"
        s = 0.0
        if q in title:
            s += 5.0
        elif q in summary:
            s += 3.0
        for term in q_terms:
            if term and term in hay:
                s += 1.5
        # 模糊相似度兜底,处理拼写/语序差异
        s += difflib.SequenceMatcher(None, q, title).ratio()
        return s

    ranked = sorted(candidates, key=score, reverse=True)
    return ranked[:limit]


# 优化(提速3):后台任务句柄集合,持引用防止被 GC。
_BG_TASKS: set = set()


def _spawn_bg(coro) -> None:
    """把一个协程丢到事件循环后台跑,不阻塞当前请求(用于滚动摘要这种"算给下一轮用"的活)。"""
    try:
        task = asyncio.create_task(coro)
    except RuntimeError:
        # 没有运行中的事件循环(正常请求里不会发生),放弃后台化
        return
    _BG_TASKS.add(task)
    task.add_done_callback(_BG_TASKS.discard)


def normalize_mode(value: Any) -> str:
    text = str(value or "Lite")
    return text if text in {"Lite", "Medium", "Zen"} else "Lite"


def normalize_intent(value: Any) -> str:
    text = str(value or "auto")
    return text if text in {"auto", "explain", "subdivide"} else "auto"


def _event(session_id: str, node_id: str | None, event_type: str, payload: dict[str, Any]) -> NodeEvent:
    return NodeEvent(
        id=new_id("evt"),
        session_id=session_id,
        node_id=node_id,
        event_type=event_type,
        payload=payload,
    )


async def _make_node(
    db: AsyncSession,
    *,
    session_id: str,
    title: str,
    parent_id: str | None,
    depth: int,
    sort_order: int,
    status: str = "pending",
    relevance: int = 0,
    importance: int = 2,
    relevance_score: int | None = None,
    difficulty: int = 2,
    summary: str = "",
    content: str = "",
    message_id: str | None = None,
    prerequisite_ids: list[str] | None = None,
    is_fundamental: bool = False,
    fp_relation: str = "",
    fp_reason: str = "",
) -> KnowledgeNode:
    rs = clamp_metric(relevance_score if relevance_score is not None else (3 if relevance else 2))
    node = KnowledgeNode(
        id=new_id("node"),
        session_id=session_id,
        parent_id=parent_id,
        title=title,
        summary=summary,
        content=content,
        status=status,
        relevance=relevance,
        importance=clamp_metric(importance),
        relevance_score=rs,
        difficulty=clamp_metric(difficulty),
        depth=depth,
        sort_order=sort_order,
        prerequisite_ids=list(prerequisite_ids or []),
        is_fundamental=is_fundamental,
        fp_relation=fp_relation[:80],
        fp_reason=fp_reason[:400],
        collapsed=False,
        created_from_message_id=message_id,
    )
    db.add(node)
    db.add(_event(session_id, node.id, "node_created", {"title": title, "parent_id": parent_id}))
    await db.flush()
    return node


def _node_to_dict(node: KnowledgeNode) -> dict[str, Any]:
    """把 KnowledgeNode 序列化成前端节点形状(SSE payload 用)。"""
    return {
        "id": node.id,
        "session_id": node.session_id,
        "parent_id": node.parent_id,
        "title": node.title,
        "summary": node.summary or "",
        "content": node.content or "",
        "status": node.status,
        "relevance": node.relevance,
        "importance": node.importance,
        "relevance_score": node.relevance_score,
        "difficulty": node.difficulty,
        "depth": node.depth,
        "sort_order": node.sort_order,
        "prerequisite_ids": node.prerequisite_ids,
        "is_fundamental": node.is_fundamental,
        "fp_relation": node.fp_relation or "",
        "fp_reason": node.fp_reason or "",
        "collapsed": node.collapsed,
        "created_from_message_id": node.created_from_message_id,
        "created_at": node.created_at.isoformat() if node.created_at else None,
        "updated_at": node.updated_at.isoformat() if node.updated_at else None,
    }


async def _fetch_nodes(db: AsyncSession, session_id: str) -> list[KnowledgeNode]:
    result = await db.execute(
        select(KnowledgeNode)
        .where(KnowledgeNode.session_id == session_id)
        .order_by(KnowledgeNode.depth.asc(), KnowledgeNode.sort_order.asc(), KnowledgeNode.created_at.asc())
    )
    return list(result.scalars())


async def _fetch_messages(db: AsyncSession, session_id: str) -> list[Message]:
    result = await db.execute(
        select(Message).where(Message.session_id == session_id).order_by(Message.created_at.asc())
    )
    return list(result.scalars())


async def _get_node(db: AsyncSession, node_id: str | None) -> KnowledgeNode | None:
    if not node_id:
        return None
    return await db.get(KnowledgeNode, node_id)


async def _node_path(db: AsyncSession, node_id: str | None) -> str:
    if not node_id:
        return "知识地图起点"
    pieces: list[str] = []
    current = await _get_node(db, node_id)
    safety = 0
    while current and safety < 12:
        pieces.append(current.title)
        current = await _get_node(db, current.parent_id) if current.parent_id else None
        safety += 1
    return " / ".join(reversed(pieces))


async def _update_status(db: AsyncSession, session_id: str, node_id: str, status: str) -> None:
    if status not in NODE_STATUSES:
        raise ValueError(f"不支持的状态: {status}")
    node = await db.get(KnowledgeNode, node_id)
    if not node or node.session_id != session_id:
        raise ValueError("节点不存在")
    node.status = status
    node.updated_at = now_utc()

    session = await db.get(LearningSession, session_id)
    if session is not None:
        session.current_node_id = node_id
        session.updated_at = now_utc()

    db.add(_event(session_id, node_id, "status_updated", {"status": status}))
    await db.flush()


def _looks_similar(candidate: str, existing: Iterable[str]) -> bool:
    """跨全树的近似 title 去重:精确相等或 ratio > 0.85 视为重复。"""
    cand = candidate.strip()
    if not cand:
        return True
    for raw in existing:
        other = (raw or "").strip()
        if not other:
            continue
        if cand == other:
            return True
        if difflib.SequenceMatcher(None, cand, other).ratio() >= FUZZY_DEDUP_RATIO:
            return True
    return False


def _created_sort_key(dt: Any) -> float:
    """把 created_at 归一成可比较的时间戳。

    SQLite 读回来的 DateTime 是 tz-naive,而内存里新建的对象是 now_utc() 的 tz-aware。
    两者直接比较会抛 "can't compare offset-naive and offset-aware datetimes"。
    naive 一律按 UTC 解释,返回 timestamp 统一比较;缺失则排到最后。
    """
    if dt is None:
        return float("inf")
    from datetime import timezone

    if getattr(dt, "tzinfo", None) is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def _build_preorder(nodes: list[KnowledgeNode]) -> list[KnowledgeNode]:
    """按 (parent_id → sort_order → created_at) 做 pre-order DFS。

    用来在整棵树上定义"下一个知识点"的稳定顺序:深度优先、自左向右、
    和右侧画布的视觉顺序一致。
    """
    by_parent: dict[str | None, list[KnowledgeNode]] = {}
    for n in nodes:
        by_parent.setdefault(n.parent_id, []).append(n)
    for kids in by_parent.values():
        kids.sort(key=lambda x: (x.sort_order, _created_sort_key(x.created_at)))

    order: list[KnowledgeNode] = []

    def visit(parent_id: str | None) -> None:
        for child in by_parent.get(parent_id, []):
            order.append(child)
            visit(child.id)

    visit(None)
    return order


def _next_pending_candidates(
    nodes: list[KnowledgeNode],
    current_node_id: str | None,
    *,
    limit: int = 3,
) -> list[KnowledgeNode]:
    """按 pre-order 取当前节点之后的若干个 pending 节点。

    前端的"下一个"按钮 hover 下拉里要展示多个候选,这里负责取齐 limit 个;
    数量不够就有几个返回几个,不补 visited 节点(用户看到带 ○ 的会迷惑)。
    """
    order = [n for n in _build_preorder(nodes) if n.depth >= 1]
    if not order:
        return []
    start_index = 0
    if current_node_id:
        for i, n in enumerate(order):
            if n.id == current_node_id:
                start_index = i + 1
                break
    out: list[KnowledgeNode] = []
    seen: set[str] = set()
    for candidate in order[start_index:]:
        if candidate.id == current_node_id or candidate.id in seen:
            continue
        if candidate.status == "pending":
            out.append(candidate)
            seen.add(candidate.id)
            if len(out) >= limit:
                return out
    for candidate in order[:start_index]:
        if candidate.id == current_node_id or candidate.id in seen:
            continue
        if candidate.status == "pending":
            out.append(candidate)
            seen.add(candidate.id)
            if len(out) >= limit:
                return out
    return out


def _next_pending_node(
    nodes: list[KnowledgeNode],
    current_node_id: str | None,
) -> KnowledgeNode | None:
    """找当前节点之后第一个【还没学过】的节点,按右侧地图的视觉顺序(pre-order)。

    规则就是 pre-order DFS 的"下一个 pending 节点":
    - 在一级节点上 → 下一个是它的第一个孩子(往下钻,先深入再横向)
    - 在最末端三级节点上 → 下一个自然回到同层后续兄弟,再不行就 parent 的兄弟
    - "下一个"的走向和地图上手指划过的顺序一致

    "学过"的定义是 status != "pending"——
    active/deepening/completed/skipped/paused 都视为已动过,不再推。
    走到末尾还没找到的话,从头扫一遍(用户可能跳着学,前面留了 pending)。
    """
    order = [n for n in _build_preorder(nodes) if n.depth >= 1]
    if not order:
        return None

    start_index = 0
    if current_node_id:
        for i, n in enumerate(order):
            if n.id == current_node_id:
                start_index = i + 1
                break

    # 主路径:从当前节点之后开始,按 pre-order 顺序找第一个 pending
    for candidate in order[start_index:]:
        if candidate.id == current_node_id:
            continue
        if candidate.status == "pending":
            return candidate
    # 兜底:再从头扫一遍 pending(支持环绕,不让"已经跳过去但还没讲的"漏掉)
    for candidate in order[:start_index]:
        if candidate.id == current_node_id:
            continue
        if candidate.status == "pending":
            return candidate
    return None


def _build_next_step_action(next_node: KnowledgeNode | None) -> dict[str, Any] | None:
    """生成"下一个:XX"按钮。前端识别 kind_hint=next_step 做特殊样式。"""
    if not next_node:
        return None
    title = (next_node.title or "").strip()
    if not title:
        return None
    return {
        "kind": "explain",
        "label": f"下一个：{title[:18]}",
        "payload": f"请围绕「{title}」开始讲解。",
        "target_node_id": next_node.id,
        "kind_hint": "next_step",
    }


def _build_retry_action(current_node: KnowledgeNode | None) -> dict[str, Any] | None:
    """生成"没听懂"按钮:让 AI 在当前节点上换个说法重讲。

    target_node_id 故意留空(None),前端 sendMessage 拿到 null 不会切节点,
    AI 仍然在当前节点上回答。
    """
    if not current_node or current_node.depth < 1:
        return None
    return {
        "kind": "explain",
        "label": "没听懂",
        "payload": (
            "我刚才没完全听懂上面这段。请换一种讲法再说一遍:用更白话、更具体的例子,"
            "或者拆成更小的步骤,但围绕的还是同一个问题。"
        ),
        "target_node_id": None,
        "kind_hint": "retry",
    }


def _build_fixed_next_actions(
    next_nodes: KnowledgeNode | list[KnowledgeNode] | None,
    *,
    current_node: KnowledgeNode | None = None,
) -> list[dict[str, Any]]:
    """对话区的导航按钮:[下一个候选 ×N, 没听懂]。

    next_nodes:
      - 传单个 KnowledgeNode → 兼容旧调用方式,只出 1 个候选
      - 传 list[KnowledgeNode] → 出 N 个候选(前端 hover 下拉展示后面 2 个)
      - None / 空 list → 整棵树学完了,不出候选

    前端约定:所有 kind_hint=next_step 的 action 都是"下一个"候选,
    第一条做主按钮,其余进 hover dropdown。
    """
    if isinstance(next_nodes, KnowledgeNode):
        candidates_iter: list[KnowledgeNode] = [next_nodes]
    elif next_nodes:
        candidates_iter = list(next_nodes)
    else:
        candidates_iter = []

    actions: list[dict[str, Any]] = []
    for candidate in candidates_iter:
        step = _build_next_step_action(candidate)
        if step:
            actions.append(step)
    retry = _build_retry_action(current_node)
    if retry:
        actions.append(retry)
    return actions


# 兼容旧函数名,内部直接转发给新实现。所有调用点最终会改成 _build_fixed_next_actions。
def _inject_next_step(
    actions: list[dict[str, Any]],  # noqa: ARG001 - 保留签名,但 AI 给的 actions 不再使用
    next_node: KnowledgeNode | None,
    *,
    current_node: KnowledgeNode | None = None,
) -> list[dict[str, Any]]:
    return _build_fixed_next_actions(next_node, current_node=current_node)


def _resolve_next_actions(
    raw_actions: Any,
    *,
    current_node_id: str,
    title_to_id: dict[str, str],
) -> list[dict[str, Any]]:
    """把 AI 给的 next_actions 规整成前端可用的结构。

    AI 给 `target_title`,我们在已有 + 新建的节点里查 ID,查不到就回落到 current_node_id。
    """
    if not isinstance(raw_actions, list):
        return []
    cleaned: list[dict[str, Any]] = []
    for item in raw_actions[:4]:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("kind") or "").strip()
        if kind not in {"explain", "subdivide"}:
            continue
        label = str(item.get("label") or "").strip()
        if not label:
            continue
        payload_text = str(item.get("payload") or label).strip()
        target_title = str(item.get("target_title") or "").strip()
        target_id: str | None = None
        if target_title:
            target_id = title_to_id.get(target_title)
            if not target_id:
                # 不区分大小写、去空格做一次兜底
                lowered = target_title.lower()
                for title, nid in title_to_id.items():
                    if title.lower() == lowered:
                        target_id = nid
                        break
        cleaned.append(
            {
                "kind": kind,
                "label": label[:40],
                "payload": payload_text[:200],
                "target_node_id": target_id or current_node_id,
            }
        )
    return cleaned[:3]


def _extract_promoted_branch_title(message: str) -> str | None:
    text = message.strip()
    match = PROMOTE_BRANCH_RE.search(text)
    if not match and any(token in text for token in (*SUBDIVIDE_KEYWORDS, "学习分支")):
        match = QUOTED_TITLE_RE.search(text)
    if not match:
        match = UNQUOTED_PROMOTE_RE.search(text)
    if not match:
        return None
    title = match.group("title").strip(" :：,，。.!！?？")
    return title[:50] if title else None


def _learning_background(session: LearningSession | None) -> str:
    text = (session.learning_background if session else "").strip()
    return text or "用户未说明背景,按有兴趣但基础不完整的新手处理。"


def _fallback_background_questions(field: str, current_problem: str) -> list[dict[str, Any]]:
    is_business = any(token in field for token in ("经营", "品牌", "商业", "门店", "运营", "营销"))
    identity_options = [
        {"label": "初/高中学生", "value": "你是中学生,数学和专业术语要按中学水平讲,多类比、少术语。"},
        {"label": "大学生 / 研究生", "value": "你是大学生或研究生,可以按本科难度讲,但跨学科术语要先解释。"},
        {"label": "在职 / 转行", "value": "你已经工作过,讲解可以用业务语言,少铺垫理论。"},
        {"label": "自学者", "value": "你是自学者,讲解要靠白话和具体例子建立直觉,再补术语。"},
    ]
    depth_options = [
        {"label": "零基础", "value": "你是零基础,需要先用白话解释概念,少用专业术语。"},
        {"label": "懂一点", "value": "你有少量接触,可以讲基础概念,但关键术语仍要顺手解释。"},
        {"label": "有经验", "value": "你有相关经验,可以少讲常识,多讲机制、指标和判断方法。"},
        {"label": "做过项目", "value": "你做过相关项目,讲解要直接进入案例拆解、指标和决策取舍。"},
    ]
    goal_options = [
        {"label": "先听懂", "value": "你的目标是先听懂,回答要短、清楚、少分支。"},
        {"label": "能判断", "value": "你的目标是能做判断,回答要给判断标准、例子和反例。"},
        {"label": "能实操", "value": "你的目标是能落地实操,回答要给步骤、检查清单和行动建议。"},
        {"label": "能表达", "value": "你的目标是能讲给别人听,回答要给清晰话术、类比和结构化表达。"},
    ]
    term_options = [
        {"label": "先翻译成人话", "value": "遇到专业术语时先用一句白话解释,再进入分析。"},
        {"label": "术语旁边解释", "value": "可以保留专业术语,但第一次出现时要立刻补一句白话解释。"},
        {"label": "可以直接讲", "value": "可以直接使用常见术语,但复杂术语要补一句边界。"},
        {"label": "多用例子", "value": "遇到抽象术语时优先配一个具体例子,再补正式定义。"},
    ]
    if is_business:
        goal_options[-1] = {
            "label": "和老板沟通",
            "value": "你要和老板沟通,回答要强调经营语言、数字口径和可汇报表达。",
        }
    return [
        {"id": "identity", "question": "你目前的身份大致是?", "options": identity_options},
        {"id": "level", "question": f"你现在对「{field}」的熟悉程度?", "options": depth_options},
        {"id": "goal", "question": f"围绕「{current_problem}」,你更想先做到什么?", "options": goal_options},
        {"id": "terms", "question": "遇到专业术语时,你希望我怎么处理?", "options": term_options},
    ]


def _fallback_subdivision_options(node: KnowledgeNode) -> dict[str, Any]:
    """DeepSeek 不可用时给一个稳定的三角度模板。

    Fallback 没办法做语义判断,默认**不**带 caution——
    不该用机械规则去劝用户别拆,把决定权留给用户。
    """
    return {
        "options": [
            {
                "angle": "类型分类",
                "label": "按几种类型分",
                "rationale": "先看这个节点能分成哪些大类,快速建立横向格局。",
            },
            {
                "angle": "构成组成",
                "label": "按组成部分拆",
                "rationale": "拆出这个节点由哪些模块/要素构成,纵向建立结构。",
            },
            {
                "angle": "指标评估",
                "label": "按怎么判断好坏",
                "rationale": "拆出衡量这个节点的几个核心指标,落到可观测的维度。",
            },
        ],
        "caution": None,
    }


def _stringify_answer_object(answer: Any) -> str:
    if isinstance(answer, str):
        return answer.strip()
    if not isinstance(answer, dict):
        return ""
    sections = [
        ("direct_answer", ""),
        ("core_mechanism", "### 核心机制"),
        ("specific_case", "### 具体例子"),
        ("connection_to_current_problem", "### 和当前问题的关系"),
        ("small_exercise", "### 小练习"),
        ("location_in_map", ""),
    ]
    parts: list[str] = []
    for key, heading in sections:
        value = str(answer.get(key) or "").strip()
        if not value:
            continue
        parts.append(f"{heading}\n\n{value}" if heading else value)
    if parts:
        return "\n\n".join(parts)
    return "\n\n".join(str(value).strip() for value in answer.values() if str(value).strip())


def _reply_from_ai_data(data: dict[str, Any]) -> str:
    reply = str(data.get("reply") or "").strip()
    if reply:
        return reply
    return _stringify_answer_object(data.get("answer"))


def _peek_followup_subject_hint(question: str, anchor_text: str) -> dict[str, str]:
    text = question.strip()
    anchor = anchor_text.strip()
    pronoun_tokens = ("它", "他", "她", "这个", "那个", "这", "那", "它们", "这些", "那些")
    definition_markers = ("是啥", "是什么", "什么意思", "啥意思", "怎么理解", "定义")
    process_markers = ("怎么", "如何", "为什么", "为何", "咋", "怎样", "什么过程", "怎么传递", "如何传递")
    starts_with_pronoun = any(text.startswith(token) for token in pronoun_tokens)
    has_anchor = bool(anchor and anchor in text)
    asks_definition = any(marker in text for marker in definition_markers)
    asks_process = any(marker in text for marker in process_markers)
    if asks_definition and not has_anchor and not starts_with_pronoun:
        subject = text
        for marker in definition_markers:
            subject = subject.replace(marker, "")
        subject = subject.strip(" ?？。；;：:")
        return {
            "kind": "new_explicit_subject",
            "subject": subject[:80] or text[:80],
            "rule": "优先回答 followup_question 中显式出现的新术语,不要把 selected_text 当主语。",
        }
    if asks_process and not starts_with_pronoun:
        return {
            "kind": "explicit_process_question",
            "subject": text[:80],
            "rule": "优先回答 followup_question 问的机制/过程,不要先重新定义 selected_text。",
        }
    if starts_with_pronoun or not text:
        return {
            "kind": "pronoun_or_ellipsis",
            "subject": anchor[:80],
            "rule": "问题使用代词或省略主语,可以结合历史判断是否沿用 selected_text。",
        }
    return {
        "kind": "explicit_question",
        "subject": text[:80],
        "rule": "优先回答 followup_question 本身,selected_text 只作为上下文。",
    }


async def _find_child_by_title(
    db: AsyncSession,
    *,
    session_id: str,
    parent_id: str,
    title: str,
) -> KnowledgeNode | None:
    result = await db.execute(
        select(KnowledgeNode).where(
            KnowledgeNode.session_id == session_id,
            KnowledgeNode.parent_id == parent_id,
            KnowledgeNode.title == title,
        )
    )
    return result.scalars().first()


class KnowledgeMapService:
    def __init__(self, ai_client: DeepSeekClient):
        self.ai_client = ai_client

    # ------------------------------------------------------------------ session

    async def background_questions(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        field = str(payload.get("field") or "").strip() or "新的学习主题"
        current_problem = (
            str(payload.get("current_problem") or "").strip() or "我想快速建立结构化认知"
        )
        mode = normalize_mode(payload.get("mode"))
        try:
            questions = await self.ai_client.background_questions(field, current_problem, mode)
            if questions:
                return questions
        except Exception as exc:  # noqa: BLE001
            print(f"[knowledge_map] background questions fallback: {exc}")
        return _fallback_background_questions(field, current_problem)

    async def background_followup(self, payload: dict[str, Any]) -> dict[str, Any]:
        """根据已答的诊断题决定要不要再追问几题。"""
        field = str(payload.get("field") or "").strip() or "新的学习主题"
        current_problem = (
            str(payload.get("current_problem") or "").strip() or "我想快速建立结构化认知"
        )
        mode = normalize_mode(payload.get("mode"))
        follow_up_round = int(payload.get("follow_up_round") or 0)
        answered_raw = payload.get("answered") or []
        answered: list[dict[str, str]] = []
        for item in answered_raw:
            if not isinstance(item, dict):
                continue
            q = str(item.get("question") or "").strip()
            a = str(item.get("answer") or "").strip()
            if q and a:
                answered.append({"question": q[:200], "answer": a[:400]})
        if not answered or follow_up_round >= 2:
            return {"need_more": False, "reason": "", "questions": []}
        try:
            return await self.ai_client.background_followup(
                field, current_problem, answered, mode, follow_up_round
            )
        except Exception as exc:  # noqa: BLE001
            print(f"[knowledge_map] background followup fallback: {exc}")
            return {"need_more": False, "reason": "", "questions": []}

    async def preview_topics(self, payload: dict[str, Any]) -> list[dict[str, str]]:
        """预览-确认流程的预览阶段:轻量 LLM 调用,只生成主干一级 title + summary。

        失败抛 RuntimeError,让上层 router 返回 5xx,前端按"出错重试"提示。
        """
        field = str(payload.get("field") or "").strip() or "新的学习主题"
        current_problem = (
            str(payload.get("current_problem") or "").strip() or "我想快速建立结构化认知"
        )
        learning_background = str(payload.get("learning_background") or "").strip()
        mode = normalize_mode(payload.get("mode"))
        topics = await self.ai_client.preview_topics(
            field, current_problem, learning_background, mode
        )
        return [
            {"title": t["title"], "summary": t.get("summary", ""), "custom": False}
            for t in topics
        ]

    async def create_session(
        self,
        db: AsyncSession,
        payload: dict[str, Any],
        *,
        user_id: str = "local_user",
    ) -> dict[str, Any]:
        field = str(payload.get("field") or "").strip() or "新的学习主题"
        current_problem = (
            str(payload.get("current_problem") or "").strip() or "我想快速建立结构化认知"
        )
        learning_background = str(payload.get("learning_background") or "").strip()
        mode = normalize_mode(payload.get("mode"))
        # 预览-确认流程会传 topics_override,后端直接用这些 title 建主干、不再调 AI 拆树。
        # children 留空,由后续 /grow-children SSE 端点流式补齐。
        topics_override_raw = payload.get("topics_override")
        topics_override: list[dict[str, str]] | None = None
        if isinstance(topics_override_raw, list):
            cleaned: list[dict[str, str]] = []
            for item in topics_override_raw:
                if not isinstance(item, dict):
                    continue
                title = str(item.get("title") or "").strip()[:50]
                if not title:
                    continue
                cleaned.append({
                    "title": title,
                    "summary": str(item.get("summary") or "").strip()[:160],
                })
            topics_override = cleaned if cleaned else None

        session = LearningSession(
            id=new_id("sess"),
            user_id=user_id or "local_user",
            title=field,
            field=field,
            current_problem=current_problem,
            learning_background=learning_background,
        )
        db.add(session)
        await db.flush()

        root = await _make_node(
            db,
            session_id=session.id,
            title=field,
            parent_id=None,
            depth=0,
            sort_order=0,
            status="active",
            relevance=1,
            summary=f"围绕“{current_problem}”建立学习地图。",
        )
        session.current_node_id = root.id

        if topics_override is not None:
            # 用户已经在预览框里编辑过主干,后端不再问 AI 拆树。
            # children 留空,由 /grow-children SSE 流式补齐。
            topics = [
                {
                    "title": item["title"],
                    "summary": item.get("summary", ""),
                    "importance": 2,
                    "relevance_score": 2,
                    "difficulty": 2,
                    "relevance": 0,
                    "children": [],
                }
                for item in topics_override
            ]
            intro = ""
        else:
            try:
                topics, intro = await self.ai_client.initial_map(
                    field, current_problem, learning_background, mode, db=db
                )
            except Exception as exc:  # noqa: BLE001
                print(f"[knowledge_map] DeepSeek initial map fallback: {exc}")
                topics = default_topics(field)
                intro = ""

        # 递归创建一级 + 二级节点;一棵完整的两层知识树
        created_titles: list[str] = []
        taken_titles: set[str] = set()
        for index, item in enumerate(topics, start=1):
            title_value = str(item.get("title") or "未命名节点")[:50]
            if _looks_similar(title_value, taken_titles):
                continue
            level1 = await _make_node(
                db,
                session_id=session.id,
                title=title_value,
                parent_id=root.id,
                depth=1,
                sort_order=index,
                status="pending",
                relevance=int(item.get("relevance", 0)),
                importance=int(item.get("importance", 2)),
                relevance_score=int(item.get("relevance_score", 2)),
                difficulty=int(item.get("difficulty", 2)),
                summary=str(item.get("summary") or ""),
            )
            created_titles.append(title_value)
            taken_titles.add(title_value)

            children_raw = item.get("children") if isinstance(item.get("children"), list) else []
            for sub_index, sub_item in enumerate(children_raw, start=1):
                sub_title = str(sub_item.get("title") or "").strip()[:50]
                if not sub_title or _looks_similar(sub_title, taken_titles):
                    continue
                await _make_node(
                    db,
                    session_id=session.id,
                    title=sub_title,
                    parent_id=level1.id,
                    depth=2,
                    sort_order=sub_index,
                    status="pending",
                    relevance=int(sub_item.get("relevance", 0)),
                    importance=int(sub_item.get("importance", 2)),
                    relevance_score=int(sub_item.get("relevance_score", 2)),
                    difficulty=int(sub_item.get("difficulty", 2)),
                    summary=str(sub_item.get("summary") or ""),
                )
                taken_titles.add(sub_title)

        if not intro:
            if topics_override is not None:
                intro = (
                    f"{field}的学习重点不是背概念,而是回答当前问题:{current_problem}。\n\n"
                    "主干已经按你确认的列表就位,我正在为每一支补充具体内容,边长边看吧。"
                )
            else:
                intro = (
                    f"{field}的学习重点不是背概念,而是回答当前问题:{current_problem}。\n\n"
                    "右侧已经为你拆好完整的知识地图。点击任意卡片就能进入对应学习,"
                    "或者直接点下方的「下一个」按钮按推荐顺序往下走。"
                )

        # 首条 intro 默认带"下一个知识点"按钮,直接接到树的第一个节点
        nodes_now = await _fetch_nodes(db, session.id)
        first_candidates = _next_pending_candidates(nodes_now, root.id)
        # 首条 intro 只给"下一个",不给"没听懂"(没什么可重讲的)
        intro_actions = _build_fixed_next_actions(first_candidates)

        db.add(
            Message(
                id=new_id("msg"),
                session_id=session.id,
                node_id=root.id,
                role="assistant",
                content=intro,
                next_actions=intro_actions,
            )
        )
        await db.flush()

        nodes = await _fetch_nodes(db, session.id)
        messages = await _fetch_messages(db, session.id)
        return {
            "session_id": session.id,
            "current_node_id": root.id,
            "initial_nodes": nodes,
            "messages": messages,
        }

    # --------------------------------------------------------------------- read

    async def grow_children_stream(
        self,
        db: AsyncSession,
        session_id: str,
        *,
        mode: str = "Lite",
    ) -> "collections.abc.AsyncIterator[tuple[str, dict[str, Any]]]":
        """预览-确认流程的"长出 children"阶段:对所有 children 为空的 level-1 节点,
        并发跑 LLM 生成 children,按原始 sort_order 顺序 yield 事件。

        yields:
          ("branch_done", {parent_id, parent_title, children: [...NodeOut-shape]})
          ("all_done", {})

        每个 branch_done 之间会 commit,保证用户中途断开也不丢已生成的 children。
        """
        session = await db.get(LearningSession, session_id)
        if not session:
            raise LookupError("session 不存在")
        nodes = await _fetch_nodes(db, session_id)
        by_parent: dict[str, list[KnowledgeNode]] = collections.defaultdict(list)
        for n in nodes:
            if n.parent_id:
                by_parent[n.parent_id].append(n)
        # 只为还没有 children 的 level-1 节点跑 LLM(允许重复进入也不重复生成)
        targets = sorted(
            (n for n in nodes if n.depth == 1 and not by_parent.get(n.id)),
            key=lambda n: n.sort_order,
        )
        if not targets:
            yield ("all_done", {})
            return

        field = session.field or "新的学习主题"
        current_problem = session.current_problem or ""
        # 并发跑所有 LLM,FIFO emit:每个 trunk 早出来也排队等前面的 emit 完
        tasks = [
            asyncio.create_task(
                self.ai_client.expand_topic_children(
                    field, current_problem, t.title, t.summary or "", mode
                )
            )
            for t in targets
        ]
        taken_titles: set[str] = {n.title for n in nodes}

        for target, task in zip(targets, tasks):
            try:
                children_raw = await task
            except Exception as exc:  # noqa: BLE001
                print(f"[knowledge_map] grow_children failed for {target.title}: {exc}")
                children_raw = []
            # 两遍:先建完同支所有兄弟(此时才有 id),再把 prerequisite_titles 映射成兄弟 id。
            made: list[tuple[KnowledgeNode, list[str]]] = []  # (节点, 它依赖的兄弟 title)
            title_to_id: dict[str, str] = {}
            for sub_index, sub in enumerate(children_raw, start=1):
                sub_title = str(sub.get("title") or "").strip()[:50]
                if not sub_title or _looks_similar(sub_title, taken_titles):
                    continue
                child = await _make_node(
                    db,
                    session_id=session_id,
                    title=sub_title,
                    parent_id=target.id,
                    depth=2,
                    sort_order=sub_index,
                    status="pending",
                    relevance=int(sub.get("relevance", 0)),
                    importance=int(sub.get("importance", 2)),
                    relevance_score=int(sub.get("relevance_score", 2)),
                    difficulty=int(sub.get("difficulty", 2)),
                    summary=str(sub.get("summary") or ""),
                )
                taken_titles.add(sub_title)
                raw_prereqs = sub.get("prerequisite_titles")
                prereq_titles = (
                    [str(p).strip() for p in raw_prereqs if str(p).strip()]
                    if isinstance(raw_prereqs, list)
                    else []
                )
                made.append((child, prereq_titles))
                title_to_id[sub_title] = child.id

            # 解析依赖:只认本支兄弟 title(方案A 同组依赖),指向自己/不存在的丢弃
            created: list[dict[str, Any]] = []
            for child, prereq_titles in made:
                resolved = [
                    title_to_id[t]
                    for t in prereq_titles
                    if t in title_to_id and title_to_id[t] != child.id
                ]
                # 去重并保持顺序
                child.prerequisite_ids = list(dict.fromkeys(resolved))
                created.append(
                    {
                        "id": child.id,
                        "session_id": child.session_id,
                        "parent_id": child.parent_id,
                        "title": child.title,
                        "summary": child.summary or "",
                        "content": child.content or "",
                        "status": child.status,
                        "relevance": child.relevance,
                        "importance": child.importance,
                        "relevance_score": child.relevance_score,
                        "difficulty": child.difficulty,
                        "depth": child.depth,
                        "sort_order": child.sort_order,
                        "prerequisite_ids": child.prerequisite_ids,
                        "collapsed": child.collapsed,
                        "created_from_message_id": child.created_from_message_id,
                        "created_at": child.created_at.isoformat() if child.created_at else None,
                        "updated_at": child.updated_at.isoformat() if child.updated_at else None,
                    }
                )
            # 每支 commit 一次,流式过程中也持久化,避免用户断网时全丢
            await db.commit()
            yield (
                "branch_done",
                {
                    "parent_id": target.id,
                    "parent_title": target.title,
                    "children": created,
                },
            )
        yield ("all_done", {})

    async def first_principles_stream(
        self,
        db: AsyncSession,
        session_id: str,
        node_id: str,
        *,
        max_depth: int = 6,
        is_disconnected: "collections.abc.Callable[[], collections.abc.Awaitable[bool]] | None" = None,
    ) -> "collections.abc.AsyncIterator[tuple[str, dict[str, Any]]]":
        """第一性原理"拆到底":从 node_id 起点,逐层往下拆出更底层的前置依赖。

        逐层深度优先:每处理一个节点,调一次 LLM 找它的 1-3 个底层依赖,建成【子节点】挂下去,
        emit 一条 `fp_layer`,commit,然后把【非触底】的子节点压栈继续拆。

        停止条件(三选一即停该分支):
          - LLM 判定 is_fundamental(触底:基础公理/最小单位)
          - LLM 没给出任何底层依赖
          - 相对起点的深度达到 max_depth(兜底防失控)

        全局停止:每次 LLM 调用前检查 is_disconnected();客户端断开就整体收手,不再烧 token。

        yields:
          ("fp_layer", {parent_id, parent_title, children:[...NodeOut-shape], reached_bottom})
          ("fp_done", {total_created})
        """
        session = await db.get(LearningSession, session_id)
        if not session:
            raise LookupError("session 不存在")
        start = await _get_node(db, node_id)
        if not start or start.session_id != session_id:
            raise LookupError("节点不存在")

        field = session.field or "新的学习主题"
        current_problem = session.current_problem or ""
        all_nodes = await _fetch_nodes(db, session_id)
        taken_titles: set[str] = {n.title for n in all_nodes}

        # 栈元素:(节点对象, 相对起点的深度)。起点相对深度 = 0
        stack: list[tuple[KnowledgeNode, int]] = [(start, 0)]
        total_created = 0

        while stack:
            node, rel_depth = stack.pop()
            if rel_depth >= max_depth:
                continue
            if is_disconnected is not None and await is_disconnected():
                break

            node_path = await _node_path(db, node.id)
            try:
                result = await self.ai_client.expand_first_principles(
                    field,
                    current_problem,
                    node.title,
                    node.summary or "",
                    node_path,
                    rel_depth,
                    max_depth,
                )
            except Exception as exc:  # noqa: BLE001
                print(f"[knowledge_map] first_principles failed for {node.title}: {exc}")
                result = {"children": [], "is_fundamental": False}

            raw_children = result.get("children") or []
            reached_bottom = bool(result.get("is_fundamental")) or not raw_children

            if reached_bottom:
                # 当前节点触底:若 LLM 明确说 fundamental 就标记它,emit 空 layer 让前端知道这支收口
                if result.get("is_fundamental") and not node.is_fundamental:
                    node.is_fundamental = True
                    await db.commit()
                yield (
                    "fp_layer",
                    {
                        "parent_id": node.id,
                        "parent_title": node.title,
                        "children": [],
                        "reached_bottom": True,
                    },
                )
                continue

            existing_siblings = [n for n in all_nodes if n.parent_id == node.id]
            sort_base = len(existing_siblings)
            created: list[dict[str, Any]] = []
            new_children: list[KnowledgeNode] = []
            for offset, sub in enumerate(raw_children, start=1):
                sub_title = str(sub.get("title") or "").strip()[:50]
                if not sub_title or _looks_similar(sub_title, taken_titles):
                    continue
                child = await _make_node(
                    db,
                    session_id=session_id,
                    title=sub_title,
                    parent_id=node.id,
                    depth=node.depth + 1,
                    sort_order=sort_base + offset,
                    status="pending",
                    summary=str(sub.get("summary") or ""),
                    is_fundamental=bool(sub.get("is_fundamental", False)),
                    fp_relation=str(sub.get("relation") or sub.get("fp_relation") or "")[:80],
                    fp_reason=str(sub.get("why") or sub.get("fp_reason") or "")[:400],
                )
                taken_titles.add(sub_title)
                all_nodes.append(child)
                new_children.append(child)
                created.append(_node_to_dict(child))
            await db.commit()
            total_created += len(created)

            yield (
                "fp_layer",
                {
                    "parent_id": node.id,
                    "parent_title": node.title,
                    "children": created,
                    "reached_bottom": False,
                },
            )

            # 非触底的新子节点继续往下拆(深度优先:逆序压栈,保持从左到右处理顺序)
            for child in reversed(new_children):
                if not child.is_fundamental:
                    stack.append((child, rel_depth + 1))

        yield ("fp_done", {"total_created": total_created})

    async def get_tree(self, db: AsyncSession, session_id: str) -> list[KnowledgeNode]:
        return await _fetch_nodes(db, session_id)

    async def get_messages(self, db: AsyncSession, session_id: str) -> list[Message]:
        return await _fetch_messages(db, session_id)

    async def search_nodes(
        self,
        db: AsyncSession,
        session_id: str,
        query: str,
        *,
        limit: int = 5,
    ) -> dict[str, Any]:
        """让 AI 对当前 session 的所有节点按相关度打分,返回 top-N。

        前端用于"右侧搜索框 → 列候选 → 用户挑一个聚焦",不直接跳转。
        典型 50-100 个节点,一次 LLM 调用足够;不做向量索引,够用就好。
        """
        query = (query or "").strip()
        if not query:
            return {"query": "", "results": []}
        nodes = await _fetch_nodes(db, session_id)
        # 去掉根节点(它是整树标题,搜出来意义不大)
        candidates = [n for n in nodes if n.parent_id]
        if not candidates:
            return {"query": query, "results": []}

        # 优化E:树大了之后,把"全部节点喂给 LLM 打分"会让 prompt 随节点数线性膨胀。
        # 先用本地零成本的关键词/模糊匹配粗筛到 RANK_PREFILTER_MAX 个,再交给 LLM 精排。
        if len(candidates) > RANK_PREFILTER_MAX:
            candidates = _prefilter_nodes_by_query(candidates, query, RANK_PREFILTER_MAX)

        # 节点行精简到 title + summary 前 80 字,控制 prompt 体积
        node_lines = []
        for index, node in enumerate(candidates, start=1):
            summary = (node.summary or "").replace("\n", " ").strip()[:80]
            node_lines.append(f"{index}. id={node.id} | {node.title} | {summary}")

        prompt = (
            f"用户在一棵知识树里搜索,想找到和查询最相关的节点。\n"
            f"查询:{query}\n\n"
            f"候选节点(共 {len(candidates)} 个,id|标题|摘要):\n"
            + "\n".join(node_lines)
            + f"\n\n请挑出最多 {limit} 个最相关的节点,按相关度从高到低排列。\n"
            "只返回 JSON,格式:\n"
            '{ "results": [ { "node_id": "...", "score": 3, "reason": "20字以内,解释为什么相关" } ] }\n'
            "score: 3=非常相关 / 2=部分相关 / 1=弱相关。\n"
            "如果一个都不相关,返回空数组。\n"
            "node_id 必须从上面的候选列表里挑,不要编造。"
        )
        try:
            data = await self.ai_client.chat(
                [
                    {"role": "system", "content": "你只输出 JSON,不输出 Markdown 或解释。"},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.15,
                purpose="rank_nodes",
                session_id=session_id,
                db=db,
            )
        except Exception as exc:  # noqa: BLE001
            print(f"[knowledge_map] node search fallback: {exc}")
            return {"query": query, "results": []}

        raw_results = data.get("results") if isinstance(data, dict) else None
        if not isinstance(raw_results, list):
            return {"query": query, "results": []}

        node_map = {n.id: n for n in candidates}
        seen_ids: set[str] = set()
        results: list[dict[str, Any]] = []
        for item in raw_results:
            if not isinstance(item, dict):
                continue
            node_id = str(item.get("node_id") or "").strip()
            if node_id not in node_map or node_id in seen_ids:
                continue
            seen_ids.add(node_id)
            try:
                score = int(item.get("score", 2))
            except (TypeError, ValueError):
                score = 2
            score = max(1, min(3, score))
            node = node_map[node_id]
            results.append(
                {
                    "node_id": node.id,
                    "title": node.title,
                    "summary": (node.summary or "")[:200],
                    "score": score,
                    "reason": str(item.get("reason") or "").strip()[:120],
                }
            )
            if len(results) >= limit:
                break
        return {"query": query, "results": results}

    async def ad_hoc_web_search(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        """用户划词触发的临时联网搜索:不关联任何 message,直接拿原始 query 调搜索 provider。

        和 deep_search_message 的差异:不刷新任何 message.search_sources,只把结果原样返回。
        失败时返回一条 status=error 的占位 source 让前端展示。
        """
        clean_query = " ".join(str(query or "").split())[:400]
        if not clean_query:
            return []
        try:
            sources = await self.ai_client.web_search_query(clean_query, limit=limit)
        except RuntimeError as exc:
            return [{
                "status": "error",
                "query": clean_query,
                "title": "",
                "link": "",
                "media": "",
                "publish_date": "",
                "content": str(exc)[:1200],
                "refer": "",
            }]
        if not sources:
            return [{
                "status": "empty",
                "query": clean_query,
                "title": "",
                "link": "",
                "media": "",
                "publish_date": "",
                "content": "",
                "refer": "",
            }]
        return sources

    async def deep_search_message(self, db: AsyncSession, message_id: str) -> Message:
        message = await db.get(Message, message_id)
        if not message:
            raise LookupError("消息不存在")
        if message.role != "assistant":
            raise ValueError("只能对 AI 回复做深度联网搜索")
        query = self._search_query_for_message(message)
        if not query:
            raise ValueError("没有可用于深度搜索的关键词")
        try:
            sources = await self.ai_client.web_search_query(query, limit=20)
            deep_sources = [{**source, "status": "deep_result"} for source in sources[:20]]
            if not deep_sources:
                deep_sources = [{
                    "status": "deep_empty",
                    "query": query,
                    "title": "",
                    "link": "",
                    "media": "",
                    "publish_date": "",
                    "content": "",
                    "refer": "",
                }]
        except Exception as exc:  # noqa: BLE001
            deep_sources = [{
                "status": "deep_error",
                "query": query,
                "title": "",
                "link": "",
                "media": "",
                "publish_date": "",
                "content": str(exc)[:1200],
                "refer": "",
            }]
        base_sources = [
            source for source in (message.search_sources or [])
            if str(source.get("status") or "") not in {"deep_result", "deep_empty", "deep_error"}
        ]
        message.search_sources = [*base_sources, *deep_sources]
        await db.flush()
        await db.refresh(message)
        return message

    async def reanswer_with_deep_search(self, db: AsyncSession, message_id: str, mode: str = "Lite") -> Message:
        source_message = await db.get(Message, message_id)
        if not source_message:
            raise LookupError("消息不存在")
        deep_sources = [
            source for source in (source_message.search_sources or [])
            if str(source.get("status") or "") == "deep_result"
        ]
        if not deep_sources:
            raise ValueError("请先完成深度联网搜索")

        session = await db.get(LearningSession, source_message.session_id)
        messages = await _fetch_messages(db, source_message.session_id)
        source_index = next((i for i, item in enumerate(messages) if item.id == source_message.id), -1)
        previous_user = None
        if source_index > 0:
            for item in reversed(messages[:source_index]):
                if item.role == "user":
                    previous_user = item
                    break
        node = await self._resolve_current_node(db, source_message.session_id, source_message.node_id)
        prompt = {
            "field": session.field if session else "",
            "current_problem": session.current_problem if session else "",
            "learning_background": _learning_background(session),
            "current_node": {
                "title": node.title,
                "summary": node.summary,
                "path": await _node_path(db, node.id),
            },
            "original_user_message": previous_user.content if previous_user else "",
            "previous_answer": source_message.content,
            "deep_search_sources": deep_sources,
            "thinking_mode": thinking_mode_profile(normalize_mode(mode)),
            "task": "reanswer_with_deep_search",
            "instructions": get_prompt_store().format_lines("deep_reanswer.instructions"),
            "json_schema": {"reply": "用深度搜索结果重写后的 Markdown 回答"},
        }
        data = await self.ai_client.chat(
            _cached_chat_messages("你只输出合法 JSON。顶层必须有 reply 字段。", prompt),
            temperature=0.35,
            enable_web_search=False,
            purpose="deep_reanswer",
            session_id=source_message.session_id,
            db=db,
        )
        reply = _reply_from_ai_data(data)
        if not reply:
            raise ValueError("LLM JSON 缺少 reply 字段")
        latest_nodes = await _fetch_nodes(db, source_message.session_id)
        current_node_for_actions = next((n for n in latest_nodes if n.id == node.id), node)
        message = Message(
            id=new_id("msg"),
            session_id=source_message.session_id,
            node_id=source_message.node_id,
            role="assistant",
            content=reply,
            next_actions=_build_fixed_next_actions(
                _next_pending_candidates(latest_nodes, node.id),
                current_node=current_node_for_actions,
            ),
            search_sources=source_message.search_sources or [],
        )
        db.add(message)
        await db.flush()
        await db.refresh(message)
        return message

    @staticmethod
    def _search_query_for_message(message: Message) -> str:
        for source in message.search_sources or []:
            query = str(source.get("query") or "").strip()
            if query:
                return query[:120]
        text = re.sub(r"\s+", " ", message.content or "").strip()
        return text[:120]

    async def list_sessions(
        self,
        db: AsyncSession,
        search: str = "",
        *,
        user_id: str | None = None,
    ) -> list[dict[str, Any]]:
        search = search.strip()
        message_count = func.count(func.distinct(Message.id)).label("message_count")
        node_count = func.count(func.distinct(KnowledgeNode.id)).label("node_count")
        stmt = (
            select(
                LearningSession.id,
                LearningSession.title,
                LearningSession.field,
                LearningSession.current_problem,
                LearningSession.learning_background,
                LearningSession.current_node_id,
                LearningSession.created_at,
                LearningSession.updated_at,
                message_count,
                node_count,
            )
            .select_from(LearningSession)
            .outerjoin(Message, Message.session_id == LearningSession.id)
            .outerjoin(KnowledgeNode, KnowledgeNode.session_id == LearningSession.id)
            .group_by(LearningSession.id)
            .order_by(LearningSession.updated_at.desc())
            .limit(80)
        )
        if user_id:
            stmt = stmt.where(LearningSession.user_id == user_id)
        if search:
            like_pattern = f"%{search}%"
            stmt = stmt.where(
                or_(
                    LearningSession.title.ilike(like_pattern),
                    LearningSession.field.ilike(like_pattern),
                    LearningSession.current_problem.ilike(like_pattern),
                    LearningSession.learning_background.ilike(like_pattern),
                )
            )
        rows = (await db.execute(stmt)).mappings().all()
        return [dict(row) for row in rows]

    # --------------------------------------------------------------------- write

    async def update_node(
        self, db: AsyncSession, node_id: str, payload: dict[str, Any]
    ) -> tuple[KnowledgeNode, list[KnowledgeNode]]:
        node = await db.get(KnowledgeNode, node_id)
        if not node:
            raise LookupError("Node not found")

        editable_text = {"title", "summary", "content"}
        editable_metric = {"importance", "relevance_score", "difficulty"}
        applied: dict[str, Any] = {}

        for key in editable_text:
            if key in payload and payload[key] is not None:
                setattr(node, key, payload[key])
                applied[key] = payload[key]
        for key in editable_metric:
            if key in payload and payload[key] is not None:
                value = clamp_metric(payload[key])
                setattr(node, key, value)
                applied[key] = value
        if "status" in payload and payload["status"] is not None:
            if payload["status"] not in NODE_STATUSES:
                raise ValueError("不支持的状态")
            node.status = payload["status"]
            applied["status"] = payload["status"]
        if "collapsed" in payload and payload["collapsed"] is not None:
            node.collapsed = bool(payload["collapsed"])
            applied["collapsed"] = node.collapsed

        if not applied:
            raise ValueError("没有可更新的字段")

        node.updated_at = now_utc()
        db.add(_event(node.session_id, node.id, "node_updated", applied))
        await db.flush()
        nodes = await _fetch_nodes(db, node.session_id)
        return node, nodes

    # --------------------------------------------------------- highlights

    async def update_message_highlights(
        self, db: AsyncSession, message_id: str, highlights: list[dict[str, Any]]
    ) -> Message:
        message = await db.get(Message, message_id)
        if not message:
            raise LookupError("Message not found")
        cleaned: list[dict[str, Any]] = []
        content_len = len(message.content or "")
        for item in highlights:
            try:
                start = int(item.get("start"))
                end = int(item.get("end"))
            except (TypeError, ValueError):
                continue
            text = str(item.get("text") or "").strip()
            if start < 0 or end <= start or end > content_len + 4 or not text:
                continue
            cleaned.append({"start": start, "end": end, "text": text[:600]})
        # 排序 + 合并重叠区间,避免存进去就乱
        cleaned.sort(key=lambda h: (h["start"], h["end"]))
        merged: list[dict[str, Any]] = []
        for item in cleaned:
            if merged and item["start"] <= merged[-1]["end"]:
                prev = merged[-1]
                if item["end"] > prev["end"]:
                    prev["end"] = item["end"]
                    prev["text"] = (message.content or "")[prev["start"] : prev["end"]]
            else:
                merged.append(dict(item))
        message.highlights = merged
        await db.flush()
        return message

    async def create_message_peek(
        self, db: AsyncSession, message_id: str, payload: dict[str, Any]
    ) -> Message:
        message = await db.get(Message, message_id)
        if not message:
            raise LookupError("Message not found")
        try:
            start = int(payload.get("start"))
            end = int(payload.get("end"))
        except (TypeError, ValueError) as exc:
            raise ValueError("start/end 不合法") from exc
        text = str(payload.get("text") or "").strip()
        if start < 0 or end <= start or not text:
            raise ValueError("划词范围不合法")

        mode = normalize_mode(payload.get("mode"))
        parent_peek_id = payload.get("parent_peek_id") or None
        source_kind = str(payload.get("source_kind") or "answer").strip()
        source_kind = "followup" if source_kind == "followup" else "answer"
        source_followup_id = str(payload.get("source_followup_id") or "").strip() or None
        peeks = [dict(item) for item in (message.peeks or []) if isinstance(item, dict)]

        # 嵌套 peek:start/end 默认相对父 peek answer;也可以相对某条 followup answer。
        parent_peek: dict[str, Any] | None = None
        if parent_peek_id:
            parent_peek = next((p for p in peeks if p.get("id") == parent_peek_id), None)
            if parent_peek is None:
                raise LookupError("Parent peek not found")
            if source_kind == "followup":
                followup = next(
                    (
                        item
                        for item in (parent_peek.get("followups") or [])
                        if isinstance(item, dict) and item.get("id") == source_followup_id
                    ),
                    None,
                )
                if followup is None:
                    raise LookupError("Peek followup not found")
                source_text = str(followup.get("answer") or "")
                if end > len(source_text) + 4:
                    raise ValueError("划词范围超出父 peek 追问答案长度")
                source_override = source_text
            else:
                source_followup_id = None
                source_text = str(parent_peek.get("answer") or "")
                if end > len(source_text) + 4:
                    raise ValueError("划词范围超出父 peek 答案长度")
                source_override = source_text
        else:
            source_kind = "answer"
            source_followup_id = None
            content = message.content or ""
            if end > len(content) + 4:
                raise ValueError("划词范围超出消息内容长度")
            source_override = None

        answer = await self._build_peek_answer(
            db,
            message,
            text=text,
            mode=mode,
            is_followup=False,
            source_paragraph_override=source_override,
        )

        # 同 parent 下同区间重复问时更新原锚点,避免长出多个重叠解释。
        # 跨不同 parent 的同区间是真正不同的 peek (语义不同),不去重。
        replaced = False
        for item in peeks:
            if (
                (item.get("parent_peek_id") or None) == parent_peek_id
                and (item.get("source_kind") or "answer") == source_kind
                and (item.get("source_followup_id") or None) == source_followup_id
                and int(item.get("start", -1)) == start
                and int(item.get("end", -1)) == end
            ):
                item.update({"text": text[:600], "answer": answer, "status": "answered"})
                replaced = True
                new_peek_id = item["id"]
                break
        if not replaced:
            new_peek_id = new_id("peek")
            peeks.append(
                {
                    "id": new_peek_id,
                    "parent_peek_id": parent_peek_id,
                    "source_kind": source_kind,
                    "source_followup_id": source_followup_id,
                    "start": start,
                    "end": end,
                    "text": text[:600],
                    "answer": answer,
                    "status": "answered",
                    "promoted_node_id": None,
                    "followups": [],
                }
            )
        # 排序键:先按 parent_peek_id(None 排前) 分组,组内按 start/end。
        # 这样老的"按 start 升序"行为对 root peek 不变,嵌套 peek 各自和同父 peek 排在一起。
        peeks.sort(
            key=lambda item: (
                item.get("parent_peek_id") or "",
                int(item.get("start", 0)),
                int(item.get("end", 0)),
            )
        )
        message.peeks = peeks
        await db.flush()
        return message

    async def add_peek_followup(
        self, db: AsyncSession, message_id: str, peek_id: str, payload: dict[str, Any]
    ) -> Message:
        message = await db.get(Message, message_id)
        if not message:
            raise LookupError("Message not found")
        question = str(payload.get("question") or "").strip()
        if not question:
            raise ValueError("question 不能为空")
        mode = normalize_mode(payload.get("mode"))
        peeks = [dict(item) for item in (message.peeks or []) if isinstance(item, dict)]
        target = next((item for item in peeks if item.get("id") == peek_id), None)
        if not target:
            raise LookupError("Peek not found")
        # 这一轮追问之前已有的 follow-up 历史,作为 AI 的短期记忆传过去,
        # 让它能做代词消解和话题追踪(否则每次追问都只看到第一轮 answer)。
        prior_followups = [
            dict(item) for item in (target.get("followups") or []) if isinstance(item, dict)
        ]
        answer = await self._build_peek_answer(
            db,
            message,
            text=question,
            mode=mode,
            is_followup=True,
            parent_answer=str(target.get("answer") or ""),
            anchor_text=str(target.get("text") or ""),
            followup_history=prior_followups,
        )
        followups = list(prior_followups)
        followups.append({"id": new_id("peekq"), "question": question[:600], "answer": answer})
        target["followups"] = followups[-8:]
        message.peeks = peeks
        await db.flush()
        return message

    # --------------------------------------------------------- subdivision options

    async def suggest_subdivision_options(
        self,
        db: AsyncSession,
        session_id: str,
        node_id: str,
        mode: str,
    ) -> dict[str, Any]:
        """让 AI 根据上下文给 3 个"拆分角度"+一个"先别拆"提醒。

        不写库,不创建节点,只返回结构化 JSON 给前端弹浮层用。
        """
        mode = normalize_mode(mode)
        node = await _get_node(db, node_id)
        if not node or node.session_id != session_id:
            raise LookupError("节点不存在")
        session = await db.get(LearningSession, session_id)
        all_nodes = await _fetch_nodes(db, session_id)
        existing_children = [n for n in all_nodes if n.parent_id == node.id]
        recent = (await _fetch_messages(db, session_id))[-8:]

        # 已经用过的 angle:写在中间分支 summary 里的"按 X 角度拆"。
        # 不需要 100% 精确,主要是给 AI 一个"别推荐同一角度"的暗示。
        used_angles: list[str] = []
        for child in existing_children:
            summary = (child.summary or "")
            match = re.search(r"按([^角]{1,8})角度拆", summary)
            if match:
                used_angles.append(match.group(1).strip())

        prompt = {
            "task": "suggest_subdivision_angles",
            "field": session.field if session else "",
            "current_problem": session.current_problem if session else "",
            "learning_background": _learning_background(session),
            "thinking_mode": thinking_mode_profile(mode),
            "current_node": {
                "title": node.title,
                "summary": node.summary,
                "path": await _node_path(db, node.id),
                "depth": node.depth,
                "status": node.status,
            },
            "existing_children_titles": [n.title for n in existing_children][:12],
            "already_used_angles": used_angles,
            "recent_messages": [
                {"role": m.role, "content": (m.content or "")[:600], "node_id": m.node_id}
                for m in recent
            ],
            "instructions": get_prompt_store().format_lines(
                "subdivision_options.instructions",
                node_depth=node.depth,
            ),
            "json_schema": {
                "options": [
                    {
                        "angle": "类型分类",
                        "label": "按几种类型来分",
                        "rationale": "这个节点是一个'有不同形态的概念',先看大格局最快",
                    }
                ],
                "caution": None,
            },
        }

        try:
            data = await self.ai_client.chat(
                _cached_chat_messages("你只输出合法 JSON。", prompt),
                temperature=0.4,
                purpose="suggest_subdivision",
                session_id=session_id,
                db=db,
            )
        except Exception as exc:  # noqa: BLE001
            print(f"[knowledge_map] subdivision options fallback: {exc}")
            return _fallback_subdivision_options(node)

        raw_options = data.get("options") if isinstance(data.get("options"), list) else []
        options: list[dict[str, str]] = []
        for item in raw_options[:3]:
            if not isinstance(item, dict):
                continue
            angle = str(item.get("angle") or "").strip()
            label = str(item.get("label") or "").strip()
            rationale = str(item.get("rationale") or "").strip()
            if not angle or not label or not rationale:
                continue
            options.append({
                "angle": angle[:24],
                "label": label[:40],
                "rationale": rationale[:120],
            })
        # caution 只在 AI 给了完整对象时才有;否则保持 None,前端就不渲染"先别拆"那块
        caution_payload: dict[str, str] | None = None
        caution_raw = data.get("caution") if isinstance(data.get("caution"), dict) else None
        if caution_raw:
            caution_rationale = str(caution_raw.get("rationale") or "").strip()
            if caution_rationale:
                caution_label = str(caution_raw.get("label") or "").strip() or "先别拆"
                caution_payload = {
                    "label": caution_label[:40],
                    "rationale": caution_rationale[:400],
                }
        # 深度兜底:AI 在深节点上仍然不给 caution 时,我们补一个,避免用户在很深的
        # 分支上完全收不到"该回头了"的信号。阈值取 node.depth >= 4。
        if caution_payload is None and node.depth >= 4:
            current_problem = (session.current_problem if session else "") or "你的学习目标"
            node_path = await _node_path(db, node.id)
            caution_payload = {
                "label": "先别拆",
                "rationale": (
                    f"你已经挖到第 {node.depth} 层(路径:{node_path})。"
                    f"继续拆「{node.title}」很可能变成在细节里堆细节,"
                    f"对「{current_problem}」这条主线的边际收益已经不大了。"
                    "建议先回上一层把整条主线串起来,确认还有哪个分支没走过,再决定要不要回来挖。"
                ),
            }
        if not options:
            return _fallback_subdivision_options(node)
        return {"options": options, "caution": caution_payload}

    async def multi_angle_subdivide(
        self,
        db: AsyncSession,
        session_id: str,
        node_id: str,
        mode: str,
        angles: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """一次性按 N 个角度并拆,每个角度产出一个中间分支 + 它的子节点。

        前端是在拆分浮层里点了"按 3 个角度全拆"主操作时触发的。
        每个角度的具体子节点数量按 mode 决定。
        """
        mode = normalize_mode(mode)
        if not angles:
            raise ValueError("angles 不能为空")
        node = await _get_node(db, node_id)
        if not node or node.session_id != session_id:
            raise LookupError("节点不存在")
        session = await db.get(LearningSession, session_id)
        profile = thinking_mode_profile(mode)

        # 每组(每个 angle 下)的子节点目标数量
        per_angle = {"Lite": 3, "Medium": 3, "Zen": 4}.get(mode, 3)

        all_nodes_before = await _fetch_nodes(db, session_id)
        existing_titles = {n.title.strip() for n in all_nodes_before if n.title}
        recent = (await _fetch_messages(db, session_id))[-6:]

        # 给 AI 的 angle 列表精简成 angle+label 形式
        angle_list = [
            {
                "angle": str(a.get("angle") or "").strip()[:24] or "未命名角度",
                "label": str(a.get("label") or "").strip()[:40] or "",
                "rationale": str(a.get("rationale") or "").strip()[:120] or "",
            }
            for a in angles
        ][:4]

        prompt = {
            "task": "multi_angle_subdivide",
            "field": session.field if session else "",
            "current_problem": session.current_problem if session else "",
            "learning_background": _learning_background(session),
            "thinking_mode": profile,
            "current_node": {
                "title": node.title,
                "summary": node.summary,
                "path": await _node_path(db, node.id),
            },
            "angles": angle_list,
            "per_angle_child_count": per_angle,
            "existing_titles": sorted(existing_titles)[:80],
            "recent_messages": [
                {"role": m.role, "content": (m.content or "")[:600], "node_id": m.node_id}
                for m in recent
            ],
            "instructions": get_prompt_store().format_lines(
                "multi_angle_subdivide.instructions",
                per_angle_child_count=per_angle,
            ),
            "json_schema": {
                "reply": "60-120 字过渡段",
                "groups": [
                    {
                        "middle_title": "护城河的几种来源",
                        "middle_summary": "按类型分类看。从品牌、网络效应、规模、专利等几种来源拆护城河。",
                        "children": [
                            {
                                "title": "品牌护城河",
                                "summary": "依赖消费者长期心智的护城河。",
                                "importance": 3,
                                "relevance_score": 3,
                                "difficulty": 2,
                            }
                        ],
                    }
                ],
            },
        }

        data = await self.ai_client.chat(
            _cached_chat_messages("你只输出合法 JSON,不要 Markdown。", prompt),
            temperature=0.45,
            purpose="multi_angle_subdivide",
            session_id=session_id,
            db=db,
        )

        reply = str(data.get("reply") or "").strip()
        if not reply:
            reply = f"按 {len(angle_list)} 个角度把「{node.title}」一次拆好了,挑一组开始看。"

        groups_raw = data.get("groups") if isinstance(data.get("groups"), list) else []
        if not groups_raw:
            raise ValueError("AI 没返回 groups")

        # 创建中间分支 + 子节点。跨整棵树 dedup,避免和已有节点撞名。
        taken_titles = set(existing_titles)
        created_ids: list[str] = []
        first_middle_id: str | None = None

        # 这一次的"用户消息":前端不会经过 receive_message 走 user 消息路径,
        # 所以这里自己造一条"用户在拆分浮层选了一次性按 N 个角度拆"的 user 消息。
        labels_text = "、".join(a["label"] or a["angle"] for a in angle_list)
        user_message = Message(
            id=new_id("msg"),
            session_id=session_id,
            node_id=node.id,
            role="user",
            content=f"请围绕「{node.title}」一次性按【{labels_text}】这几个角度全部拆开。",
        )
        db.add(user_message)
        await db.flush()

        sibling_count = await db.scalar(
            select(func.count()).where(KnowledgeNode.parent_id == node.id)
        ) or 0

        for group_idx, (angle_item, group) in enumerate(zip(angle_list, groups_raw[: len(angle_list)])):
            if not isinstance(group, dict):
                continue
            middle_title = str(group.get("middle_title") or "").strip()[:50]
            if not middle_title or _looks_similar(middle_title, taken_titles):
                # 退一步:用 angle label 当兜底标题
                fallback_title = f"按{angle_item['angle']}看「{node.title[:14]}」"
                if _looks_similar(fallback_title, taken_titles):
                    continue
                middle_title = fallback_title
            middle_summary = str(group.get("middle_summary") or "").strip()[:160]
            if not middle_summary.startswith("按"):
                middle_summary = f"按{angle_item['angle']}角度看。{middle_summary}".strip()
            middle = await _make_node(
                db,
                session_id=session_id,
                title=middle_title,
                parent_id=node.id,
                depth=node.depth + 1,
                sort_order=sibling_count + group_idx + 1,
                status="deepening",
                relevance=1,
                importance=2,
                relevance_score=3,
                difficulty=2,
                summary=middle_summary,
                message_id=user_message.id,
            )
            created_ids.append(middle.id)
            taken_titles.add(middle_title)
            if first_middle_id is None:
                first_middle_id = middle.id

            raw_children = group.get("children") if isinstance(group.get("children"), list) else []
            calibrated = calibrate_relevance_distribution(raw_children[:per_angle + 1])
            for child_idx, child_raw in enumerate(calibrated, start=1):
                if not isinstance(child_raw, dict):
                    continue
                child_title = str(child_raw.get("title") or "").strip()[:50]
                if not child_title or _looks_similar(child_title, taken_titles):
                    continue
                child_summary = str(child_raw.get("summary") or "").strip()[:160]
                relevance_score = clamp_metric(
                    child_raw.get("relevance_score", 3 if child_raw.get("relevance") else 2)
                )
                child_node = await _make_node(
                    db,
                    session_id=session_id,
                    title=child_title,
                    parent_id=middle.id,
                    depth=middle.depth + 1,
                    sort_order=child_idx,
                    status="pending",
                    relevance=1 if relevance_score >= 3 else 0,
                    importance=clamp_metric(child_raw.get("importance", 2)),
                    relevance_score=relevance_score,
                    difficulty=clamp_metric(child_raw.get("difficulty", 2)),
                    summary=child_summary,
                    message_id=user_message.id,
                )
                created_ids.append(child_node.id)
                taken_titles.add(child_title)

        if not created_ids:
            raise ValueError("AI 返回的 groups 没能成功落库")

        # 把父节点状态置为 deepening,current_node_id 切到第一个新中间分支
        node.status = "deepening"
        node.updated_at = now_utc()
        target_current = first_middle_id or node.id
        if session is not None:
            session.current_node_id = target_current
            session.updated_at = now_utc()
        db.add(_event(session_id, node.id, "multi_angle_subdivided", {"angles": [a["angle"] for a in angle_list]}))

        # 写助手消息。导航按钮固定就两个:[下一个, 没听懂](见 _build_fixed_next_actions)
        all_nodes_now = await _fetch_nodes(db, session_id)
        current_node_for_actions = next((n for n in all_nodes_now if n.id == target_current), node)
        next_actions = _build_fixed_next_actions(
            _next_pending_candidates(all_nodes_now, target_current),
            current_node=current_node_for_actions,
        )
        assistant_message = Message(
            id=new_id("msg"),
            session_id=session_id,
            node_id=target_current,
            role="assistant",
            content=reply,
            next_actions=next_actions,
        )
        db.add(assistant_message)
        await db.flush()

        nodes_final = await _fetch_nodes(db, session_id)
        messages_final = await _fetch_messages(db, session_id)
        return {
            "reply": reply,
            "current_node_id": target_current,
            "created_node_ids": created_ids,
            "nodes": nodes_final,
            "messages": messages_final,
        }

    async def add_caution_note(
        self,
        db: AsyncSession,
        session_id: str,
        node_id: str,
        rationale: str,
        mode: str,
    ) -> Message:
        """用户在拆分浮层选了"先别拆"时,把 AI 给的理由作为一条 assistant 消息存下来。

        不动节点状态、不创建子节点,纯展示用。
        """
        node = await _get_node(db, node_id)
        if not node or node.session_id != session_id:
            raise LookupError("节点不存在")
        rationale_text = rationale.strip()
        if not rationale_text:
            raise ValueError("rationale 不能为空")
        body = (
            f"**先不拆「{node.title}」**\n\n{rationale_text}\n\n"
            "_这是你在「拆分」浮层里选了「先别拆」的记录。需要时随时再点卡片上的拆分。_"
        )
        message = Message(
            id=new_id("msg"),
            session_id=session_id,
            node_id=node.id,
            role="assistant",
            content=body,
            next_actions=[],
        )
        db.add(message)
        await db.flush()
        return message

    # --------------------------------------------------------- peek

    async def _build_peek_answer(
        self,
        db: AsyncSession,
        message: Message,
        *,
        text: str,
        mode: str,
        is_followup: bool,
        parent_answer: str = "",
        anchor_text: str = "",
        followup_history: list[dict[str, Any]] | None = None,
        source_paragraph_override: str | None = None,
    ) -> str:
        node = await _get_node(db, message.node_id)
        session = await db.get(LearningSession, message.session_id)
        limit = {"Lite": 180, "Medium": 360, "Zen": 760}.get(mode, 180)
        # 只保留最近 8 轮的 follow-up 历史。AI 拿到这个才能做代词消解和话题追踪。
        history_for_ai: list[dict[str, str]] = []
        for item in (followup_history or [])[-8:]:
            if not isinstance(item, dict):
                continue
            q = str(item.get("question") or "").strip()
            a = str(item.get("answer") or "").strip()
            if q and a:
                history_for_ai.append({"question": q[:600], "answer": a[:1000]})
        prompt = {
            "field": session.field if session else "",
            "current_problem": session.current_problem if session else "",
            "learning_background": _learning_background(session),
            "task": "peek_followup" if is_followup else "peek_definition",
            "current_node": {
                "title": node.title if node else "",
                "summary": node.summary if node else "",
                "path": await _node_path(db, node.id) if node else "当前对话",
            },
            # 嵌套 peek 时 source_paragraph 是父 peek 的 answer,不是消息正文 ——
            # AI 看到的是用户当前真正在读的那段文字,而不是更上层的语境。
            "source_paragraph": (source_paragraph_override or message.content or "")[:1600],
            "selected_text": anchor_text if is_followup else text,
            "followup_question": text if is_followup else "",
            "followup_subject_hint": _peek_followup_subject_hint(text, anchor_text) if is_followup else {},
            "parent_peek_answer": parent_answer[:1000],
            "anchor_text": anchor_text,
            "followup_history": history_for_ai,
            # 主体 instructions 走 prompt_store("peek.instructions");追问 vs 首轮的逻辑性条件
            # (是否复述锚点、是否消化 followup_history)是代码控制的硬逻辑,不开放编辑。
            "instructions": [
                *get_prompt_store().format_lines("peek.instructions", char_limit=limit),
                (
                    "这是卡片内追问。必须直接回答 followup_question,不要复述 parent_peek_answer,"
                    "不要重新定义 selected_text,除非 followup_question 明确就在问 selected_text。"
                    "回答第一句禁止使用「你说的」「你刚刚划词的」「selected_text 是」「这个词是」这类复述锚点的开头。"
                    if is_followup
                    else "先用一句话定义/解释 selected_text,再补一句它和原文/当前节点的关系。"
                ),
                (
                    "你看到的 followup_history 是这张速览卡片里【从早到晚】的对话记录,"
                    "它是你的短期记忆。回答前先扫一遍,再决定 followup_question 的真实主语:"
                    "  - selected_text 只是这张速览卡片的锚点,不是每个追问的默认主语。"
                    "  - 如果 followup_subject_hint.kind = new_explicit_subject:必须解释 subject 指向的新术语,"
                    "不要用「你说的 selected_text 是……」开头,也不要把答案拉回 selected_text。"
                    "  - 如果 followup_subject_hint.kind = explicit_process_question:必须直接回答过程/机制,"
                    "比如「信息先……再……最后……」;不要先重讲 selected_text 的定义。"
                    "  - 如果 followup_subject_hint.kind = explicit_question:第一句必须直接回答 followup_question,"
                    "selected_text 只能放在第二句以后作为背景关系。"
                    "  - 如果 followup_question 用了代词(他/它/这个/那个/它们)或没有显式主语:"
                    "    * 先在最近 1-2 轮 followup_history 里找最强的指代候选;"
                    "    * 如果上一轮答案里整段都在讲 X,而 X 和 selected_text 不一样,代词大概率指 X;"
                    "    * 如果上一轮答案就是围绕 selected_text 讲的,继续按 selected_text 回答。"
                    "  - 如果 followup_question 明确出现一个新名词/新术语,比如「神经递质是啥」,"
                    "直接解释这个新术语;最后最多用一句话说明它和 selected_text 的关系。"
                    "  - 如果用户明确说\"我说 X\"或\"是 X\"或\"回到 X\",X 就是新主语,不要再回到上一轮的话题。"
                    "  - 不确定时:用一句反问澄清(例:\"你这里问的是机器学习,还是上一段说的意图识别?\"),不要硬猜。"
                    if is_followup
                    else "首轮定义不需要参考 followup_history。"
                ),
            ],
            "json_schema": {"answer": "简短解释文本"},
        }
        try:
            # 速览解释:常常需要"最新"信息(最近新闻、品牌毛利率、最新动态),启用联网搜索
            data = await self.ai_client.chat(
                _cached_chat_messages("你只输出合法 JSON。", prompt),
                temperature=0.35,
                enable_web_search=True,
                purpose="peek_followup" if is_followup else "peek_definition",
                session_id=message.session_id,
                db=db,
            )
            answer = str(data.get("answer") or "").strip()
            if answer:
                return answer[:2000]
        except Exception as exc:  # noqa: BLE001
            print(f"[knowledge_map] peek fallback: {exc}")
        relation = f"它和「{node.title}」这段内容相关。" if node else "它和当前这段话相关。"
        if is_followup:
            subject = anchor_text or "这个词"
            return f"你这次问的是「{text}」。结合原文,它应围绕「{subject}」继续展开,而不是重复定义。{relation}"
        return f"{text} 是这段话里的一个卡点。先把它理解成:围绕原文语境需要补齐的背景概念。{relation}"

    # --------------------------------------------------------------- messages

    async def receive_message(
        self, db: AsyncSession, session_id: str, payload: dict[str, Any]
    ) -> tuple[str, dict[str, Any]]:
        message_text = str(payload.get("message") or "").strip()
        if not message_text:
            raise ValueError("message 不能为空")

        current_node_id = payload.get("current_node_id")
        mode = normalize_mode(payload.get("mode"))
        requested_intent = normalize_intent(payload.get("intent"))
        resolved_intent = self._resolve_intent(message_text, requested_intent)
        promoted_title = str(payload.get("promoted_title") or "").strip() or None
        subdivision_angle = str(payload.get("subdivision_angle") or "").strip() or None

        user_message = Message(
            id=new_id("msg"),
            session_id=session_id,
            node_id=current_node_id,
            role="user",
            content=message_text,
        )
        db.add(user_message)
        await db.flush()

        try:
            if resolved_intent == "subdivide":
                return await self._build_subdivide_reply(
                    db,
                    session_id,
                    message_text,
                    current_node_id,
                    user_message.id,
                    mode,
                    promoted_title,
                    subdivision_angle=subdivision_angle,
                )
            return await self._build_explain_reply(
                db, session_id, message_text, current_node_id, user_message.id, mode
            )
        except Exception as exc:  # noqa: BLE001
            print(f"[knowledge_map] LLM reply fallback ({resolved_intent}): {exc}")
            return await self._build_local_reply(
                db,
                session_id,
                message_text,
                current_node_id,
                user_message.id,
                mode,
                resolved_intent,
                promoted_title,
                subdivision_angle=subdivision_angle,
            )

    async def receive_message_stream(self, db: AsyncSession, session_id: str, payload: dict[str, Any]):
        """流式入口:explain 真流式逐字吐;subdivide / 本地兜底则整段一次性吐。

        统一产出:多个 ("token", 片段) → 一个 ("done", patch)。patch 与 receive_message 同构。
        """
        message_text = str(payload.get("message") or "").strip()
        if not message_text:
            raise ValueError("message 不能为空")

        current_node_id = payload.get("current_node_id")
        mode = normalize_mode(payload.get("mode"))
        requested_intent = normalize_intent(payload.get("intent"))
        resolved_intent = self._resolve_intent(message_text, requested_intent)
        promoted_title = str(payload.get("promoted_title") or "").strip() or None
        subdivision_angle = str(payload.get("subdivision_angle") or "").strip() or None

        user_message = Message(
            id=new_id("msg"),
            session_id=session_id,
            node_id=current_node_id,
            role="user",
            content=message_text,
        )
        db.add(user_message)
        await db.flush()

        try:
            if resolved_intent == "explain":
                # explain 走真流式
                async for ev in self._build_explain_reply_stream(
                    db, session_id, message_text, current_node_id, mode
                ):
                    yield ev
                return
            # subdivide 不流式(回复只是过渡句),整段算完一次性吐
            reply, patch = await self._build_subdivide_reply(
                db, session_id, message_text, current_node_id, user_message.id, mode,
                promoted_title, subdivision_angle=subdivision_angle,
            )
        except Exception as exc:  # noqa: BLE001
            print(f"[knowledge_map] LLM stream fallback ({resolved_intent}): {exc}")
            reply, patch = await self._build_local_reply(
                db, session_id, message_text, current_node_id, user_message.id,
                mode, resolved_intent, promoted_title, subdivision_angle=subdivision_angle,
            )
        yield ("token", reply)
        yield ("done", patch)

    @staticmethod
    def _resolve_intent(message_text: str, requested_intent: str) -> str:
        if requested_intent in {"explain", "subdivide"}:
            return requested_intent
        text = message_text.strip()
        if any(token in text for token in SUBDIVIDE_KEYWORDS):
            return "subdivide"
        return "explain"

    @staticmethod
    def _context_summary_due(
        session: LearningSession, all_messages: list[Message]
    ) -> dict[str, Any] | None:
        """优化C:判断窗口外是否攒够了未折叠的旧消息。纯计算、无 LLM、无 IO。

        够了就返回 {convo, prior, new_count} 给后台任务用;不够返回 None。
        """
        out_of_window = (
            all_messages[:-EXPLAIN_HISTORY_WINDOW] if len(all_messages) > EXPLAIN_HISTORY_WINDOW else []
        )
        already = session.context_summary_count or 0
        new_slice = out_of_window[already:]
        if len(new_slice) < CONTEXT_SUMMARY_BATCH:
            return None
        convo = [
            {"role": m.role, "content": (m.content or "").strip()[:400]}
            for m in new_slice
            if (m.content or "").strip()
        ]
        return {"convo": convo, "prior": session.context_summary or "", "new_count": len(out_of_window)}

    async def _run_context_summary_update(
        self, session_id: str, prior: str, convo: list[dict[str, str]], new_count: int
    ) -> None:
        """提速3:在后台把旧对话折叠进滚动摘要,不占用主回答的延迟。

        计数已在请求里同步推进过(防重复触发),这里只负责算出新摘要文本并写回。
        写库带几次重试,避开 SQLite 与主请求事务的短暂写锁竞争。失败安静放弃。
        """
        prompt = {
            "task": "context_summary",
            "instructions": [
                "你在维护一段学习对话的滚动摘要,用于给后续回答提供远期上下文。",
                "把【已有摘要】和【新增对话】合并成一段更新后的摘要。",
                f"摘要控制在 {CONTEXT_SUMMARY_MAX_CHARS} 字以内,只保留对后续讲解有用的结论、",
                "用户已确认掌握/仍困惑的点、关键事实与决定,丢弃寒暄和重复。",
            ],
            "json_schema": {"summary": "更新后的滚动摘要纯文本"},
            "previous_summary": prior,
            "new_messages": convo,
        }
        try:
            data = await self.ai_client.chat(
                _cached_chat_messages("你只输出合法 JSON,顶层必须有 summary 字段。", prompt),
                temperature=0.2,
                purpose="context_summary",
                session_id=session_id,
            )
            summary = str(data.get("summary") or "").strip()[:CONTEXT_SUMMARY_MAX_CHARS]
        except Exception as exc:  # noqa: BLE001
            print(f"[knowledge_map] context summary skipped: {exc}")
            return
        if not summary:
            return

        from app.db.base import get_session_factory

        factory = get_session_factory()
        for attempt in range(5):
            try:
                async with factory() as db:
                    sess = await db.get(LearningSession, session_id)
                    if sess is None:
                        return
                    sess.context_summary = summary
                    await db.commit()
                return
            except Exception as exc:  # noqa: BLE001
                if attempt == 4:
                    print(f"[knowledge_map] context summary write failed: {exc}")
                    return
                await asyncio.sleep(0.3 * (attempt + 1))

    _EXPLAIN_SYSTEM_RULE = (
        "你只输出合法 JSON,不输出 Markdown 代码块。顶层必须有 reply 字段。"
        "不要输出 answer 字段。reply 字段内部可以用 Markdown 文本。"
    )

    async def _explain_chat_messages(
        self,
        db: AsyncSession,
        session_id: str,
        message: str,
        current_node_id: str | None,
        mode: str,
    ) -> tuple[list[dict[str, str]], KnowledgeNode]:
        """构造 explain 的 chat messages(含滚动摘要调度),返回 (messages, 当前节点)。

        非流式 _build_explain_reply 与流式 _build_explain_reply_stream 共用,保证 prompt 一致。
        """
        node = await self._resolve_current_node(db, session_id, current_node_id)
        session = await db.get(LearningSession, session_id)
        profile = thinking_mode_profile(mode)
        all_messages = await _fetch_messages(db, session_id)
        recent = all_messages[-EXPLAIN_HISTORY_WINDOW:]
        # 提速3:本轮直接用已存的滚动摘要(不在关键路径上调 LLM);
        # 若窗外又攒够一批旧消息,只同步推进计数(防重复触发),折叠交给后台任务。
        prior_context_summary = (session.context_summary if session else "") or ""
        if session is not None:
            due = self._context_summary_due(session, all_messages)
            if due is not None:
                session.context_summary_count = due["new_count"]
                if due["convo"]:
                    _spawn_bg(
                        self._run_context_summary_update(
                            session.id, due["prior"], due["convo"], due["new_count"]
                        )
                    )

        # 当前节点的兄弟 / 子节点列表给 AI 看,方便它在 next_actions 里建议跳转
        all_nodes = await _fetch_nodes(db, session_id)
        siblings = [n for n in all_nodes if n.parent_id == node.parent_id and n.id != node.id]
        children = [n for n in all_nodes if n.parent_id == node.id]

        deep_target = {"Lite": 520, "Medium": 1000, "Zen": 1800}.get(mode, 520)
        term_target = {"Lite": 280, "Medium": 520, "Zen": 880}.get(mode, 280)
        grouping_target = {"Lite": 520, "Medium": 820, "Zen": 1200}.get(mode, 520)

        # children 数量提示给 AI:分组节点(有 children)在讲解时只做"导览",不要把子节点内容提前讲完
        child_titles = [n.title for n in children][:12]
        child_summaries = [
            {"title": n.title, "summary": (n.summary or "")[:120]}
            for n in children[:12]
        ]
        is_grouping_node = len(children) > 0

        prompt = {
            "field": session.field if session else "",
            "current_problem": session.current_problem if session else "",
            "learning_background": _learning_background(session),
            "current_node": {
                "id": node.id,
                "title": node.title,
                "summary": node.summary,
                "status": node.status,
                "path": await _node_path(db, node.id),
                "is_grouping_node": is_grouping_node,
                "children_count": len(children),
            },
            "sibling_titles": [n.title for n in siblings][:12],
            "existing_child_titles": child_titles,
            "existing_children_with_summary": child_summaries,
            "user_message": message,
            "thinking_mode": profile,
            # 优化C:窗口外旧对话的滚动摘要,补回"最近 N 条"丢掉的远期上下文
            "prior_context_summary": prior_context_summary,
            "recent_messages": [
                {"role": m.role, "content": (m.content or "")[:600], "node_id": m.node_id} for m in recent
            ],
            "task": "explain",
            "instructions": get_prompt_store().format_lines(
                "explain.instructions",
                is_grouping_node=is_grouping_node,
                mode=mode,
                grouping_target=grouping_target,
                term_target=term_target,
                deep_target=deep_target,
                current_problem=(session.current_problem if session else ""),
            ),
            "json_schema": {
                "reply": "Markdown 文本。第一段必须是直接回答用户提问。结尾一句话尾注说明地图位置。",
                "status": "active|completed|deepening|paused",
                "summary": "当前节点一句话沉淀(<=120 字)",
                "content": "节点正文短 Markdown(<=400 字)",
                "next_actions": [
                    {
                        "kind": "explain",
                        "label": "举个其他例子",
                        "target_title": "",
                        "payload": "再给我一个不同行业的例子",
                    }
                ],
            },
        }

        return _cached_chat_messages(self._EXPLAIN_SYSTEM_RULE, prompt), node

    async def _finalize_explain(
        self,
        db: AsyncSession,
        session_id: str,
        node: KnowledgeNode,
        data: dict[str, Any],
        *,
        fallback_reply: str = "",
    ) -> tuple[str, dict[str, Any]]:
        """explain 的副作用收尾:写节点状态/摘要/正文 + 存助手消息 + 出 patch。

        非流式与流式共用。fallback_reply:流式已吐出的文本,JSON 解析异常时兜底用。
        """
        reply = _reply_from_ai_data(data) or fallback_reply
        if not reply:
            raise ValueError("LLM JSON 缺少 reply 字段")

        status = str(data.get("status") or "active").strip()
        if status not in NODE_STATUSES:
            status = "active"
        await _update_status(db, session_id, node.id, status)

        summary = str(data.get("summary") or "").strip()[:240]
        content = str(data.get("content") or "").strip()
        if summary:
            node.summary = summary
        if content:
            node.content = content
        node.updated_at = now_utc()

        # 对话区只有两个固定按钮:[下一个, 没听懂]。AI 给的 next_actions 在此忽略,
        # 简化用户决策——AI 提出的"侧向建议"对学习节奏干扰过多。
        latest_nodes = await _fetch_nodes(db, session_id)
        current_node_for_actions = next((n for n in latest_nodes if n.id == node.id), node)
        next_actions = _build_fixed_next_actions(
            _next_pending_candidates(latest_nodes, node.id),
            current_node=current_node_for_actions,
        )

        db.add(
            Message(
                id=new_id("msg"),
                session_id=session_id,
                node_id=node.id,
                role="assistant",
                content=reply,
                next_actions=next_actions,
                # 把网页搜索的来源原样存到 message,前端 renderSearchSources 会展示卡片
                search_sources=data.get("_web_search_sources") or [],
            )
        )
        await db.flush()
        return reply, await self._make_patch(db, session_id, node.id, status, [])

    async def _build_explain_reply(
        self,
        db: AsyncSession,
        session_id: str,
        message: str,
        current_node_id: str | None,
        user_message_id: str,
        mode: str,
        promoted_title: str | None = None,
    ) -> tuple[str, dict[str, Any]]:
        # 主对话讲解:启用 web_search 让 AI 拿到中文最新行情/动态,
        # 检索结果会以 system message 注入,同时存到 message.search_sources 给前端展示
        messages, node = await self._explain_chat_messages(db, session_id, message, current_node_id, mode)
        data = await self.ai_client.chat(
            messages, temperature=0.5, enable_web_search=True,
            purpose="explain", session_id=session_id, db=db,
        )
        return await self._finalize_explain(db, session_id, node, data)

    async def _build_explain_reply_stream(
        self,
        db: AsyncSession,
        session_id: str,
        message: str,
        current_node_id: str | None,
        mode: str,
    ):
        """流式版主讲解:边生成边 yield ("token", 片段),最后 yield ("done", patch)。"""
        messages, node = await self._explain_chat_messages(db, session_id, message, current_node_id, mode)
        streamed: list[str] = []
        data: dict[str, Any] = {}
        try:
            async for kind, val in self.ai_client.chat_stream(
                messages, temperature=0.5, enable_web_search=True,
                purpose="explain", session_id=session_id, db=db,
            ):
                if kind == "token":
                    streamed.append(val)
                    yield ("token", val)
                elif kind == "data":
                    data = val
        except Exception:  # noqa: BLE001
            # 还没吐出任何字 → 交给上层走本地兜底;已经吐了字 → 用已吐内容收尾,不再二次兜底
            if not streamed:
                raise
        _, patch = await self._finalize_explain(
            db, session_id, node, data, fallback_reply="".join(streamed)
        )
        yield ("done", patch)

    async def _build_subdivide_reply(
        self,
        db: AsyncSession,
        session_id: str,
        message: str,
        current_node_id: str | None,
        user_message_id: str,
        mode: str,
        promoted_title: str | None = None,
        *,
        subdivision_angle: str | None = None,
    ) -> tuple[str, dict[str, Any]]:
        node = await self._resolve_current_node(db, session_id, current_node_id)
        session = await db.get(LearningSession, session_id)
        profile = thinking_mode_profile(mode)
        promoted_title = (promoted_title or _extract_promoted_branch_title(message) or "").strip() or None
        subdivision_angle = (subdivision_angle or "").strip() or None
        promoted_parent = node
        created_nodes: list[str] = []
        created_titles: list[str] = []
        created_child_titles: list[str] = []

        if promoted_title and promoted_title != node.title:
            existing_branch = await _find_child_by_title(
                db,
                session_id=session_id,
                parent_id=node.id,
                title=promoted_title,
            )
            if existing_branch:
                node = existing_branch
                if node.created_from_message_id == user_message_id:
                    created_nodes.append(node.id)
                    created_titles.append(node.title)
            else:
                sibling_count = await db.scalar(
                    select(func.count()).where(KnowledgeNode.parent_id == node.id)
                ) or 0
                branch_summary = f"从「{promoted_parent.title}」里提升出来的案例/概念分支。"
                if subdivision_angle:
                    branch_summary = f"按{subdivision_angle}角度拆。{branch_summary}"
                node = await _make_node(
                    db,
                    session_id=session_id,
                    title=promoted_title,
                    parent_id=promoted_parent.id,
                    depth=promoted_parent.depth + 1,
                    sort_order=sibling_count + 1,
                    status="deepening",
                    relevance=1,
                    importance=2,
                    relevance_score=3,
                    difficulty=2,
                    summary=branch_summary,
                    message_id=user_message_id,
                )
                created_nodes.append(node.id)
                created_titles.append(node.title)

        all_nodes = await _fetch_nodes(db, session_id)
        recent = (await _fetch_messages(db, session_id))[-6:]

        existing_paths: list[str] = []
        node_path_cache: dict[str, str] = {}

        def path_of(target: KnowledgeNode) -> str:
            if target.id in node_path_cache:
                return node_path_cache[target.id]
            chain: list[str] = [target.title]
            cursor = target
            safety = 0
            id_to_node = {n.id: n for n in all_nodes}
            while cursor.parent_id and safety < 12:
                cursor = id_to_node.get(cursor.parent_id)
                if not cursor:
                    break
                chain.append(cursor.title)
                safety += 1
            result = " / ".join(reversed(chain))
            node_path_cache[target.id] = result
            return result

        for n in all_nodes:
            if n.id == node.id:
                continue
            existing_paths.append(path_of(n))

        existing_titles_all = {n.title.strip() for n in all_nodes if n.title}
        existing_titles_of_current = {n.title.strip() for n in all_nodes if n.parent_id == node.id}

        target_count = child_limit_for_mode(mode)

        angle_directives: list[str] = []
        if subdivision_angle:
            angle_directives = [
                f"【硬约束】这一次必须严格按「{subdivision_angle}」这一个角度拆,不要混合别的视角。",
                "  - 比如 angle='步骤流程' 就只拆做这件事的先后步骤,不要顺手列出'分类'或'指标'。",
                "  - 比如 angle='指标评估' 就只拆判断好坏的指标,不要回去拆构成。",
                "  - 比如 angle='风险失败模式' 就只列踩坑模式,不要混进'类型'。",
                "  - 如果 angle 是用户自己写的短语(比如'按客群拆'),严格按字面理解,生成的 children 必须都符合那个字面切分。",
            ]

        prompt = {
            "field": session.field if session else "",
            "current_problem": session.current_problem if session else "",
            "learning_background": _learning_background(session),
            "current_node": {
                "id": node.id,
                "title": node.title,
                "summary": node.summary,
                "path": await _node_path(db, node.id),
            },
            "promoted_from_parent": promoted_parent.title if promoted_title else "",
            "subdivision_angle": subdivision_angle or "",
            "user_message": message,
            "thinking_mode": profile,
            "target_child_count": target_count,
            "existing_paths_in_map": existing_paths[:80],
            "existing_children_of_this_node": sorted(existing_titles_of_current),
            "recent_messages": [
                {"role": m.role, "content": (m.content or "")[:600], "node_id": m.node_id} for m in recent
            ],
            "task": "subdivide",
            # 主体 instructions 走 prompt_store(后台可编辑),angle_directives 是代码动态生成的拆分角度硬约束,
            # 编辑入口不暴露 —— 拼到主体后面让 LLM 同样能看到。
            "instructions": [
                *get_prompt_store().format_lines(
                    "subdivide.instructions",
                    target_child_count=target_count,
                ),
                *angle_directives,
            ],
            "json_schema": {
                "reply": "60-120 字过渡段",
                "status": "deepening",
                "summary": "当前节点一句话沉淀(<=120 字)",
                "middle_title": "中间分支卡片标题(12-20 字,有信息量)",
                "middle_summary": "按X角度看。XX是这张分组卡片的主题,下面挂具体节点。",
                "children": [
                    {
                        "title": "子节点标题(<=24 字)",
                        "summary": "一句话摘要(<=80 字)",
                        "importance": 2,
                        "relevance_score": 2,
                        "difficulty": 2,
                    }
                ],
                "next_actions": [
                    {
                        "kind": "explain",
                        "label": "从「X」开始讲",
                        "target_title": "X",
                        "payload": "请围绕「X」开始讲解。",
                    }
                ],
            },
        }

        data = await self.ai_client.chat(
            _cached_chat_messages("你只输出合法 JSON,不输出 Markdown 代码块。", prompt),
            temperature=0.45,
            purpose="subdivide",
            session_id=session_id,
            db=db,
        )

        reply = _reply_from_ai_data(data)
        if not reply:
            raise ValueError("LLM JSON 缺少 reply 字段")

        status = "deepening"
        await _update_status(db, session_id, node.id, status)

        summary = str(data.get("summary") or "").strip()[:240]
        if summary:
            node.summary = summary
        node.updated_at = now_utc()

        # === 两步法核心:如果没走速览的 promoted_title 路径(node 还是用户当前节点),
        #     用 AI 给的 middle_title 建一张'分组卡片',把后续 children 都挂到它下面。
        #     这样保证整个产品里所有拆分都遵循"中间分支 + 具体子节点"的两层产物。
        if node.id == promoted_parent.id:
            ai_middle_title = str(data.get("middle_title") or "").strip()[:50]
            ai_middle_summary = str(data.get("middle_summary") or "").strip()[:240]
            if ai_middle_title and not _looks_similar(ai_middle_title, existing_titles_all):
                if subdivision_angle and not ai_middle_summary.startswith("按"):
                    ai_middle_summary = (
                        f"按{subdivision_angle}角度看。{ai_middle_summary}".strip()
                    )
                elif not ai_middle_summary:
                    ai_middle_summary = f"按{subdivision_angle or '当前视角'}角度看「{node.title}」的几个具体方向。"
                sibling_count = await db.scalar(
                    select(func.count()).where(KnowledgeNode.parent_id == node.id)
                ) or 0
                middle_node = await _make_node(
                    db,
                    session_id=session_id,
                    title=ai_middle_title,
                    parent_id=node.id,
                    depth=node.depth + 1,
                    sort_order=sibling_count + 1,
                    status="deepening",
                    relevance=1,
                    importance=2,
                    relevance_score=3,
                    difficulty=2,
                    summary=ai_middle_summary,
                    message_id=user_message_id,
                )
                created_nodes.append(middle_node.id)
                created_titles.append(middle_node.title)
                existing_titles_all.add(middle_node.title)
                # children 全部挂到中间分支下
                node = middle_node

        children_raw = data.get("children") if isinstance(data.get("children"), list) else []
        child_created_count = 0
        if children_raw:
            limited = calibrate_relevance_distribution(children_raw[: child_limit_for_mode(mode)])
            current_count = await db.scalar(
                select(func.count()).where(KnowledgeNode.parent_id == node.id)
            ) or 0
            # 跨全树 dedup:精确 + 模糊
            taken_titles = set(existing_titles_all)
            for offset, item in enumerate(limited, start=1):
                title = str(item.get("title") or "").strip()
                if not title:
                    continue
                if title in existing_titles_of_current:
                    continue
                if _looks_similar(title, taken_titles):
                    continue
                child_summary = str(item.get("summary") or "").strip()
                relevance_score = clamp_metric(
                    item.get("relevance_score", 3 if item.get("relevance", 1) else 1)
                )
                child = await _make_node(
                    db,
                    session_id=session_id,
                    title=title[:50],
                    parent_id=node.id,
                    depth=node.depth + 1,
                    sort_order=current_count + offset,
                    status="pending",
                    relevance=1 if relevance_score >= 3 else 0,
                    importance=clamp_metric(item.get("importance", 2)),
                    relevance_score=relevance_score,
                    difficulty=clamp_metric(item.get("difficulty", 2)),
                    summary=child_summary[:160],
                    message_id=user_message_id,
                )
                created_nodes.append(child.id)
                created_titles.append(title)
                created_child_titles.append(title)
                child_created_count += 1
                taken_titles.add(title)

        # 兜底:DeepSeek 返回 children=[] 时,用本地模板补一组
        if not child_created_count:
            existing_count = await db.scalar(
                select(func.count()).where(KnowledgeNode.parent_id == node.id)
            ) or 0
            if not existing_count:
                taken_titles = set(existing_titles_all)
                for index, (title, child_summary) in enumerate(child_topics(node.title, mode), start=1):
                    if _looks_similar(title, taken_titles):
                        continue
                    child = await _make_node(
                        db,
                        session_id=session_id,
                        title=title[:50],
                        parent_id=node.id,
                        depth=node.depth + 1,
                        sort_order=index,
                        status="pending",
                        relevance=1,
                        importance=2,
                        relevance_score=3,
                        difficulty=2,
                        summary=child_summary[:160],
                        message_id=user_message_id,
                    )
                    created_nodes.append(child.id)
                    created_titles.append(title)
                    created_child_titles.append(title)
                    child_created_count += 1
                    taken_titles.add(title)

        # 对话区只有两个固定按钮:[下一个, 没听懂]。AI 给的 next_actions 不再使用——
        # 拆完之后"下一个"会自然指向第一个 pending 子节点。
        all_nodes_now = await _fetch_nodes(db, session_id)
        current_node_for_actions = next((n for n in all_nodes_now if n.id == node.id), node)
        next_actions = _build_fixed_next_actions(
            _next_pending_candidates(all_nodes_now, node.id),
            current_node=current_node_for_actions,
        )

        db.add(
            Message(
                id=new_id("msg"),
                session_id=session_id,
                node_id=node.id,
                role="assistant",
                content=reply,
                next_actions=next_actions,
            )
        )
        await db.flush()
        return reply, await self._make_patch(db, session_id, node.id, status, created_nodes)

    async def _build_local_reply(
        self,
        db: AsyncSession,
        session_id: str,
        message: str,
        current_node_id: str | None,
        user_message_id: str,
        mode: str,
        intent: str,
        promoted_title: str | None = None,
        *,
        subdivision_angle: str | None = None,
    ) -> tuple[str, dict[str, Any]]:
        node = await self._resolve_current_node(db, session_id, current_node_id)
        text = message.strip()
        subdivision_angle = (subdivision_angle or "").strip() or None
        created_nodes: list[str] = []
        created_titles: list[str] = []
        created_child_titles: list[str] = []

        if any(token in text for token in COMPLETE_KEYWORDS) or any(token in text for token in SKIP_KEYWORDS):
            status = "completed" if any(token in text for token in COMPLETE_KEYWORDS) else "skipped"
            await _update_status(db, session_id, node.id, status)
            if not node.summary:
                node.summary = f"{node.title}已标记为{'完成' if status == 'completed' else '跳过'}。"
            reply = (
                f"当前位置:{await _node_path(db, node.id)}\n\n"
                f"已把「{node.title}」标记为{'已完成' if status == 'completed' else '已跳过'}。"
                "你可以选择右侧另一个节点继续,也可以让我回到上一级梳理下一步。"
            )
            latest_nodes = await _fetch_nodes(db, session_id)
            current_node_for_actions = next((n for n in latest_nodes if n.id == node.id), node)
            next_actions = _build_fixed_next_actions(
                _next_pending_candidates(latest_nodes, node.id),
                current_node=current_node_for_actions,
            )
            db.add(
                Message(
                    id=new_id("msg"),
                    session_id=session_id,
                    node_id=node.id,
                    role="assistant",
                    content=reply,
                    next_actions=next_actions,
                )
            )
            await db.flush()
            return reply, await self._make_patch(db, session_id, node.id, status, [])

        if intent == "subdivide":
            promoted_title = (promoted_title or _extract_promoted_branch_title(text) or "").strip() or None
            promoted_parent = node
            if promoted_title and promoted_title != node.title:
                existing_branch = await _find_child_by_title(
                    db,
                    session_id=session_id,
                    parent_id=node.id,
                    title=promoted_title,
                )
                if existing_branch:
                    node = existing_branch
                    if node.created_from_message_id == user_message_id:
                        created_nodes.append(node.id)
                        created_titles.append(node.title)
                else:
                    sibling_count = await db.scalar(
                        select(func.count()).where(KnowledgeNode.parent_id == node.id)
                    ) or 0
                    branch_summary = f"从「{promoted_parent.title}」里提升出来的案例/概念分支。"
                    if subdivision_angle:
                        branch_summary = f"按{subdivision_angle}角度拆。{branch_summary}"
                    node = await _make_node(
                        db,
                        session_id=session_id,
                        title=promoted_title,
                        parent_id=promoted_parent.id,
                        depth=promoted_parent.depth + 1,
                        sort_order=sibling_count + 1,
                        status="deepening",
                        relevance=1,
                        importance=2,
                        relevance_score=3,
                        difficulty=2,
                        summary=branch_summary,
                        message_id=user_message_id,
                    )
                    created_nodes.append(node.id)
                    created_titles.append(node.title)
            # 两步法兜底:本地 fallback 时也保证先长出一个'中间分支'卡片再挂子节点
            elif node.id == promoted_parent.id:
                sibling_count = await db.scalar(
                    select(func.count()).where(KnowledgeNode.parent_id == node.id)
                ) or 0
                angle_label = subdivision_angle or "综合视角"
                fallback_middle_title = f"{node.title[:14]}的{angle_label}拆解"
                all_titles_for_dedup = {n.title for n in await _fetch_nodes(db, session_id)}
                if not _looks_similar(fallback_middle_title, all_titles_for_dedup):
                    branch_summary = f"按{angle_label}角度看「{promoted_parent.title}」的几个具体方向。"
                    node = await _make_node(
                        db,
                        session_id=session_id,
                        title=fallback_middle_title,
                        parent_id=promoted_parent.id,
                        depth=promoted_parent.depth + 1,
                        sort_order=sibling_count + 1,
                        status="deepening",
                        relevance=1,
                        importance=2,
                        relevance_score=3,
                        difficulty=2,
                        summary=branch_summary,
                        message_id=user_message_id,
                    )
                    created_nodes.append(node.id)
                    created_titles.append(node.title)
            status = "deepening"
            await _update_status(db, session_id, node.id, status)
            existing_count = await db.scalar(
                select(func.count()).where(KnowledgeNode.parent_id == node.id)
            ) or 0
            if not existing_count:
                all_titles = {n.title for n in await _fetch_nodes(db, session_id)}
                for index, (title, summary) in enumerate(child_topics(node.title, mode), start=1):
                    if _looks_similar(title, all_titles):
                        continue
                    child = await _make_node(
                        db,
                        session_id=session_id,
                        title=title,
                        parent_id=node.id,
                        depth=node.depth + 1,
                        sort_order=index,
                        status="pending",
                        relevance=1,
                        importance=2,
                        relevance_score=3,
                        difficulty=2,
                        summary=summary,
                        message_id=user_message_id,
                    )
                    created_nodes.append(child.id)
                    created_titles.append(title)
                    created_child_titles.append(title)
                    all_titles.add(title)
            reply = f"已把「{node.title}」按几个常见角度拆开了,选一个开始看。"
        else:
            status = "active"
            await _update_status(db, session_id, node.id, status)
            if not node.summary:
                node.summary = f"{node.title}是当前主题里需要讲深的关键点。"
            node.content = f"用户围绕该节点追问:{text}"
            node.updated_at = now_utc()
            session = await db.get(LearningSession, session_id)
            background_note = _learning_background(session)
            reply = (
                f"**直接回答**:你问的「{text[:40]}」,落在「{node.title}」这条线上。"
                f"{node.summary or node.title + '是当前主题中的关键判断点。'}\n\n"
                f"**展开**:把「{node.title}」放到一次老板提问场景里——他问你「问题出在哪」,"
                "你不要只报数字,要把它拆到这个节点对应的几个驱动因素上,挑出最异常的一个。"
                f"我会按你的背景来讲:{background_note}\n\n"
                "**小练习**:用一句话写出你会如何向老板解释这个节点的价值。\n\n"
                f"_这块在地图里的位置:{await _node_path(db, node.id)}_"
            )

        all_nodes_now = await _fetch_nodes(db, session_id)
        # 对话区只有两个固定按钮:[下一个, 没听懂]。其余 fallback 建议都不再展示。
        current_node_for_actions = next((n for n in all_nodes_now if n.id == node.id), node)
        next_actions = _build_fixed_next_actions(
            _next_pending_candidates(all_nodes_now, node.id),
            current_node=current_node_for_actions,
        )

        db.add(
            Message(
                id=new_id("msg"),
                session_id=session_id,
                node_id=node.id,
                role="assistant",
                content=reply,
                next_actions=next_actions,
            )
        )
        await db.flush()
        return reply, await self._make_patch(db, session_id, node.id, status, created_nodes)

    # ------------------------------------------------------------------ helpers

    async def _resolve_current_node(
        self, db: AsyncSession, session_id: str, node_id: str | None
    ) -> KnowledgeNode:
        node = await _get_node(db, node_id)
        if not node:
            session = await db.get(LearningSession, session_id)
            if session and session.current_node_id:
                node = await _get_node(db, session.current_node_id)
        if not node:
            raise ValueError("当前没有可用节点")
        return node

    async def _make_patch(
        self,
        db: AsyncSession,
        session_id: str,
        current_node_id: str,
        status: str,
        created_node_ids: list[str],
    ) -> dict[str, Any]:
        nodes = await _fetch_nodes(db, session_id)
        messages = await _fetch_messages(db, session_id)
        return {
            "current_node_id": current_node_id,
            "updated_node_id": current_node_id,
            "created_node_ids": created_node_ids,
            "status": status,
            "nodes": nodes,
            "messages": messages,
        }
