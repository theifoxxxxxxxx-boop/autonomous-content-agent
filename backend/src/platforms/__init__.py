from __future__ import annotations

from .base import PlatformAdapter
from .douyin import DouyinAdapter
from .xhs import XhsAdapter


def get_platform_adapter(platform: str) -> PlatformAdapter:
    platform_lower = platform.lower()
    if platform_lower == "douyin":
        return DouyinAdapter
    if platform_lower == "xhs":
        return XhsAdapter
    raise ValueError(f"Unsupported platform: {platform}")
