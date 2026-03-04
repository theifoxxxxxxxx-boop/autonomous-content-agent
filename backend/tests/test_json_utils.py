from __future__ import annotations

import pytest

from src.utils.json_utils import extract_json_object


def test_extract_json_from_markdown_fence():
    text = """
Some explanation text.
```json
{
  "overview": "ok",
  "features": ["a", "b",],
  "tone": "neutral"
}
```
"""
    parsed = extract_json_object(text)
    assert parsed["overview"] == "ok"
    assert parsed["features"] == ["a", "b"]


def test_repair_missing_comma_between_pairs():
    text = '{"overview":"value" "features":["x"], "tone":"calm"}'
    parsed = extract_json_object(text)
    assert parsed["overview"] == "value"
    assert parsed["features"] == ["x"]


def test_escape_unescaped_inner_quotes():
    text = '{"overview":"He said "good" and left","features":["x"],"tone":"calm"}'
    parsed = extract_json_object(text)
    assert 'He said "good" and left' == parsed["overview"]


def test_parse_error_contains_context():
    with pytest.raises(ValueError) as exc_info:
        extract_json_object('{"overview": "x", "features": [1, 2, }')
    assert "Unable to parse JSON object" in str(exc_info.value)
    assert "raw=" in str(exc_info.value)


def test_repair_unescaped_quotes_with_pipe_and_name():
    text = (
        '{"passed": false, "issues": ["标题包含竖线符号"|"", "正文包含第三方平台导流内容（提及具体用户故事）"], '
        '"rewrite_instructions": ["将标题中的竖线符号"|\"替换为其他中文标点，例如破折号、冒号或间隔号。", '
        '"删除或模糊化正文中关于具体用户"阿杰"在上海外滩拍摄的故事，改为概括性描述用户受益场景。"]}'
    )
    parsed = extract_json_object(text)
    assert parsed["passed"] is False
    assert isinstance(parsed["issues"], list)
    assert isinstance(parsed["rewrite_instructions"], list)
