"""
LLM backend abstraction — supports Ollama (local) and Anthropic (cloud).

Set LLM_BACKEND="ollama" in .env to run everything locally.
Set LLM_BACKEND="anthropic" (default) to use Claude API.

Ollama requires the ollama service running locally: https://ollama.com
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
# Ollama backend
# ---------------------------------------------------------------------------

class OllamaBackend(LLMBackend):
    """
    Calls the Ollama REST API at http://localhost:11434/api/chat.

    Recommended models for governance analysis (in order of capability):
      - llama3.1:70b     (best quality, needs ~40GB RAM)
      - qwen2.5:32b      (strong, needs ~20GB RAM)
      - mixtral:8x7b     (good balance, ~26GB RAM)
      - llama3.1:8b      (lightweight, ~5GB RAM, lower quality)
      - mistral:7b       (lightweight, ~5GB RAM)
    """

    def __init__(
        self,
        model: str = "llama3.1:8b",
        base_url: str = "http://localhost:11434",
    ) -> None:
        self.model = model
        self.base_url = base_url.rstrip("/")

    def chat(self, system: str, messages: list[dict], max_tokens: int = 2048) -> LLMResponse:
        ollama_messages = [{"role": "system", "content": system}]
        for m in messages:
            ollama_messages.append({"role": m["role"], "content": m["content"]})

        payload = {
            "model": self.model,
            "messages": ollama_messages,
            "stream": False,
            "options": {
                "num_predict": max_tokens,
                "temperature": 0.3,  # Lower temp for structured output
            },
        }

        for attempt in range(3):
            try:
                resp = requests.post(
                    f"{self.base_url}/api/chat",
                    json=payload,
                    timeout=300,  # Local models can be slow
                )
                resp.raise_for_status()
                data = resp.json()
                return LLMResponse(text=data["message"]["content"])
            except requests.ConnectionError:
                if attempt == 0:
                    print(
                        f"  [!] Cannot connect to Ollama at {self.base_url}. "
                        "Make sure 'ollama serve' is running."
                    )
                raise
            except requests.Timeout:
                if attempt < 2:
                    time.sleep(2 ** attempt)
                    continue
                raise
            except Exception:
                if attempt < 2:
                    time.sleep(1)
                    continue
                raise

    def is_available(self) -> bool:
        """Check if Ollama is running and the model is pulled."""
        try:
            resp = requests.get(f"{self.base_url}/api/tags", timeout=5)
            if resp.status_code != 200:
                return False
            models = [m["name"] for m in resp.json().get("models", [])]
            # Check if our model (possibly without tag) is available
            return any(
                self.model in m or m.startswith(self.model.split(":")[0])
                for m in models
            )
        except Exception:
            return False

    def pull_model(self) -> None:
        """Pull the model if not already available."""
        print(f"  Pulling model {self.model} (this may take a while) ...")
        resp = requests.post(
            f"{self.base_url}/api/pull",
            json={"name": self.model, "stream": False},
            timeout=3600,
        )
        resp.raise_for_status()
        print(f"  Model {self.model} ready.")


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def get_backend() -> LLMBackend:
    """
    Create the appropriate LLM backend based on environment config.

    LLM_BACKEND=ollama   → OllamaBackend
    LLM_BACKEND=anthropic → AnthropicBackend (default)
    """
    backend_type = os.getenv("LLM_BACKEND", "anthropic").lower().strip()

    if backend_type == "ollama":
        model = os.getenv("OLLAMA_MODEL", "llama3.1:8b")
        base_url = os.getenv("OLLAMA_URL", "http://localhost:11434")
        return OllamaBackend(model=model, base_url=base_url)
    else:
        model = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-6")
        api_key = os.getenv("ANTHROPIC_API_KEY")
        return AnthropicBackend(model=model, api_key=api_key)


def parse_json_response(raw: str) -> dict | list | None:
    """
    Extract JSON from an LLM response, handling code fences and stray text.
    Works for both Claude (clean JSON) and Ollama models (often wrapped).
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
