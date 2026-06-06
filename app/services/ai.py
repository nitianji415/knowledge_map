"""统一的 LLM 客户端 + 提示词构造。

全系统只走一条 OpenAI Chat Completions 兼容协议:
用 `openai` SDK + (LLM_BASE_URL, LLM_API_KEY, LLM_MODEL) 三件套调用。
DeepSeek / Moonshot / 自建 vLLM / OpenRouter 等任何 OpenAI 兼容服务,改 base_url 就能换。
"""

from __future__ import annotations

import json
import re
from typing import Any

import httpx
from openai import AsyncOpenAI

from app.core.config import Settings
from app.services import anysearch as anysearch_client
from app.services import web_search as open_websearch_client
from app.services.topics import default_topics

# 优化D:注入给 LLM 的网页检索结果上限(条数 + 单条正文字符数)。
WEB_SEARCH_INJECT_MAX = 5
WEB_SEARCH_INJECT_CONTENT_CHARS = 600

# 优化(提速4):搜索路由快路径关键词。
# 命中"概念/原理"词且不含"时效/事实"词 → 多半不用联网,直接跳过那次路由 LLM,省一次往返;
# 其余(含时效词、或两类都不命中的模糊情况)仍交给 LLM 路由判断,保持"宽松、AI 决定"。
_SEARCH_FRESHNESS_HINTS = (
    "最新", "近期", "目前", "现在", "今年", "去年", "实时", "最近", "如今",
    "2023", "2024", "2025", "2026",
    "价格", "股价", "行情", "市值", "营收", "财报", "销量", "份额", "市场规模",
    "政策", "新闻", "发布", "上市", "排名", "趋势", "动态", "数据", "现状", "多少钱",
)
_SEARCH_CONCEPTUAL_HINTS = (
    "为什么", "原理", "怎么", "如何", "区别", "什么是", "是什么", "概念", "定义",
    "举例", "例子", "解释", "推导", "作用", "机制", "理解", "通俗", "原因", "本质",
    "步骤", "流程", "区分", "联系", "比喻", "公式", "证明",
)


def _resolve_prompt(key: str, **variables: object) -> str:
    """从 prompt_store 拉单条 prompt 模板,做变量替换返回单字符串。
    用在 prompt = f'''...''' 这种把整段 prompt 当 user message 发的调用点。
    """
    # 局部 import 避免 ai.py import-time 依赖 prompt_store
    from app.services.prompt_store import get_prompt_store

    return get_prompt_store().format(key, **variables)


def _resolve_prompt_lines(key: str, **variables: object) -> list[str]:
    """从 prompt_store 拉单条 prompt,按行切返回 list[str]。
    用在 instructions=[...] 这种以列表形式注入到结构化 prompt 的调用点。
    """
    from app.services.prompt_store import get_prompt_store

    return get_prompt_store().format_lines(key, **variables)


_REPLY_KEY_RE = re.compile(r'"reply"\s*:\s*"')
_JSON_STR_ESCAPES = {'"': '"', "\\": "\\", "/": "/", "n": "\n", "t": "\t", "r": "\r", "b": "\b", "f": "\f"}


def extract_partial_reply(raw: str) -> str:
    """从"还在流式增长中"的 JSON 文本里,尽力解出 reply 字段当前已到的内容(已反转义)。

    真流式用:每来一段就对累计 buffer 调一次,把新增的字符当 token 推给前端。
    - reply 还没出现 → 返回 ""(顺序里 reply 不在最前时,前几片可能先空着)
    - 遇到未闭合的转义/unicode(\\、\\uXX 被切在 buffer 末尾)→ 停在那里,等下一片补全
    - 遇到未转义的结束引号 → reply 已完整,返回完整值
    """
    m = _REPLY_KEY_RE.search(raw)
    if not m:
        return ""
    i = m.end()
    out: list[str] = []
    n = len(raw)
    while i < n:
        c = raw[i]
        if c == "\\":
            if i + 1 >= n:
                break  # 转义被切断,等下一片
            nxt = raw[i + 1]
            if nxt == "u":
                if i + 6 > n:
                    break  # \uXXXX 不完整
                try:
                    out.append(chr(int(raw[i + 2 : i + 6], 16)))
                except ValueError:
                    out.append(raw[i : i + 6])
                i += 6
                continue
            out.append(_JSON_STR_ESCAPES.get(nxt, nxt))
            i += 2
            continue
        if c == '"':
            break  # 字符串结束
        out.append(c)
        i += 1
    return "".join(out)


def _usage_int(usage: Any, *names: str) -> int:
    """从 OpenAI usage 对象里取整数字段;DeepSeek 的 prompt_cache_* 在 model_extra 里。"""
    if usage is None:
        return 0
    extra = getattr(usage, "model_extra", None) or {}
    for name in names:
        value = getattr(usage, name, None)
        if value is None and isinstance(extra, dict):
            value = extra.get(name)
        if value is not None:
            try:
                return int(value)
            except (TypeError, ValueError):
                continue
    return 0


async def _record_llm_usage(
    *,
    purpose: str,
    session_id: str | None,
    model: str,
    usage: Any,
    web_search: bool,
    db: Any = None,
) -> None:
    """把单次 LLM 调用的 token 用量写进 llm_usage_events。任何异常都吞掉,绝不影响主对话。

    传入 db(发起请求的同一个 session)时,把用量行 add 进该事务、随请求一起提交——
    这是 SQLite 上唯一安全的做法(单写锁,另开连接写会 "database is locked")。
    没有 db 时退回独立 session 兜底(Postgres 没问题;SQLite 高并发下可能被跳过)。
    """
    try:
        prompt_tokens = _usage_int(usage, "prompt_tokens")
        completion_tokens = _usage_int(usage, "completion_tokens")
        total_tokens = _usage_int(usage, "total_tokens") or (prompt_tokens + completion_tokens)
        cache_hit = _usage_int(usage, "prompt_cache_hit_tokens", "cached_tokens")
        cache_miss = _usage_int(usage, "prompt_cache_miss_tokens")
        if cache_miss == 0 and cache_hit and prompt_tokens:
            cache_miss = max(prompt_tokens - cache_hit, 0)

        # 局部 import 避免 import-time 依赖 DB 层
        from app.db.models import LlmUsageEvent, new_id

        event = LlmUsageEvent(
            id=new_id("usage"),
            session_id=session_id,
            purpose=purpose[:40],
            model=model[:80],
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            cache_hit_tokens=cache_hit,
            cache_miss_tokens=cache_miss,
            web_search=web_search,
        )
        if db is not None:
            db.add(event)  # 不在这里 commit,跟随请求事务一起落库
        else:
            from app.db.base import get_session_factory

            factory = get_session_factory()
            async with factory() as own_db:
                own_db.add(event)
                await own_db.commit()
        print(
            f"[knowledge_map] usage purpose={purpose} prompt={prompt_tokens} "
            f"completion={completion_tokens} cache_hit={cache_hit} cache_miss={cache_miss}"
        )
    except Exception as exc:  # noqa: BLE001
        print(f"[knowledge_map] _record_llm_usage skipped: {exc}")


def clamp_metric(value: Any) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return 2
    return max(1, min(3, number))


def extract_json_object(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.startswith("json"):
            cleaned = cleaned[4:].strip()
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError("LLM 响应中未发现 JSON")
    return json.loads(cleaned[start : end + 1])


def _shorten_for_log(text: str, max_chars: int) -> str:
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    return f"{text[:max_chars]}\n...[truncated {len(text) - max_chars} chars]"


# 「下一个」/「拆开」按钮自动生成的固定句式,从「」里抽出真正的主题
# 形式: 请围绕「X」开始讲解 / 拆开 / 继续讲 / 展开 ...(尾巴句号可有可无)
_BOILERPLATE_USER_MSG_RE = re.compile(
    r"^请围绕[「『\"]([^」』\"]+)[」』\"]"
    r"(?:开始讲解|继续讲|讲讲|展开|拆开|拆分|说一下|介绍一下)?"
    r"[。.]?\s*$"
)


def _strip_user_msg_boilerplate(user_msg: str) -> str:
    """剥掉「下一个」/「拆开」按钮自动生成的固定句式,只留括号里的真实主题。
    例:'请围绕「文档解析与分块策略」开始讲解。' → '文档解析与分块策略'
    匹配不到就原样返回。
    """
    if not user_msg:
        return ""
    match = _BOILERPLATE_USER_MSG_RE.match(user_msg.strip())
    if match:
        return match.group(1).strip()
    return user_msg.strip()


def extract_search_context(messages: list[dict[str, str]]) -> dict[str, str]:
    """方案 A:从 messages 末尾的 JSON 用户消息里按任务类型抽出最具体的 seed query。

    业务侧的 prompt payload 里 task ∈ {peek_definition, peek_followup, explain, ...},
    不同任务"最值钱"的字段不一样——
      - peek_followup:追问本身最具体,只用 followup_question
      - peek_definition:划词文本 + field
      - explain:current_node.title 最精准(就是用户当前在学的节点本身);
                user_message 经常是「请围绕「X」开始讲解」这种固定模板,清洗后只剩 X
      - 其他:current_node.title + 用户输入 + field
    避免老逻辑那种"全字段连缀"——会把搜索引擎搞糊涂。
    """
    if not messages:
        return {"task": "", "field": "", "seed": ""}
    raw = str(messages[-1].get("content") or "").strip()
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        payload = None
    if not isinstance(payload, dict):
        # 非 JSON,直接当原文搜
        return {"task": "", "field": "", "seed": raw[:120]}

    task = str(payload.get("task") or "")
    field = str(payload.get("field") or "").strip()
    current_problem = str(payload.get("current_problem") or "").strip()
    learning_background = str(payload.get("learning_background") or "").strip()

    def _val(key: str) -> str:
        return str(payload.get(key) or "").strip()

    def _node_title() -> str:
        cn = payload.get("current_node")
        if isinstance(cn, dict):
            return str(cn.get("title") or "").strip()
        return ""

    def _node_summary() -> str:
        cn = payload.get("current_node")
        if isinstance(cn, dict):
            return str(cn.get("summary") or "").strip()
        return ""

    def _node_path() -> str:
        cn = payload.get("current_node")
        if isinstance(cn, dict):
            return str(cn.get("path") or "").strip()
        return ""

    if task == "peek_followup":
        # 追问就是最精确的搜索意图,不要再拼锚点文本和领域(会污染)
        seed = _val("followup_question")
    elif task == "peek_definition":
        selected = _val("selected_text")
        seed = f"{selected} {field}".strip() if selected else field
    elif task == "explain":
        # explain 任务不能只搜 current_node.title。很多节点标题是抽象章节名
        # (如"行业全景与竞争格局"),必须把学习领域、目标、节点路径/摘要一起给 query agent。
        node_title = _node_title()
        node_summary = _node_summary()
        node_path = _node_path()
        cleaned_user = _strip_user_msg_boilerplate(_val("user_message"))
        parts = [
            f"领域:{field}" if field else "",
            f"学习目标:{current_problem}" if current_problem else "",
            f"节点路径:{node_path}" if node_path else "",
            f"当前节点:{node_title}" if node_title else "",
            f"节点摘要:{node_summary}" if node_summary else "",
            f"用户问题:{cleaned_user}" if cleaned_user and cleaned_user != node_title else "",
        ]
        seed = "\n".join(part for part in parts if part)
    else:
        # 兜底:挑几个最有信息量的拼,但不再带 current_problem(那是整段长描述)
        parts: list[str] = []
        node_title = _node_title()
        if node_title:
            parts.append(node_title)
        for key in ("followup_question", "selected_text"):
            v = _val(key)
            if v:
                parts.append(v)
        cleaned = _strip_user_msg_boilerplate(_val("user_message"))
        if cleaned and cleaned not in parts:
            parts.append(cleaned)
        if field:
            parts.append(field)
        seed = " ".join(parts)

    seed = " ".join(seed.split())[:500]
    return {
        "task": task,
        "field": field,
        "current_problem": current_problem,
        "learning_background": learning_background[:240],
        "node_title": _node_title(),
        "node_summary": _node_summary()[:240],
        "node_path": _node_path(),
        "user_message": _strip_user_msg_boilerplate(_val("user_message")),
        "seed": seed,
    }


def build_web_search_query(messages: list[dict[str, str]]) -> str:
    """向后兼容的薄包装:只返回 seed 字符串。"""
    return extract_search_context(messages)["seed"]


def fallback_refined_search_query(seed: str, task: str, context: dict[str, str] | None = None) -> str:
    """LLM query-agent 不可用时的规则兜底。

    explain 场景下 seed 可能是带标签的长上下文,不能直接喂给搜索引擎;
    这里尽量压成"具体领域 + 节点搜索意图"。
    """
    context = context or {}
    field = str(context.get("field") or "").strip()
    node_title = str(context.get("node_title") or "").strip()
    user_message = str(context.get("user_message") or "").strip()
    base = field or user_message or seed
    title = node_title or user_message
    parts: list[str] = []
    if base:
        parts.append(base)
    title_terms = title.replace("与", " ").replace("/", " ").strip()
    if any(token in title for token in ("行业", "全景", "市场")):
        parts.extend(["市场规模", "2025"])
    if any(token in title for token in ("竞争", "格局", "玩家")):
        parts.extend(["竞争格局", "主要玩家"])
    if any(token in title for token in ("价格", "成本", "毛利", "利润")):
        parts.extend(["成本", "毛利率", "2025"])
    if not parts and title_terms:
        parts.append(title_terms)
    if len(parts) == 1 and seed:
        parts.append(seed)
    refined = " ".join(parts)
    for ch in ("领域", "学习目标", "节点路径", "当前节点", "节点摘要", "用户问题", ":", "：", "\n"):
        refined = refined.replace(ch, " ")
    return " ".join(refined.split())[:80] or seed[:80]


def thinking_mode_profile(mode: str) -> dict[str, str]:
    normalized = mode if mode in {"Lite", "Medium", "Zen"} else "Lite"
    return {
        "Lite": {"name": "Lite", "initial_rule": "保持轻量,优先 5 到 8 个一级节点,intro 3 到 5 句话。"},
        "Medium": {"name": "Medium", "initial_rule": "中等拆分,优先 8 到 12 个一级节点,intro 4 到 6 句话。"},
        "Zen": {"name": "Zen", "initial_rule": "深度拆分,优先 12 到 18 个一级节点,intro 5 到 8 句话。"},
    }[normalized]


def _looks_similar_topic(title: str, taken: set[str]) -> bool:
    """简单的相似度查重:防止 AI 生成"消费决策"和"消费者决策"这种近义重复。"""
    if not title:
        return True
    norm = title.replace(" ", "").lower()
    if norm in taken:
        return True
    for existing in taken:
        e_norm = existing.replace(" ", "").lower()
        if norm == e_norm:
            return True
        if len(norm) >= 4 and len(e_norm) >= 4 and (norm in e_norm or e_norm in norm):
            return True
    return False


def calibrate_relevance_distribution(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if len(items) < 3:
        return items
    high_indexes = [i for i, item in enumerate(items) if clamp_metric(item.get("relevance_score", 2)) >= 3]
    max_high = max(1, (len(items) + 1) // 2)
    for index in high_indexes[max_high:]:
        items[index]["relevance_score"] = 2
        items[index]["relevance"] = 0
    for item in items:
        score = clamp_metric(item.get("relevance_score", 2))
        item["relevance_score"] = score
        item["relevance"] = 1 if score >= 3 else 0
    return items


def child_limit_for_mode(mode: str) -> int:
    return {"Lite": 4, "Medium": 7, "Zen": 12}.get(mode, 4)


class DeepSeekClient:
    """LLM 通用客户端(类名保留兼容旧 import)。

    走单一 OpenAI 兼容协议:(LLM_BASE_URL, LLM_API_KEY, LLM_MODEL)。
    所有配置优先从 LayeredSettings(DB)读,落空回 env / 默认值,
    设置页改完下一次请求立即生效。
    """

    def __init__(self, settings: Settings, timeout: float = 60.0):
        self.settings = settings
        self.timeout = timeout

    def _resolved(self, key: str, env_default: Any) -> str:
        """DB 优先,env 兜底,空值返回 ''。env_default 任意类型,统一 str()。"""
        # 局部 import 避免循环
        from app.services.settings_store import get_layered_settings

        try:
            layered = get_layered_settings()
            db_val = layered.get(key).value
            if db_val:
                return db_val
        except Exception:  # noqa: BLE001
            pass
        if env_default is None:
            return ""
        return str(env_default)

    def _resolved_int(self, key: str, env_default: int) -> int:
        raw = self._resolved(key, env_default)
        try:
            return int(raw)
        except (TypeError, ValueError):
            return env_default

    def _resolved_bool(self, key: str, env_default: bool) -> bool:
        raw = self._resolved(key, env_default)
        return str(raw).strip().lower() in {"1", "true", "yes", "on"}

    def _client(self) -> AsyncOpenAI:
        api_key = self._resolved("LLM_API_KEY", self.settings.llm_api_key)
        if not api_key:
            raise RuntimeError("LLM_API_KEY 未配置")
        base_url = self._resolved("LLM_BASE_URL", self.settings.llm_base_url) \
            or "https://api.deepseek.com/v1"
        return AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=self.timeout,
        )

    def _model(self) -> str:
        return self._resolved("LLM_MODEL", self.settings.llm_model) or "deepseek-chat"

    def _log_llm_response(self, label: str, content: str) -> None:
        if not self.settings.llm_log_responses:
            return
        preview = _shorten_for_log(content, self.settings.llm_log_max_chars)
        print(f"[knowledge_map] LLM raw response ({label}, {len(content)} chars):\n{preview}")

    async def _create_and_record(
        self,
        client: AsyncOpenAI,
        request_kwargs: dict[str, Any],
        *,
        purpose: str,
        session_id: str | None,
        web_search: bool,
        db: Any = None,
    ):
        """统一调 completions.create 并把 token 用量落库(失败不阻塞主流程)。"""
        response = await client.chat.completions.create(**request_kwargs)
        await _record_llm_usage(
            purpose=purpose,
            session_id=session_id,
            model=str(request_kwargs.get("model") or ""),
            usage=getattr(response, "usage", None),
            web_search=web_search,
            db=db,
        )
        return response

    async def _augment_with_search(
        self, messages: list[dict[str, str]], *, enable_web_search: bool, db: Any
    ) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
        """按需联网搜索,把结果注入成一条 system message。返回 (新messages, 原始sources)。

        chat() 和 chat_stream() 共用:路由决策只做一次,注入结果按 D 封顶截断。
        """
        search_sources: list[dict[str, str]] = []
        if not enable_web_search:
            return messages, search_sources

        search_provider = self._search_provider()
        print(f"[knowledge_map] web_search route: provider={search_provider}")
        search_coro = None
        if search_provider in ("open", "anysearch"):
            ctx = extract_search_context(messages)
            if ctx.get("seed"):
                need_search, refined = await self._refine_search_query(
                    ctx["seed"], ctx["task"], ctx["field"], ctx, db=db
                )
            else:
                need_search, refined = False, ""
            if need_search:
                if search_provider == "open":
                    search_coro = self._open_web_search(messages, db=db, refined=refined)
                else:
                    search_coro = self._anysearch_with_fallback(messages, db=db, refined=refined)
        else:
            print(f"[knowledge_map] web_search SKIPPED (provider={search_provider})")

        if search_coro is not None:
            try:
                search_sources = await search_coro
            except RuntimeError as exc:
                search_sources = [{
                    "status": "error", "query": build_web_search_query(messages),
                    "title": "", "link": "", "media": "", "publish_date": "",
                    "content": str(exc)[:1200], "refer": "",
                }]

        result_sources = [s for s in search_sources if s.get("status") == "result"]
        if result_sources:
            # 优化D:注入封顶 + 单条正文截断;完整结果仍存库给前端
            injected = [
                {**s, "content": str(s.get("content") or "")[:WEB_SEARCH_INJECT_CONTENT_CHARS]}
                for s in result_sources[:WEB_SEARCH_INJECT_MAX]
            ]
            messages = [
                *messages,
                {
                    "role": "system",
                    "content": (
                        "以下是后端刚刚通过网页检索拿到的网页结果。"
                        "回答时优先使用这些结果,但仍必须只输出用户要求的 JSON。"
                        f"\n{json.dumps(injected, ensure_ascii=False)}"
                    ),
                },
            ]
        return messages, search_sources

    async def chat(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.35,
        enable_web_search: bool = False,
        purpose: str = "chat",
        session_id: str | None = None,
        db: Any = None,
    ) -> dict[str, Any]:
        """统一走 OpenAI 兼容协议。

        enable_web_search=True 时按 SEARCH_PROVIDER 走 open / anysearch,拿到
        检索结果后塞成一条 system message 喂回 LLM。
        返回 dict 里带 `_web_search_sources` 字段(可能为空),供上层存到 message.search_sources。
        """
        messages, search_sources = await self._augment_with_search(
            messages, enable_web_search=enable_web_search, db=db
        )

        client = self._client()
        model = self._model()
        request_kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": self._resolved_int("LLM_MAX_TOKENS", self.settings.llm_max_tokens),
            "response_format": {"type": "json_object"},
        }

        try:
            response = await self._create_and_record(
                client, request_kwargs,
                purpose=purpose, session_id=session_id, web_search=enable_web_search, db=db,
            )
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"LLM 请求失败: {exc}") from exc

        content = response.choices[0].message.content or ""
        self._log_llm_response(model, content)
        try:
            data = extract_json_object(content)
        except (json.JSONDecodeError, ValueError):
            # JSON 解析失败时一次性重试:在 messages 末尾追加 system 指令
            retry_messages = [
                *messages,
                {
                    "role": "system",
                    "content": (
                        "上一轮输出不是合法 JSON。请严格只输出一个 JSON object,"
                        "不要 Markdown,不要解释,不要代码块,字段必须符合前面约定的 json_schema。"
                    ),
                },
            ]
            try:
                response = await self._create_and_record(
                    client, {**request_kwargs, "messages": retry_messages},
                    purpose=f"{purpose}_jsonretry", session_id=session_id, web_search=enable_web_search, db=db,
                )
            except Exception as exc:  # noqa: BLE001
                raise RuntimeError(f"LLM JSON 重试失败: {exc}") from exc
            content = response.choices[0].message.content or ""
            self._log_llm_response(f"{model}-json-retry", content)
            data = extract_json_object(content)

        data["_web_search_sources"] = search_sources
        return data

    async def chat_stream(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.35,
        enable_web_search: bool = False,
        purpose: str = "chat",
        session_id: str | None = None,
        db: Any = None,
    ):
        """流式版 chat:边生成边把 reply 字段的新增片段 yield 出去。

        产出顺序:多个 ("token", 片段) → 最后一个 ("data", 完整解析后的 dict)。
        ("data", ...) 里带 `_web_search_sources`,字段与 chat() 返回一致,供上层做副作用。
        reply 之外的字段(status/summary/content/next_actions)等整段生成完才解析。
        """
        messages, search_sources = await self._augment_with_search(
            messages, enable_web_search=enable_web_search, db=db
        )
        client = self._client()
        model = self._model()
        request_kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": self._resolved_int("LLM_MAX_TOKENS", self.settings.llm_max_tokens),
            "response_format": {"type": "json_object"},
            "stream": True,
            "stream_options": {"include_usage": True},
        }

        raw = ""
        emitted = 0
        usage_obj = None
        try:
            stream = await client.chat.completions.create(**request_kwargs)
            async for chunk in stream:
                if getattr(chunk, "usage", None) is not None:
                    usage_obj = chunk.usage  # include_usage:用量在最后一块
                if not getattr(chunk, "choices", None):
                    continue
                piece = getattr(chunk.choices[0].delta, "content", None) or ""
                if not piece:
                    continue
                raw += piece
                reply_so_far = extract_partial_reply(raw)
                if len(reply_so_far) > emitted:
                    yield ("token", reply_so_far[emitted:])
                    emitted = len(reply_so_far)
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"LLM 流式请求失败: {exc}") from exc

        await _record_llm_usage(
            purpose=purpose, session_id=session_id, model=model,
            usage=usage_obj, web_search=enable_web_search, db=db,
        )
        self._log_llm_response(f"{model}-stream", raw)

        try:
            data = extract_json_object(raw)
        except (json.JSONDecodeError, ValueError):
            # 流式产物不是合法 JSON:用已抽出的 reply 兜底,其余字段留默认
            data = {"reply": extract_partial_reply(raw) or raw.strip()}
        data["_web_search_sources"] = search_sources
        yield ("data", data)

    async def _refine_search_query(
        self, seed: str, task: str, field: str, context: dict[str, str] | None = None, *, db: Any = None
    ) -> tuple[bool, str]:
        """搜索路由 + query 改写,一次轻量 LLM 调用同时决定:
          1. need_search —— 这一轮要不要联网搜?(宽松:拿不准就搜,只有明显不依赖时效/外部
             事实的纯原理/定义/推导/举例才判 False)
          2. query —— 改写成中文搜索引擎友好的 2-5 个关键词

        返回 (need_search, query)。失败/超时一律退回 (True, seed 兜底改写),不阻塞主流程。
        成本:每轮主对话 +1 次 mini LLM 调用(本来就有,这里只是多让它顺手判断要不要搜)。
        """
        if not seed:
            return (False, "")
        context = context or {}
        # 非主对话的短专有名词通常本身就是好 query,且划词速览这类一般值得搜 → 直接搜,省一次调用
        if task != "explain" and len(seed) <= 16 and not any(p in seed for p in ("？", "?", "吗", "呢", "啥")):
            return (True, seed)
        # 提速4:快路径——明显的概念/原理题(且无时效词)直接判定不搜,省掉这次路由 LLM 往返
        scan = f"{seed} {context.get('user_message') or ''}"
        has_fresh = any(k in scan for k in _SEARCH_FRESHNESS_HINTS)
        if not has_fresh and any(k in scan for k in _SEARCH_CONCEPTUAL_HINTS):
            print(f"[knowledge_map] search routing(fast): 概念题跳过路由 LLM (seed={seed[:30]!r})")
            return (False, "")
        try:
            client = self._client()
            model = self._model()
            task_label = {
                "peek_followup": "划词追问",
                "peek_definition": "划词速览",
                "explain": "主对话讲解",
            }.get(task, "通用")
            context_lines = []
            for label, key in (
                ("领域", "field"),
                ("学习目标", "current_problem"),
                ("学习背景", "learning_background"),
                ("节点路径", "node_path"),
                ("当前节点", "node_title"),
                ("节点摘要", "node_summary"),
                ("用户问题", "user_message"),
            ):
                value = str(context.get(key) or "").strip()
                if value:
                    context_lines.append(f"{label}:{value}")
            context_block = "\n".join(context_lines) or f"领域:{field or '通用'}\n用户意图:{seed}"
            instruction = (
                "你是一个会调用搜索工具的学习型 AI agent。先判断这一轮要不要联网搜索,再给出搜索关键词。\n"
                "【need_search 判断】默认 true,宁可多搜不要漏搜。只有当问题明显是【不依赖时效信息、"
                "也不依赖外部具体事实】的纯概念时才填 false,例如:讲原理/定义/推导/打比方/举个例子/"
                "解释刚才那段话。一旦涉及最新动态、市场行情、价格、政策、公司经营、具体数字/年份、"
                "'是什么/有哪些'这类需要事实佐证的,一律 true。\n"
                "【query 规则】\n"
                "  - 2-5 个关键词,空格分隔,不带引号/标点\n"
                "  - 不要照抄'行业全景与竞争格局'、'基础概念'这类抽象节点名;保留具体领域/行业/品牌/对象\n"
                "  - 专有名词、品牌名、数字、年份优先;删掉'什么/怎么样/有哪些/最'\n"
                "  - 涉及市场/竞争/价格/政策/最新数据时加'2025'或'最新'\n"
                "  - need_search=false 时 query 可以留空或给个兜底词\n"
                '只输出 JSON: {"need_search": true/false, "query": "关键词"}\n'
                "示例:\n"
                '  输入: 用户问题:某公司最新财报数据 → {"need_search": true, "query": "公司名 财报 2025"}\n'
                '  输入: 用户问题:再深入讲讲 softmax 为什么要除以根号dk → {"need_search": false, "query": ""}\n'
                '  输入: 用户问题:神经递质是啥 → {"need_search": true, "query": "神经递质 作用机制"}\n'
                '  输入: 用户问题:举个3个token的小例子 → {"need_search": false, "query": ""}\n'
                f"\n场景:{task_label}\n上下文:\n{context_block}\n原始 seed:{seed}"
            )
            response = await self._create_and_record(
                client,
                {
                    "model": model,
                    "messages": [{"role": "user", "content": instruction}],
                    "temperature": 0.1,
                    "max_tokens": 120,
                    "response_format": {"type": "json_object"},
                },
                purpose="refine_query",
                session_id=None,
                web_search=False,
                db=db,
            )
            data = extract_json_object(response.choices[0].message.content or "")
            need_search = bool(data.get("need_search", True))
            refined = str(data.get("query") or "").strip()
            for ch in ("，", ",", "、", "。", ".", ":", "：", '"', '"', "\n"):
                refined = refined.replace(ch, " ")
            refined = " ".join(refined.split())[:80]
            if not need_search:
                print(f"[knowledge_map] search routing: AI 判定本轮无需联网 (seed={seed[:30]!r})")
            return (need_search, refined or seed)
        except Exception as exc:  # noqa: BLE001
            # 失败不阻塞:宽松地默认搜,query 走规则兜底
            print(f"[knowledge_map] _refine_search_query fallback: {exc}")
            return (True, fallback_refined_search_query(seed, task, context))

    def _search_provider(self) -> str:
        """检索路由:open / anysearch / off。"""
        raw = self._resolved("SEARCH_PROVIDER", self.settings.search_provider) or "open"
        provider = raw.strip().lower()
        return provider if provider in {"open", "anysearch", "off"} else "open"

    async def _open_web_search(
        self, messages: list[dict[str, str]], *, db: Any = None, refined: str | None = None
    ) -> list[dict[str, str]]:
        """调本地 open-webSearch daemon 拿检索结果。
        前提:`cd external/open-webSearch && npm run serve` 已启动,默认监听 127.0.0.1:3210。
        refined 已由上层路由算好时直接用,否则自己跑一次路由(兼容单独调用)。
        """
        context = extract_search_context(messages)
        seed = context["seed"]
        if not seed:
            return []
        if refined is None:
            need_search, refined = await self._refine_search_query(seed, context["task"], context["field"], context, db=db)
            if not need_search:
                return []
        engines = [e.strip() for e in self.settings.open_websearch_engines.split(",") if e.strip()]
        try:
            sources = await open_websearch_client.search(
                base_url=self.settings.open_websearch_url,
                query=refined,
                engines=engines,
                search_mode=self.settings.open_websearch_search_mode,
                limit=self.settings.open_websearch_limit,
                timeout=self.settings.open_websearch_timeout,
            )
        except httpx.HTTPError as exc:
            raise RuntimeError(f"open-webSearch 请求失败: {exc!r}") from exc
        for source in sources:
            source["query"] = refined
        if sources:
            return sources
        return [{
            "status": "empty",
            "query": refined,
            "title": "",
            "link": "",
            "media": "",
            "publish_date": "",
            "content": "",
            "refer": "",
        }]

    async def _anysearch_with_fallback(
        self, messages: list[dict[str, str]], *, db: Any = None, refined: str | None = None
    ) -> list[dict[str, str]]:
        """anysearch 失败 / 0 召回 时,自动落到本地 open-webSearch daemon。
        日志会打出哪条路径出结果,排查问题方便。
        """
        try:
            sources = await self._anysearch_web_search(messages, db=db, refined=refined)
            result_count = sum(1 for s in sources if s.get("status") == "result")
            if result_count > 0:
                return sources
            print(
                "[knowledge_map] anysearch returned 0 result sources, "
                "falling back to open-webSearch"
            )
        except RuntimeError as exc:
            print(
                f"[knowledge_map] anysearch failed: {exc}, "
                f"falling back to open-webSearch"
            )
        try:
            fallback_sources = await self._open_web_search(messages, db=db, refined=refined)
            return fallback_sources
        except RuntimeError as exc:
            print(f"[knowledge_map] fallback open-webSearch also failed: {exc}")
            return [{
                "status": "error",
                "query": "",
                "title": "",
                "link": "",
                "media": "",
                "publish_date": "",
                "content": f"anysearch + open 都失败: {exc}"[:1200],
                "refer": "",
            }]

    async def _anysearch_web_search(
        self, messages: list[dict[str, str]], *, db: Any = None, refined: str | None = None
    ) -> list[dict[str, str]]:
        """调 AnySearch /v1/search 拿统一搜索结果。

        api_key 可空(走匿名/IP 限额),设了的话用 Bearer 认证(更高并发 + 付费额度)。
        返回 normalize 后的 source 列表,和 _open_web_search 同构。
        refined 已由上层路由算好时直接用,否则自己跑一次路由(兼容单独调用)。
        """
        context = extract_search_context(messages)
        seed = context["seed"]
        if not seed:
            print("[knowledge_map] anysearch: seed 为空,跳过搜索")
            return []
        if refined is None:
            need_search, refined = await self._refine_search_query(seed, context["task"], context["field"], context, db=db)
            if not need_search:
                return []
        api_key = self._resolved("ANYSEARCH_API_KEY", self.settings.anysearch_api_key) or None
        zone = (self.settings.anysearch_zone or "").strip() or None
        language = (self.settings.anysearch_language or "").strip() or None
        content_limit = self._resolved_int(
            "ANYSEARCH_CONTENT_LIMIT", self.settings.anysearch_content_char_limit
        )
        auth_mode = "bearer" if api_key else "anonymous"
        print(
            f"[knowledge_map] anysearch query={refined!r} auth={auth_mode} "
            f"content_limit={content_limit}"
        )
        try:
            sources = await anysearch_client.search(
                base_url=self.settings.anysearch_base_url,
                query=refined,
                api_key=api_key,
                max_results=self._resolved_int("ANYSEARCH_LIMIT", self.settings.anysearch_limit),
                zone=zone,
                language=language,
                timeout=self.settings.anysearch_timeout,
                content_char_limit=content_limit,
            )
        except httpx.HTTPError as exc:
            print(f"[knowledge_map] anysearch HTTP 错误: {exc!r}")
            raise RuntimeError(f"anysearch 请求失败: {exc!r}") from exc
        print(f"[knowledge_map] anysearch got {len(sources)} sources")
        for source in sources:
            source["query"] = refined
        if sources:
            return sources
        return [{
            "status": "empty",
            "query": refined,
            "title": "",
            "link": "",
            "media": "",
            "publish_date": "",
            "content": "",
            "refer": "anysearch",
        }]

    async def web_search_query(self, query: str, *, limit: int = 20) -> list[dict[str, str]]:
        """按明确 query 做一次联网搜索,用于用户主动触发的深度搜索。"""
        refined = " ".join(str(query or "").split())[:120]
        if not refined:
            return []
        search_provider = self._search_provider()
        if search_provider == "open":
            engines = [e.strip() for e in self.settings.open_websearch_engines.split(",") if e.strip()]
            try:
                sources = await open_websearch_client.search(
                    base_url=self.settings.open_websearch_url,
                    query=refined,
                    engines=engines,
                    search_mode=self.settings.open_websearch_search_mode,
                    limit=limit,
                    timeout=self.settings.open_websearch_timeout,
                )
            except httpx.HTTPError as exc:
                raise RuntimeError(f"open-webSearch 请求失败: {exc!r}") from exc
            for source in sources:
                source["query"] = refined
            return sources
        if search_provider == "anysearch":
            api_key = self._resolved("ANYSEARCH_API_KEY", self.settings.anysearch_api_key) or None
            zone = (self.settings.anysearch_zone or "").strip() or None
            language = (self.settings.anysearch_language or "").strip() or None
            content_limit = self._resolved_int(
                "ANYSEARCH_CONTENT_LIMIT", self.settings.anysearch_content_char_limit
            )
            anysearch_failed = False
            try:
                sources = await anysearch_client.search(
                    base_url=self.settings.anysearch_base_url,
                    query=refined,
                    api_key=api_key,
                    # 深度搜索时拉满一些,前端会限制展示数量
                    max_results=min(
                        max(
                            self._resolved_int("ANYSEARCH_LIMIT", self.settings.anysearch_limit),
                            limit,
                        ),
                        100,
                    ),
                    zone=zone,
                    language=language,
                    timeout=self.settings.anysearch_timeout,
                    content_char_limit=content_limit,
                )
                if sources:
                    for source in sources:
                        source["query"] = refined
                    return sources
                print(
                    "[knowledge_map] deep-search anysearch 0 sources, fallback to open-webSearch"
                )
            except (httpx.HTTPError, RuntimeError) as exc:
                print(
                    f"[knowledge_map] deep-search anysearch failed: {exc!r}, "
                    f"fallback to open-webSearch"
                )
                anysearch_failed = True
            # === Fallback: open-webSearch daemon ===
            engines = [e.strip() for e in self.settings.open_websearch_engines.split(",") if e.strip()]
            try:
                fb_sources = await open_websearch_client.search(
                    base_url=self.settings.open_websearch_url,
                    query=refined,
                    engines=engines,
                    search_mode=self.settings.open_websearch_search_mode,
                    limit=limit,
                    timeout=self.settings.open_websearch_timeout,
                )
                for source in fb_sources:
                    source["query"] = refined
                return fb_sources
            except httpx.HTTPError as fb_exc:
                # anysearch 已经失败,fallback 也挂了 → 把两层错误信息塞进 source 让前端看到
                err_msg = (
                    f"anysearch 0 召回 + open-webSearch 也失败: {fb_exc!r}"
                    if not anysearch_failed
                    else f"anysearch + open-webSearch 都失败: {fb_exc!r}"
                )[:1200]
                raise RuntimeError(err_msg) from fb_exc
        raise RuntimeError("联网搜索已关闭")

    async def initial_map(
        self,
        field: str,
        current_problem: str,
        learning_background: str = "",
        mode: str = "Lite",
        db: Any = None,
    ) -> tuple[list[dict[str, Any]], str]:
        """首轮一次性拆出完整的两层知识树。

        返回 topics 列表,每项可以带一个 `children` 数组(二级节点)。
        档位 + learning_background 决定一级节点数量和每个分支的展开深度。
        """
        profile = thinking_mode_profile(mode)
        background_text = learning_background.strip() or "用户未说明背景,默认按有兴趣但基础不完整的新手处理。"
        # 主体 instructions 走 prompt_store(后台可编辑);JSON 格式硬约束是结构契约,留在代码里
        instructions = _resolve_prompt(
            "initial_map.instructions",
            field=field,
            current_problem=current_problem,
            background_text=background_text,
            mode_name=profile["name"],
        )
        prompt = instructions + """

JSON 格式:
{
  "topics": [
    {
      "title": "一级节点标题",
      "importance": 3, "relevance_score": 3, "difficulty": 2,
      "summary": "一句话摘要",
      "children": [
        {
          "title": "二级节点标题",
          "importance": 2, "relevance_score": 2, "difficulty": 2,
          "summary": "一句话摘要"
        }
      ]
    }
  ],
  "intro": "3 到 5 句话,解释这个领域解决什么问题,并提示用户从右侧树的第 1 个节点开始,点击卡片即可进入学习"
}
"""
        # 首轮拆树:temperature 调到 0.5,鼓励 AI 探索更多分支,
        # 避免 DeepSeek 默认温度下贴着下限走、覆盖不完整
        data = await self.chat(
            [
                {"role": "system", "content": "你只输出合法 JSON,不输出 Markdown。"},
                {"role": "user", "content": prompt},
            ],
            temperature=0.5,
            purpose="initial_map",
            db=db,
        )

        def _clean_topic(item: Any, *, with_children: bool) -> dict[str, Any] | None:
            if not isinstance(item, dict):
                return None
            title = str(item.get("title") or "").strip()
            if not title:
                return None
            summary = str(item.get("summary") or "").strip()
            relevance_score = clamp_metric(
                item.get("relevance_score", 3 if item.get("relevance") else 2)
            )
            cleaned: dict[str, Any] = {
                "title": title[:40],
                "importance": clamp_metric(item.get("importance", 2)),
                "relevance_score": relevance_score,
                "difficulty": clamp_metric(item.get("difficulty", 2)),
                "relevance": 1 if relevance_score >= 3 else 0,
                "summary": summary[:160],
            }
            if with_children:
                raw_children = item.get("children")
                children: list[dict[str, Any]] = []
                if isinstance(raw_children, list):
                    for sub in raw_children:
                        sub_clean = _clean_topic(sub, with_children=False)
                        if sub_clean is not None:
                            children.append(sub_clean)
                cleaned["children"] = calibrate_relevance_distribution(children)
            return cleaned

        topics: list[dict[str, Any]] = []
        for item in data.get("topics", []):
            cleaned = _clean_topic(item, with_children=True)
            if cleaned is None:
                continue
            # 两步法守门:一级节点必须有 ≥ 1 个 children(理想 ≥ 2)。
            # 否则它就是"光秃秃的知识点",而不是分组卡片——直接跳过。
            if not cleaned.get("children"):
                continue
            topics.append(cleaned)

        intro = str(data.get("intro") or "").strip()
        if not topics:
            fallback = default_topics(field)
            # fallback 自带 children;保险起见删掉没 children 的项
            fallback = [t for t in fallback if t.get("children")]
            return calibrate_relevance_distribution(fallback), intro
        return calibrate_relevance_distribution(topics), intro

    async def preview_topics(
        self,
        field: str,
        current_problem: str,
        learning_background: str = "",
        mode: str = "Lite",
        db: Any = None,
    ) -> list[dict[str, str]]:
        """轻量预览:只生成主干一级节点(title + 一句话 summary),不展开 children。

        用在"预览-编辑-确认"流程的预览阶段,LLM 调用规模比 initial_map 小一半以上,
        典型 2-3s 返回。失败抛 RuntimeError 让上层提示用户重试。
        """
        profile = thinking_mode_profile(mode)
        background_text = learning_background.strip() or "用户未说明背景,默认按有兴趣但基础不完整的新手处理。"
        instructions = _resolve_prompt(
            "preview_topics.instructions",
            field=field,
            current_problem=current_problem,
            background_text=background_text,
            mode_name=profile["name"],
        )
        prompt = instructions + """

只输出 JSON:
{
  "topics": [
    {"title": "一级节点标题(≤24 字)", "summary": "一句话(≤60 字)"}
  ]
}
"""
        data = await self.chat(
            [
                {"role": "system", "content": "你只输出合法 JSON,不输出 Markdown。"},
                {"role": "user", "content": prompt},
            ],
            temperature=0.4,
            purpose="preview_topics",
            db=db,
        )
        raw_topics = data.get("topics")
        if not isinstance(raw_topics, list) or not raw_topics:
            raise RuntimeError("AI 未返回有效的主卡片列表")
        cleaned: list[dict[str, str]] = []
        taken: set[str] = set()
        for item in raw_topics:
            if not isinstance(item, dict):
                continue
            title = str(item.get("title") or "").strip()[:40]
            if not title or _looks_similar_topic(title, taken):
                continue
            taken.add(title)
            cleaned.append({
                "title": title,
                "summary": str(item.get("summary") or "").strip()[:160],
            })
        if not cleaned:
            raise RuntimeError("AI 返回的主卡片都无法解析")
        return cleaned

    async def expand_topic_children(
        self,
        field: str,
        current_problem: str,
        topic_title: str,
        topic_summary: str,
        mode: str = "Lite",
        db: Any = None,
    ) -> list[dict[str, Any]]:
        """围绕单个一级主干节点,生成 3-8 个具体的二级子节点(含 importance/relevance/difficulty)。

        用于"确认后 SSE 流式生长":每个主干并发跑一次,FIFO 推给前端,前端边收边演。
        失败时返回空列表(不阻塞其他主干的生成),由前端选择是否兜底显示"未展开"。
        """
        profile = thinking_mode_profile(mode)
        child_count = {"Lite": "3-4", "Medium": "4-6", "Zen": "5-8"}.get(profile["name"], "3-4")
        instructions = _resolve_prompt(
            "expand_topic_children.instructions",
            field=field,
            current_problem=current_problem,
            topic_title=topic_title,
            topic_summary=topic_summary or "(未提供)",
            child_count=child_count,
        )
        prompt = instructions + """

只输出 JSON:
{
  "children": [
    {"title":"...", "summary":"...专业人士常用：...", "importance":2, "relevance_score":2, "difficulty":2, "prerequisites":[]}
  ]
}
"""
        try:
            data = await self.chat(
                [
                    {"role": "system", "content": "你只输出合法 JSON,不输出 Markdown。"},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.45,
                purpose="expand_topic_children",
                db=db,
            )
        except Exception as exc:  # noqa: BLE001
            print(f"[knowledge_map] expand_topic_children fallback (topic={topic_title}): {exc}")
            return []
        raw_children = data.get("children") if isinstance(data, dict) else None
        if not isinstance(raw_children, list):
            return []
        cleaned: list[dict[str, Any]] = []
        taken: set[str] = set()
        for item in raw_children:
            if not isinstance(item, dict):
                continue
            title = str(item.get("title") or "").strip()[:40]
            if not title or _looks_similar_topic(title, taken):
                continue
            taken.add(title)
            relevance_score = clamp_metric(
                item.get("relevance_score", 3 if item.get("relevance") else 2)
            )
            raw_prereqs = item.get("prerequisites")
            prereq_titles = (
                [str(p).strip()[:40] for p in raw_prereqs if str(p).strip()]
                if isinstance(raw_prereqs, list)
                else []
            )
            cleaned.append({
                "title": title,
                "summary": str(item.get("summary") or "").strip()[:160],
                "importance": clamp_metric(item.get("importance", 2)),
                "relevance_score": relevance_score,
                "difficulty": clamp_metric(item.get("difficulty", 2)),
                "relevance": 1 if relevance_score >= 3 else 0,
                # 同批兄弟依赖,按 title 表达;后端建节点后映射成 id
                "prerequisite_titles": prereq_titles,
            })
        return calibrate_relevance_distribution(cleaned)

    async def expand_first_principles(
        self,
        field: str,
        current_problem: str,
        node_title: str,
        node_summary: str,
        node_path: str,
        current_depth: int,
        max_depth: int,
        db: Any = None,
    ) -> dict[str, Any]:
        """第一性原理"拆到底":对一个知识点,找出 1-3 个更底层的前置依赖。

        返回 {"children": [...], "is_fundamental": bool}:
          - children:更底层的前置依赖(title/summary/is_fundamental),空 = 已触底
          - is_fundamental:当前节点本身是否已经是基础公理/最小单位
        失败或解析不出时返回空 children + is_fundamental=False(由上层决定是否兜底停止)。
        """
        instructions = _resolve_prompt(
            "first_principles.instructions",
            field=field,
            current_problem=current_problem,
            node_title=node_title,
            node_summary=node_summary or "(未提供)",
            node_path=node_path or node_title,
            current_depth=current_depth,
            max_depth=max_depth,
        )
        prompt = instructions + """

只输出 JSON:
{
  "is_fundamental": false,
  "children": [
    {
      "title":"...",
      "summary":"为什么它是上层的地基",
      "relation":"它和父知识点的知识关联",
      "why":"为什么按第一性原理必须拆到这里",
      "is_fundamental": false
    }
  ]
}
"""
        try:
            data = await self.chat(
                [
                    {"role": "system", "content": "你只输出合法 JSON,不输出 Markdown。"},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.3,
                purpose="expand_first_principles",
                db=db,
            )
        except Exception as exc:  # noqa: BLE001
            print(f"[knowledge_map] expand_first_principles fallback (node={node_title}): {exc}")
            return {"children": [], "is_fundamental": False}
        if not isinstance(data, dict):
            return {"children": [], "is_fundamental": False}
        raw_children = data.get("children") if isinstance(data.get("children"), list) else []
        cleaned: list[dict[str, Any]] = []
        taken: set[str] = set()
        for item in raw_children:
            if not isinstance(item, dict):
                continue
            title = str(item.get("title") or "").strip()[:40]
            if not title or _looks_similar_topic(title, taken):
                continue
            taken.add(title)
            cleaned.append({
                "title": title,
                "summary": str(item.get("summary") or "").strip()[:160],
                "relation": str(item.get("relation") or item.get("fp_relation") or "").strip()[:80],
                "why": str(item.get("why") or item.get("fp_reason") or "").strip()[:400],
                "is_fundamental": bool(item.get("is_fundamental", False)),
            })
            if len(cleaned) >= 3:  # 硬上限:每层最多 3 个底层依赖
                break
        return {
            "children": cleaned,
            "is_fundamental": bool(data.get("is_fundamental", False)),
        }

    async def background_questions(
        self,
        field: str,
        current_problem: str,
        mode: str = "Lite",
        db: Any = None,
    ) -> list[dict[str, Any]]:
        prompt = {
            "field": field,
            "current_problem": current_problem,
            "thinking_mode": thinking_mode_profile(mode),
            "task": "generate_learning_background_questions",
            # 主体 instructions 走 prompt_store("background_quiz.instructions"),
            # admin 可在设置页编辑
            "instructions": _resolve_prompt_lines("background_quiz.instructions", field=field),
            # 注意:json_schema 例子用的是结构示例,不要被字段名 "starting_point" 暗示
            # 必须出 "起点" 类的题。每个 field 都该单独设计自己的题。
            "json_schema": {
                "questions": [
                    {
                        "id": "<field 相关的英文短标识>",
                        "question": "<提问文本,必须用到 field 内具体名词>",
                        "options": [
                            {
                                "label": "<≤12 字按钮文案>",
                                "value": "<这个选择对后续教学策略的具体影响,带 field 内名词,≤200 字>",
                            }
                        ],
                    }
                ]
            },
        }
        # 出题温度调到 0.55:鼓励 AI 跳出模板,出 field-specific 的题
        data = await self.chat(
            [
                {"role": "system", "content": "你只输出合法 JSON。"},
                {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
            ],
            temperature=0.55,
            purpose="background_questions",
            db=db,
        )
        questions: list[dict[str, Any]] = []
        for index, item in enumerate(data.get("questions", []), start=1):
            if not isinstance(item, dict):
                continue
            question = str(item.get("question") or "").strip()
            raw_options = item.get("options") if isinstance(item.get("options"), list) else []
            options = []
            for option in raw_options[:4]:
                if not isinstance(option, dict):
                    continue
                label = str(option.get("label") or "").strip()
                value = str(option.get("value") or label).strip()
                if label and value:
                    options.append({"label": label[:80], "value": value[:240]})
            if question and len(options) == 4:
                questions.append(
                    {
                        "id": str(item.get("id") or f"q{index}")[:40],
                        "question": question[:160],
                        "options": options,
                    }
                )
        return questions[:5]

    async def background_followup(
        self,
        field: str,
        current_problem: str,
        answered: list[dict[str, str]],
        mode: str = "Lite",
        follow_up_round: int = 0,
        db: Any = None,
    ) -> dict[str, Any]:
        """根据已经答完的问题,判断要不要继续追问、追问什么。

        返回 {need_more: bool, reason: str, questions: list}。
        AI 觉得用户的底子和学习目标对该 field 还有"暧昧地带"才追问,
        否则 need_more=false,直接进入生成流程。
        """
        prompt = {
            "field": field,
            "current_problem": current_problem,
            "thinking_mode": thinking_mode_profile(mode),
            "follow_up_round": follow_up_round,
            "answered": answered,
            "task": "decide_followup_questions",
            "instructions": _resolve_prompt_lines("background_followup.instructions"),
            "json_schema": {
                "need_more": True,
                "reason": "你说自己是中学生,但流体力学需要确认数学基础,我再问一题。",
                "questions": [
                    {
                        "id": "math_level",
                        "question": "你目前数学学到哪一步了?",
                        "options": [
                            {"label": "初中代数", "value": "你的数学到初中代数,涉及微积分的部分要先讲直观图形和类比,跳过推导。"}
                        ],
                    }
                ],
            },
        }
        # 强制收手:已追问 2 轮就不再调 AI,直接 need_more=false
        if follow_up_round >= 2:
            return {"need_more": False, "reason": "", "questions": []}
        data = await self.chat(
            [
                {"role": "system", "content": "你只输出合法 JSON。"},
                {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
            ],
            temperature=0.3,
            purpose="background_followup",
            db=db,
        )
        need_more = bool(data.get("need_more"))
        reason = str(data.get("reason") or "").strip()[:200]
        questions: list[dict[str, Any]] = []
        if need_more:
            for index, item in enumerate(data.get("questions", []), start=1):
                if not isinstance(item, dict):
                    continue
                question = str(item.get("question") or "").strip()
                raw_options = item.get("options") if isinstance(item.get("options"), list) else []
                options = []
                for option in raw_options[:4]:
                    if not isinstance(option, dict):
                        continue
                    label = str(option.get("label") or "").strip()
                    value = str(option.get("value") or label).strip()
                    if label and value:
                        options.append({"label": label[:80], "value": value[:240]})
                if question and len(options) == 4:
                    questions.append({
                        "id": str(item.get("id") or f"fu{follow_up_round}_{index}")[:40],
                        "question": question[:160],
                        "options": options,
                    })
            if not questions:
                # 模型说要追问但没给有效题——视为不追问
                need_more = False
        return {
            "need_more": need_more,
            "reason": reason if need_more else "",
            "questions": questions[:3],
        }
