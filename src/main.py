"""Code Review Agent – CLI-Einstiegspunkt."""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

import click
from rich.console import Console
from rich.logging import RichHandler
from rich.table import Table
from rich.text import Text

from src.config import AppConfig
from src.git_provider import LocalGitProvider, MergeRequest
from src.orchestrator import ReviewOrchestrator

console = Console()

# ──────────────────────────────────────────────
# Logging Setup
# ──────────────────────────────────────────────


def setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(message)s",
        datefmt="[%X]",
        handlers=[RichHandler(rich_tracebacks=True, markup=True)],
    )


# ──────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────


@click.group()
@click.option(
    "--config",
    "-c",
    type=click.Path(exists=True, dir_okay=False),
    help="Pfad zur Konfigurationsdatei",
)
@click.option(
    "--log-level",
    default=None,
    type=click.Choice(["DEBUG", "INFO", "WARNING", "ERROR"]),
    help="Log-Level (überschreibt config.yaml)",
)
@click.pass_context
def cli(ctx: click.Context, config: str | None, log_level: str | None) -> None:
    """🤖 Code Review Agent – Automatisierte Code-Reviews mit Ollama."""
    ctx.ensure_object(dict)

    # Config laden
    cfg = AppConfig.load(config)
    if log_level:
        cfg.logging.level = log_level

    setup_logging(cfg.logging.level)
    ctx.obj["config"] = cfg


@cli.command()
@click.pass_context
def health(ctx: click.Context) -> None:
    """Prüft die LLM-Verbindung (Ollama oder DeepSeek/OpenAI)."""
    from src.llm_client import LLMClient

    cfg: AppConfig = ctx.obj["config"]
    llm = LLMClient(cfg.ollama)

    provider_name = cfg.ollama.provider
    console.print(f"\n[bold]🔍 Code Review Agent – Health Check ({provider_name})[/bold]\n")

    with console.status(f"[bold green]Prüfe {provider_name}-Verbindung..."):
        healthy = llm.health_check()

    if healthy:
        if provider_name == "openai":
            console.print(f"✅  [green]{cfg.ollama.api_model}[/green] erreichbar unter [cyan]{cfg.ollama.api_base_url}[/cyan]")
        else:
            console.print(f"✅  Ollama erreichbar unter [cyan]{cfg.ollama.base_url}[/cyan]")
            console.print(f"   Modell: [green]{cfg.ollama.model}[/green]")
    else:
        if provider_name == "openai":
            console.print(
                f"❌  Keine Verbindung zu [red]{cfg.ollama.api_base_url}[/red]\n"
                f"   Modell: [yellow]{cfg.ollama.api_model}[/yellow]\n"
                f"   Prüfe CRA_API_KEY oder api_key in config.yaml"
            )
        else:
            console.print(
                f"❌  Keine Verbindung zu Ollama unter [red]{cfg.ollama.base_url}[/red]\n"
                f"   Starte Ollama mit: [yellow]ollama serve[/yellow]"
            )
        sys.exit(1)

    console.print(f"\nKonfiguration: [blue]{_safe_config_dump(cfg)}[/blue]")


@cli.command()
@click.argument("diff_file", type=click.Path(exists=True, dir_okay=False), required=False)
@click.option(
    "--output", "-o",
    type=click.Path(dir_okay=False),
    help="JSON-Output-Datei für Ergebnisse",
)
@click.option(
    "--format", "-f",
    type=click.Choice(["rich", "json", "text"]),
    default="rich",
    help="Ausgabeformat (Standard: rich)",
)
@click.option(
    "--min-severity",
    type=click.Choice(["info", "warning", "error"]),
    default="info",
    help="Minimale Severity für die Anzeige",
)
@click.pass_context
def diff(
    ctx: click.Context,
    diff_file: str | None,
    output: str | None,
    format: str,
    min_severity: str,
) -> None:
    """Führt ein Review auf einer Diff-Datei oder dem aktuellen Working Tree aus."""
    cfg: AppConfig = ctx.obj["config"]
    orchestrator = ReviewOrchestrator(cfg)

    if diff_file:
        diff_text = Path(diff_file).read_text()
    else:
        # Nutze lokales Git
        git = LocalGitProvider(Path.cwd(), cfg.git)
        diff_text = git.get_diff()

    if not diff_text.strip():
        console.print("[yellow]⚠️  Kein Diff gefunden (Working Tree ist sauber).[/yellow]")
        return

    with console.status("[bold green]🔍 Führe Code-Review durch..."):
        result = orchestrator.review_diff(diff_text)

    # Output
    if output:
        _write_json_output(result, output)
        console.print(f"[green]✅  Ergebnis gespeichert: {output}[/green]")

    if format == "json":
        _print_json(result)
    elif format == "text":
        _print_text(result)
    else:
        _print_rich(result, min_severity)


@cli.command()
@click.option("--target-branch", "-t", default="main", help="Ziel-Branch (default: main)")
@click.option("--source-branch", "-s", default=None, help="Quell-Branch (default: aktueller Branch)")
@click.option("--output", "-o", type=click.Path(dir_okay=False), help="JSON-Output-Datei")
@click.option("--min-severity", default="info", help="Minimale Severity")
@click.pass_context
def branch(
    ctx: click.Context,
    target_branch: str,
    source_branch: str | None,
    output: str | None,
    min_severity: str,
) -> None:
    """Vergleicht zwei Branches und reviewed die Änderungen."""
    cfg: AppConfig = ctx.obj["config"]

    if source_branch is None:
        import subprocess
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True,
        )
        source_branch = result.stdout.strip()

    mr = MergeRequest(
        id="local",
        title=f"Review {source_branch} → {target_branch}",
        description="",
        source_branch=source_branch,
        target_branch=target_branch,
        author="",
    )

    orchestrator = ReviewOrchestrator(cfg)

    with console.status(f"[bold green]🔍 Vergleiche {source_branch} → {target_branch}..."):
        git = LocalGitProvider(Path.cwd(), cfg.git)
        diff_text = git.get_diff(mr)
        result = orchestrator.review_diff(diff_text)

    if output:
        _write_json_output(result, output)
        console.print(f"[green]✅  Ergebnis gespeichert: {output}[/green]")

    _print_rich(result, min_severity)


@cli.command()
@click.option("--mr-id", required=True, help="Merge Request / Pull Request ID")
@click.option(
    "--provider",
    type=click.Choice(["github", "gitlab"]),
    default="github",
    help="Git-Provider",
)
@click.pass_context
def mr(ctx: click.Context, mr_id: str, provider: str) -> None:
    """Reviewed einen Merge Request / Pull Request."""
    cfg: AppConfig = ctx.obj["config"]

    orchestrator = ReviewOrchestrator(cfg)
    mr_obj = MergeRequest(
        id=mr_id,
        title=f"MR #{mr_id}",
        description="",
        source_branch="",
        target_branch="",
        author="",
        provider=provider,
    )

    with console.status(f"[bold green]🔍 Review von MR #{mr_id}..."):
        # Wähle Provider
        if provider == "github":
            from src.github_client import GitHubProvider
            orchestrator.git = GitHubProvider(Path.cwd())
        elif provider == "gitlab":
            from src.gitlab_client import GitLabProvider
            orchestrator.git = GitLabProvider(Path.cwd())

        result = orchestrator.review_merge_request(mr_obj)

    _print_rich(result)


@cli.command()
@click.option("--port", "-p", default=8000, help="Webhook-Port")
@click.option("--host", default="0.0.0.0", help="Webhook-Host")
@click.pass_context
def webhook(ctx: click.Context, port: int, host: str) -> None:
    """Startet den Webhook-Server für eingehende PR/MR-Events."""
    cfg: AppConfig = ctx.obj["config"]

    try:
        import uvicorn
    except ImportError:
        console.print(
            "[red]Fehler: FastAPI/uvicorn nicht installiert.\n"
            "Installiere: pip install code-review-agent[webhook][/red]"
        )
        sys.exit(1)

    console.print(f"[green]🚀 Starte Webhook-Server auf {host}:{port}[/green]")
    console.print(f"   GitHub: http://{host}:{port}/webhook/github")
    console.print(f"   GitLab: http://{host}:{port}/webhook/gitlab")

    uvicorn.run(
        "examples.webhook_server:app",
        host=host,
        port=port,
        reload=False,
    )


# ──────────────────────────────────────────────
# Output-Formatter
# ──────────────────────────────────────────────


def _print_rich(result, min_severity: str = "info") -> None:
    """Gibt das Ergebnis farbig auf der Konsole aus."""
    from src.orchestrator import ReviewResult

    severity_levels = {"info": 0, "warning": 1, "error": 2}
    min_level = severity_levels.get(min_severity, 0)

    # Zusammenfassung
    console.print("\n[bold]📋 Review-Zusammenfassung[/bold]")
    console.print(f"   {result.summary}")
    console.print(f"   Score: [bold]{result.score}/100[/bold]")
    console.print(f"   🔴 Errors:   {result.error_count}")
    console.print(f"   🟡 Warnings: {result.warning_count}")
    console.print(f"   🔵 Infos:    {result.info_count}")

    if not result.findings:
        console.print("\n[green]✅  Keine Probleme gefunden![/green]")
        return

    # Findings
    console.print("\n[bold]📝 Review-Kommentare:[/bold]\n")

    for finding in result.findings:
        f_level = severity_levels.get(finding.severity, 0)
        if f_level < min_level:
            continue

        sev_colors = {"error": "red", "warning": "yellow", "info": "blue"}
        sev_icons = {"error": "🔴", "warning": "🟡", "info": "🔵"}

        color = sev_colors.get(finding.severity, "white")
        icon = sev_icons.get(finding.severity, "⚪")

        # Datei:Zeile
        loc = f"[cyan]{finding.file_path}[/cyan]"
        if finding.line:
            loc += f":[yellow]{finding.line}[/yellow]"

        console.print(f"{icon} [bold {color}]{finding.severity.upper()}[/bold {color}] | {loc}")
        console.print(f"   {finding.message}")

        if finding.suggestion:
            console.print(f"   [dim]Vorschlag:[/dim] [green]{finding.suggestion[:200]}...[/green]"
                          if len(finding.suggestion) > 200
                          else f"   [dim]Vorschlag:[/dim] [green]{finding.suggestion}[/green]")

        console.print(f"   [dim]({finding.category})[/dim]")
        console.print()


def _print_json(result) -> None:
    """Gibt das Ergebnis als JSON aus."""
    data = {
        "summary": result.summary,
        "score": result.score,
        "error_count": result.error_count,
        "warning_count": result.warning_count,
        "info_count": result.info_count,
        "findings": [f.to_dict() for f in result.findings],
    }
    console.print(json.dumps(data, indent=2, ensure_ascii=False))


def _print_text(result) -> None:
    """Gibt das Ergebnis als einfachen Text aus."""
    lines = [
        "=" * 60,
        "CODE REVIEW ERGEBNIS",
        "=" * 60,
        f"Summary: {result.summary}",
        f"Score: {result.score}/100",
        f"Errors: {result.error_count} | Warnings: {result.warning_count} | Infos: {result.info_count}",
        "",
    ]

    for f in result.findings:
        lines.append(f"[{f.severity.upper()}] {f.file_path}:{f.line or '?'}")
        lines.append(f"  {f.message}")
        if f.suggestion:
            lines.append(f"  -> {f.suggestion[:150]}")
        lines.append("")

    console.print("\n".join(lines))


def _write_json_output(result, path: str) -> None:
    """Schreibt das Ergebnis als JSON-Datei."""
    data = {
        "summary": result.summary,
        "score": result.score,
        "counts": {
            "error": result.error_count,
            "warning": result.warning_count,
            "info": result.info_count,
        },
        "findings": [f.to_dict() for f in result.findings],
    }
    Path(path).write_text(json.dumps(data, indent=2, ensure_ascii=False))


def _safe_config_dump(cfg: AppConfig) -> dict:
    """Gibt die Konfiguration ohne Secret-Werte zurück."""
    data = cfg.model_dump()
    ollama = data.get("ollama")
    if isinstance(ollama, dict) and ollama.get("api_key"):
        ollama["api_key"] = "***"
    return data


# ──────────────────────────────────────────────
# Einstiegspunkt
# ──────────────────────────────────────────────

if __name__ == "__main__":
    cli()
