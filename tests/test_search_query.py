"""网页搜索 query 改写上下文。"""

from __future__ import annotations

import json

from app.services.ai import extract_search_context, fallback_refined_search_query


def test_explain_search_context_keeps_project_background_for_generic_node() -> None:
    messages = [
        {
            "role": "user",
            "content": json.dumps(
                {
                    "task": "explain",
                    "field": "新茶饮品牌经营分析",
                    "current_problem": "和老板讲清楚单店模型",
                    "current_node": {
                        "title": "行业全景与竞争格局",
                        "summary": "理解行业规模、主要玩家和竞争态势",
                        "path": "连锁零售经营分析 / 行业全景与竞争格局",
                    },
                    "user_message": "请围绕「行业全景与竞争格局」开始讲解。",
                },
                ensure_ascii=False,
            ),
        }
    ]

    context = extract_search_context(messages)

    assert context["task"] == "explain"
    assert "新茶饮品牌经营分析" in context["seed"]
    assert "和老板讲清楚单店模型" in context["seed"]
    assert "行业全景与竞争格局" in context["seed"]
    assert context["node_title"] == "行业全景与竞争格局"


def test_fallback_query_turns_generic_industry_node_into_searchable_terms() -> None:
    refined = fallback_refined_search_query(
        "领域:新茶饮品牌经营分析 当前节点:行业全景与竞争格局",
        "explain",
        {
            "field": "新茶饮品牌经营分析",
            "node_title": "行业全景与竞争格局",
        },
    )

    assert "新茶饮品牌经营分析" in refined
    assert "市场规模" in refined
    assert "竞争格局" in refined
    assert "2025" in refined
