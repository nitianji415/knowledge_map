"""intent 分流 + next_actions 持久化的回归。

测试走本地 fallback(DeepSeek 不可用),验证:
- intent=subdivide 会触发拆分,创建子节点
- intent=explain 不创建子节点
- 默认 intent=auto,根据关键词推断
- AI message 上能拿到 next_actions
- 创建会话时 intro 消息已经带了起步建议
"""

from __future__ import annotations

import json

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.services.knowledge import KnowledgeMapService, _reply_from_ai_data


async def _create(client: AsyncClient) -> dict:
    response = await client.post(
        "/api/sessions",
        json={"field": "连锁零售经营分析", "current_problem": "和老板讲清楚单店模型"},
    )
    assert response.status_code == 201
    return response.json()


async def _collect_done(client: AsyncClient, session_id: str, body: dict) -> dict:
    async with client.stream(
        "POST", f"/api/sessions/{session_id}/messages/stream", json=body
    ) as response:
        assert response.status_code == 200
        chunks: list[str] = []
        async for chunk in response.aiter_text():
            chunks.append(chunk)
    text = "".join(chunks)
    done_events = [seg for seg in text.split("\n\n") if seg.startswith("event: done")]
    assert done_events, "missing done event"
    data_line = next(line for line in done_events[-1].splitlines() if line.startswith("data: "))
    return json.loads(data_line.removeprefix("data: "))


async def test_intro_message_has_starter_actions(client: AsyncClient) -> None:
    payload = await _create(client)
    messages = payload["messages"]
    assert messages
    intro = messages[-1]
    assert intro["role"] == "assistant"
    assert intro["next_actions"], "intro 必须给起步建议"
    for action in intro["next_actions"]:
        assert action["kind"] in {"explain", "subdivide"}


def test_reply_from_ai_data_accepts_flash_answer_object() -> None:
    reply = _reply_from_ai_data(
        {
            "answer": {
                "direct_answer": "要算出单店每天至少卖多少才不亏,先算盈亏平衡点。",
                "core_mechanism": "固定成本除以单杯贡献毛利。",
                "specific_case": "如果固定成本每天1000元,单杯贡献毛利10元,每天至少100杯。",
                "connection_to_current_problem": "这能帮你判断单店模型是否健康。",
                "small_exercise": "列出租金、人力、原料和客单价。",
                "location_in_map": "连锁零售经营分析 / 单店经济模型 / 盈亏平衡",
            }
        }
    )

    assert reply.startswith("要算出单店每天至少卖多少才不亏")
    assert "### 核心机制" in reply
    assert "每天至少100杯" in reply


def _pick_first_level1(payload: dict) -> dict:
    """随便挑一个一级节点;首轮树的一级节点都已经有 children。"""
    return next(n for n in payload["initial_nodes"] if n["depth"] == 1)


async def test_subdivide_intent_creates_children(client: AsyncClient) -> None:
    payload = await _create(client)
    session_id = payload["session_id"]
    leaf = _pick_first_level1(payload)
    children_before = {
        n["id"] for n in payload["initial_nodes"] if n["parent_id"] == leaf["id"]
    }

    done = await _collect_done(
        client,
        session_id,
        {"message": "拆开看", "current_node_id": leaf["id"], "intent": "subdivide", "mode": "Lite"},
    )
    # 两步法:subdivide 不再直接给 leaf 加 children,而是先建一个'中间分支'卡片,再在它下面挂子节点
    new_children = [
        n
        for n in done["nodes"]
        if n["parent_id"] == leaf["id"] and n["id"] not in children_before
    ]
    assert new_children, "subdivide 应该在 leaf 下长出一个新的中间分支卡片"
    middle = new_children[0]
    grand_children = [n for n in done["nodes"] if n["parent_id"] == middle["id"]]
    assert grand_children, "中间分支卡片下面必须挂具体子节点(两步法)"
    assert done["status"] == "deepening"
    assert done["created_node_ids"]


async def test_explain_intent_does_not_create_children(client: AsyncClient) -> None:
    payload = await _create(client)
    session_id = payload["session_id"]
    leaf = next(n for n in payload["initial_nodes"] if n["depth"] == 1)
    children_before = {
        n["id"] for n in payload["initial_nodes"] if n["parent_id"] == leaf["id"]
    }

    done = await _collect_done(
        client,
        session_id,
        {"message": "讲讲这个", "current_node_id": leaf["id"], "intent": "explain", "mode": "Lite"},
    )
    children_after = {n["id"] for n in done["nodes"] if n["parent_id"] == leaf["id"]}
    assert children_after == children_before, "explain 不能新增子节点"
    assert done["created_node_ids"] == []


async def test_auto_intent_keyword_routes_to_subdivide(client: AsyncClient) -> None:
    payload = await _create(client)
    session_id = payload["session_id"]
    leaf = _pick_first_level1(payload)
    children_before = {
        n["id"] for n in payload["initial_nodes"] if n["parent_id"] == leaf["id"]
    }

    done = await _collect_done(
        client,
        session_id,
        {"message": "请把这个拆开", "current_node_id": leaf["id"], "mode": "Lite"},
    )
    new_under_leaf = [
        n
        for n in done["nodes"]
        if n["parent_id"] == leaf["id"] and n["id"] not in children_before
    ]
    assert new_under_leaf, "auto + '拆' 关键词应当走 subdivide,在 leaf 下长出新中间分支"


async def test_promoted_phrase_creates_intermediate_branch_before_children(client: AsyncClient) -> None:
    payload = await _create(client)
    session_id = payload["session_id"]
    leaf = next(n for n in payload["initial_nodes"] if n["depth"] == 1)

    done = await _collect_done(
        client,
        session_id,
        {
            "message": "请把「区域加盟模式」展开成一个真正的学习分支。",
            "current_node_id": leaf["id"],
            "intent": "subdivide",
            "mode": "Lite",
        },
    )

    branch = next(n for n in done["nodes"] if n["title"] == "区域加盟模式")
    assert branch["parent_id"] == leaf["id"]
    branch_children = [n for n in done["nodes"] if n["parent_id"] == branch["id"]]
    assert branch_children, "提升出的中间节点下面必须继续长出子分支"
    assert done["current_node_id"] == branch["id"]
    assert branch["id"] in done["created_node_ids"]


async def test_promoted_phrase_accepts_shorter_and_curly_quote_variants(client: AsyncClient) -> None:
    payload = await _create(client)
    session_id = payload["session_id"]
    leaf = next(n for n in payload["initial_nodes"] if n["depth"] == 1)

    done = await _collect_done(
        client,
        session_id,
        {
            "message": "把“高性价比”展开成一个真正的学习分支。",
            "current_node_id": leaf["id"],
            "intent": "subdivide",
            "mode": "Lite",
        },
    )

    branch = next(n for n in done["nodes"] if n["title"] == "高性价比")
    assert branch["parent_id"] == leaf["id"]
    assert [n for n in done["nodes"] if n["parent_id"] == branch["id"]]


async def test_explicit_promoted_title_creates_intermediate_branch(client: AsyncClient) -> None:
    payload = await _create(client)
    session_id = payload["session_id"]
    leaf = next(n for n in payload["initial_nodes"] if n["depth"] == 1)

    done = await _collect_done(
        client,
        session_id,
        {
            "message": "展开成一个真正的学习分支",
            "current_node_id": leaf["id"],
            "intent": "subdivide",
            "mode": "Lite",
            "promoted_title": "高性价比",
        },
    )

    branch = next(n for n in done["nodes"] if n["title"] == "高性价比")
    assert branch["parent_id"] == leaf["id"]
    assert [n for n in done["nodes"] if n["parent_id"] == branch["id"]]


async def test_promoted_title_works_when_ai_returns_children(
    session_factory: async_sessionmaker,
) -> None:
    class FakeAI:
        async def initial_map(
            self,
            field: str,
            current_problem: str,
            learning_background: str = "",
            mode: str = "Lite",
            **_kwargs,
        ):
            return ([{"title": "成本结构优化", "summary": "控制成本", "relevance_score": 3}], "intro")

        async def chat(self, messages: list[dict[str, str]], *, temperature: float = 0.35, **_kwargs) -> dict:
            return {
                "reply": "已围绕高性价比拆开。",
                "summary": "高性价比的实现路径。",
                "children": [
                    {"title": "低价心智", "summary": "用价格降低尝试门槛", "importance": 2, "relevance_score": 3, "difficulty": 1},
                    {"title": "价值感包装", "summary": "让用户觉得占便宜", "importance": 2, "relevance_score": 2, "difficulty": 2},
                ],
                "next_actions": [],
            }

    service = KnowledgeMapService(FakeAI())  # type: ignore[arg-type]
    async with session_factory() as db:
        created = await service.create_session(
            db,
            {"field": "连锁零售经营分析", "current_problem": "理解成本和定位"},
        )
        parent = next(n for n in created["initial_nodes"] if n.depth == 1)
        _reply, done = await service.receive_message(
            db,
            created["session_id"],
            {
                "message": "请把高性价比展开成一个真正的学习分支。",
                "current_node_id": parent.id,
                "intent": "subdivide",
                "mode": "Lite",
                "promoted_title": "高性价比",
            },
        )

    branch = next(n for n in done["nodes"] if n.title == "高性价比")
    assert branch.parent_id == parent.id
    branch_children = [n for n in done["nodes"] if n.parent_id == branch.id]
    assert {n.title for n in branch_children} == {"低价心智", "价值感包装"}
    assert done["current_node_id"] == branch.id


async def test_assistant_message_carries_next_actions(client: AsyncClient) -> None:
    payload = await _create(client)
    session_id = payload["session_id"]
    leaf = next(n for n in payload["initial_nodes"] if n["depth"] == 1)

    done = await _collect_done(
        client,
        session_id,
        {"message": "拆开", "current_node_id": leaf["id"], "intent": "subdivide", "mode": "Lite"},
    )
    last_assistant = [m for m in done["messages"] if m["role"] == "assistant"][-1]
    assert last_assistant["next_actions"], "subdivide 后助手消息必须带 next_actions"
    for action in last_assistant["next_actions"]:
        assert action["kind"] in {"explain", "subdivide"}
        assert action["label"]
