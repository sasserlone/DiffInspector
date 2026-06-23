# 🤖 Code Review Agent

**Automatisiertes Code-Review mit lokalem Ollama-LLM, Git-Integration und konfigurierbarem Regelwerk.**

> Review von Pull Requests, Merge Requests oder lokalen Diffs – vollständig offline und ohne externe API-Kosten.

---

## 📋 Architektur

```
GitLab/GitHub PR
      │
Webhook / Pipeline Job / CLI
      │
Review Orchestrator ──── Diff Analyzer ──── Context Retriever
      │                                               │
      ├──── Ollama LLM (lokal, z.B. CodeLlama) ───────┘
      │
Rule Engine / Validator
      │
Review Comments zurück in PR / Ausgabe auf Konsole
```

### Komponenten

| Komponente | Aufgabe |
|---|---|
| **Diff Analyzer** | Parst rohe Git-Diffs in strukturierte Daten (Hunks, Zeilen, Status) |
| **Context Retriever** | Holt Dateikontext, betroffene Funktionen, verwandte Dateien |
| **LLM Client** | Kommuniziert mit lokalem Ollama-Server |
| **Rule Engine** | Lädt Regelwerke, validiert und dedupliziert Findings |
| **Git Provider** | Abstraktion für GitHub, GitLab, lokales Git |
| **Review Orchestrator** | Steuert den gesamten Ablauf |
| **Webhook Server** | FastAPI-Server für eingehende PR/MR-Events |

---

## 🚀 Schnellstart

### 1. Voraussetzungen

- **Python 3.10+**
- **Ollama** (lokal installiert und gestartet: `ollama serve`)
- **Ein LLM-Modell** in Ollama, z.B.:

```bash
ollama pull codellama:7b        # Empfohlen für Code-Review
# Alternativen:
ollama pull deepseek-coder:6.7b
ollama pull llama3:8b
ollama pull mistral:7b
```

### 2. Installation

```bash
# Repository klonen
cd code-review-agent

# Virtuelle Umgebung (optional)
python -m venv venv
source venv/bin/activate

# Installation (lokal)
pip install -e .

# Mit GitHub/GitLab-Support
pip install -e ".[all]"

# Oder selektiv:
pip install -e ".[github]"      # Nur GitHub
pip install -e ".[webhook]"     # Nur Webhook-Server
```

### 3. Konfiguration

Die Konfiguration erfolgt über `config.yaml` (siehe Beispiel) oder Umgebungsvariablen:

```bash
# Minimal-Konfiguration per env
export CRA_OLLAMA_MODEL="codellama:7b"
export GITHUB_TOKEN="ghp_..."
export GITLAB_TOKEN="glpat-..."
```

### 4. Health-Check

```bash
code-review-agent health
```

### 5. Erste Schritte

```bash
# Review des aktuellen Working Tree
code-review-agent diff

# Review aus Diff-Datei
code-review-agent diff changes.diff

# Review zwischen Branches
code-review-agent branch --target-branch main

# Review eines GitHub PR
code-review-agent mr --mr-id 42 --provider github
```

---

## 🎮 CLI-Kommandos

### `code-review-agent health`

Prüft, ob Ollama erreichbar ist und das Modell existiert.

### `code-review-agent diff [diff_file]`

Führt ein Review auf einem Diff durch.

| Option | Beschreibung |
|---|---|
| `diff_file` | Pfad zur Diff-Datei (optional – sonst Working Tree) |
| `-o, --output FILE` | JSON-Ergebnis speichern |
| `-f, --format rich|json|text` | Ausgabeformat |
| `--min-severity info|warning|error` | Minimale Severity |

### `code-review-agent branch`

Vergleicht zwei Branches und reviewed die Änderungen.

| Option | Beschreibung |
|---|---|
| `-t, --target-branch` | Ziel-Branch (default: main) |
| `-s, --source-branch` | Quell-Branch (default: aktueller) |
| `-o, --output FILE` | JSON-Ergebnis speichern |

### `code-review-agent mr`

Reviewed einen Pull Request / Merge Request.

| Option | Beschreibung |
|---|---|
| `--mr-id` | PR/MR-ID |
| `--provider github|gitlab` | Git-Provider |

### `code-review-agent webhook`

Startet den Webhook-Server für automatische Reviews.

| Option | Beschreibung |
|---|---|
| `-p, --port` | Port (default: 8000) |
| `--host` | Host (default: 0.0.0.0) |

---

## 🌐 Webhook-Server (GitHub / GitLab)

Der Webhook-Server empfängt PR/MR-Events und führt automatisch ein Review durch.

```bash
# Mit installiertem Agent
code-review-agent webhook --port 8000

# Oder direkt via Python
python -m examples.webhook_server
```

### GitHub Webhook einrichten

```
Repository → Settings → Webhooks → Add webhook
Payload URL: http://dein-server:8000/webhook/github
Content type: application/json
Secret: (optional, über WEBHOOK_SECRET)
Events: Pull requests
```

Umgebungsvariablen:
```bash
export GITHUB_TOKEN="ghp_..."
export WEBHOOK_SECRET="..."   # Optional
```

### GitLab Webhook einrichten

```
Project → Settings → Webhooks → Add webhook
URL: http://dein-server:8000/webhook/gitlab
Secret Token: (optional, über WEBHOOK_SECRET)
Trigger: Merge Request Events
```

Umgebungsvariablen:
```bash
export GITLAB_TOKEN="glpat-..."
export CI_PROJECT_ID="123"    # Optional
export WEBHOOK_SECRET="..."   # Optional
```

---

## ⚙️ Konfiguration

### `config.yaml`

```yaml
ollama:
  base_url: "http://localhost:11434"
  model: "codellama:7b"
  temperature: 0.2
  max_tokens: 2048
  num_ctx: 8192

review:
  max_diff_lines: 500
  chunk_size: 200
  parallel_chunks: 0
  output_dir: "./reports"

git:
  remote: "origin"
  work_dir: "/tmp/code-review-agent"

logging:
  level: "INFO"
```

### Umgebungsvariablen

| Variable | Überschreibt |
|---|---|
| `CRA_OLLAMA_BASE_URL` | `ollama.base_url` |
| `CRA_OLLAMA_MODEL` | `ollama.model` |
| `CRA_OLLAMA_TEMPERATURE` | `ollama.temperature` |
| `CRA_REVIEW_MAX_DIFF_LINES` | `review.max_diff_lines` |
| `CRA_REVIEW_CHUNK_SIZE` | `review.chunk_size` |
| `CRA_LOG_LEVEL` | `logging.level` |
| `GITHUB_TOKEN` | GitHub API-Token |
| `GITLAB_TOKEN` | GitLab API-Token |
| `WEBHOOK_SECRET` | Secret für Webhooks |

---

## 📦 Projektstruktur

```
src/
├── __init__.py          # Package-Init
├── config.py            # Konfiguration (YAML + Env-Overrides)
├── main.py              # CLI-Einstiegspunkt (Click + Rich)
├── llm_client.py        # Ollama-Client (generate, health_check)
├── diff_analyzer.py     # Diff-Parser (Hunks, Zeilen, Status)
├── context_retriever.py # Kontext-Helfer (Dateien, Funktionen)
├── review_prompt.py     # Prompt-Builder für LLM
├── response_parser.py   # LLM-Output → strukturierte Findings
├── rule_engine.py       # Regelwerk-Loader und Validator
├── git_provider.py      # Git-Provider-Abstraktion (ABC)
├── github_client.py     # GitHub-Integration
├── gitlab_client.py     # GitLab-Integration
└── orchestrator.py      # Review-Orchestrator (Hauptlogik)

rules/
├── default.yaml         # Standard-Regeln (Style, Bugs, Performance)
└── security.yaml        # Security-Regeln

examples/
└── webhook_server.py    # FastAPI-Webhook-Server

config.yaml              # Beispiel-Konfiguration
pyproject.toml           # Package-Definition
requirements.txt         # Abhängigkeiten
```

---

## 🧠 Prompt-Engineering

Das System verwendet **zwei Prompt-Ebenen**:

1. **System-Prompt**: Definiert die Rolle des Senior-Entwicklers, Review-Kriterien und das Antwortformat.
2. **File-Prompt**: Enthält den konkreten Diff + Kontext (betroffene Funktionen, Umgebungszeilen, relevante Regeln).

Das LLM antwortet im strukturierten Format:

```
FILE: src/main.py
LINE: 42
SEVERITY: error
CATEGORY: bug
MESSAGE: Möglicher off-by-one-Fehler in der Schleife
SUGGESTION:
for i in range(len(items)):
```

---

## 🔌 Erweiterbarkeit

### Eigenes Regelwerk

Erstelle eine neue YAML-Datei in `rules/`:

```yaml
rules:
  - id: "MY-001"
    severity: "warning"
    category: "style"
    pattern: "Eigenes Pattern"
    message: "Eigene Nachricht"
```

Aktivieren in `config.yaml`:

```yaml
rules:
  profiles:
    default:
      - "default.yaml"
      - "security.yaml"
      - "mein-regelwerk.yaml"
```

### Eigener Git-Provider

Implementiere das `GitProvider`-ABC aus `git_provider.py`:

```python
from src.git_provider import GitProvider, MergeRequest, ReviewComment

class MyCustomProvider(GitProvider):
    def get_diff(self, mr: MergeRequest) -> str:
        ...
    def post_comments(self, mr, comments) -> int:
        ...
    def update_status(self, mr, status, description) -> None:
        ...
```

---

## 📊 CI/CD-Integration

### GitHub Actions

```yaml
name: Code Review
on:
  pull_request:
    types: [opened, synchronize]

jobs:
  review:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Ollama starten
        run: |
          docker run -d --name ollama -p 11434:11434 ollama/ollama
          ollama pull codellama:7b
      - name: Code Review
        run: |
          pip install code-review-agent[github]
          code-review-agent mr --mr-id ${{ github.event.number }} --provider github
        env:
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
          CRA_OLLAMA_BASE_URL: "http://localhost:11434"
```

### GitLab CI

```yaml
code-review:
  stage: test
  image: python:3.11
  only:
    - merge_requests
  script:
    - apt-get update && apt-get install -y curl
    - curl -fsSL https://ollama.com/install.sh | sh
    - ollama serve &
    - ollama pull codellama:7b
    - pip install code-review-agent[gitlab]
    - code-review-agent mr --mr-id $CI_MERGE_REQUEST_IID --provider gitlab
  variables:
    GITLAB_TOKEN: $CI_JOB_TOKEN
```

---

## 📝 Ausgabe-Beispiel

```
📋 Review-Zusammenfassung
   3 Dateien geändert, 2 modified, 1 added, 45 Einfügungen(+), 12 Löschungen(-)
   Score: 75/100
   🔴 Errors:   1
   🟡 Warnings: 2
   🔵 Infos:    1

🔴 ERROR | src/auth.py:47
   Hartcodierte Secrets wurden erkannt. Verwende Umgebungsvariablen.
   (security)

🟡 WARNING | src/api.py:23
   Diese Funktion hat keine Typannotationen.
   (style)
```

---

## 🔧 Technologie-Entscheidungen

| Entscheidung | Warum |
|---|---|
| **Python** | Einfachste Sprache für Scripting + API-Integration |
| **Ollama** | Lokales LLM ohne API-Kosten, keine Datenverlassen |
| **Click + Rich** | CLI-Framework mit漂亮的 Ausgabe |
| **FastAPI** | Modernes Webhook-Framework, async, auto-docs |
| **PyGithub** | Vollständige GitHub-API-Abdeckung |
| **python-gitlab** | Vollständige GitLab-API-Abdeckung |
| **Pydantic** | Typsichere Konfiguration |
| **YAML-Regeln** | Einfach erweiterbar ohne Code-Änderungen |

---

## 📄 Lizenz

MIT
