from __future__ import annotations

from .base import PlatformAdapter


XhsAdapter = PlatformAdapter(
    name="xhs",
    creator_center_url="https://creator.xiaohongshu.com/publish/publish",
    publish_entry_keywords=["\u53d1\u5e03\u7b14\u8bb0", "\u56fe\u6587", "\u53d1\u5e03\u56fe\u6587", "\u53d1\u5e03", "\u4e0a\u4f20"],
    upload_trigger_keywords=["\u4e0a\u4f20", "\u6dfb\u52a0\u56fe\u7247", "\u4e0a\u4f20\u56fe\u6587", "\u9009\u62e9\u56fe\u7247"],
    publish_button_keywords=["\u53d1\u5e03", "\u53d1\u5e03\u7b14\u8bb0"],
    fallback_publish_entry_selectors=[
        "a:has-text('\u53d1\u5e03\u7b14\u8bb0')",
        "button:has-text('\u53d1\u5e03\u7b14\u8bb0')",
        "button:has-text('\u56fe\u6587')",
        "div[role='tab']:has-text('\u56fe\u6587')",
        "a:has-text('\u53d1\u5e03')",
    ],
    title_selectors=[
        "input[placeholder*='\u586b\u5199\u6807\u9898']",
        "input[placeholder*='\u6dfb\u52a0\u6807\u9898']",
        "input[placeholder*='\u6807\u9898']",
        "textarea[placeholder*='\u6807\u9898']",
        "[contenteditable='true'][data-placeholder*='\u6807\u9898']",
        "[role='textbox'][data-placeholder*='\u6807\u9898']",
        "input.c-input_inner",
    ],
    content_selectors=[
        "div.ql-editor",
        "textarea[placeholder*='\u8f93\u5165\u6b63\u6587']",
        "textarea[placeholder*='\u8f93\u5165\u6b63\u6587\u63cf\u8ff0']",
        "textarea[placeholder*='\u6b63\u6587']",
        "textarea[placeholder*='\u5185\u5bb9']",
        "[contenteditable='true'][data-placeholder*='\u8f93\u5165\u6b63\u6587']",
        "[contenteditable='true'][data-placeholder*='\u8f93\u5165\u6b63\u6587\u63cf\u8ff0']",
        "[contenteditable='true'][data-placeholder*='\u6b63\u6587']",
        "[role='textbox'][data-placeholder*='\u6b63\u6587']",
        "div[contenteditable='true']",
    ],
    upload_input_selectors=[
        "input[accept*='image']",
        "input[accept*='png']",
        "input[accept*='jpg']",
        "input[accept*='jpeg']",
        "input[type='file']",
    ],
    publish_button_selectors=[
        "button:has-text('\u53d1\u5e03')",
        "button:has-text('\u53d1\u5e03\u7b14\u8bb0')",
    ],
)
