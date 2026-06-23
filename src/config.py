"""Konfigurationsmanagement für den Code Review Agent."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field


class OllamaConfig(BaseModel):
    base_url: str = "http://localhost:11434"  # WSL: http://host.docker.internal:11434
    model: str = "codellama:7b"
    temperature: float = 0.2
    max_tokens: int = 2048
    timeout: int = 120
    num_ctx: int = 8192


class ReviewConfig(BaseModel):
    max_diff_lines: int = 500
    chunk_size: int = 200
    parallel_chunks: int = 0
    comment_label: str = "🤖 AI Review"
    output_dir: str = "./reports"


class RulesConfig(BaseModel):
    enabled: bool = True
    rules_dir: str = "./rules"
    profiles: dict[str, list[str]] = Field(
        default_factory=lambda: {
            "default": ["default.yaml", "security.yaml"],
            "security": ["security.yaml"],
            "minimal": ["default.yaml"],
        }
    )


class GitConfig(BaseModel):
    remote: str = "origin"
    work_dir: str = "/tmp/code-review-agent"


class LoggingConfig(BaseModel):
    level: str = "INFO"
    format: str = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"


class AppConfig(BaseModel):
    ollama: OllamaConfig = OllamaConfig()
    review: ReviewConfig = ReviewConfig()
    rules: RulesConfig = RulesConfig()
    git: GitConfig = GitConfig()
    logging: LoggingConfig = LoggingConfig()

    @classmethod
    def load(cls, path: str | Path | None = None) -> AppConfig:
        """Lädt Konfiguration aus YAML-Datei + Umgebungsvariablen."""
        cfg = cls()

        if path is None:
            # Standard-Pfade durchsuchen
            candidates = [
                Path.cwd() / "config.yaml",
                Path.cwd() / "config.yml",
                Path.home() / ".config/code-review-agent/config.yaml",
                Path("/etc/code-review-agent/config.yaml"),
            ]
        else:
            candidates = [Path(path)]

        yaml_data: dict[str, Any] = {}
        for candidate in candidates:
            if candidate.exists():
                with open(candidate) as f:
                    yaml_data = yaml.safe_load(f) or {}
                break

        if yaml_data:
            cfg = cls._apply_yaml(cfg, yaml_data)

        cfg = cls._apply_env_overrides(cfg)
        return cfg

    @classmethod
    def _apply_yaml(cls, cfg: AppConfig, data: dict) -> AppConfig:
        """Wendet YAML-Werte auf die Config an."""
        if "ollama" in data:
            for k, v in data["ollama"].items():
                if hasattr(cfg.ollama, k):
                    setattr(cfg.ollama, k, v)
        if "review" in data:
            for k, v in data["review"].items():
                if hasattr(cfg.review, k):
                    setattr(cfg.review, k, v)
        if "rules" in data:
            for k, v in data["rules"].items():
                if hasattr(cfg.rules, k):
                    setattr(cfg.rules, k, v)
        if "git" in data:
            for k, v in data["git"].items():
                if hasattr(cfg.git, k):
                    setattr(cfg.git, k, v)
        if "logging" in data:
            for k, v in data["logging"].items():
                if hasattr(cfg.logging, k):
                    setattr(cfg.logging, k, v)
        return cfg

    @classmethod
    def _apply_env_overrides(cls, cfg: AppConfig) -> AppConfig:
        """Umgebungsvariablen überschreiben YAML-Werte."""
        mappings = {
            "CRA_OLLAMA_BASE_URL": ("ollama", "base_url"),
            "CRA_OLLAMA_MODEL": ("ollama", "model"),
            "CRA_OLLAMA_TEMPERATURE": ("ollama", "temperature"),
            "CRA_REVIEW_MAX_DIFF_LINES": ("review", "max_diff_lines"),
            "CRA_REVIEW_CHUNK_SIZE": ("review", "chunk_size"),
            "CRA_REVIEW_PARALLEL_CHUNKS": ("review", "parallel_chunks"),
            "CRA_RULES_ENABLED": ("rules", "enabled"),
            "CRA_RULES_DIR": ("rules", "rules_dir"),
            "CRA_GIT_REMOTE": ("git", "remote"),
            "CRA_GIT_WORK_DIR": ("git", "work_dir"),
            "CRA_LOG_LEVEL": ("logging", "level"),
        }
        for env_var, (section, key) in mappings.items():
            val = os.environ.get(env_var)
            if val is not None:
                section_obj = getattr(cfg, section)
                # Typkonvertierung
                current = getattr(section_obj, key)
                if isinstance(current, bool):
                    val = val.lower() in ("1", "true", "yes")
                elif isinstance(current, int):
                    val = int(val)
                elif isinstance(current, float):
                    val = float(val)
                setattr(section_obj, key, val)
        return cfg
