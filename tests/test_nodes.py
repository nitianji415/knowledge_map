"""节点更新 + 折叠状态持久化。"""

from __future__ import annotations

from httpx import AsyncClient

from app.services.knowledge import KnowledgeMapService


async def _bootstrap(client: AsyncClient) -> tuple[str, str]:
    response = await client.post(
        "/api/sessions",
        json={"field": "前端工程", "current_problem": "理清打包链路"},
    )
    payload = response.json()
    return payload["session_id"], payload["current_node_id"]


async def test_collapse_persists_on_server(client: AsyncClient) -> None:
    session_id, _ = await _bootstrap(client)
    tree = (await client.get(f"/api/sessions/{session_id}/tree")).json()
    main_node = next(n for n in tree["nodes"] if n["depth"] == 1)
    assert main_node["collapsed"] is False

    patch = await client.patch(
        f"/api/nodes/{main_node['id']}",
        json={"collapsed": True},
    )
    assert patch.status_code == 200
    assert patch.json()["node"]["collapsed"] is True

    refetched = (await client.get(f"/api/sessions/{session_id}/tree")).json()
    refreshed = next(n for n in refetched["nodes"] if n["id"] == main_node["id"])
    assert refreshed["collapsed"] is True


async def test_update_node_validates_metric_range(client: AsyncClient) -> None:
    session_id, _ = await _bootstrap(client)
    tree = (await client.get(f"/api/sessions/{session_id}/tree")).json()
    node_id = tree["nodes"][0]["id"]

    bad = await client.patch(f"/api/nodes/{node_id}", json={"importance": 5})
    assert bad.status_code == 422  # Pydantic 拒掉 >3 的值


async def test_update_unknown_node_returns_404(client: AsyncClient) -> None:
    response = await client.patch("/api/nodes/node_does_not_exist", json={"collapsed": True})
    assert response.status_code == 404


async def test_first_principles_edge_explanation_fields_are_returned(
    session_factory,
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
            return ([{"title": "组织力", "summary": "组织协作能力", "relevance_score": 3}], "intro")

        async def expand_first_principles(
            self,
            field: str,
            current_problem: str,
            node_title: str,
            node_summary: str,
            node_path: str,
            current_depth: int,
            max_depth: int,
        ):
            return {
                "is_fundamental": False,
                "children": [
                    {
                        "title": "组织行为学",
                        "summary": "解释组织中个体与群体行为",
                        "relation": "解释组织行为的底层变量",
                        "why": "组织力表现为多人协作结果,第一性原理要先拆到能解释行为、激励与群体互动的学科。",
                        "is_fundamental": False,
                    }
                ],
            }

    service = KnowledgeMapService(FakeAI())  # type: ignore[arg-type]
    async with session_factory() as db:
        result = await service.create_session(
            db,
            {"field": "组织管理", "current_problem": "理解组织力", "mode": "Lite"},
        )
        root_child_id = next(n.id for n in result["initial_nodes"] if n.depth == 1)
        events = []
        async for event_type, data in service.first_principles_stream(
            db, result["session_id"], root_child_id, max_depth=1
        ):
            events.append((event_type, data))

    layer = next(data for event_type, data in events if event_type == "fp_layer" and data["children"])
    child = layer["children"][0]
    assert child["fp_relation"] == "解释组织行为的底层变量"
    assert "第一性原理" in child["fp_reason"]
