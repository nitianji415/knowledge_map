"""LLM 不可用时的本地 fallback 主题模板(领域无关的通用结构)。"""

from __future__ import annotations

from typing import Any


def topic(
    title: str,
    relevance: int,
    summary: str,
    importance: int = 2,
    difficulty: int = 2,
    relevance_score: int | None = None,
) -> dict[str, Any]:
    relevance_score = relevance_score if relevance_score is not None else (3 if relevance else 1)
    return {
        "title": title,
        "relevance": 1 if relevance_score >= 3 else 0,
        "importance": importance,
        "relevance_score": relevance_score,
        "difficulty": difficulty,
        "summary": summary,
    }


def default_topics(field: str) -> list[dict[str, Any]]:
    """LLM 不可用时的兜底模板:一套领域无关的通用学习地图,带二级 children
    让结构与 AI 生成路径保持一致。具体领域内容由 LLM 在线生成。"""

    def child(title: str, summary: str, *, importance: int = 2, difficulty: int = 2, score: int = 2) -> dict[str, Any]:
        return topic(title, 1 if score >= 3 else 0, summary, importance, difficulty, score)

    return [
        {
            **topic("领域全景", 1, "先建立这个领域解决什么问题的整体框架。", 3, 1, 3),
            "children": [
                child("它解决什么问题", "用一句话说清这个领域的核心命题。", score=3),
                child("关键概念地图", "把这个领域绕不开的几个核心术语串起来。"),
            ],
        },
        {
            **topic("关键角色与流程", 1, "识别谁参与、如何协作、价值如何流动。", 2, 2, 2),
            "children": [
                child("主要角色", "梳理这条链路上的人和组织。"),
                child("价值流动", "看价值如何在角色之间传递。"),
            ],
        },
        {
            **topic("核心指标", 1, "找到判断好坏、发现问题和推动改进的指标。", 3, 2, 3),
            "children": [
                child("健康度指标", "用来判断当前状态是否正常。", score=3),
                child("行动指标", "用来驱动具体改进动作。"),
            ],
        },
        {
            **topic("典型场景", 1, "用真实业务场景理解知识如何落地。", 2, 1, 2),
            "children": [
                child("常见判断题", "通过日常决策场景看知识怎么用。"),
                child("代表性案例", "找一个典型案例完整走一遍。"),
            ],
        },
        {
            **topic("常见误区", 0, "提前避开新手最容易混淆的概念。", 2, 1),
            "children": [
                child("初学者陷阱", "新手最容易踩的两三个坑。"),
                child("反直觉结论", "和直觉相反但实际成立的判断。"),
            ],
        },
        {
            **topic("行动清单", 1, "沉淀可以直接执行和复盘的步骤。", 3, 2, 2),
            "children": [
                child("可执行步骤", "把知识转化成具体可做的事。"),
                child("复盘要点", "做完之后要从哪些维度回看效果。"),
            ],
        },
    ]


def child_topics(title: str, mode: str = "Lite") -> list[tuple[str, str]]:
    """LLM 不可用时,围绕任意节点标题生成一组领域无关的通用子主题。"""
    topics: list[tuple[str, str]] = [
        (f"{title}的关键概念", "先把核心概念讲清楚。"),
        (f"{title}的判断指标", "用指标判断当前状态好坏。"),
        (f"{title}的实战问题", "把知识落到真实业务问题里。"),
    ]
    if mode == "Zen":
        return topics + [
            (f"{title}的底层原理", "进一步拆出背后的机制和约束。"),
            (f"{title}的案例复盘", "用真实案例检验理解是否能落地。"),
            (f"{title}的行动模板", "沉淀可复用的分析和执行步骤。"),
        ]
    if mode == "Medium":
        return topics + [(f"{title}的应用边界", "明确什么时候适用、什么时候不适用。")]
    return topics
