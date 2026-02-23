"""
LLM backend abstraction — supports Gemini (Google) and Anthropic (Claude).

Set LLM_BACKEND="gemini" in .env to use Gemini 2.5 Flash-Lite.
Set LLM_BACKEND="anthropic" (default) to use Claude API.
"""

import json
import os
import re
import time
from dataclasses import dataclass

import requests
from dotenv import load_dotenv

load_dotenv()


@dataclass
class LLMResponse:
    text: str


class LLMBackend:
    """Unified interface for LLM inference."""

    def chat(
        self,
        system: str,
        messages: list[dict],
        max_tokens: int = 2048,
    ) -> LLMResponse:
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Anthropic backend
# ---------------------------------------------------------------------------

class AnthropicBackend(LLMBackend):
    def __init__(self, model: str = "claude-sonnet-4-6", api_key: str | None = None) -> None:
        import anthropic
        self.model = model
        self.client = anthropic.Anthropic(api_key=api_key) if api_key else anthropic.Anthropic()

    def chat(self, system: str, messages: list[dict], max_tokens: int = 2048) -> LLMResponse:
        resp = self.client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            system=system,
            messages=messages,
        )
        return LLMResponse(text=resp.content[0].text)


# ---------------------------------------------------------------------------
# Gemini backend (Google AI — Gemini 2.5 Flash-Lite)
# ---------------------------------------------------------------------------

class GeminiBackend(LLMBackend):
    """
    Calls the Google Gemini API via the google-generativeai SDK.

    Default model: gemini-2.5-flash-lite (fast, cheap, good for structured tasks).
    Requires GEMINI_API_KEY in .env.

    Get an API key at https://aistudio.google.com/apikey
    """

    def __init__(
        self,
        model: str = "gemini-2.5-flash-lite",
        api_key: str | None = None,
    ) -> None:
        import google.generativeai as genai
        api_key = api_key or os.getenv("GEMINI_API_KEY", "")
        if not api_key:
            raise ValueError(
                "GEMINI_API_KEY not set. Get one at https://aistudio.google.com/apikey"
            )
        genai.configure(api_key=api_key)
        self.model_name = model
        self.genai = genai

    def chat(self, system: str, messages: list[dict], max_tokens: int = 2048) -> LLMResponse:
        model = self.genai.GenerativeModel(
            model_name=self.model_name,
            system_instruction=system,
            generation_config=self.genai.GenerationConfig(
                temperature=0.3,
                max_output_tokens=max_tokens,
            ),
        )

        # Convert messages to Gemini content format
        contents = []
        for m in messages:
            role = "user" if m["role"] == "user" else "model"
            contents.append({"role": role, "parts": [m["content"]]})

        for attempt in range(3):
            try:
                response = model.generate_content(contents=contents)
                return LLMResponse(text=response.text)
            except Exception as exc:
                # Retry on transient errors (rate limits, server errors)
                err_str = str(exc).lower()
                if attempt < 2 and any(
                    kw in err_str
                    for kw in ["429", "500", "503", "rate", "quota", "overloaded"]
                ):
                    wait = 2 ** (attempt + 1)
                    time.sleep(wait)
                    continue
                raise

    def test_connection(self) -> bool:
        """Quick test to verify the API key and model work."""
        try:
            resp = self.chat(
                system="Reply with exactly: OK",
                messages=[{"role": "user", "content": "Test"}],
                max_tokens=10,
            )
            return bool(resp.text.strip())
        except Exception:
            return False

    def list_models(self) -> list[str]:
        """List available Gemini models."""
        try:
            return [
                m.name for m in self.genai.list_models()
                if "generateContent" in (m.supported_generation_methods or [])
            ]
        except Exception:
            return []


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def get_backend() -> LLMBackend:
    """
    Create the appropriate LLM backend based on environment config.

    LLM_BACKEND=gemini    → GeminiBackend (Gemini 2.5 Flash-Lite)
    LLM_BACKEND=anthropic → AnthropicBackend (default)
    """
    backend_type = os.getenv("LLM_BACKEND", "anthropic").lower().strip()

    if backend_type == "gemini":
        model = os.getenv("GEMINI_MODEL", "gemini-2.5-flash-lite")
        api_key = os.getenv("GEMINI_API_KEY")
        return GeminiBackend(model=model, api_key=api_key)
    else:
        model = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-6")
        api_key = os.getenv("ANTHROPIC_API_KEY")
        return AnthropicBackend(model=model, api_key=api_key)


def parse_json_response(raw: str) -> dict | list | None:
    """
    Extract JSON from an LLM response, handling code fences and stray text.
    Works for both Claude (clean JSON) and Gemini (sometimes wrapped).
    """
    raw = raw.strip()
    # Strip markdown code fences
    raw = re.sub(r"^```(?:json)?\s*\n?", "", raw)
    raw = re.sub(r"\n?```\s*$", "", raw)
    raw = raw.strip()

    # Try direct parse
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    # Try to find JSON array or object in the text
    for pattern in [r"\[[\s\S]*\]", r"\{[\s\S]*\}"]:
        match = re.search(pattern, raw)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                continue

    return None
