"""Rule Engine – Validiert LLM-Output gegen ein Regelwerk und filtert Ergebnisse."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from src.diff_analyzer import FileDiff

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────
# Datenmodelle
# ──────────────────────────────────────────────


@dataclass
class Rule:
    id: str
    severity: str  # error | warning | info
    category: str  # style | bug | security | performance | maintainability
    pattern: str
    message: str


@dataclass
class ReviewFinding:
    """Ein einzelner Review-Fund."""

    rule_id: str
    severity: str
    category: str
    file_path: str
    line: int | None
    column: int | None
    message: str
    suggestion: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "severity": self.severity,
            "category": self.category,
            "file_path": self.file_path,
            "line": self.line,
            "column": self.column,
            "message": self.message,
            "suggestion": self.suggestion,
        }


@dataclass
class ReviewResult:
    """Das vollständige Ergebnis eines Reviews."""

    findings: list[ReviewFinding] = field(default_factory=list)
    summary: str = ""
    llm_feedback: str = ""
    file: FileDiff | None = None

    @property
    def error_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == "error")

    @property
    def warning_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == "warning")

    @property
    def info_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == "info")

    @property
    def score(self) -> int:
        """Bewertung 0-100 (100 = perfekt)."""
        error_penalty = self.error_count * 15
        warning_penalty = self.warning_count * 5
        info_penalty = self.info_count * 1
        score = max(0, 100 - error_penalty - warning_penalty - info_penalty)
        return score


# ──────────────────────────────────────────────
# Rule Engine
# ──────────────────────────────────────────────


class RuleEngine:
    """Lädt und wendet Regelwerke auf Review-Ergebnisse an."""

    def __init__(self, rules_dir: str | Path) -> None:
        self.rules_dir = Path(rules_dir)
        self._rules: list[Rule] = []
        self._loaded_profiles: set[str] = set()

    def load_profile(self, profile_name: str, rule_files: list[str]) -> None:
        """Lädt ein bestimmtes Regel-Profil."""
        key = f"{profile_name}:{','.join(sorted(rule_files))}"
        if key in self._loaded_profiles:
            return

        for rule_file in rule_files:
            path = self.rules_dir / rule_file
            if not path.exists():
                logger.warning("Regeldatei nicht gefunden: %s", path)
                continue

            with open(path) as f:
                data = yaml.safe_load(f)

            if not data or "rules" not in data:
                continue

            for rule_data in data["rules"]:
                self._rules.append(Rule(
                    id=rule_data["id"],
                    severity=rule_data.get("severity", "info"),
                    category=rule_data.get("category", "style"),
                    pattern=rule_data.get("pattern", ""),
                    message=rule_data.get("message", ""),
                ))

            logger.info(
                "Regelwerk '%s' geladen: %d Regeln aus %s",
                profile_name,
                len(data["rules"]),
                rule_file,
            )

        self._loaded_profiles.add(key)

    def get_relevant_rules(
        self,
        file_diff: FileDiff,
    ) -> list[Rule]:
        """Filtert Regeln, die für den Dateityp relevant sind."""
        ext = file_diff.extension
        relevant = self._rules[:]

        # Sprachspezifische Filter (können erweitert werden)
        language_map = {
            ".py": ["STYLE-003", "BUG-003", "BUG-005"],
            ".js": ["STYLE-003"],
            ".ts": [],
            ".java": [],
            ".go": [],
            ".rs": [],
        }

        # Nicht-sprachspezifische Regeln immer behalten
        language_rules = set(language_map.get(ext, []))

        if language_rules:
            relevant = [
                r for r in relevant
                if r.id in language_rules or r.category in ("security", "bug", "performance")
            ]

        return relevant

    def validate_findings(
        self,
        findings: list[ReviewFinding],
    ) -> list[ReviewFinding]:
        """Validiert und dedupliziert Findings."""
        seen: set[str] = set()
        validated: list[ReviewFinding] = []

        for finding in findings:
            key = f"{finding.file_path}:{finding.line}:{finding.rule_id}"
            if key in seen:
                continue
            seen.add(key)
            validated.append(finding)

        return validated

    def get_severity_filter(self, min_severity: str) -> list[str]:
        """Gibt alle Severity-Level ab min_severity zurück."""
        levels = ["info", "warning", "error"]
        try:
            idx = levels.index(min_severity)
            return levels[idx:]
        except ValueError:
            return ["error"]
