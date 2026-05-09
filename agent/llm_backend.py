"""
LLM backend abstraction — supports four backends:

  - "gemini"     Gemini 2.5 Flash-Lite via Google AI API
  - "anthropic"  Claude API
  - "local"      Local LLM speaking the OpenAI-compatible /v1/chat/completions
                 protocol (LM Studio, llama-server, vLLM, gbrain, etc.).
                 Configure with LOCAL_LLM_URL and LOCAL_LLM_MODEL.
  - "ollama"     Ollama's native /api/chat protocol

Set LLM_BACKEND in .env to pick the active backend.
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
# Local LLM — OpenAI-compatible /v1/chat/completions
# ---------------------------------------------------------------------------

class LocalLLMBackend(LLMBackend):
    """
    Talk to a locally-installed LLM that speaks the OpenAI Chat Completions
    protocol. Works with: LM Studio, llama-server (llama.cpp), vLLM, Jan,
    LocalAI, Ollama's OpenAI-compat endpoint, and any tool that exposes the
    same `/v1/chat/completions` shape — including `gbrain`.

    Required env:
      LOCAL_LLM_URL    Base URL, e.g. http://127.0.0.1:1234/v1   (LM Studio)
                                       http://127.0.0.1:11434/v1 (Ollama OpenAI-compat)
                                       http://127.0.0.1:8080/v1  (llama-server)
      LOCAL_LLM_MODEL  Model identifier the server expects (e.g. "llama3.1:8b",
                       "mistral-7b", or whatever your local model is called).

    Optional:
      LOCAL_LLM_API_KEY  Bearer token if the local server requires one.
    """

    def __init__(
        self,
        url: str | None = None,
        model: str | None = None,
        api_key: str | None = None,
    ) -> None:
        self.base_url = (url or os.getenv("LOCAL_LLM_URL", "http://127.0.0.1:1234/v1")).rstrip("/")
        self.model = model or os.getenv("LOCAL_LLM_MODEL", "local-model")
        self.api_key = api_key or os.getenv("LOCAL_LLM_API_KEY", "")

    def chat(self, system: str, messages: list[dict], max_tokens: int = 2048) -> LLMResponse:
        payload_messages = [{"role": "system", "content": system}] + [
            {"role": m["role"], "content": m["content"]} for m in messages
        ]
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        for attempt in range(3):
            try:
                resp = requests.post(
                    f"{self.base_url}/chat/completions",
                    headers=headers,
                    json={
                        "model": self.model,
                        "messages": payload_messages,
                        "max_tokens": max_tokens,
                        "temperature": 0.3,
                        "stream": False,
                    },
                    timeout=300,
                )
                resp.raise_for_status()
                data = resp.json()
                text = data["choices"][0]["message"]["content"]
                return LLMResponse(text=text)
            except (requests.ConnectionError, requests.Timeout) as exc:
                if attempt < 2:
                    time.sleep(2 ** (attempt + 1))
                    continue
                raise
            except requests.HTTPError as exc:
                # Some servers return 5xx briefly on warm-up
                if resp.status_code in (500, 502, 503, 504) and attempt < 2:
                    time.sleep(2 ** (attempt + 1))
                    continue
                raise

    def test_connection(self) -> bool:
        try:
            resp = self.chat(
                system="Reply with exactly: OK",
                messages=[{"role": "user", "content": "Test"}],
                max_tokens=10,
            )
            return bool(resp.text.strip())
        except Exception:
            return False


# ---------------------------------------------------------------------------
# Ollama — native /api/chat protocol
# ---------------------------------------------------------------------------

class OllamaBackend(LLMBackend):
    """
    Ollama's native chat endpoint. Use this if your local Ollama is not
    running with the OpenAI-compat shim. Otherwise prefer LocalLLMBackend
    pointed at http://127.0.0.1:11434/v1.

    Required env:
      OLLAMA_URL    Default http://127.0.0.1:11434
      OLLAMA_MODEL  e.g. "llama3.1:8b"
    """

    def __init__(self, url: str | None = None, model: str | None = None) -> None:
        self.base_url = (url or os.getenv("OLLAMA_URL", "http://127.0.0.1:11434")).rstrip("/")
        self.model = model or os.getenv("OLLAMA_MODEL", "llama3.1:8b")

    def chat(self, system: str, messages: list[dict], max_tokens: int = 2048) -> LLMResponse:
        payload_messages = [{"role": "system", "content": system}] + [
            {"role": m["role"], "content": m["content"]} for m in messages
        ]
        for attempt in range(3):
            try:
                resp = requests.post(
                    f"{self.base_url}/api/chat",
                    json={
                        "model": self.model,
                        "messages": payload_messages,
                        "stream": False,
                        "options": {
                            "num_predict": max_tokens,
                            "temperature": 0.3,
                        },
                    },
                    timeout=300,
                )
                resp.raise_for_status()
                data = resp.json()
                return LLMResponse(text=data["message"]["content"])
            except (requests.ConnectionError, requests.Timeout):
                if attempt < 2:
                    time.sleep(2 ** (attempt + 1))
                    continue
                raise

    def test_connection(self) -> bool:
        try:
            resp = self.chat(
                system="Reply with exactly: OK",
                messages=[{"role": "user", "content": "Test"}],
                max_tokens=10,
            )
            return bool(resp.text.strip())
        except Exception:
            return False


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def get_backend() -> LLMBackend:
    """
    Create the LLM backend chosen by LLM_BACKEND (case-insensitive).

      LLM_BACKEND=local      LocalLLMBackend (OpenAI-compat HTTP — gbrain, LM Studio, ...)
      LLM_BACKEND=ollama     OllamaBackend (native /api/chat)
      LLM_BACKEND=gemini     GeminiBackend (Gemini 2.5 Flash-Lite)
      LLM_BACKEND=anthropic  AnthropicBackend
    """
    backend_type = os.getenv("LLM_BACKEND", "anthropic").lower().strip()

    if backend_type == "local":
        return LocalLLMBackend()
    if backend_type == "ollama":
        return OllamaBackend()
    if backend_type == "gemini":
        model = os.getenv("GEMINI_MODEL", "gemini-2.5-flash-lite")
        api_key = os.getenv("GEMINI_API_KEY")
        return GeminiBackend(model=model, api_key=api_key)

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
