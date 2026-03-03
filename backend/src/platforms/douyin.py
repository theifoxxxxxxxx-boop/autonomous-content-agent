from __future__ import annotations

from .base import PlatformAdapter


DouyinAdapter = PlatformAdapter(
    name="douyin",
    creator_center_url="https://creator.douyin.com/",
    publish_entry_keywords=["发布", "发布作品", "图文", "上传"],
    upload_trigger_keywords=["上传", "添加图片", "上传图片", "添加作品"],
    publish_button_keywords=["发布", "立即发布"],
    fallback_publish_entry_selectors=[
        "a:has-text('发布')",
        "button:has-text('发布')",
        "[data-e2e*='publish']",
    ],
    title_selectors=[
        "input[placeholder*='标题']",
        "textarea[placeholder*='标题']",
        "[contenteditable='true'][aria-label*='标题']",
    ],
    content_selectors=[
        "textarea[placeholder*='描述']",
        "textarea[placeholder*='正文']",
        "[contenteditable='true'][aria-label*='描述']",
    ],
    upload_input_selectors=[
        "input[type='file']",
        "input[accept*='image']",
    ],
    publish_button_selectors=[
        "button:has-text('发布')",
        "[type='submit']:has-text('发布')",
    ],
)
