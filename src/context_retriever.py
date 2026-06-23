"""Context-Retriever – Holt Kontext aus dem Repository für bessere Reviews."""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from typing import Protocol

from src.diff_analyzer import FileDiff

logger = logging.getLogger(__name__)


class GitCommandRunner(Protocol):
    """Protokoll für Git-Befehlsausführung (ermöglicht Tests/Mocking)."""

    def run(self, cmd: list[str], cwd: str | None = None) -> str: ...


class SubprocessRunner:
    """Echte Git-Befehlsausführung via subprocess."""

    def run(self, cmd: list[str], cwd: str | None = None) -> str:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=cwd,
            timeout=30,
        )
        if result.returncode != 0:
            logger.warning("Git-Befehl fehlgeschlagen: %s\n%s", cmd, result.stderr)
            return ""
        return result.stdout.strip()


class ContextRetriever:
    """Holt relevanten Kontext aus dem Repository.

    Für jede geänderte Datei werden:
    - Der vollständige Inhalt der Datei (vor und nach dem Diff)
    - Nahegelegene verwandte Dateien (gleiches Verzeichnis, Imports)
    - Funktions-/Klassendefinitionen, die durch den Diff betroffen sind
    """

    def __init__(
        self,
        repo_path: str | Path,
        git_runner: GitCommandRunner | None = None,
    ) -> None:
        self.repo_path = Path(repo_path)
        self._git = git_runner or SubprocessRunner()

    # ──────────────────────────────────────────
    # Public API
    # ──────────────────────────────────────────

    def get_file_content(self, file_path: str, ref: str = "HEAD") -> str | None:
        """Gibt den Inhalt einer Datei aus einem bestimmten Git-Ref zurück."""
        try:
            return self._git.run(
                ["git", "show", f"{ref}:{file_path}"],
                cwd=str(self.repo_path),
            )
        except Exception:
            return None

    def get_new_file_content(self, file_path: str) -> str | None:
        """Gibt den Inhalt der neuen Version einer Datei zurück (Working Tree)."""
        full_path = self.repo_path / file_path
        if full_path.exists():
            return full_path.read_text()
        return None

    def get_surrounding_context(
        self,
        file_diff: FileDiff,
        context_lines: int = 5,
    ) -> dict[str, list[tuple[int, str]]]:
        """Holt Kontext-Zeilen vor/nach jedem Hunk für eine Datei."""
        content = self.get_file_content(file_diff.new_path)
        if content is None:
            return {}

        lines = content.split("\n")
        context: dict[str, list[tuple[int, str]]] = {}

        for i, hunk in enumerate(file_diff.hunks):
            start = max(0, hunk.new_start - context_lines - 1)
            end = min(
                len(lines),
                hunk.new_start + hunk.new_count + context_lines,
            )
            key = f"hunk_{i + 1}"
            context[key] = [
                (idx + 1, lines[idx]) for idx in range(start, end)
            ]

        return context

    def get_related_files(self, file_path: str, depth: int = 1) -> list[str]:
        """Findet verwandte Dateien (gleiches Verzeichnis, Imports)."""
        related: list[str] = []

        # 1. Gleiches Verzeichnis
        dir_path = Path(file_path).parent
        try:
            for f in (self.repo_path / dir_path).iterdir():
                if f.is_file() and f.suffix in (".py", ".js", ".ts", ".go", ".rs", ".java"):
                    rel = str(f.relative_to(self.repo_path))
                    if rel != file_path:
                        related.append(rel)
        except (FileNotFoundError, ValueError):
            pass

        # 2. Importierte Module erkennen (nur Python)
        if file_path.endswith(".py"):
            content = self.get_new_file_content(file_path)
            if content:
                import re
                for match in re.finditer(
                    r"^(?:from\s+(\S+)\s+import|import\s+(\S+))",
                    content,
                    re.MULTILINE,
                ):
                    module = match.group(1) or match.group(2)
                    # Konvertiere Modul-Pfad zu Dateipfad
                    mod_path = module.replace(".", "/") + ".py"
                    if (self.repo_path / mod_path).exists() and mod_path != file_path:
                        related.append(mod_path)

        return related[:15]  # max 15 verwandte Dateien

    def get_changed_functions(
        self,
        file_diff: FileDiff,
    ) -> list[dict[str, str | int]]:
        """Ermittelt, welche Funktionen/Klassen von Änderungen betroffen sind.

        Erkennt Python-Funktions-/Klassendefinitionen, die im Bereich
        der Hunks liegen.
        """
        content = self.get_new_file_content(file_diff.new_path)
        if content is None:
            return []

        lines = content.split("\n")
        functions: list[dict[str, str | int]] = []

        # Einfaches Pattern-Matching für Python/JS/TS-Funktionen
        func_pattern = (
            r"^(?:(?:async\s+)?(?:def|class|function|const\s+\w+\s*=\s*(?:async\s+)?"
            r"\(?[^)]*\)?\s*(?:=>)?)\s+(\w+))"
        )
        import re

        # Alle Definitionen finden
        definitions: list[tuple[int, str]] = []
        for idx, line in enumerate(lines, 1):
            match = re.match(func_pattern, line.strip())
            if match:
                definitions.append((idx, match.group(1)))

        # Prüfen, welche Definitionen von Hunks berührt werden
        for func_line, func_name in definitions:
            for hunk in file_diff.hunks:
                hunk_end = hunk.new_start + hunk.new_count
                if func_line >= hunk.new_start and func_line <= hunk_end:
                    functions.append({
                        "name": func_name,
                        "line": func_line,
                        "type": "function" if not func_name[0].isupper() else "class",
                    })
                    break

        return functions
