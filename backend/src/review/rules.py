from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class BannedRule:
    pattern: str
    suggestion: str


# Keep illegal ad claims, but avoid over-blocking neutral wording such as:
# 最近 / 最佳 / 最后 / 最初
BANNED_RULES: list[BannedRule] = [
    BannedRule(
        pattern=r"全网第一|行业第一|全国第一|第一品牌|销量第一|No\.?1|TOP1",
        suggestion="改为客观可验证描述，如“口碑较好”",
    ),
    BannedRule(
        pattern=r"第一(?!步|次|章|季|期|天|个|眼|时间|印象|反应|阶段|现场)",
        suggestion="改为“前列/领先梯队/表现优秀”",
    ),
    BannedRule(pattern=r"顶级", suggestion="改为“高品质/高标准”"),
    BannedRule(pattern=r"国家级", suggestion="改为“专业级/行业认可”"),
]


EMOJI_PATTERN = re.compile(
    "["
    "\U0001F300-\U0001F5FF"
    "\U0001F600-\U0001F64F"
    "\U0001F680-\U0001F6FF"
    "\U0001F900-\U0001F9FF"
    "\u2600-\u26FF"
    "\u2700-\u27BF"
    "]+",
    flags=re.UNICODE,
)

ZH_PATTERN = re.compile(r"[\u3400-\u4DBF\u4E00-\u9FFF]")


def approximate_chinese_char_count(text: str) -> int:
    cleaned = re.sub(r"\s+", "", text or "")
    zh_count = len(ZH_PATTERN.findall(cleaned))
    return zh_count if zh_count > 0 else len(cleaned)


def count_emoji(text: str) -> int:
    if not text:
        return 0
    return len(EMOJI_PATTERN.findall(text))


def detect_banned_terms(text: str) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    if not text:
        return findings
    for rule in BANNED_RULES:
        if re.search(rule.pattern, text, flags=re.IGNORECASE):
            findings.append({"pattern": rule.pattern, "suggestion": rule.suggestion})
    return findings


def evaluate_deterministic_rules(platform: str, title: str, content: str) -> dict[str, Any]:
    issues: list[str] = []
    rewrite_instructions: list[str] = []
    replacement_suggestions: list[dict[str, str]] = []
    advisory_suggestions: list[str] = []

    zh_count = approximate_chinese_char_count(content)
    if platform == "xhs" and not (200 <= zh_count <= 500):
        issues.append(f"小红书正文字数需在 200-500，当前约 {zh_count}。")
        rewrite_instructions.append("将正文改写到 200-500 字，保持分段和信息密度。")

    emoji_count = count_emoji(content)
    if platform == "xhs" and emoji_count < 3:
        issues.append(f"Emoji 数量不足（当前 {emoji_count}，至少 3 个）。")
        rewrite_instructions.append("补充自然 Emoji 增强氛围，至少 3 个。")

    banned = detect_banned_terms(f"{title}\n{content}")
    if banned:
        for item in banned:
            issues.append(f"命中疑似极限词/违禁词：{item['pattern']}")
            replacement_suggestions.append(item)
        rewrite_instructions.append("替换极限词，改为客观可验证表达。")

    title_len = len((title or "").strip())
    has_hook = bool(re.search(r"[?!？！]|[0-9]", title or ""))
    if title_len < 12 or not has_hook:
        advisory_suggestions.append("标题吸引力可优化：建议增加数字、结果导向或反差。")

    if len((content or "").strip().splitlines()) < 3:
        issues.append("正文结构可读性不足（分段不够）。")
        rewrite_instructions.append("正文拆分为 3-6 段，每段一句核心观点。")

    passed = len(issues) == 0
    return {
        "passed": passed,
        "issues": issues,
        "rewrite_instructions": rewrite_instructions,
        "replacement_suggestions": replacement_suggestions,
        "advisory_suggestions": advisory_suggestions,
        "zh_char_count": zh_count,
        "emoji_count": emoji_count,
    }
