"""Response-Parser – Extrahiert strukturierte Findings aus LLM-Textausgaben."""

from __future__ import annotations

import logging
import re
from typing import Any

from src.rule_engine import ReviewFinding

logger = logging.getLogger(__name__)

# Regex für das erwartete Antwortformat
FINDING_BLOCK_RE = re.compile(
    r"FILE:\s*(.+?)\s*\n"
    r"LINE:\s*(\d+)\s*\n"
    r"SEVERITY:\s*(error|warning|info)\s*\n"
    r"CATEGORY:\s*(bug|security|performance|maintainability|style)\s*\n"
    r"MESSAGE:\s*(.+?)\s*\n"
    r"(?:SUGGESTION:\s*\n?```(?:\w+)?\n?(.*?)```)?",
    re.DOTALL,
)

# Alternativ: nummerierte Liste
LIST_FINDING_RE = re.compile(
    r"(?:\d+[.)]\s*)?\*\*?(?:Datei|File)[*:]?\s*(.+?)[\n\r]+"
    r"(?:\*\*?Zeile|Line)[*:]?\s*(\d+|N/A)[\n\r]+"
    r"(?:\*\*?Art|Severity|Typ)[*:]?\s*(Fehler|Warnung|Hinweis|error|warning|info)[\n\r]+"
    r"(?:\*\*?Kategorie|Category)[*:]?\s*(.+?)[\n\r]+"
    r"(?:\*\*?Nachricht|Message)[*:]?\s*(.+?)(?=(?:\n\n|\Z|\d+[.)]))",
    re.DOTALL,
)


class ResponseParser:
    """Parst die LLM-Textausgabe in strukturierte ReviewFinding-Objekte."""

    def parse(self, llm_response: str, default_file: str = "") -> list[ReviewFinding]:
        """Parst die LLM-Antwort in eine Liste von Findings."""
        findings: list[ReviewFinding] = []

        # Versuche strukturiertes Format
        findings.extend(self._parse_structured(llm_response, default_file))

        # Fallback: Versuche Listen-Format
        if not findings:
            findings.extend(self._parse_list_format(llm_response, default_file))

        # Fallback: Freitext-Parsing
        if not findings:
            findings.extend(self._parse_freeform(llm_response, default_file))

        logger.info(
            "LLM-Response geparst: %d Findings (%d strukturiert)",
            len(findings),
            len([f for f in findings if f.rule_id]),
        )
        return findings

    def _parse_structured(
        self,
        text: str,
        default_file: str,
    ) -> list[ReviewFinding]:
        """Parst das strukturierte FILE/LINE/SEVERITY/... Format."""
        findings: list[ReviewFinding] = []

        for match in FINDING_BLOCK_RE.finditer(text):
            file_path = match.group(1).strip() or default_file
            line_str = match.group(2).strip()
            line = int(line_str) if line_str.isdigit() else None
            severity = match.group(3).strip().lower()
            category = match.group(4).strip().lower()
            message = match.group(5).strip()
            suggestion = (match.group(6) or "").strip()

            # Normalisiere severity
            severity_map = {"fehler": "error", "warnung": "warning", "hinweis": "info"}
            severity = severity_map.get(severity, severity)

            findings.append(ReviewFinding(
                rule_id="LLM-FINDING",
                severity=severity,
                category=category,
                file_path=file_path,
                line=line,
                column=None,
                message=message,
                suggestion=suggestion,
            ))

        return findings

    def _parse_list_format(
        self,
        text: str,
        default_file: str,
    ) -> list[ReviewFinding]:
        """Parst nummerierte/ungeordnete Listen."""
        findings: list[ReviewFinding] = []

        for match in LIST_FINDING_RE.finditer(text):
            file_path = match.group(1).strip() or default_file
            line_str = match.group(2).strip()
            line = int(line_str) if line_str.isdigit() else None
            severity_raw = match.group(3).strip().lower()
            category = match.group(4).strip().lower()
            message = match.group(5).strip()

            severity_map = {
                "fehler": "error", "warnung": "warning", "hinweis": "info",
                "error": "error", "warning": "warning", "info": "info",
            }
            severity = severity_map.get(severity_raw, "info")

            findings.append(ReviewFinding(
                rule_id="LLM-FINDING",
                severity=severity,
                category=category,
                file_path=file_path,
                line=line,
                column=None,
                message=message,
                suggestion="",
            ))

        return findings

    def _parse_freeform(
        self,
        text: str,
        default_file: str,
    ) -> list[ReviewFinding]:
        """Fallback: Parst Freitext nach bekannten Mustern."""
        findings: list[ReviewFinding] = []

        # Suche nach "-\s*" Bullet Points mit Dateireferenzen
        bullet_pattern = re.compile(
            r"[-*]\s*(?:\[(error|warning|info)\]\s*)?"
            r"(?:In\s+)?`?([^`\n]+?)`?(?::(\d+))?\s*[:-]\s*(.+)",
            re.IGNORECASE,
        )

        for match in bullet_pattern.finditer(text):
            severity = (match.group(1) or "info").strip().lower()
            file_path = (match.group(2) or default_file).strip()
            line_str = match.group(3)
            line = int(line_str) if line_str else None
            message = match.group(4).strip()

            findings.append(ReviewFinding(
                rule_id="LLM-FINDING",
                severity=severity,
                category="style",
                file_path=file_path,
                line=line,
                column=None,
                message=message,
                suggestion="",
            ))

        return findings
