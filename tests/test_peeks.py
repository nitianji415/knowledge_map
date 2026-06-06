"""Peek 速览解释:划词解释不进入主对话流,只挂在原消息锚点上。"""

from __future__ import annotations

import json

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.services.knowledge import KnowledgeMapService


def _merged_prompt(messages: list[dict[str, str]]) -> dict:
    """优化A 之后 prompt 被拆成 [固定 system 块] + [每轮 user 块];
    测试里把两块合回完整 dict,沿用原有断言。"""
    merged: dict = {}
    for msg in messages:
        content = msg.get("content", "")
        brace = content.find("{")
        if brace == -1:
            continue
        try:
            payload = json.loads(content[brace:])
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(payload, dict):
            merged.update(payload)
    return merged


async def _create_session(client: AsyncClient) -> dict:
    response = await client.post(
        "/api/sessions",
        json={"field": "财报分析", "current_problem": "看懂资产负债表"},
    )
    assert response.status_code == 201
    return response.json()


async def test_create_peek_persists_on_message_without_new_message(client: AsyncClient) -> None:
    payload = await _create_session(client)
    intro = payload["messages"][-1]
    before = await client.get(f"/api/sessions/{payload['session_id']}/messages")
    before_count = len(before.json()["messages"])

    response = await client.post(
        f"/api/messages/{intro['id']}/peeks",
        json={"start": 0, "end": 4, "text": intro["content"][:4], "mode": "Lite"},
    )

    assert response.status_code == 200
    saved = response.json()
    assert saved["peeks"]
    assert saved["peeks"][0]["answer"]

    after = await client.get(f"/api/sessions/{payload['session_id']}/messages")
    assert len(after.json()["messages"]) == before_count


async def test_peek_followup_stays_inside_peek(client: AsyncClient) -> None:
    payload = await _create_session(client)
    intro = payload["messages"][-1]
    peek_response = await client.post(
        f"/api/messages/{intro['id']}/peeks",
        json={"start": 0, "end": 4, "text": intro["content"][:4], "mode": "Lite"},
    )
    peek = peek_response.json()["peeks"][0]

    response = await client.post(
        f"/api/messages/{intro['id']}/peeks/{peek['id']}/followups",
        json={"question": "这里还有一个词不懂", "mode": "Lite"},
    )

    assert response.status_code == 200
    saved = response.json()
    saved_peek = next(item for item in saved["peeks"] if item["id"] == peek["id"])
    assert saved_peek["followups"]
    assert saved_peek["followups"][0]["answer"]


async def test_nested_peek_anchors_to_parent_answer(client: AsyncClient) -> None:
    """嵌套速览:在父 peek 的 answer 上划词应该:
    - 新生成一个 peek,parent_peek_id 指向父
    - start/end 校验用的是父 answer 长度,不是消息正文长度
    """
    payload = await _create_session(client)
    intro = payload["messages"][-1]
    # 先建一个根 peek
    root = (await client.post(
        f"/api/messages/{intro['id']}/peeks",
        json={"start": 0, "end": 4, "text": intro["content"][:4], "mode": "Lite"},
    )).json()
    root_peek = root["peeks"][0]
    parent_answer = root_peek["answer"]
    assert len(parent_answer) >= 6, "父 peek 必须有 answer 才能在上面再划词"

    # 在父 peek 的 answer 里划一段
    response = await client.post(
        f"/api/messages/{intro['id']}/peeks",
        json={
            "start": 0,
            "end": min(4, len(parent_answer)),
            "text": parent_answer[:4],
            "mode": "Lite",
            "parent_peek_id": root_peek["id"],
        },
    )
    assert response.status_code == 200
    saved = response.json()
    nested = next(
        p for p in saved["peeks"] if p.get("parent_peek_id") == root_peek["id"]
    )
    assert nested["text"] == parent_answer[:4]
    assert nested["answer"], "嵌套 peek 应该有自己的 answer"
    # 根 peek 也仍然在(不被嵌套覆盖)
    assert any(p["id"] == root_peek["id"] for p in saved["peeks"])


async def test_nested_peek_rejects_out_of_range(client: AsyncClient) -> None:
    """嵌套 peek 的 start/end 超出父 answer 长度时应该 400。"""
    payload = await _create_session(client)
    intro = payload["messages"][-1]
    root = (await client.post(
        f"/api/messages/{intro['id']}/peeks",
        json={"start": 0, "end": 4, "text": intro["content"][:4], "mode": "Lite"},
    )).json()
    root_peek = root["peeks"][0]
    parent_answer_len = len(root_peek["answer"])

    response = await client.post(
        f"/api/messages/{intro['id']}/peeks",
        json={
            "start": parent_answer_len + 100,
            "end": parent_answer_len + 110,
            "text": "out of range",
            "mode": "Lite",
            "parent_peek_id": root_peek["id"],
        },
    )
    assert response.status_code == 400


async def test_nested_peek_unknown_parent_returns_404(client: AsyncClient) -> None:
    payload = await _create_session(client)
    intro = payload["messages"][-1]
    response = await client.post(
        f"/api/messages/{intro['id']}/peeks",
        json={
            "start": 0,
            "end": 4,
            "text": "abcd",
            "mode": "Lite",
            "parent_peek_id": "peek_does_not_exist",
        },
    )
    assert response.status_code == 404


async def test_nested_peek_can_anchor_on_followup_answer(client: AsyncClient) -> None:
    payload = await _create_session(client)
    intro = payload["messages"][-1]
    root = (await client.post(
        f"/api/messages/{intro['id']}/peeks",
        json={"start": 0, "end": 4, "text": intro["content"][:4], "mode": "Lite"},
    )).json()
    root_peek = root["peeks"][0]

    followed = await client.post(
        f"/api/messages/{intro['id']}/peeks/{root_peek['id']}/followups",
        json={"question": "这句话再解释一下", "mode": "Lite"},
    )
    saved_root = next(p for p in followed.json()["peeks"] if p["id"] == root_peek["id"])
    followup = saved_root["followups"][0]
    selected = followup["answer"][:3]

    response = await client.post(
        f"/api/messages/{intro['id']}/peeks",
        json={
            "start": 0,
            "end": len(selected),
            "text": selected,
            "mode": "Lite",
            "parent_peek_id": root_peek["id"],
            "source_kind": "followup",
            "source_followup_id": followup["id"],
        },
    )

    assert response.status_code == 200
    child = next(
        p
        for p in response.json()["peeks"]
        if p.get("parent_peek_id") == root_peek["id"]
        and p.get("source_kind") == "followup"
    )
    assert child["source_followup_id"] == followup["id"]
    assert child["text"] == selected


async def test_peek_followup_prompt_prioritizes_latest_question(
    session_factory: async_sessionmaker,
) -> None:
    class FakeAI:
        def __init__(self) -> None:
            self.prompt: dict | None = None

        async def chat(
            self,
            messages: list[dict[str, str]],
            *,
            temperature: float = 0.35,
            enable_web_search: bool = False,
            **_kwargs,
        ) -> dict:
            self.prompt = _merged_prompt(messages)
            return {"answer": "口味偏主观,要看甜度、料感和门店稳定性。"}

    fake_ai = FakeAI()
    service = KnowledgeMapService(fake_ai)  # type: ignore[arg-type]
    async with session_factory() as db:
        payload = await service.create_session(
            db,
            {"field": "连锁零售经营分析", "current_problem": "理解差异化定位"},
        )
        message = payload["messages"][-1]
        message.content = "差异化定位靠半杯都是料做差异化。"
        message.peeks = [
            {
                "id": "peek_test",
                "start": 0,
                "end": 5,
                "text": "差异化定位",
                "answer": "差异化定位是一个零售品牌。",
                "status": "answered",
                "promoted_node_id": None,
                "followups": [],
            }
        ]
        await db.commit()

        saved = await service.add_peek_followup(
            db,
            message.id,
            "peek_test",
            {"question": "好喝吗", "mode": "Lite"},
        )

    assert saved.peeks[0]["followups"][0]["answer"].startswith("口味偏主观")
    assert fake_ai.prompt is not None
    assert fake_ai.prompt["task"] == "peek_followup"
    assert fake_ai.prompt["followup_question"] == "好喝吗"
    assert fake_ai.prompt["selected_text"] == "差异化定位"


async def test_peek_followup_new_term_does_not_default_to_anchor(
    session_factory: async_sessionmaker,
) -> None:
    class FakeAI:
        def __init__(self) -> None:
            self.prompt: dict | None = None

        async def chat(
            self,
            messages: list[dict[str, str]],
            *,
            temperature: float = 0.35,
            enable_web_search: bool = False,
            **_kwargs,
        ) -> dict:
            self.prompt = _merged_prompt(messages)
            return {"answer": "神经递质是神经元之间传递信息的化学信号分子。"}

    fake_ai = FakeAI()
    service = KnowledgeMapService(fake_ai)  # type: ignore[arg-type]
    async with session_factory() as db:
        payload = await service.create_session(
            db,
            {"field": "神经科学", "current_problem": "理解神经网络"},
        )
        message = payload["messages"][-1]
        message.content = "突触通过释放神经递质传递信号。"
        message.peeks = [
            {
                "id": "peek_synapse",
                "start": 0,
                "end": 2,
                "text": "突触",
                "answer": "突触是神经元之间的连接点。",
                "status": "answered",
                "promoted_node_id": None,
                "followups": [
                    {
                        "id": "peekq_1",
                        "question": "那它是细胞吗?",
                        "answer": "不是,突触不是细胞本身。",
                    }
                ],
            }
        ]
        await db.commit()

        saved = await service.add_peek_followup(
            db,
            message.id,
            "peek_synapse",
            {"question": "神经递质是啥", "mode": "Lite"},
        )

    assert saved.peeks[0]["followups"][-1]["answer"].startswith("神经递质")
    assert fake_ai.prompt is not None
    assert fake_ai.prompt["selected_text"] == "突触"
    assert fake_ai.prompt["followup_question"] == "神经递质是啥"
    assert fake_ai.prompt["followup_subject_hint"]["kind"] == "new_explicit_subject"
    assert fake_ai.prompt["followup_subject_hint"]["subject"] == "神经递质"


async def test_peek_zen_prompt_allows_fuller_answer(
    session_factory: async_sessionmaker,
) -> None:
    class FakeAI:
        def __init__(self) -> None:
            self.prompt: dict | None = None

        async def chat(
            self,
            messages: list[dict[str, str]],
            *,
            temperature: float = 0.35,
            enable_web_search: bool = False,
            **_kwargs,
        ) -> dict:
            self.prompt = _merged_prompt(messages)
            return {"answer": "突触是神经元之间传递信号的连接结构。"}

    fake_ai = FakeAI()
    service = KnowledgeMapService(fake_ai)  # type: ignore[arg-type]
    async with session_factory() as db:
        payload = await service.create_session(
            db,
            {"field": "神经科学", "current_problem": "理解神经网络"},
        )
        message = payload["messages"][-1]
        message.content = "突触通过释放神经递质传递信号。"
        await db.commit()

        await service.create_message_peek(
            db,
            message.id,
            {"start": 0, "end": 2, "text": "突触", "mode": "Zen"},
        )

    assert fake_ai.prompt is not None
    instructions = "\n".join(fake_ai.prompt["instructions"])
    assert "控制在 760 字以内" in instructions
    assert "Zen 模式要明显更充分" in instructions


async def test_peek_followup_process_question_does_not_redefine_anchor(
    session_factory: async_sessionmaker,
) -> None:
    class FakeAI:
        def __init__(self) -> None:
            self.prompt: dict | None = None

        async def chat(
            self,
            messages: list[dict[str, str]],
            *,
            temperature: float = 0.35,
            enable_web_search: bool = False,
            **_kwargs,
        ) -> dict:
            self.prompt = _merged_prompt(messages)
            return {"answer": "信息通常先在神经元内形成电信号,到达突触后转成化学信号传给下一个神经元。"}

    fake_ai = FakeAI()
    service = KnowledgeMapService(fake_ai)  # type: ignore[arg-type]
    async with session_factory() as db:
        payload = await service.create_session(
            db,
            {"field": "神经科学", "current_problem": "理解神经网络"},
        )
        message = payload["messages"][-1]
        message.content = "神经网络通过神经元和突触处理信息。"
        message.peeks = [
            {
                "id": "peek_network",
                "start": 0,
                "end": 4,
                "text": "神经网络",
                "answer": "神经网络由大量神经元和突触组成。",
                "status": "answered",
                "promoted_node_id": None,
                "followups": [],
            }
        ]
        await db.commit()

        saved = await service.add_peek_followup(
            db,
            message.id,
            "peek_network",
            {"question": "信息怎么传递的", "mode": "Lite"},
        )

    assert saved.peeks[0]["followups"][-1]["answer"].startswith("信息通常先")
    assert fake_ai.prompt is not None
    assert fake_ai.prompt["selected_text"] == "神经网络"
    assert fake_ai.prompt["followup_subject_hint"]["kind"] == "explicit_process_question"
    instructions = "\n".join(fake_ai.prompt["instructions"])
    assert "回答第一句禁止使用" in instructions
    assert "不要先重讲 selected_text 的定义" in instructions
