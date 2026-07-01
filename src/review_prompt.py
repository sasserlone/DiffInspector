"""Prompt-Builder – Erzeugt strukturierte Prompts für Code-Reviews mit Ollama."""

from __future__ import annotations

from src.diff_analyzer import FileDiff, Hunk
from src.rule_engine import Rule

SYSTEM_PROMPT_TEMPLATE = """Du bist ein Senior-Entwickler im Code-Review.

DIFF-LESART: '-' = ALT (gelöscht), '+' = NEU (eingereichter Code).
Kommentiere nur konkrete Probleme im NEUEN Code und nur auf Zeilen, die im Diff mit '+' hinzugefügt wurden.

Finde nur belegbare Probleme:
1. Typos/Tippfehler – falsche Attribut-/Methodennamen (z.B. .teext statt .text)
2. Fehler/Bugs – Logikfehler, Edge Cases, off-by-one
3. Security – echte Secrets, Injection, unsichere API-Nutzung
4. Performance – klar ineffizienter neuer Code

Nicht melden:
- Spekulationen ohne direkte Evidenz im Diff
- "Keine Änderung nötig" oder Lob
- reine Konfigurationspräferenzen als Race Condition, Bug oder Error
- Style/Maintainability als error
- Vorschläge, die `.env` per `source` als Shell-Code ausführen

Severity-Regeln:
- error nur für nachweisbare Runtime-Bugs, Syntaxfehler, echte Secrets oder klar gefährliche Security-Probleme
- warning für plausible Risiken, unsichere Muster oder robuste Verbesserungen
- info für kleine Hinweise
- Wörter wie "könnte", "möglicherweise", "falls" dürfen nie SEVERITY: error sein

## Format (EXAKT einhalten!):
FILE: <pfad>
LINE: <zeile>
SEVERITY: <error|warning|info>
CATEGORY: <bug|security|performance|maintainability|style>
MESSAGE: <1 Satz>
SUGGESTION: <1-2 Zeilen Code>

Trenne Funde mit ---. Max 3 Findings.
Wenn alles gut ist: KEINE FINDINGS
"""


def build_diff_prompt(file_diff: FileDiff, rules: list[Rule] | None = None) -> str:
    """Baut den User-Prompt für eine einzelne Datei."""
    lines: list[str] = []
    lines.append(f"## Datei: {file_diff.new_path} (Status: {file_diff.status})")
    lines.append("")

    if rules:
        lines.append("### Besonders zu beachten:")
        for rule in rules:
            lines.append(f"- [{rule.severity.upper()}] {rule.pattern}")
        lines.append("")

    lines.append("### Diff:")
    lines.append("```diff")
    lines.append(file_diff.raw_diff)
    lines.append("```")
    lines.append("")

    return "\n".join(lines)


def build_combined_prompt(
    file_diffs: list[FileDiff],
    rules: list[Rule] | None = None,
    context: dict[str, str] | None = None,
) -> tuple[str, str]:
    """Baut System-Prompt und User-Prompt für ein Batch-Review.

    Returns:
        (system_prompt, user_prompt)
    """
    system = SYSTEM_PROMPT_TEMPLATE

    user_parts: list[str] = []
    user_parts.append("# Code-Review: Änderungsübersicht\n")

    if context and context.get("summary"):
        user_parts.append(f"Gesamt: {context['summary']}\n")

    user_parts.append("---\n")

    for fd in file_diffs:
        user_parts.append(build_diff_prompt(fd, rules))
        user_parts.append("---\n")

    return system, "\n".join(user_parts)


def build_file_prompt(
    file_diff: FileDiff,
    context_lines: dict[str, list[tuple[int, str]]] | None = None,
    related_functions: list[dict[str, str | int]] | None = None,
    rules: list[Rule] | None = None,
) -> str:
    """Baut einen detaillierten Prompt für eine einzelne Datei mit Kontext."""
    parts: list[str] = []
    parts.append(f"## Datei: {file_diff.new_path}")
    parts.append(f"Status: {file_diff.status}")
    parts.append("")

    added_line_numbers = [
        str(line_no)
        for hunk in file_diff.hunks
        for line_no, _ in hunk.added_lines
    ]
    if added_line_numbers:
        parts.append("### Erlaubte Kommentarzeilen")
        parts.append(", ".join(added_line_numbers))
        parts.append("")

    if related_functions:
        parts.append("### Betroffene Funktionen/Klassen:")
        for func in related_functions:
            parts.append(f"- {func['type'].title()}: `{func['name']}` (Zeile {func['line']})")
        parts.append("")

    if context_lines:
        parts.append("### Kontext (mit Änderungen):")
        for hunk_name, lines in context_lines.items():
            parts.append(f"#### {hunk_name}:")
            parts.append("```")
            for line_num, content in lines:
                marker = " "
                parts.append(f"{marker}{line_num:4d} {content}")
            parts.append("```")
            parts.append("")

    if rules:
        parts.append("### Anwendbare Regeln:")
        for rule in rules:
            parts.append(f"- [{rule.severity.upper()}] {rule.id}: {rule.pattern}")
        parts.append("")

    parts.append("### Diff:")
    parts.append("```diff")
    parts.append(file_diff.raw_diff)
    parts.append("```")

    return "\n".join(parts)
