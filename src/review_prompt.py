"""Prompt-Builder – Erzeugt strukturierte Prompts für Code-Reviews mit Ollama."""

from __future__ import annotations

from src.diff_analyzer import FileDiff, Hunk
from src.rule_engine import Rule

SYSTEM_PROMPT_TEMPLATE = """Du bist ein erfahrener Senior-Entwickler, der einen sorgfältigen Code-Review durchführt.

## Deine Aufgabe
Analysiere den bereitgestellten Code-Diff und finde:
1. **Fehler & Bugs** – Logische Fehler, Race Conditions, Edge Cases
2. **Sicherheitslücken** – Injection, Secrets, unsichere API-Nutzung
3. **Performance-Probleme** – Ineffiziente Algorithmen, unnötige Operationen
4. **Wartbarkeit** – Komplexität, fehlende Dokumentation, Code-Duplikate
5. **Code-Style** – Verstöße gegen Projekt-Konventionen

## Regeln für deine Antwort
- Sei **konstruktiv und präzise**. Kein Lob für einfachen Code.
- Jeder Kommentar muss eine **konkrete Zeile oder einen Bereich** referenzieren.
- Gib bei jedem Fund eine **konkrete Verbesserung** als Code-Beispiel.
- Wenn du keinen Fehler findest, sag "KEINE FINDINGS" am Ende.
- Priorisiere **Fehler und Sicherheit** vor Style.

## Antwortformat
Gib deine Review-Kommentare im folgenden Format aus:

```
FILE: <dateipfad>
LINE: <zeilennummer>
SEVERITY: <error|warning|info>
CATEGORY: <bug|security|performance|maintainability|style>
MESSAGE: <Kurzbeschreibung des Problems>
SUGGESTION:
```verbesserter Code```
```

Trenne mehrere Funde mit "---".
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
