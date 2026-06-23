"""Review-Orchestrator – Steuert den gesamten Review-Prozess."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from src.config import AppConfig
from src.context_retriever import ContextRetriever
from src.diff_analyzer import DiffParser, DiffResult, FileDiff
from src.git_provider import GitProvider, LocalGitProvider, MergeRequest, ReviewComment
from src.llm_client import LLMClient
from src.response_parser import ResponseParser
from src.review_prompt import build_file_prompt
from src.rule_engine import ReviewFinding, ReviewResult, RuleEngine

logger = logging.getLogger(__name__)


class ReviewOrchestrator:
    """Zentrale Steuerungsklasse für den Review-Prozess.

    Ablauf:
    1. Diff holen (von PR/CLI)
    2. Diff parsen
    3. Für jede Datei: Kontext holen + Regeln anwenden
    4. LLM-Prompt bauen + an Ollama senden
    5. Antwort parsen + mit Rule Engine validieren
    6. Kommentare generieren + zurück in den PR/posteten
    """

    def __init__(
        self,
        config: AppConfig,
        git_provider: GitProvider | None = None,
        repo_path: str | Path | None = None,
    ) -> None:
        self.config = config
        self.llm = LLMClient(config.ollama)
        self.parser = DiffParser()
        self.response_parser = ResponseParser()
        self.rule_engine = RuleEngine(config.rules.rules_dir)
        self.repo_path = Path(repo_path or Path.cwd())

        # Git-Provider
        if git_provider is not None:
            self.git = git_provider
        else:
            self.git = LocalGitProvider(self.repo_path, config.git)

        # Context-Retriever
        self.context = ContextRetriever(self.repo_path)

    # ──────────────────────────────────────────
    # Haupt-Review-Methoden
    # ──────────────────────────────────────────

    def review_diff(self, diff_text: str) -> ReviewResult:
        """Führt ein Review auf einem rohen Diff-Text durch."""
        logger.info("Starte Code-Review (%d Zeilen Diff)", len(diff_text.split("\n")))

        # 1. Diff parsen
        diff_result = self.parser.parse(diff_text)
        logger.info("Diff geparst: %s", diff_result.summary)

        if not diff_result.files:
            logger.warning("Keine änderungen im Diff gefunden.")
            return ReviewResult(summary="Keine Änderungen gefunden.")

        # 2. Regel-Profil laden
        self.rule_engine.load_profile("default", self.config.rules.profiles["default"])

        # 3. Chunking bei großen Diffs
        all_findings: list[ReviewFinding] = []
        llm_feedback_parts: list[str] = []

        total_changes = sum(f.total_changes for f in diff_result.files)
        needs_chunking = total_changes > self.config.review.max_diff_lines

        if needs_chunking:
            logger.info(
                "Diff ist groß (%d Zeilen), verwende Chunking (%d Zeilen/Chunk)",
                total_changes,
                self.config.review.chunk_size,
            )
            file_groups = self._chunk_files(diff_result.files)
        else:
            file_groups = [diff_result.files]

        # 4. Review pro Chunk
        for i, file_group in enumerate(file_groups, 1):
            if len(file_groups) > 1:
                logger.info("Review Chunk %d/%d (%d Dateien)", i, len(file_groups), len(file_group))

            result = self._review_files(file_group)
            all_findings.extend(result.findings)
            if result.llm_feedback:
                llm_feedback_parts.append(result.llm_feedback)

        # 5. Findings validieren/deduplizieren
        all_findings = self.rule_engine.validate_findings(all_findings)

        final = ReviewResult(
            findings=all_findings,
            summary=diff_result.summary,
            llm_feedback="\n\n".join(llm_feedback_parts),
        )
        logger.info(
            "Review abgeschlossen: %d Errors, %d Warnings, %d Infos (Score: %d)",
            final.error_count,
            final.warning_count,
            final.info_count,
            final.score,
        )
        return final

    def review_merge_request(self, mr: MergeRequest) -> ReviewResult:
        """Holt den Diff eines MR und führt ein Review durch."""
        logger.info("Review für MR #%s: %s", mr.id, mr.title)

        diff_text = self.git.get_diff(mr)
        result = self.review_diff(diff_text)

        # Poste Kommentare
        if result.findings:
            comments = self._findings_to_comments(result.findings, mr)
            posted = self.git.post_comments(mr, comments)
            logger.info("%d Kommentare gepostet", posted)

        # Update Status
        if result.error_count > 0:
            self.git.update_status(
                mr,
                "failure",
                f"{result.error_count} Fehler, {result.warning_count} Warnungen gefunden",
            )
        elif result.warning_count > 0:
            self.git.update_status(
                mr,
                "success",
                f"{result.warning_count} Warnungen, {result.info_count} Hinweise",
            )
        else:
            self.git.update_status(
                mr,
                "success",
                f"✅ Keine Probleme gefunden (Score: {result.score})",
            )

        return result

    # ──────────────────────────────────────────
    # Interne Methoden
    # ──────────────────────────────────────────

    def _review_files(self, file_diffs: list[FileDiff]) -> ReviewResult:
        """Führt ein Review für eine Liste von Datei-Diffs durch."""
        findings: list[ReviewFinding] = []
        llm_parts: list[str] = []

        for fd in file_diffs:
            # Binärdateien überspringen
            if fd.is_binary:
                logger.debug("Überspringe Binärdatei: %s", fd.new_path)
                continue

            logger.debug("Review: %s (%d Änderungen)", fd.new_path, fd.total_changes)

            # Kontext holen
            context = self.context.get_surrounding_context(fd)
            functions = self.context.get_changed_functions(fd)

            # Relevante Regeln
            rules = self.rule_engine.get_relevant_rules(fd)

            # Prompt bauen
            prompt = build_file_prompt(
                file_diff=fd,
                context_lines=context,
                related_functions=functions,
                rules=rules,
            )

            try:
                # LLM aufrufen
                system_prompt = (
                    "Du bist ein Senior-Entwickler, der einen Code-Review durchführt.\n"
                    "Analysiere den folgenden Diff und gib strukturierte Kommentare aus.\n"
                    "Verwende das Format:\n"
                    "FILE: <pfad>\nLINE: <zeile>\nSEVERITY: <error|warning|info>\n"
                    "CATEGORY: <bug|security|performance|maintainability|style>\n"
                    "MESSAGE: <nachricht>\n"
                    "SUGGESTION:\n```\n<code>\n```\n"
                )

                llm_response = self.llm.generate(
                    system_prompt=system_prompt,
                    user_prompt=prompt,
                )
                llm_parts.append(llm_response)

                # Response parsen
                file_findings = self.response_parser.parse(llm_response, fd.new_path)
                findings.extend(file_findings)

            except Exception as e:
                logger.error("Fehler beim Review von %s: %s", fd.new_path, e)

        return ReviewResult(
            findings=findings,
            llm_feedback="\n\n".join(llm_parts),
        )

    def _chunk_files(self, files: list[FileDiff]) -> list[list[FileDiff]]:
        """Teilt viele Dateien in Chunks auf (für große Diffs)."""
        chunks: list[list[FileDiff]] = []
        current: list[FileDiff] = []
        current_lines = 0

        for fd in files:
            file_lines = fd.total_changes
            if current_lines + file_lines > self.config.review.chunk_size and current:
                chunks.append(current)
                current = []
                current_lines = 0
            current.append(fd)
            current_lines += file_lines

        if current:
            chunks.append(current)

        return chunks

    def _findings_to_comments(
        self,
        findings: list[ReviewFinding],
        mr: MergeRequest,
    ) -> list[ReviewComment]:
        """Konvertiert Findings in ReviewComment-Objekte."""
        return [
            ReviewComment(
                file_path=f.file_path,
                line=f.line,
                body=self._format_comment(f),
                side="RIGHT",
            )
            for f in findings
        ]

    def _format_comment(self, finding: ReviewFinding) -> str:
        """Formatiert ein Finding als GitHub/GitLab-kompatiblen Kommentar."""
        severity_icons = {
            "error": "🔴",
            "warning": "🟡",
            "info": "🔵",
        }
        icon = severity_icons.get(finding.severity, "⚪")

        lines = [
            f"{icon} **{finding.severity.upper()}** | {finding.category}",
            "",
            finding.message,
        ]
        if finding.suggestion:
            lines.extend([
                "",
                "**Vorschlag:**",
                f"```\n{finding.suggestion}\n```",
            ])
        lines.append("")
        lines.append(f"--- *🤖 AI Code Review ({finding.rule_id})*")
        return "\n".join(lines)
