"""🤖 Code Review Agent – Webhook-Server (FastAPI)

Empfängt Webhook-Events von GitHub und GitLab, triggert automatische
Code-Reviews und postet die Ergebnisse als PR/MR-Kommentare.

Starten:
    code-review-agent webhook --port 8000

Oder:
    python -m examples.webhook_server

GitHub Webhook einrichten:
    Repository → Settings → Webhooks → Add webhook
    Payload URL: http://dein-server:8000/webhook/github
    Content type: application/json
    Secret: (optional, wird via WEBHOOK_SECRET konfiguriert)
    Events: Pull requests

GitLab Webhook einrichten:
    Project → Settings → Webhooks → Add webhook
    URL: http://dein-server:8000/webhook/gitlab
    Secret Token: (optional)
    Trigger: Merge Request Events
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request

from src.config import AppConfig
from src.git_provider import MergeRequest
from src.orchestrator import ReviewOrchestrator

logger = logging.getLogger(__name__)

app = FastAPI(title="Code Review Agent", version="0.1.0")

# ──────────────────────────────────────────────
# Initialisierung
# ──────────────────────────────────────────────

config = AppConfig.load()
orchestrator = ReviewOrchestrator(config)
webhook_secret = os.environ.get("WEBHOOK_SECRET", "")


# ──────────────────────────────────────────────
# Hilfsfunktionen
# ──────────────────────────────────────────────


def _verify_signature(payload: bytes, signature: str | None) -> bool:
    """Verifiziert GitHub HMAC-SHA256-Signatur."""
    if not webhook_secret:
        return True  # Kein Secret konfiguriert
    if not signature:
        return False

    expected = "sha256=" + hmac.new(
        webhook_secret.encode(),
        payload,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


def _verify_gitlab_token(token: str | None) -> bool:
    """Verifiziert GitLab Webhook-Token."""
    if not webhook_secret:
        return True
    return token == webhook_secret


# ──────────────────────────────────────────────
# Webhook-Endpunkte
# ──────────────────────────────────────────────


@app.post("/webhook/github")
async def github_webhook(request: Request) -> dict:
    """Empfängt GitHub Pull-Request-Webhooks."""
    payload = await request.body()
    signature = request.headers.get("x-hub-signature-256")

    if not _verify_signature(payload, signature):
        raise HTTPException(status_code=403, detail="Ungültige Signatur")

    event = request.headers.get("x-github-event", "")
    if event != "pull_request":
        return {"status": "ignored", "event": event}

    data = json.loads(payload)
    action = data.get("action", "")
    pr = data.get("pull_request", {})

    # Nur bei opened / synchronize / reopened reviewen
    if action not in ("opened", "synchronize", "reopened"):
        return {"status": "ignored", "action": action}

    mr = MergeRequest(
        id=str(pr["number"]),
        title=pr.get("title", ""),
        description=pr.get("body", ""),
        source_branch=pr["head"]["ref"],
        target_branch=pr["base"]["ref"],
        author=pr["user"]["login"],
        diff_url=pr["diff_url"],
        provider="github",
    )

    logger.info(
        "GitHub Webhook: PR #%s (%s) %s → %s",
        mr.id, mr.title, mr.source_branch, mr.target_branch,
    )

    try:
        from src.github_client import GitHubProvider
        token = os.environ.get("GITHUB_TOKEN", "")
        orchestrator.git = GitHubProvider(Path.cwd(), token)
        result = orchestrator.review_merge_request(mr)

        return {
            "status": "completed",
            "mr_id": mr.id,
            "score": result.score,
            "error_count": result.error_count,
            "warning_count": result.warning_count,
            "info_count": result.info_count,
        }
    except Exception as e:
        logger.exception("Fehler beim Review von PR #%s", mr.id)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/webhook/gitlab")
async def gitlab_webhook(request: Request) -> dict:
    """Empfängt GitLab Merge-Request-Webhooks."""
    token = request.headers.get("x-gitlab-token")
    if not _verify_gitlab_token(token):
        raise HTTPException(status_code=403, detail="Ungültiger Token")

    data = await request.json()
    event_type = data.get("object_kind", "")
    if event_type != "merge_request":
        return {"status": "ignored", "event": event_type}

    mr_data = data.get("object_attributes", {})
    action = mr_data.get("action", "")

    if action not in ("open", "update", "reopen"):
        return {"status": "ignored", "action": action}

    mr = MergeRequest(
        id=str(mr_data["iid"]),
        title=mr_data.get("title", ""),
        description=mr_data.get("description", ""),
        source_branch=mr_data["source_branch"],
        target_branch=mr_data["target_branch"],
        author=data.get("user", {}).get("name", ""),
        diff_url=mr_data.get("url", ""),
        provider="gitlab",
    )

    logger.info(
        "GitLab Webhook: MR !%s (%s) %s → %s",
        mr.id, mr.title, mr.source_branch, mr.target_branch,
    )

    try:
        from src.gitlab_client import GitLabProvider
        project_id = data.get("project", {}).get("id", "")
        token = os.environ.get("GITLAB_TOKEN", "")
        orchestrator.git = GitLabProvider(
            Path.cwd(),
            token=token,
            project_id=str(project_id),
        )
        result = orchestrator.review_merge_request(mr)

        return {
            "status": "completed",
            "mr_id": mr.id,
            "score": result.score,
            "error_count": result.error_count,
            "warning_count": result.warning_count,
            "info_count": result.info_count,
        }
    except Exception as e:
        logger.exception("Fehler beim Review von MR !%s", mr.id)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/health")
async def health_check() -> dict:
    """Gesundheitscheck-Endpunkt."""
    from src.llm_client import LLMClient
    llm = LLMClient(config.ollama)
    ollama_ok = llm.health_check()

    return {
        "status": "ok" if ollama_ok else "degraded",
        "ollama": {
            "url": config.ollama.base_url,
            "model": config.ollama.model,
            "reachable": ollama_ok,
        },
    }


@app.get("/")
async def root() -> dict:
    """Root-Endpunkt mit Übersicht."""
    return {
        "service": "Code Review Agent",
        "version": "0.1.0",
        "endpoints": {
            "health": "/health",
            "github_webhook": "/webhook/github",
            "gitlab_webhook": "/webhook/gitlab",
        },
        "docs": "/docs",
    }


# ──────────────────────────────────────────────
# Direkter Start
# ──────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", "8000"))
    host = os.environ.get("HOST", "0.0.0.0")
    uvicorn.run("examples.webhook_server:app", host=host, port=port, reload=True)
