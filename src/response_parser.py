"""Response-Parser – Extrahiert strukturierte Findings aus LLM-Textausgaben."""

from __future__ import annotations

import logging
import re
from typing import Any

from src.rule_engine import ReviewFinding

logger = logging.getLogger(__name__)


class ResponseParser:
    """Parst die LLM-Textausgabe in strukturierte ReviewFinding-Objekte.

    Funktionsweise:
    1. Text an "FILE:"-Markern in Blöcke aufteilen
    2. Pro Block: LINE, SEVERITY, CATEGORY, MESSAGE, SUGGESTION
       zeilenweise per Prefix extrahieren
    3. Findings mit <5 Zeichen Nachricht oder fehlender Severity verwerfen
    """

    # Felder, die in einem Finding-Block vorkommen können
    FIELD_PREFIXES = {
        "line": ("LINE:", "Line:", "Zeile:"),
        "severity": ("SEVERITY:", "Severity:", "Art:"),
        "category": ("CATEGORY:", "Category:", "Kategorie:"),
        "message": ("MESSAGE:", "Message:", "Nachricht:", "MSG:"),
        "suggestion": ("SUGGESTION:", "Suggestion:", "Vorschlag:"),
    }

    def parse(self, llm_response: str, default_file: str = "") -> list[ReviewFinding]:
        """Parst die LLM-Antwort in eine Liste von Findings."""
        findings: list[ReviewFinding] = []

        # 1. Block-Parsing (primär)
        findings.extend(self._parse_blocks(llm_response, default_file))

        # 2. Fallback: Freitext-Parsing (wenn Block-Parsing nichts fand)
        if not findings:
            findings.extend(self._parse_freeform(llm_response, default_file))

        logger.info(
            "LLM-Response geparst: %d Findings",
            len(findings),
        )
        return findings

    def _parse_blocks(
        self,
        text: str,
        default_file: str,
    ) -> list[ReviewFinding]:
        """Teilt den Text an FILE: auf und parst jeden Block zeilenweise."""
        findings: list[ReviewFinding] = []
        text = text.replace("\r\n", "\n")

        blocks = re.split(
            r"(?:^|\n)\s*---\s*(?:\n|$)|\n\s*(?=FILE:)",
            text,
            flags=re.IGNORECASE,
        )

        for block in blocks:
            block = block.strip()
            if not block or block.upper().startswith("KEINE FINDINGS"):
                continue

            finding = self._parse_single_block(block, default_file)
            if finding is not None:
                findings.append(finding)

        return findings

    def _parse_single_block(
        self,
        block: str,
        default_file: str,
    ) -> ReviewFinding | None:
        """Parst einen einzelnen Finding-Block zeilenweise."""
        lines = block.split("\n")

        fields: dict[str, str] = {}
        current_field = None
        current_value: list[str] = []

        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue

            # Prüfe, ob die Zeile mit einem bekannten Prefix beginnt
            matched_field = self._match_field_prefix(stripped)

            if matched_field is not None:
                # Vorheriges Feld speichern
                if current_field is not None and current_value:
                    fields[current_field] = " ".join(current_value).strip()

                current_field = matched_field
                # Wert nach dem Prefix extrahieren
                value = self._strip_prefix(stripped, current_field)
                current_value = [value] if value else []
            elif current_field is not None:
                # Fortsetzung des aktuellen Feldes (z.B. mehrzeilige MESSAGE)
                current_value.append(stripped)

        # Letztes Feld speichern
        if current_field is not None and current_value:
            fields[current_field] = " ".join(current_value).strip()

        # Pflichtfelder prüfen
        file_path = fields.get("file", default_file)
        if not file_path:
            # Versuche FILE: aus der ersten Zeile zu extrahieren
            file_path = self._extract_file_from_line(block, default_file)

        severity = fields.get("severity", "").lower()
        message = fields.get("message", "")

        if not severity or len(message) < 5:
            return None

        # Normalisiere severity
        severity_map = {"fehler": "error", "warnung": "warning", "hinweis": "info"}
        severity = severity_map.get(severity, severity)

        # Line parsen
        line_str = fields.get("line", "")
        line = int(re.sub(r"\D", "", line_str)) if re.search(r"\d+", line_str) else None

        # Category
        category = fields.get("category", "style").lower()
        suggestion = self._clean_suggestion(fields.get("suggestion", ""))

        return ReviewFinding(
            rule_id="LLM-FINDING",
            severity=severity,
            category=category,
            file_path=file_path,
            line=line,
            column=None,
            message=message,
            suggestion=suggestion,
        )

    def _clean_suggestion(self, raw: str) -> str:
        """Extrahiert den verbesserten Code aus der Suggestion."""
        if not raw:
            return ""
        raw = re.sub(r"(?m)^\s*---\s*$", "", raw)
        # Entferne ```diff, ```python, ``` etc.
        raw = re.sub(r"```\w*", "", raw)
        # Bevorzuge die "+"-Zeile (neuer Code) falls vorhanden
        plus_lines = re.findall(r"^\s*\+(\s*.+)", raw, re.MULTILINE)
        if plus_lines:
            raw = "\n".join(l.strip() for l in plus_lines)
        else:
            # Sonst entferne +/- Prefixe
            raw = re.sub(r"^\s*[-+]\s*", "", raw, flags=re.MULTILINE)
        return raw.strip()[:200]

    def _match_field_prefix(self, line: str) -> str | None:
        """Erkennt, ob die Zeile mit einem Feld-Prefix beginnt."""
        for field, prefixes in self.FIELD_PREFIXES.items():
            for prefix in prefixes:
                if line.upper().startswith(prefix.upper()):
                    return field
        # Spezialfall: FILE: auch ohne Doppelpunkt-Variante
        if line.upper().startswith("FILE:"):
            return "file"
        return None

    def _strip_prefix(self, line: str, field: str) -> str:
        """Entfernt den Feld-Prefix und gibt den Wert zurück."""
        prefixes = self.FIELD_PREFIXES.get(field, ())
        if field == "file":
            prefixes = ("FILE:", "File:", "file:")

        for prefix in prefixes:
            if line.upper().startswith(prefix.upper()):
                return line[len(prefix):].strip()
        return line

    def _extract_file_from_line(self, block: str, default: str) -> str:
        """Extrahiert Dateiname aus FILE:-Zeile (Fallback)."""
        m = re.search(r"FILE:\s*(.+?)[\s\n]", block, re.IGNORECASE)
        return m.group(1).strip() if m else default

    def _parse_freeform(
        self,
        text: str,
        default_file: str,
    ) -> list[ReviewFinding]:
        """Fallback: Findet Dateireferenzen in Fließtext.

        Funktionsweise:
        1. Finde alle Backtick-quotierten Dateinamen (z.B. `config.yaml`)
        2. Extrahiere den umgebenden Satz als Message
        3. Suche nach Zeilennummern im Kontext
        """
        findings: list[ReviewFinding] = []
        lines = text.replace("\r\n", "\n").split("\n")

        current_finding: dict[str, str | int | None] = {}
        in_bullet = False

        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue

            # Bullet-Punkt erkennen
            is_bullet = bool(re.match(r"^[-*+]\s", stripped))

            if is_bullet:
                # Vorherigen Fund speichern
                finding = self._finalize_freeform(current_finding, default_file)
                if finding:
                    findings.append(finding)
                current_finding = {}
                in_bullet = True

                # Dateiname aus Backticks extrahieren
                file_match = re.search(r"`([^`]+?\.[^`]+)`", stripped)
                if file_match:
                    current_finding["file"] = file_match.group(1)
                elif in_bullet:
                    # Dateiname ohne Backticks (z.B. "In config.yaml,")
                    raw = re.sub(r"^[-*+]\s*(?:In\s+)?", "", stripped)
                    raw = re.sub(r"[,.:;].*$", "", raw).strip()
                    if "." in raw and "/" in raw:
                        current_finding["file"] = raw

                # Message = rest nach Dateireferenz
                msg = self._extract_msg_freeform(stripped)
                current_finding["message"] = msg

                # Zeilennummer
                line_m = re.search(r"(?:line|zeile|Zeile|Line)\s*(\d+)", stripped)
                if line_m:
                    current_finding["line"] = int(line_m.group(1))

            elif in_bullet and current_finding.get("line") is None:
                # Fortsetzung des Bullets (z.B. Sub-Bullet mit Line-Angabe)
                line_m = re.search(r"(?:line|zeile|Zeile|Line)\s*(\d+)", stripped)
                if line_m:
                    current_finding["line"] = int(line_m.group(1))
                # Sub-Bullets an die Message anhängen
                existing = current_finding.get("message", "")
                if existing and len(stripped) > 5:
                    current_finding["message"] = existing + " " + stripped

        # Letzten Fund speichern
        finding = self._finalize_freeform(current_finding, default_file)
        if finding:
            findings.append(finding)

        return findings

    def _extract_msg_freeform(self, line: str) -> str:
        """Extrahiert die Nachricht aus einer Bullet-Zeile."""
        # Entferne Bullet + "In "
        cleaned = re.sub(r"^[-*+]\s*(?:In\s+)?", "", line)
        # Entferne den Dateinamen (Backticks oder ohne)
        cleaned = re.sub(r"`[^`]+`\s*,?\s*", "", cleaned, count=1)
        # Fallback: wenn kein Backtick, entferne ersten Pfad
        if cleaned == re.sub(r"^[-*+]\s*(?:In\s+)?", "", line):
            cleaned = re.sub(r"^[/\w.-]+\.\w+\s*,?\s*", "", cleaned)
        cleaned = re.sub(r"^there\s+(?:is|are)\s+", "", cleaned, flags=re.IGNORECASE)
        return cleaned.strip().strip(':').strip()[:200]

    def _finalize_freeform(
        self,
        data: dict,
        default_file: str,
    ) -> ReviewFinding | None:
        """Erzeugt ein ReviewFinding aus gesammelten Freeform-Daten."""
        file_path = data.get("file", default_file)
        message = data.get("message", "")
        line = data.get("line")

        if not file_path or not message or len(str(message)) < 5:
            return None
        if "/" not in file_path and "." not in file_path:
            return None

        return ReviewFinding(
            rule_id="LLM-FINDING",
            severity="info",
            category="style",
            file_path=str(file_path),
            line=line,
            column=None,
            message=str(message),
            suggestion="",
        )
