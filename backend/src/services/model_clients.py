from __future__ import annotations

import json
from typing import Any

from anthropic import AsyncAnthropic
from langchain_openai import ChatOpenAI
from openai import AsyncOpenAI

from src.config import Settings
from src.utils.image_utils import encode_image_to_base64
from src.utils.json_utils import extract_json_object


class ModelClients:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._openai_client = AsyncOpenAI(
            api_key=settings.openai_api_key or "EMPTY",
            base_url=settings.openai_base_url or None,
        )
        self._deepseek_client = AsyncOpenAI(
            api_key=settings.deepseek_api_key or "EMPTY",
            base_url=settings.deepseek_base_url,
        )
        self._anthropic_client = AsyncAnthropic(api_key=settings.anthropic_api_key or "EMPTY")

    def build_browser_llm(self) -> ChatOpenAI:
        return ChatOpenAI(
            model=self.settings.deepseek_model,
            api_key=self.settings.deepseek_api_key,
            base_url=self.settings.deepseek_base_url,
            temperature=0.2,
        )

    async def analyze_images(self, image_paths: list[str]) -> dict[str, Any]:
        if self.settings.mock_mode:
            return {
                "overview": "画面展示了产品使用前后对比和细节特写。",
                "features": ["色彩干净", "主体清晰", "有生活场景感"],
                "tone": "可信、真实、轻种草",
                "suggested_angle": "从真实体验和对比结果切入。",
                "keywords": ["实测", "前后对比", "避坑", "质感"],
            }

        if self.settings.vision_provider.lower() == "claude":
            return await self._analyze_with_claude(image_paths)
        return await self._analyze_with_gpt4o(image_paths)

    async def generate_copy(
        self,
        platform: str,
        requirement: str,
        vision_analysis: dict[str, Any],
        critique_feedback: str,
    ) -> dict[str, Any]:
        if self.settings.mock_mode:
            hashtags = ["#实测", "#自媒体运营", "#内容创作", "#效率工具", "#经验分享"]
            content = (
                "今天把这个流程完整跑了一遍，真的省了很多时间😀\n"
                "第一步先看图提炼核心卖点，再按平台语气去写文案🔥\n"
                "主编审稿会卡字数和风险词，能避免翻车✅\n"
                "最后自动打开发布页，停在发布按钮前，人工确认更安心🧠\n"
                "如果你也做账号，可以直接套这套流程试试🚀\n"
                "评论区告诉我你最想自动化哪一步呀👇"
            )
            return {"title": "2张图做出可发爆款笔记？这套流程我真香了！", "content": content, "hashtags": hashtags}

        prompt = (
            "你是资深新媒体编导。请输出严格 JSON，不要 markdown。\n"
            "目标平台: {platform}\n"
            "用户需求: {requirement}\n"
            "视觉分析: {vision}\n"
            "审稿反馈（如有）: {feedback}\n"
            "要求:\n"
            "1) 输出 title + content + hashtags(list)\n"
            "2) content 用中文，分段，包含 emoji\n"
            "3) hashtags 3-8 个\n"
            "4) 避免极限词/夸大承诺\n"
            "JSON schema: "
            '{{"title":"", "content":"", "hashtags":["#a","#b"]}}'
        ).format(
            platform=platform,
            requirement=requirement,
            vision=json.dumps(vision_analysis, ensure_ascii=False),
            feedback=critique_feedback or "无",
        )

        response = await self._deepseek_client.chat.completions.create(
            model=self.settings.deepseek_model,
            temperature=0.6,
            messages=[{"role": "user", "content": prompt}],
        )
        text = response.choices[0].message.content or "{}"
        result = extract_json_object(text)
        hashtags = result.get("hashtags", [])
        content = result.get("content", "")
        if hashtags and isinstance(hashtags, list):
            content = f"{content}\n\n{' '.join(hashtags)}"
        return {
            "title": str(result.get("title", "")).strip(),
            "content": content.strip(),
            "hashtags": hashtags if isinstance(hashtags, list) else [],
        }

    async def llm_editorial_review(
        self,
        platform: str,
        requirement: str,
        title: str,
        content: str,
        deterministic_issues: list[str],
    ) -> dict[str, Any]:
        if self.settings.mock_mode:
            return {
                "passed": len(deterministic_issues) == 0,
                "issues": [],
                "rewrite_instructions": [],
            }

        prompt = (
  "你是内容团队主编。你必须只输出**纯 JSON**，禁止输出任何解释文字、禁止 Markdown、禁止 ```。\n"
  "【输入】\n"
  "platform: {platform}\n"
  "requirement: {requirement}\n"
  "title: {title}\n"
  "content: {content}\n"
  "deterministic_issues: {issues}\n\n"
  "【硬规则】\n"
  "1) 如果 deterministic_issues 非空：passed 必须为 false。\n"
  "2) issues 必须是简短字符串数组；rewrite_instructions 必须给可直接用于重写的指令（中文、可执行）。\n"
  "3) 合规风险（极限词/虚假承诺/诱导等）属于重大问题，必须 passed=false 并给替换建议。\n\n"
  "【主观审校标准（只在 deterministic_issues 为空时使用）】\n"
  "- 如果只是小优化（语气更顺/更吸引、结构更清晰、增加一点细节），仍然 passed=true，并把建议写到 issues。\n"
  "- 只有在明显不符合用户需求、逻辑严重不通、明显夸大/违规、标题党过界时，passed=false。\n\n"
  "【输出 JSON schema】\n"
  '{"passed": true/false, "issues": ["..."], "rewrite_instructions": ["..."]}'
).format(
            platform=platform,
            requirement=requirement,
            title=title,
            content=content,
            issues=json.dumps(deterministic_issues, ensure_ascii=False),
        )
        response = await self._deepseek_client.chat.completions.create(
            model=self.settings.deepseek_model,
            temperature=0.2,
            messages=[{"role": "user", "content": prompt}],
        )
        text = response.choices[0].message.content or "{}"
        result = extract_json_object(text)
        return {
            "passed": bool(result.get("passed", False)),
            "issues": list(result.get("issues", [])),
            "rewrite_instructions": list(result.get("rewrite_instructions", [])),
        }

    async def _analyze_with_gpt4o(self, image_paths: list[str]) -> dict[str, Any]:
        content: list[dict[str, Any]] = [
            {
                "type": "text",
                "text": (
                    "请分析这些图片并输出严格 JSON: "
                    '{"overview":"", "features":[""], "tone":"", "suggested_angle":"", "keywords":[""]}'
                ),
            }
        ]
        for image_path in image_paths:
            encoded, mime_type = encode_image_to_base64(image_path)
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{mime_type};base64,{encoded}"},
                }
            )

        response = await self._openai_client.chat.completions.create(
            model=self.settings.gpt4o_model,
            temperature=0.2,
            messages=[{"role": "user", "content": content}],
        )
        text = response.choices[0].message.content or "{}"
        return extract_json_object(text)

    async def _analyze_with_claude(self, image_paths: list[str]) -> dict[str, Any]:
        content: list[dict[str, Any]] = [
            {
                "type": "text",
                "text": (
                    "请分析这些图片并输出严格 JSON: "
                    '{"overview":"", "features":[""], "tone":"", "suggested_angle":"", "keywords":[""]}'
                ),
            }
        ]
        for image_path in image_paths:
            encoded, mime_type = encode_image_to_base64(image_path)
            content.append(
                {
                    "type": "image",
                    "source": {"type": "base64", "media_type": mime_type, "data": encoded},
                }
            )
        response = await self._anthropic_client.messages.create(
            model=self.settings.claude_model,
            max_tokens=800,
            messages=[{"role": "user", "content": content}],
        )
        raw_text = "".join(
            block.text for block in response.content if getattr(block, "type", "") == "text"
        )
        return extract_json_object(raw_text)
