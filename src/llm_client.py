"""Ollama-LLM-Client für Code-Reviews."""

from __future__ import annotations

import json
import logging
from typing import Any

import requests

from src.config import OllamaConfig

logger = logging.getLogger(__name__)


class LLMClient:
    """Client für die Kommunikation mit einem lokalen Ollama-Server."""

    def __init__(self, config: OllamaConfig) -> None:
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
        """Sendet einen Prompt an Ollama und gibt die Antwort zurück.

        Args:
            system_prompt: System-Prompt mit Instruktionen.
            user_prompt: Der eigentliche Code/Prompt zum Review.
            temperature: Optional – überschreibt Config.
            max_tokens: Optional – überschreibt Config.
            format: Optional – 'json' für strukturierte Antworten.

        Returns:
            Generierter Text aus dem LLM.
        """
        payload = self._build_payload(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=temperature,
            max_tokens=max_tokens,
            format=format,
        )

        try:
            response = self.session.post(
                f"{self.config.base_url}/api/generate",
                json=payload,
                timeout=self.config.timeout,
            )
            response.raise_for_status()
            return self._parse_stream(response)
        except requests.exceptions.ConnectionError:
            logger.error(
                "Keine Verbindung zu Ollama unter %s. "
                "Ist 'ollama serve' gestartet?",
                self.config.base_url,
            )
            raise
        except requests.exceptions.Timeout:
            logger.error(
                "Ollama-Timeout nach %ds – das Modell %s ist evtl. "
                "noch nicht geladen oder der Prompt ist zu groß.",
                self.config.timeout,
                self.config.model,
            )
            raise
        except requests.exceptions.RequestException as e:
            logger.error("Ollama-API-Fehler: %s", e)
            raise

    def generate_json(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> dict[str, Any]:
        """Generiert eine strukturierte JSON-Antwort (Ollama 0.3+)."""
        text = self.generate(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=temperature,
            max_tokens=max_tokens,
            format="json",
        )
        return json.loads(text)

    def health_check(self) -> bool:
        """Prüft, ob Ollama erreichbar ist und das Modell existiert."""
        try:
            resp = self.session.get(
                f"{self.config.base_url}/api/tags",
                timeout=5,
            )
            resp.raise_for_status()
            models = resp.json().get("models", [])
            model_names = [m["name"] for m in models]

            # Prüfe, ob das konfigurierte Modell gelistet ist
            configured = self.config.model
            if configured not in model_names:
                # Prüfe ohne Tag
                base = configured.split(":")[0]
                available = [m for m in model_names if m.startswith(base)]
                if available:
                    logger.warning(
                        "Modell '%s' nicht gefunden, aber '%s' ist verfügbar. "
                        "Setze CRA_OLLAMA_MODEL=%s",
                        configured,
                        available[0],
                        available[0],
                    )
                else:
                    logger.warning(
                        "Modell '%s' nicht in Ollama gefunden. "
                        "Installieren mit: ollama pull %s",
                        configured,
                        configured,
                    )
                    return False
            return True
        except requests.exceptions.RequestException:
            return False

    # ──────────────────────────────────────────
    # Interne Hilfsmethoden
    # ──────────────────────────────────────────

    def _build_payload(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float | None,
        max_tokens: int | None,
        format: str | None,
    ) -> dict[str, Any]:
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
        return payload

    def _parse_stream(self, response: requests.Response) -> str:
        """Parst die Ollama-Response (auch für nicht-streaming)."""
        data = response.json()
        return data.get("response", "")
