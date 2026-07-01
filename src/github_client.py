"""GitHub-Integration für den Code Review Agent."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from src.git_provider import (
    GitProvider,
    MergeRequest,
    ReviewComment,
)

logger = logging.getLogger(__name__)


class GitHubProvider(GitProvider):
    """Integration mit GitHub via PyGithub oder REST API."""

    def __init__(
        self,
        repo_path: str | Path,
        token: str | None = None,
    ) -> None:
        self.repo_path = Path(repo_path)
        self.token = token or os.environ.get("GITHUB_TOKEN") or ""
        self._repo = None

        if not self.token:
            logger.warning(
                "Kein GITHUB_TOKEN gesetzt. "
                "Setze die Umgebungsvariable GITHUB_TOKEN für GitHub-Integration."
            )

    def _get_repo_and_pr(self, mr: MergeRequest) -> tuple[Any, Any]:
        """Holt Repository und PR-Objekt von GitHub."""
        try:
            from github import Github, GithubIntegration
        except ImportError:
            raise ImportError(
                "PyGithub ist nicht installiert. "
                "Installiere es mit: pip install code-review-agent[github]"
            )

        g = Github(self.token)
        # Extrahiere owner/repo aus dem Diff-URL oder verwende Kontext
        # Fallback: lokales Git Remote parsen
        if mr.diff_url:
            # URL-Format: https://github.com/owner/repo/pull/123
            parts = mr.diff_url.replace("https://github.com/", "").split("/")
            owner, repo_name = parts[0], parts[1]
        else:
            owner, repo_name = self._parse_remote()

        repo = g.get_repo(f"{owner}/{repo_name}")
        pr = repo.get_pull(int(mr.id))
        return repo, pr

    def get_diff(self, mr: MergeRequest) -> str:
        """Holt den Diff eines Pull Requests über die GitHub API."""
        _, pr = self._get_repo_and_pr(mr)

        # Diff über API als Text holen
        import requests
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github.v3.diff",
        }
        url = pr.url.replace("api.github.com/repos", "api.github.com/repos") + "/files"
        # Besser: pr.diff_url direkt verwenden
        diff_url = pr.diff_url
        logger.info("Hole Diff von: %s", diff_url)

        resp = requests.get(diff_url, headers=headers, timeout=60)
        resp.raise_for_status()
        return resp.text

    def post_comments(self, mr: MergeRequest, comments: list[ReviewComment]) -> int:
        """Postet Inline-Kommentare als PR Review."""
        _, pr = self._get_repo_and_pr(mr)
        latest_commit = pr.get_commits().reversed[0]

        review_comments = []
        general_comments = []
        for c in comments:
            if not c.file_path or c.line is None:
                general_comments.append(c.body)
                continue
            review_comments.append({
                "path": c.file_path,
                "line": c.line,
                "body": c.body,
                "commit_id": c.commit_id or latest_commit.sha,
                "side": c.side,
            })

        if review_comments:
            pr.create_review(
                commit=latest_commit,
                body="\n\n---\n\n".join(general_comments)
                or "🤖 **AI Code Review** – Automatisch generierte Review-Kommentare.",
                event="COMMENT",
                comments=review_comments,
            )
        elif general_comments:
            pr.create_issue_comment("\n\n---\n\n".join(general_comments))

        logger.info("%d Kommentare an PR #%s gesendet", len(comments), mr.id)
        return len(comments)

    def update_status(
        self,
        mr: MergeRequest,
        status: str,
        description: str,
    ) -> None:
        """Setzt den Commit-Status auf dem neuesten Commit."""
        _, pr = self._get_repo_and_pr(mr)
        latest_commit = pr.get_commits().reversed[0]

        state_map = {
            "pending": "pending",
            "success": "success",
            "failure": "failure",
            "error": "error",
        }
        state = state_map.get(status, "pending")

        latest_commit.create_status(
            state=state,
            description=description[:140],
            context="code-review-agent 🤖",
        )
        logger.info("Status '%s' für PR #%s gesetzt", state, mr.id)

    def _parse_remote(self) -> tuple[str, str]:
        """Parst owner/repo aus 'git remote -v'."""
        import subprocess
        result = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
            cwd=str(self.repo_path),
            timeout=10,
        )
        url = result.stdout.strip()

        # Formate: git@github.com:owner/repo.git oder https://github.com/owner/repo
        if "github.com" not in url:
            raise ValueError("Kein GitHub-Remote gefunden")

        if url.startswith("git@"):
            path = url.split("github.com:")[1]
        else:
            path = url.split("github.com/")[1]

        path = path.replace(".git", "")
        parts = path.split("/")
        return parts[0], parts[1]
