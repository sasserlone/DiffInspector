"""Diff-Analyzer – Parst Git-Diffs und extrahiert relevante Informationen."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────
# Datenmodelle
# ──────────────────────────────────────────────


@dataclass
class Hunk:
    """Ein einzelner Hunk (zusammenhängender Änderungsblock) in einem Diff."""

    old_start: int
    old_count: int
    new_start: int
    new_count: int
    lines: list[str] = field(default_factory=list)

    @property
    def added_lines(self) -> list[tuple[int, str]]:
        """Gibt (Zeilenummer, Inhalt) für hinzugefügte Zeilen zurück."""
        result: list[tuple[int, str]] = []
        line_num = self.new_start
        for line in self.lines:
            if line.startswith("+") and not line.startswith("+++"):
                result.append((line_num, line[1:]))
                line_num += 1
            elif line.startswith("-"):
                pass  # keine Zeilennummer im neuen File
            else:
                line_num += 1
        return result

    @property
    def removed_lines(self) -> list[tuple[int, str]]:
        """Gibt (Zeilenummer, Inhalt) für entfernte Zeilen zurück."""
        result: list[tuple[int, str]] = []
        line_num = self.old_start
        for line in self.lines:
            if line.startswith("-") and not line.startswith("---"):
                result.append((line_num, line[1:]))
                line_num += 1
            elif line.startswith("+"):
                pass
            else:
                line_num += 1
        return result


@dataclass
class FileDiff:
    """Änderungen an einer einzelnen Datei."""

    old_path: str
    new_path: str
    status: str  # added, modified, deleted, renamed
    hunks: list[Hunk] = field(default_factory=list)
    raw_diff: str = ""

    @property
    def is_binary(self) -> bool:
        """Prüft, ob der Diff auf eine Binärdatei verweist."""
        return "Binary files" in self.raw_diff

    @property
    def extension(self) -> str:
        return Path(self.new_path).suffix

    @property
    def total_changes(self) -> int:
        added = sum(len(h.added_lines) for h in self.hunks)
        removed = sum(len(h.removed_lines) for h in self.hunks)
        return added + removed


@dataclass
class DiffResult:
    """Das Ergebnis der vollständigen Diff-Analyse."""

    files: list[FileDiff] = field(default_factory=list)
    summary: str = ""

    @property
    def total_files(self) -> int:
        return len(self.files)

    @property
    def total_additions(self) -> int:
        return sum(f.total_changes for f in self.files)

    @property
    def max_file_size_exceeded(self) -> bool:
        return any(f.total_changes > 1000 for f in self.files)


# ──────────────────────────────────────────────
# Parser
# ──────────────────────────────────────────────

HUNK_HEADER_RE = re.compile(
    r"^@@ -(\d+),?(\d*) \+(\d+),?(\d*) @@(?:\s+(.*))?"
)
FILE_HEADER_RE = re.compile(
    r"^(?:diff --git a/(.+?) b/(.+?)|Index: (.+?))$"
)
NEW_FILE_RE = re.compile(r"^new file mode")
DELETED_FILE_RE = re.compile(r"^deleted file mode")
RENAME_RE = re.compile(r"^rename from")


class DiffParser:
    """Parst einen rohen Git-Diff-String in strukturierte Daten."""

    def parse(self, raw_diff: str) -> DiffResult:
        """Parst den gesamten Diff-String."""
        result = DiffResult()

        if not raw_diff.strip():
            return result

        # Normalisiere Zeilenumbrüche
        raw_diff = raw_diff.replace("\r\n", "\n")
        lines = raw_diff.split("\n")

        current_file: FileDiff | None = None
        current_hunk: Hunk | None = None
        # Mutable-Container für Closure: clear/append statt Neu-Zuweisung
        raw_lines: list[str] = []

        def flush_raw() -> None:
            if current_file is not None:
                current_file.raw_diff = "\n".join(raw_lines)

        for line in lines:
            # Dateikopf
            file_match = FILE_HEADER_RE.match(line)
            if file_match:
                flush_raw()
                raw_lines.clear()
                raw_lines.append(line)

                current_hunk = None
                old_path = file_match.group(1) or file_match.group(3) or ""
                new_path = file_match.group(2) or file_match.group(3) or ""
                current_file = FileDiff(
                    old_path=old_path,
                    new_path=new_path,
                    status="modified",
                )
                result.files.append(current_file)
                continue

            if current_file is None:
                continue

            raw_lines.append(line)

            # Datei-Status erkennen
            if line.startswith("new file mode"):
                current_file.status = "added"
                continue
            if line.startswith("deleted file mode"):
                current_file.status = "deleted"
                continue
            if line.startswith("rename from"):
                current_file.status = "renamed"
                current_file.old_path = line.split("rename from ")[1]
                continue
            if line.startswith("rename to"):
                if current_file.status == "renamed":
                    current_file.new_path = line.split("rename to ")[1]
                continue
            if line.startswith("--- /dev/null"):
                current_file.status = "added"
                continue
            if line.startswith("+++ /dev/null"):
                current_file.status = "deleted"
                continue

            # Binary
            if line.startswith("Binary files"):
                current_file.is_binary
                continue

            # Hunk-Header
            hunk_match = HUNK_HEADER_RE.match(line)
            if hunk_match:
                old_start = int(hunk_match.group(1))
                old_count_str = hunk_match.group(2)
                new_start = int(hunk_match.group(3))
                new_count_str = hunk_match.group(4)

                old_count = int(old_count_str) if old_count_str else 1
                new_count = int(new_count_str) if new_count_str else 1

                current_hunk = Hunk(
                    old_start=old_start,
                    old_count=old_count,
                    new_start=new_start,
                    new_count=new_count,
                )
                current_file.hunks.append(current_hunk)
                continue

            # Zeilen innerhalb eines Hunks
            if current_hunk is not None:
                current_hunk.lines.append(line)

        # Letzte Datei finalisieren + Summary
        flush_raw()
        result.summary = self._generate_summary(result)
        return result

    def parse_file(self, diff_text: str, file_path: str) -> FileDiff:
        """Parst den Diff einer einzelnen Datei."""
        # Füge künstlichen Dateikopf hinzu, falls nötig
        if not diff_text.startswith("diff --git"):
            header = f"diff --git a/{file_path} b/{file_path}\n"
            diff_text = header + diff_text
        result = self.parse(diff_text)
        return result.files[0] if result.files else FileDiff(
            old_path=file_path,
            new_path=file_path,
            status="modified",
        )

    def _generate_summary(self, result: DiffResult) -> str:
        """Erzeugt eine menschenlesbare Zusammenfassung."""
        total_added = 0
        total_removed = 0
        counts: dict[str, int] = {}

        for f in result.files:
            added = sum(len(h.added_lines) for h in f.hunks)
            removed = sum(len(h.removed_lines) for h in f.hunks)
            total_added += added
            total_removed += removed
            counts[f.status] = counts.get(f.status, 0) + 1

        parts = [
            f"{result.total_files} Dateien geändert",
        ]
        for status, count in sorted(counts.items()):
            parts.append(f"{count} {status}")
        parts.append(f"{total_added} Einfügungen(+)")
        parts.append(f"{total_removed} Löschungen(-)")

        return ", ".join(parts)
