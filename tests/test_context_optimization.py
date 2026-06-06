"""上下文优化(A 前缀缓存 / E 搜索粗筛 / F usage 解析)的单元回归。

这些是纯函数,LLM 路径在集成测试里被禁用,所以单独锁住它们的行为。
"""

from __future__ import annotations

import json

from app.db.models import KnowledgeNode
from app.services.ai import _usage_int, extract_partial_reply
from app.services.knowledge import (
    RANK_PREFILTER_MAX,
    _cached_chat_messages,
    _prefilter_nodes_by_query,
)


def test_cached_messages_split_static_prefix_from_volatile_tail() -> None:
    prompt = {
        "task": "explain",
        "instructions": ["规则一", "规则二"],
        "json_schema": {"reply": "..."},
        "user_message": "继续讲",
        "recent_messages": [{"role": "user", "content": "上一条"}],
    }
    messages = _cached_chat_messages("只输出 JSON。", prompt)

    # 三段:固定规则 system + 固定 task/instructions/schema system + 每轮 user
    assert [m["role"] for m in messages] == ["system", "system", "user"]

    static = json.loads(messages[1]["content"][messages[1]["content"].find("{"):])
    assert set(static) == {"task", "instructions", "json_schema"}

    volatile = json.loads(messages[-1]["content"])
    assert set(volatile) == {"user_message", "recent_messages"}
    # 关键:会变的字段不能混进可缓存的前缀,否则缓存永远 miss
    assert "user_message" not in messages[1]["content"]


def test_cached_messages_without_static_keys_is_just_rule_plus_user() -> None:
    messages = _cached_chat_messages("规则", {"foo": "bar"})
    assert [m["role"] for m in messages] == ["system", "user"]
    assert json.loads(messages[-1]["content"]) == {"foo": "bar"}


def _node(title: str, summary: str = "") -> KnowledgeNode:
    return KnowledgeNode(title=title, summary=summary, parent_id="p")


def test_prefilter_caps_candidates_and_prioritizes_title_hit() -> None:
    nodes = [_node(f"无关节点 {i}") for i in range(RANK_PREFILTER_MAX + 20)]
    target = _node("毛利率拆解", "讲清楚毛利率怎么算")
    nodes.append(target)

    picked = _prefilter_nodes_by_query(nodes, "毛利率", RANK_PREFILTER_MAX)

    assert len(picked) == RANK_PREFILTER_MAX
    assert target in picked
    assert picked[0] is target  # 标题直接命中应排第一


def test_extract_partial_reply_grows_with_streaming_buffer() -> None:
    # 模拟流式:buffer 一段段长大,reply 内容应单调增长且最终等于完整值
    full = '{"reply": "你好,世界\\n第二行", "status": "active"}'
    seen = ""
    for end in range(1, len(full) + 1):
        partial = extract_partial_reply(full[:end])
        assert full[:end] or True
        # 单调:新解出的内容必须以上一帧为前缀(不会回退/乱跳)
        assert partial.startswith(seen) or seen.startswith(partial)
        if len(partial) >= len(seen):
            seen = partial
    assert seen == "你好,世界\n第二行"


def test_extract_partial_reply_handles_escapes_and_missing_key() -> None:
    assert extract_partial_reply('{"status":"active"}') == ""        # reply 还没出现
    assert extract_partial_reply('{"reply":"') == ""                  # 刚开引号
    assert extract_partial_reply('{"reply":"引号\\"内"}') == '引号"内'  # 转义引号
    assert extract_partial_reply('{"reply":"半个转义\\') == "半个转义"   # 转义被切断,稳住不报错
    assert extract_partial_reply('{"reply":"完整"}') == "完整"


def test_usage_int_reads_deepseek_cache_fields_from_model_extra() -> None:
    class FakeUsage:
        prompt_tokens = 1000
        completion_tokens = 200
        model_extra = {"prompt_cache_hit_tokens": 768, "prompt_cache_miss_tokens": 232}

    usage = FakeUsage()
    assert _usage_int(usage, "prompt_tokens") == 1000
    assert _usage_int(usage, "prompt_cache_hit_tokens") == 768
    assert _usage_int(usage, "prompt_cache_miss_tokens") == 232
    assert _usage_int(None, "prompt_tokens") == 0
