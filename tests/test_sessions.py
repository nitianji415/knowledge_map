"""会话核心闭环:创建 → 拿树 → 拿消息 → 列表 → 流式回复。"""

from __future__ import annotations

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db.models import LearningSession
from app.services.knowledge import KnowledgeMapService


async def _create_session(client: AsyncClient) -> dict:
    response = await client.post(
        "/api/sessions",
        json={"field": "连锁零售经营分析", "current_problem": "和老板讲清楚单店模型"},
    )
    assert response.status_code == 201
    return response.json()


async def test_create_session_returns_initial_tree(client: AsyncClient) -> None:
    payload = await _create_session(client)
    assert payload["session_id"]
    assert payload["current_node_id"]
    assert len(payload["initial_nodes"]) >= 2  # 至少 root + 一级节点
    assert any(n["depth"] == 0 for n in payload["initial_nodes"])
    assert any(n["depth"] == 1 for n in payload["initial_nodes"])
    for node in payload["initial_nodes"]:
        assert node["collapsed"] is False


async def test_background_questions_endpoint_returns_button_options(client: AsyncClient) -> None:
    response = await client.post(
        "/api/sessions/background-questions",
        json={
            "field": "连锁零售经营分析",
            "current_problem": "和老板讲清楚单店模型",
            "mode": "Lite",
        },
    )

    assert response.status_code == 200
    questions = response.json()["questions"]
    assert len(questions) >= 2
    assert all(len(q["options"]) >= 2 for q in questions)
    assert all(q["options"][0]["label"] for q in questions)


async def test_tree_and_messages_endpoints(client: AsyncClient) -> None:
    payload = await _create_session(client)
    session_id = payload["session_id"]

    tree = await client.get(f"/api/sessions/{session_id}/tree")
    assert tree.status_code == 200
    assert len(tree.json()["nodes"]) == len(payload["initial_nodes"])

    messages = await client.get(f"/api/sessions/{session_id}/messages")
    assert messages.status_code == 200
    assert any(m["role"] == "assistant" for m in messages.json()["messages"])


async def test_list_sessions_filtering(client: AsyncClient) -> None:
    await _create_session(client)
    response = await client.get("/api/sessions", params={"search": "零售"})
    assert response.status_code == 200
    sessions = response.json()["sessions"]
    assert sessions
    assert any("零售" in s["field"] for s in sessions)


async def test_settings_update_takes_effect_immediately(client: AsyncClient) -> None:
    """PATCH /api/settings 后,新值应立刻进内存缓存(_resolved 实时读它),无需重启。

    回归此前 bug:update_settings 只 flush 不 commit,reload 用独立 session 读到旧值。
    """
    from app.services.settings_store import get_layered_settings

    resp = await client.patch("/api/settings", json={"updates": {"LLM_MODEL": "regression-model-xyz"}})
    assert resp.status_code == 200
    # AI 客户端 _resolved() 读的就是这个内存缓存——存完应立即是新值
    assert get_layered_settings().get("LLM_MODEL").value == "regression-model-xyz"


async def test_message_stream_writes_done_event(client: AsyncClient) -> None:
    payload = await _create_session(client)
    session_id = payload["session_id"]
    node_id = payload["current_node_id"]

    async with client.stream(
        "POST",
        f"/api/sessions/{session_id}/messages/stream",
        json={"message": "继续深入", "current_node_id": node_id, "mode": "Lite"},
    ) as response:
        assert response.status_code == 200
        chunks: list[str] = []
        async for chunk in response.aiter_text():
            chunks.append(chunk)
    body = "".join(chunks)
    assert "event: token" in body
    assert "event: done" in body


async def test_learning_background_is_passed_to_initial_map(
    session_factory: async_sessionmaker,
) -> None:
    class FakeAI:
        def __init__(self) -> None:
            self.learning_background = ""

        async def initial_map(
            self,
            field: str,
            current_problem: str,
            learning_background: str = "",
            mode: str = "Lite",
            **_kwargs,
        ):
            self.learning_background = learning_background
            return ([{"title": "基础概念", "summary": "先补基础", "relevance_score": 3}], "intro")

    fake_ai = FakeAI()
    service = KnowledgeMapService(fake_ai)  # type: ignore[arg-type]
    async with session_factory() as db:
        payload = await service.create_session(
            db,
            {
                "field": "财报分析",
                "current_problem": "看懂资产负债表",
                "learning_background": "我是零基础,不懂财务术语",
                "mode": "Lite",
            },
        )
        session = await db.get(LearningSession, payload["session_id"])

    assert fake_ai.learning_background == "我是零基础,不懂财务术语"
    assert session is not None
    assert session.learning_background == "我是零基础,不懂财务术语"
