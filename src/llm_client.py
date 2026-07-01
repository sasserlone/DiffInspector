"""LLM-Client für Code-Reviews – unterstützt Ollama + OpenAI-kompatible APIs (DeepSeek, OpenAI, etc.)."""

from __future__ import annotations

import json
import logging
import os
from typing import Any

import requests

from src.config import LLMConfig

logger = logging.getLogger(__name__)


class LLMClient:
    """Client für LLM-Kommunikation (Ollama oder OpenAI-kompatibel)."""

    def __init__(self, config: LLMConfig) -> None:
        self.config = config
        self.session = requests.Session()
        self.session.timeout = config.timeout

    # ──────────────────────────────────────────
    # Public API
    # ──────────────────────────────────────────

    def generate(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        format: str | None = None,
    ) -> str:
        """Sendet einen Prompt an das konfigurierte LLM und gibt die Antwort zurück."""
        if self.config.provider == "deepseek" or self.config.provider == "openai":
            return self._generate_openai(system_prompt, user_prompt, temperature, max_tokens)
        return self._generate_ollama(system_prompt, user_prompt, temperature, max_tokens, format)

    def health_check(self) -> bool:
        """Prüft, ob der LLM-Provider erreichbar ist."""
        if self.config.provider == "deepseek" or self.config.provider == "openai":
            return self._health_check_openai()
        return self._health_check_ollama()

    def generate_json(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> dict[str, Any]:
        """Generiert eine strukturierte JSON-Antwort (Ollama 0.3+ / API)."""
        text = self.generate(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=temperature,
            max_tokens=max_tokens,
            format="json",
        )
        return json.loads(text)

    # ──────────────────────────────────────────
    # Ollama
    # ──────────────────────────────────────────

    def _generate_ollama(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float | None,
        max_tokens: int | None,
        format: str | None,
    ) -> str:
        payload: dict[str, Any] = {
            "model": self.config.model,
            "system": system_prompt,
            "prompt": user_prompt,
            "stream": False,
            "options": {
                "temperature": temperature or self.config.temperature,
                "num_predict": max_tokens or self.config.max_tokens,
                "num_ctx": self.config.num_ctx,
            },
        }
        if format == "json":
            payload["format"] = "json"

        try:
            response = self.session.post(
                f"{self.config.base_url}/api/generate",
                json=payload,
                timeout=self.config.timeout,
            )
            response.raise_for_status()
            return response.json().get("response", "")
        except requests.exceptions.ConnectionError:
            logger.error(
                "Keine Verbindung zu Ollama unter %s. Ist 'ollama serve' gestartet?",
                self.config.base_url,
            )
            raise
        except requests.exceptions.Timeout:
            logger.error(
                "Ollama-Timeout nach %ds – das Modell %s ist evtl. noch nicht geladen.",
                self.config.timeout, self.config.model,
            )
            raise
        except requests.exceptions.RequestException as e:
            logger.error("Ollama-API-Fehler: %s", e)
            raise

    def _health_check_ollama(self) -> bool:
        try:
            resp = self.session.get(f"{self.config.base_url}/api/tags", timeout=5)
            resp.raise_for_status()
            models = resp.json().get("models", [])
            model_names = [m["name"] for m in models]
            configured = self.config.model
            if configured not in model_names:
                base = configured.split(":")[0]
                available = [m for m in model_names if m.startswith(base)]
                if available:
                    logger.warning(
                        "Modell '%s' nicht gefunden, aber '%s' verfügbar.",
                        configured, available[0],
                    )
                else:
                    logger.warning("Modell '%s' nicht in Ollama gefunden.", configured)
                    return False
            return True
        except requests.exceptions.RequestException:
            return False

    # ──────────────────────────────────────────
    # OpenAI-kompatibel (DeepSeek, OpenAI, etc.)
    # ──────────────────────────────────────────

    def _generate_openai(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float | None,
        max_tokens: int | None,
    ) -> str:
        api_key = self.config.api_key or self._env_key()
        if not api_key:
            raise ValueError(
                "Kein API-Key. Setze CRA_API_KEY, DEEPSEEK_API_KEY oder api_key in config.yaml"
            )

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.config.api_model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": temperature or self.config.temperature,
            "max_tokens": max_tokens or self.config.max_tokens,
            "stream": False,
        }

        try:
            response = self.session.post(
                f"{self.config.api_base_url}/v1/chat/completions",
                headers=headers, json=payload,
                timeout=self.config.timeout,
            )
            response.raise_for_status()
            return response.json()["choices"][0]["message"]["content"]
        except requests.exceptions.RequestException as e:
            logger.error("API-Fehler (%s): %s", self.config.api_base_url, e)
            if hasattr(e, "response") and e.response is not None:
                logger.error("Response: %s", e.response.text[:500])
            raise

    def _health_check_openai(self) -> bool:
        api_key = self.config.api_key or self._env_key()
        if not api_key:
            return False
        try:
            resp = self.session.get(
                f"{self.config.api_base_url}/v1/models",
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=10,
            )
            return resp.status_code == 200
        except requests.exceptions.RequestException:
            return False

    def _env_key(self) -> str:
        return (os.environ.get("CRA_API_KEY", "")
                or os.environ.get("DEEPSEEK_API_KEY", "")
                or os.environ.get("OPENAI_API_KEY", ""))
