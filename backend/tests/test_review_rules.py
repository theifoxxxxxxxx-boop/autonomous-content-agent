from __future__ import annotations

from src.review.rules import approximate_chinese_char_count, detect_banned_terms, evaluate_deterministic_rules
from src.workflow.routing import review_route


def test_approximate_chinese_char_count():
    text = "这是一个测试文本123"
    assert approximate_chinese_char_count(text) >= 8


def test_detect_banned_terms_only_illegal_superlatives():
    findings = detect_banned_terms("这款是全网第一，国家级标准，顶级体验")
    patterns = {item["pattern"] for item in findings}
    assert any("第一" in pattern for pattern in patterns)
    assert any("顶级" in pattern for pattern in patterns)
    assert any("国家级" in pattern for pattern in patterns)


def test_allow_neutral_most_phrases():
    findings = detect_banned_terms("最近更新，最后一版，最初方案，最佳实践")
    assert findings == []


def test_review_route_logic():
    assert review_route({"review_passed": True, "retry_count": 0, "max_retries": 3}) == "to_browser"
    assert review_route({"review_passed": False, "retry_count": 1, "max_retries": 3}) == "to_rewrite"
    assert review_route({"review_passed": False, "retry_count": 3, "max_retries": 3}) == "to_notify"


def test_xhs_length_rule():
    result = evaluate_deterministic_rules(
        platform="xhs",
        title="测试标题",
        content="太短了😀😀😀",
    )
    assert result["passed"] is False
    assert any("200-500" in issue for issue in result["issues"])


def test_xhs_emoji_rule_min_3():
    result = evaluate_deterministic_rules(
        platform="xhs",
        title="这是一个满足长度和结构的标题123",
        content=(
            "这是第一段内容，长度足够用于测试规则系统。😀\n"
            "这是第二段内容，也包含充分信息用于审核。😀\n"
            "这是第三段内容，保持可读性但只有两个 emoji。"
        ),
    )
    assert any("至少 3 个" in issue for issue in result["issues"])


def test_title_hook_only_advisory_not_blocking():
    result = evaluate_deterministic_rules(
        platform="xhs",
        title="普通标题",
        content=(
            "这是一段满足字数的示例内容" * 20
            + "\n这是第二段示例😀"
            + "\n这是第三段示例😀😀"
        ),
    )
    assert any("标题吸引力可优化" in item for item in result["advisory_suggestions"])
    assert all("标题吸引力" not in item for item in result["issues"])
