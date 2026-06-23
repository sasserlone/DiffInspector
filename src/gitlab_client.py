"""GitLab-Integration für den Code Review Agent."""

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


class GitLabProvider(GitProvider):
    """Integration mit GitLab via python-gitlab oder REST API."""

    def __init__(
        self,
        repo_path: str | Path,
        token: str | None = None,
        project_id: str | None = None,
    ) -> None:
        self.repo_path = Path(repo_path)
        self.token = token or os.environ.get("GITLAB_TOKEN") or ""
        self.project_id = project_id or os.environ.get("CI_PROJECT_ID", "")
        self.url = os.environ.get("CI_SERVER_URL", "https://gitlab.com")
        self._gl = None

        if not self.token:
            logger.warning(
                "Kein GITLAB_TOKEN gesetzt. "
                "Setze die Umgebungsvariable GITLAB_TOKEN für GitLab-Integration."
            )

    def _get_gl_objects(self, mr: MergeRequest) -> tuple[Any, Any]:
        """Holt GitLab-Projekt und MR-Objekt."""
        try:
            import gitlab
        except ImportError:
            raise ImportError(
                "python-gitlab ist nicht installiert. "
                "Installiere es mit: pip install code-review-agent[gitlab]"
            )

        self._gl = gitlab.Gitlab(self.url, private_token=self.token)

        if not self.project_id:
            self.project_id = self._parse_project_id()

        project = self._gl.projects.get(self.project_id)
        merge_request = project.mergerequests.get(int(mr.id))
        return project, merge_request

    def get_diff(self, mr: MergeRequest) -> str:
        """Holt den Diff eines Merge Requests über die GitLab API."""
        _, merge_request = self._get_gl_objects(mr)

        # Sammle alle Diffs
        diffs = merge_request.diffs.list(get_all=True)
        diff_texts = []
        for d in diffs:
            diff = merge_request.diffs.get(d.id)
            if hasattr(diff, "diff"):
                diff_texts.append(diff.diff)

        return "\n".join(diff_texts)

    def post_comments(self, mr: MergeRequest, comments: list[ReviewComment]) -> int:
        """Postet Inline-Kommentare im Merge Request (als einzelne Diskussionen)."""
        project, merge_request = self._get_gl_objects(mr)

        # Hol den neuesten Commit
        commits = merge_request.commits()
        latest_sha = commits[0]["id"] if commits else mr.id

        posted = 0
        for c in comments:
            try:
                merge_request.discussions.create({
                    "body": c.body,
                    "position": {
                        "position_type": "text",
                        "new_path": c.file_path,
                        "new_line": c.line or 1,
                        "base_sha": merge_request.diff_refs["base_sha"],
                        "start_sha": merge_request.diff_refs["start_sha"],
                        "head_sha": merge_request.diff_refs["head_sha"],
                    },
                })
                posted += 1
            except Exception as e:
                logger.warning(
                    "Konnte Kommentar in %s:%d nicht posten: %s",
                    c.file_path, c.line, e,
                )

        logger.info(
            "%d/%d Kommentare an MR !%s gesendet",
            posted, len(comments), mr.id,
        )
        return posted

    def update_status(
        self,
        mr: MergeRequest,
        status: str,
        description: str,
    ) -> None:
        """Setzt den Merge-Request-Status (GitLab CI/CD-Status)."""
        try:
            _, merge_request = self._get_gl_objects(mr)

            state_map = {
                "pending": "pending",
                "success": "success",
                "failure": "failed",
                "error": "failed",
            }
            state = state_map.get(status, "pending")

            merge_request.statuses.create({
                "state": state,
                "name": "code-review-agent",
                "description": description[:140],
                "target_url": "",
            })
            logger.info("Status '%s' für MR !%s gesetzt", state, mr.id)
        except Exception as e:
            logger.error("Status-Update fehlgeschlagen: %s", e)

    def _parse_project_id(self) -> str:
        """Parst die Projekt-ID aus 'git remote -v'."""
        import subprocess
        import re

        result = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
            cwd=str(self.repo_path),
            timeout=10,
        )
        url = result.stdout.strip()

        # GitLab-URL-Formate:
        # git@gitlab.com:group/subgroup/project.git
        # https://gitlab.com/group/subgroup/project
        if "gitlab" not in url:
            logger.warning("Kein GitLab-Remote erkannt, verwende Platzhalter")
            return "0"

        if url.startswith("git@"):
            path = url.split("gitlab.com:")[1] if "gitlab.com:" in url else url
        else:
            path = url.split("gitlab.com/")[1] if "gitlab.com/" in url else url

        path = re.sub(r"\.git$", "", path)
        return path.replace("/", "%2F")  # URL-Encoding für GitLab API
