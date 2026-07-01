"""Rule Engine – Validiert LLM-Output gegen ein Regelwerk und filtert Ergebnisse."""

from __future__ import annotations

import logging
import re
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

    VALID_SEVERITIES = {"error", "warning", "info"}
    VALID_CATEGORIES = {"bug", "security", "performance", "maintainability", "style"}
    NON_FINDING_MARKERS = (
        "keine änderung nötig",
        "keine aenderung nötig",
        "keine aenderung noetig",
        "keine probleme",
        "alles gut",
        "no issue",
        "no finding",
    )
    SPECULATIVE_MARKERS = (
        "könnte",
        "koennte",
        "möglicherweise",
        "moeglicherweise",
        "potenziell",
        "potentially",
        "might",
        "may ",
        "falls ",
        "wenn ",
    )
    UNSAFE_SUGGESTION_MARKERS = (
        "source ",
        'source "$env_file"',
        "source '$env_file'",
        "source $env_file",
        "source .env",
        "set -a; source",
    )
    SECRET_VALUE_RE = re.compile(
        r"(?i)(?:api[_-]?key|token|secret|password)\s*[:=]\s*['\"]"
        r"(?!\s*['\"])(?!\s*\$\{)(?!\s*<)[^'\"]{8,}['\"]"
    )
    DANGEROUS_CODE_RE = re.compile(
        r"(?i)\b(eval|exec|pickle\.loads|subprocess\.(?:run|popen|call)|os\.system)\b"
    )

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
        file_diffs: list[FileDiff] | None = None,
    ) -> list[ReviewFinding]:
        """Validiert, normalisiert und dedupliziert Findings.

        LLM-Findings werden nur übernommen, wenn sie auf eine tatsächlich
        hinzugefügte Diff-Zeile zeigen. Infrastruktur-Findings wie Syntax- oder
        Review-Fehler bleiben erhalten, damit Ausfälle nicht als grüne Reviews
        enden.
        """
        seen: set[str] = set()
        validated: list[ReviewFinding] = []
        added_lines_by_file = self._added_lines_by_file(file_diffs or [])
        added_text_by_location = self._added_text_by_location(file_diffs or [])
        changed_files = set(added_lines_by_file)

        for finding in findings:
            normalized = self._normalize_finding(finding)
            if normalized is None:
                continue

            if normalized.rule_id == "LLM-FINDING":
                if changed_files and normalized.file_path not in changed_files:
                    logger.debug("Verwerfe Finding für nicht geänderte Datei: %s", normalized.file_path)
                    continue
                valid_lines = added_lines_by_file.get(normalized.file_path, set())
                if valid_lines and normalized.line not in valid_lines:
                    logger.debug(
                        "Verwerfe Finding außerhalb hinzugefügter Zeilen: %s:%s",
                        normalized.file_path,
                        normalized.line,
                    )
                    continue
                line_text = added_text_by_location.get((normalized.file_path, normalized.line or -1), "")
                normalized = self._calibrate_llm_finding(normalized, line_text)
                if normalized is None:
                    continue

            key = (
                f"{normalized.file_path}:{normalized.line}:"
                f"{normalized.severity}:{normalized.category}:{normalized.message.lower()}"
            )
            if key in seen:
                continue
            seen.add(key)
            validated.append(normalized)

        return validated

    def _normalize_finding(self, finding: ReviewFinding) -> ReviewFinding | None:
        severity = finding.severity.strip().lower()
        category = finding.category.strip().lower()
        message = " ".join(finding.message.split())

        severity = {
            "fehler": "error",
            "warnung": "warning",
            "hinweis": "info",
        }.get(severity, severity)

        category = {
            "maint": "maintainability",
            "maintenance": "maintainability",
            "performance issue": "performance",
        }.get(category, category)

        if severity not in self.VALID_SEVERITIES:
            severity = "info"
        if category not in self.VALID_CATEGORIES:
            category = "maintainability"
        if len(message) < 5:
            return None

        message_lower = message.lower()
        if any(marker in message_lower for marker in self.NON_FINDING_MARKERS):
            return None
        suggestion = finding.suggestion.strip()
        suggestion_lower = suggestion.lower()
        if any(marker in suggestion_lower for marker in self.UNSAFE_SUGGESTION_MARKERS):
            logger.debug("Verwerfe Finding mit unsicherem Vorschlag: %s", suggestion)
            return None

        if category in {"style", "maintainability"} and severity == "error":
            severity = "warning"

        return ReviewFinding(
            rule_id=finding.rule_id,
            severity=severity,
            category=category,
            file_path=finding.file_path.strip(),
            line=finding.line,
            column=finding.column,
            message=message,
            suggestion=suggestion,
        )

    def _calibrate_llm_finding(
        self,
        finding: ReviewFinding,
        line_text: str,
    ) -> ReviewFinding | None:
        """Reduziert Halluzinationen und aggressive Severities aus LLM-Output."""
        message_lower = finding.message.lower()
        suggestion_lower = finding.suggestion.lower()
        line_lower = line_text.lower()
        severity = finding.severity
        category = finding.category

        if any(marker in message_lower for marker in self.SPECULATIVE_MARKERS):
            severity = "warning" if severity == "error" else severity

        if "hardcod" in message_lower and "api_key" in line_lower and '""' in line_text:
            return None
        if "api-key" in message_lower and "api_key" in line_lower and '""' in line_text:
            return None

        if "command injection" in message_lower or "injection" in message_lower:
            has_shell_execution = any(
                marker in line_lower
                for marker in ("eval ", "exec ", "bash -c", "sh -c", "os.system", "subprocess")
            )
            if not has_shell_execution:
                severity = "warning"

        if category == "security" and severity == "error":
            has_hard_evidence = (
                bool(self.SECRET_VALUE_RE.search(line_text))
                or bool(self.DANGEROUS_CODE_RE.search(line_text))
                or any(marker in line_lower for marker in ("eval ", "exec ", "os.system", "subprocess"))
            )
            if not has_hard_evidence:
                severity = "warning"

        if "source" in suggestion_lower and ".env" in suggestion_lower:
            return None

        if severity == finding.severity and category == finding.category:
            return finding

        return ReviewFinding(
            rule_id=finding.rule_id,
            severity=severity,
            category=category,
            file_path=finding.file_path,
            line=finding.line,
            column=finding.column,
            message=finding.message,
            suggestion=finding.suggestion,
        )

    def _added_lines_by_file(self, file_diffs: list[FileDiff]) -> dict[str, set[int]]:
        result: dict[str, set[int]] = {}
        for fd in file_diffs:
            result[fd.new_path] = {
                line_no
                for hunk in fd.hunks
                for line_no, _ in hunk.added_lines
            }
        return result

    def _added_text_by_location(self, file_diffs: list[FileDiff]) -> dict[tuple[str, int], str]:
        result: dict[tuple[str, int], str] = {}
        for fd in file_diffs:
            for hunk in fd.hunks:
                for line_no, text in hunk.added_lines:
                    result[(fd.new_path, line_no)] = text
        return result

    def get_severity_filter(self, min_severity: str) -> list[str]:
        """Gibt alle Severity-Level ab min_severity zurück."""
        levels = ["info", "warning", "error"]
        try:
            idx = levels.index(min_severity)
            return levels[idx:]
        except ValueError:
            return ["error"]
