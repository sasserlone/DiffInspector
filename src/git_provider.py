"""Abstraktion für Git-Provider (GitHub, GitLab, lokales Git)."""

from __future__ import annotations

import logging
import subprocess
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.config import GitConfig

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────
# Datenmodelle
# ──────────────────────────────────────────────


@dataclass
class MergeRequest:
    """Repräsentiert einen Pull Request / Merge Request."""

    id: str
    title: str
    description: str
    source_branch: str
    target_branch: str
    author: str
    diff_url: str = ""
    provider: str = ""  # "github" | "gitlab" | "local"


@dataclass
class ReviewComment:
    """Ein einzelner Review-Kommentar."""

    file_path: str
    line: int | None
    body: str
    commit_id: str | None = None
    side: str = "RIGHT"  # LEFT | RIGHT (welche Seite im Diff)


@dataclass
class PatchResult:
    """Ergebnis einer lokalen Patch-Operation."""

    success: bool
    diff_text: str = ""
    error: str = ""


# ──────────────────────────────────────────────
# Abstrakte Basisklasse
# ──────────────────────────────────────────────


class GitProvider(ABC):
    """Abstrakte Schnittstelle für Git-Provider."""

    @abstractmethod
    def get_diff(self, mr: MergeRequest) -> str:
        """Holt den vollständigen Diff eines Merge Requests."""
        ...

    @abstractmethod
    def post_comments(self, mr: MergeRequest, comments: list[ReviewComment]) -> int:
        """Postet Review-Kommentare in den MR. Gibt die Anzahl zurück."""
        ...

    @abstractmethod
    def update_status(
        self,
        mr: MergeRequest,
        status: str,
        description: str,
    ) -> None:
        """Setzt den PR/MR-Status (z.B. 'pending', 'success', 'failure')."""
        ...


# ──────────────────────────────────────────────
# Lokales Git
# ──────────────────────────────────────────────


class LocalGitProvider(GitProvider):
    """Nutzt lokales Git für Reviews im Working Tree oder zwischen Branches."""

    def __init__(self, repo_path: str | Path, config: GitConfig | None = None) -> None:
        self.repo_path = Path(repo_path)
        self.config = config or GitConfig()

    def get_diff(self, mr: MergeRequest | None = None) -> str:
        """Holt den Diff zwischen Branches oder unstaged changes."""
        if mr is not None:
            return self._run(
                ["git", "diff", f"{mr.target_branch}..{mr.source_branch}"]
            )
        # Default: unstaged + staged changes
        unstaged = self._run(["git", "diff"])
        staged = self._run(["git", "diff", "--cached"])
        return f"{unstaged}\n{staged}" if unstaged and staged else (unstaged or staged)

    def post_comments(self, mr: MergeRequest, comments: list[ReviewComment]) -> int:
        """Lokales Git kann keine Kommentare posten – gibt sie stattdessen aus."""
        for c in comments:
            loc = f"{c.file_path}:{c.line}" if c.line else c.file_path
            print(f"\n--- {loc} ---\n{c.body}\n")
        return len(comments)

    def update_status(
        self,
        mr: MergeRequest,
        status: str,
        description: str,
    ) -> None:
        logger.info(
            "[LocalGit] Status: %s – %s (für %s)",
            status, description, mr.id,
        )

    def _run(self, cmd: list[str]) -> str:
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                cwd=str(self.repo_path),
                timeout=30,
            )
            return result.stdout.strip()
        except subprocess.TimeoutExpired:
            logger.error("Git-Befehl zeitüberschritten: %s", cmd)
            return ""
        except Exception as e:
            logger.error("Git-Fehler: %s", e)
            return ""
