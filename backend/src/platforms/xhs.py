from __future__ import annotations

from .base import PlatformAdapter


XhsAdapter = PlatformAdapter(
    name="xhs",
    creator_center_url="https://creator.xiaohongshu.com/publish/publish",
    publish_entry_keywords=["发布笔记", "图文", "发布图文", "发布", "上传"],
    upload_trigger_keywords=["上传", "添加图片", "上传图文", "选择图片"],
    publish_button_keywords=["发布", "发布笔记"],
    fallback_publish_entry_selectors=[
        "a:has-text('发布笔记')",
        "button:has-text('发布笔记')",
        "button:has-text('图文')",
        "div[role='tab']:has-text('图文')",
        "a:has-text('发布')",
    ],
    title_selectors=[
        "input[placeholder*='标题']",
        "textarea[placeholder*='标题']",
        "[contenteditable='true'][data-placeholder*='标题']",
    ],
    content_selectors=[
        "textarea[placeholder*='正文']",
        "textarea[placeholder*='内容']",
        "[contenteditable='true'][data-placeholder*='正文']",
    ],
    upload_input_selectors=[
        "input[accept*='image']",
        "input[accept*='png']",
        "input[accept*='jpg']",
        "input[accept*='jpeg']",
        "input[type='file']",
    ],
    publish_button_selectors=[
        "button:has-text('发布')",
        "button:has-text('发布笔记')",
    ],
)
