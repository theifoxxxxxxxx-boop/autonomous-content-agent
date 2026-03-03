from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class BannedRule:
    pattern: str
    suggestion: str


BANNED_RULES: list[BannedRule] = [
    BannedRule(pattern=r"全网第一", suggestion="全网热议"),
    BannedRule(pattern=r"第一", suggestion="前列"),
    BannedRule(pattern=r"绝对", suggestion="相对更"),
    BannedRule(pattern=r"100%", suggestion="高概率"),
    BannedRule(pattern=r"最", suggestion="更"),
    BannedRule(pattern=r"顶级", suggestion="高品质"),
    BannedRule(pattern=r"无敌", suggestion="表现很强"),
    BannedRule(pattern=r"唯一", suggestion="少见"),
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

    zh_count = approximate_chinese_char_count(content)
    if platform == "xhs" and not (200 <= zh_count <= 500):
        issues.append(f"小红书正文中文字符数需在 200-500，当前约 {zh_count}。")
        rewrite_instructions.append("将正文改写到 200-500 字，保持分段和信息密度。")

    emoji_count = count_emoji(content)
    if emoji_count < 6:
        issues.append(f"Emoji 数量不足（当前 {emoji_count}，要求至少 6）。")
        rewrite_instructions.append("在每段加入自然的 Emoji 表达情绪，至少 6 个。")

    banned = detect_banned_terms(f"{title}\n{content}")
    if banned:
        for item in banned:
            issues.append(f"命中疑似极限词/违禁词：{item['pattern']}")
            replacement_suggestions.append(item)
        rewrite_instructions.append("替换所有极限词，改为客观可验证表达。")

    title_len = len((title or "").strip())
    has_hook = bool(re.search(r"[?!？！]|[0-9]", title or ""))
    if title_len < 12 or not has_hook:
        issues.append("标题吸引力不足（建议更强钩子、数字或反差）。")
        rewrite_instructions.append("重写标题：加入数字/结果导向/反差，但不夸大。")

    if len((content or "").strip().splitlines()) < 3:
        issues.append("正文结构不够可读（分段不足）。")
        rewrite_instructions.append("正文拆分为 3-6 段，每段一句核心观点。")

    passed = len(issues) == 0
    return {
        "passed": passed,
        "issues": issues,
        "rewrite_instructions": rewrite_instructions,
        "replacement_suggestions": replacement_suggestions,
        "zh_char_count": zh_count,
        "emoji_count": emoji_count,
    }
