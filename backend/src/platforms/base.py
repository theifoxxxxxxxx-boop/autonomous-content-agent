from __future__ import annotations

from abc import ABC
from dataclasses import dataclass


@dataclass(frozen=True)
class PlatformAdapter(ABC):
    name: str
    creator_center_url: str
    publish_entry_keywords: list[str]
    upload_trigger_keywords: list[str]
    publish_button_keywords: list[str]
    fallback_publish_entry_selectors: list[str]
    title_selectors: list[str]
    content_selectors: list[str]
    upload_input_selectors: list[str]
    publish_button_selectors: list[str]

    def browser_task(self, title: str, content: str, image_count: int) -> str:
        return (
            f"你是内容操盘手，请在 {self.name} 创作中心完成图文草稿。\n"
            f"1. 打开 {self.creator_center_url}\n"
            "2. 寻找“发布/发布笔记/图文/上传”等入口并进入发布编辑页\n"
            f"3. 填写标题：{title}\n"
            f"4. 填写正文：{content}\n"
            f"5. 上传 {image_count} 张图片\n"
            "6. 找到发布按钮后立刻停止，并汇报“已就绪待人工确认发布”。\n"
            "硬性限制：严禁点击发布按钮。"
        )
