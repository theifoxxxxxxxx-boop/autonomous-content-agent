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

        self._openai_client: AsyncOpenAI | None = None
        openai_key = (settings.openai_api_key or "").strip()
        if openai_key:
            self._openai_client = AsyncOpenAI(
                api_key=openai_key,
                base_url=settings.openai_base_url or None,
            )

        self._deepseek_client = AsyncOpenAI(
            api_key=(settings.deepseek_api_key or "EMPTY").strip() or "EMPTY",
            base_url=settings.deepseek_base_url,
        )
        self._anthropic_client = AsyncAnthropic(
            api_key=(settings.anthropic_api_key or "EMPTY").strip() or "EMPTY"
        )

    def _ensure_required_key(self, env_name: str, value: str, node_name: str) -> None:
        if self.settings.mock_mode:
            return
        if not (value or "").strip():
            raise RuntimeError(f"{node_name}: missing required env `{env_name}` in backend/.env")

    def _parse_json_output(self, node_name: str, text: str) -> dict[str, Any]:
        try:
            return extract_json_object(text)
        except ValueError as exc:
            preview = (text or "").replace("\n", "\\n").replace("\r", "\\r")
            if len(preview) > 300:
                preview = preview[:300] + "..."
            raise RuntimeError(
                f"{node_name}: invalid JSON in model output. {exc}. raw_preview={preview}"
            ) from exc

    def build_browser_llm(self) -> ChatOpenAI:
        self._ensure_required_key("DEEPSEEK_API_KEY", self.settings.deepseek_api_key, "Node D")
        return ChatOpenAI(
            model=self.settings.deepseek_model,
            api_key=self.settings.deepseek_api_key,
            base_url=self.settings.deepseek_base_url,
            temperature=0.2,
        )

    async def analyze_images(self, image_paths: list[str]) -> dict[str, Any]:
        if self.settings.mock_mode:
            return {
                "overview": "Product before-after comparison with detail close-ups.",
                "features": ["clear subject", "real-life scene", "readable visual contrast"],
                "tone": "authentic, trustworthy, practical",
                "suggested_angle": "focus on measurable changes and real experience",
                "keywords": ["hands-on", "comparison", "tips", "experience"],
            }

        provider = (self.settings.vision_provider or "claude").strip().lower()
        if provider in {"claude", "anthropic"}:
            return await self._analyze_with_claude(image_paths)
        if provider in {"gpt4o", "gpt-4o", "openai"}:
            return await self._analyze_with_gpt4o(image_paths)
        raise RuntimeError(
            f"Node A: unsupported VISION_PROVIDER `{self.settings.vision_provider}`. "
            "Use `claude` or `gpt4o`."
        )

    async def generate_copy(
        self,
        platform: str,
        requirement: str,
        vision_analysis: dict[str, Any],
        critique_feedback: str,
    ) -> dict[str, Any]:
        if self.settings.mock_mode:
            hashtags = ["#test", "#content", "#workflow", "#automation"]
            content = (
                "This is a mock draft for local integration testing.\n"
                "It keeps multi-line formatting and hashtags for parser checks."
            )
            return {
                "title": "Mock content output",
                "content": content,
                "hashtags": hashtags,
            }

        self._ensure_required_key("DEEPSEEK_API_KEY", self.settings.deepseek_api_key, "Node B")
        prompt = (
            "You are a senior social content editor.\n"
            "Return ONLY one valid JSON object.\n"
            "No markdown, no code fences, no explanation text.\n"
            'If a string contains double quotes, escape them as \\".\n'
            "Platform: {platform}\n"
            "Requirement: {requirement}\n"
            "Vision analysis: {vision}\n"
            "Review feedback: {feedback}\n"
            "Rules:\n"
            "1) output keys: title, content, hashtags\n"
            "2) content should be Chinese and segmented with line breaks and emoji\n"
            "3) hashtags should contain 3-8 items\n"
            "4) avoid exaggerated claims\n"
            'Schema: {{"title":"", "content":"", "hashtags":["#a","#b"]}}'
        ).format(
            platform=platform,
            requirement=requirement,
            vision=json.dumps(vision_analysis, ensure_ascii=False),
            feedback=critique_feedback or "none",
        )

        try:
            response = await self._deepseek_client.chat.completions.create(
                model=self.settings.deepseek_model,
                temperature=0.6,
                messages=[{"role": "user", "content": prompt}],
            )
        except Exception as exc:
            raise RuntimeError(f"Node B: DeepSeek API call failed: {exc}") from exc

        text = response.choices[0].message.content or "{}"
        result = self._parse_json_output("Node B", text)
        hashtags = result.get("hashtags", [])
        content = result.get("content", "")
        if hashtags and isinstance(hashtags, list):
            content = f"{content}\n\n{' '.join(hashtags)}"
        return {
            "title": str(result.get("title", "")).strip(),
            "content": str(content).strip(),
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

        self._ensure_required_key("DEEPSEEK_API_KEY", self.settings.deepseek_api_key, "Node C")
        prompt = (
            "You are the editorial reviewer.\n"
            "Return ONLY one valid JSON object.\n"
            "No markdown, no code fences, no explanation text.\n"
            'If a string contains double quotes, escape them as \\".\n'
            "Input:\n"
            "platform: {platform}\n"
            "requirement: {requirement}\n"
            "title: {title}\n"
            "content: {content}\n"
            "deterministic_issues: {issues}\n"
            "Hard rules:\n"
            "1) if deterministic_issues is not empty, passed must be false\n"
            "2) issues must be short strings\n"
            "3) rewrite_instructions must be actionable Chinese instructions\n"
            '{{"passed": true/false, "issues": ["..."], "rewrite_instructions": ["..."]}}'
        ).format(
            platform=platform,
            requirement=requirement,
            title=title,
            content=content,
            issues=json.dumps(deterministic_issues, ensure_ascii=False),
        )

        try:
            response = await self._deepseek_client.chat.completions.create(
                model=self.settings.deepseek_model,
                temperature=0.2,
                messages=[{"role": "user", "content": prompt}],
            )
        except Exception as exc:
            raise RuntimeError(f"Node C: DeepSeek API call failed: {exc}") from exc

        text = response.choices[0].message.content or "{}"
        result = self._parse_json_output("Node C", text)
        return {
            "passed": bool(result.get("passed", False)),
            "issues": list(result.get("issues", [])),
            "rewrite_instructions": list(result.get("rewrite_instructions", [])),
        }

    async def _analyze_with_gpt4o(self, image_paths: list[str]) -> dict[str, Any]:
        self._ensure_required_key("OPENAI_API_KEY", self.settings.openai_api_key, "Node A")
        if not self._openai_client:
            raise RuntimeError(
                "Node A: OPENAI_API_KEY is missing. "
                "Set `VISION_PROVIDER=claude` if you want Anthropic vision."
            )

        content: list[dict[str, Any]] = [
            {
                "type": "text",
                "text": (
                    "Analyze the images and return ONLY one valid JSON object.\n"
                    "No markdown and no explanations.\n"
                    'Escape double quotes in string values as \\".\n'
                    '{"overview":"", "features":[""], "tone":"", '
                    '"suggested_angle":"", "keywords":[""]}'
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

        try:
            response = await self._openai_client.chat.completions.create(
                model=self.settings.gpt4o_model,
                temperature=0.2,
                messages=[{"role": "user", "content": content}],
            )
        except Exception as exc:
            raise RuntimeError(f"Node A: OpenAI vision API call failed: {exc}") from exc

        text = response.choices[0].message.content or "{}"
        return self._parse_json_output("Node A", text)

    async def _analyze_with_claude(self, image_paths: list[str]) -> dict[str, Any]:
        self._ensure_required_key("ANTHROPIC_API_KEY", self.settings.anthropic_api_key, "Node A")
        content: list[dict[str, Any]] = [
            {
                "type": "text",
                "text": (
                    "Analyze the images and return ONLY one valid JSON object.\n"
                    "Do NOT wrap with markdown code fences such as ```json.\n"
                    "Do NOT output any explanation text before or after JSON.\n"
                    'If a string contains double quotes, escape them as \\".\n'
                    "Ensure commas are present between all fields.\n"
                    '{"overview":"", "features":[""], "tone":"", '
                    '"suggested_angle":"", "keywords":[""]}'
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

        configured_model = (self.settings.claude_model or "").strip() or "claude-sonnet-4-6"
        fallback_models = [
            "claude-sonnet-4-6",
            "claude-sonnet-4-5-20250929",
            "claude-sonnet-4-20250514",
            "claude-3-haiku-20240307",
        ]
        models_to_try: list[str] = [configured_model]
        for fallback in fallback_models:
            if fallback not in models_to_try:
                models_to_try.append(fallback)

        last_error: Exception | None = None
        response = None
        for model_name in models_to_try:
            try:
                response = await self._anthropic_client.messages.create(
                    model=model_name,
                    max_tokens=800,
                    messages=[{"role": "user", "content": content}],
                )
                break
            except Exception as exc:
                last_error = exc
                error_text = str(exc)
                if "not_found_error" in error_text and model_name != models_to_try[-1]:
                    continue
                raise RuntimeError(f"Node A: Claude vision API call failed: {exc}") from exc

        if response is None:
            raise RuntimeError(f"Node A: Claude vision API call failed: {last_error}")

        raw_text = "".join(
            block.text for block in response.content if getattr(block, "type", "") == "text"
        )
        return self._parse_json_output("Node A", raw_text)
