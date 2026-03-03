from src.review.rules import approximate_chinese_char_count, detect_banned_terms, evaluate_deterministic_rules
from src.workflow.routing import review_route


def test_approximate_chinese_char_count():
    text = "这是一个测试文本123"
    assert approximate_chinese_char_count(text) >= 8


def test_detect_banned_terms():
    findings = detect_banned_terms("这是全网第一，效果100%稳定")
    patterns = {item["pattern"] for item in findings}
    assert "全网第一" in patterns
    assert "100%" in patterns


def test_review_route_logic():
    assert review_route({"review_passed": True, "retry_count": 0, "max_retries": 3}) == "to_browser"
    assert review_route({"review_passed": False, "retry_count": 1, "max_retries": 3}) == "to_rewrite"
    assert review_route({"review_passed": False, "retry_count": 3, "max_retries": 3}) == "to_notify"


def test_xhs_length_rule():
    result = evaluate_deterministic_rules(platform="xhs", title="测试标题", content="太短了😀😀😀😀😀😀")
    assert result["passed"] is False
    assert any("200-500" in issue for issue in result["issues"])
