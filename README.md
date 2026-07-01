# Code Review Agent

Ein pragmatischer Code-Review-Agent fuer lokale Diffs, Branch-Vergleiche und PR/MR-Reviews. Der Agent kombiniert Git-Diff-Parsing, Kontextsammlung, ein LLM und eine nachgelagerte Rule Engine, damit aus frei formulierten Modellantworten moeglichst wenige, konkrete Review-Findings entstehen.

Der Agent ist kein deterministischer Compiler und kein Ersatz fuer Tests. Er ist ein Assistenzwerkzeug: hilfreich fuer erste Hinweise, aber bewusst mit Validierung, Deduplizierung und konservativer Severity-Kalibrierung gebaut.

## Architektur

```text
Git diff / GitHub PR / GitLab MR
        |
        v
Review Orchestrator
        |
        +--> Diff Analyzer
        +--> Context Retriever
        +--> LLM Client (Ollama oder OpenAI-kompatible API)
        |
        v
Response Parser
        |
        v
Rule Engine / Validator
        |
        v
Konsole / JSON / PR-MR-Kommentare
```

## Aktueller Stand

Der Prototyp unterstuetzt:

- Review des lokalen Working Trees
- Review einer Diff-Datei
- Branch-Vergleich
- GitHub- und GitLab-Provider
- optionalen FastAPI-Webhook-Server
- Ollama oder OpenAI-kompatible Chat-Completion-APIs
- Parsing von LLM-Antworten im `FILE/LINE/SEVERITY/CATEGORY/MESSAGE/SUGGESTION`-Format
- Validierung gegen geaenderte Diff-Zeilen
- Herunterstufung spekulativer Findings
- Blockieren unsicherer Vorschlaege wie `source .env`
- Redaction von API-Keys in der Health-Ausgabe

Wichtig: LLM-Ergebnisse koennen zwischen zwei Laeufen variieren. Der Validator reduziert Rauschen, garantiert aber keine identischen Ergebnisse.

## Installation

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

Mit optionalen Integrationen:

```bash
pip install -e ".[github]"
pip install -e ".[gitlab]"
pip install -e ".[webhook]"
pip install -e ".[all]"
```

Alternativ kann der lokale Wrapper verwendet werden:

```bash
./code-review-agent health
./code-review-agent diff
```

Der Wrapper liest eine lokale `.env` als einfache `KEY=VALUE`-Datei ein. Die Datei wird nicht als Shell-Skript ausgefuehrt.

## Konfiguration

Die Konfiguration liegt in `config.yaml`. Secrets sollten nicht in diese Datei geschrieben werden. Nutze stattdessen `.env` oder echte Umgebungsvariablen.

### Ollama

```yaml
ollama:
  provider: "ollama"
  base_url: "http://localhost:11434"
  model: "codellama:7b"
  temperature: 0.0
  max_tokens: 300
  timeout: 120
  num_ctx: 4096
```

Voraussetzung:

```bash
ollama serve
ollama pull codellama:7b
```

### DeepSeek oder andere OpenAI-kompatible APIs

Der aktuelle LLM-Client verzweigt fuer OpenAI-kompatible APIs ueber `provider: "openai"`. DeepSeek wird dabei ueber `api_base_url` genutzt.

```yaml
ollama:
  provider: "openai"
  api_key: ""
  api_base_url: "https://api.deepseek.com"
  api_model: "deepseek-chat"
  temperature: 0.0
  max_tokens: 300
```

API-Key per Umgebung:

```bash
export CRA_API_KEY="..."
```

oder in `.env`:

```bash
CRA_API_KEY="..."
```

`.env` ist in `.gitignore` enthalten und darf nicht committed werden.

## Wichtige Umgebungsvariablen

| Variable | Bedeutung |
|---|---|
| `CRA_LLM_PROVIDER` | `ollama` oder `openai` |
| `CRA_OLLAMA_BASE_URL` | Ollama-Endpoint |
| `CRA_OLLAMA_MODEL` | Ollama-Modell |
| `CRA_API_KEY` | API-Key fuer OpenAI-kompatible Provider |
| `DEEPSEEK_API_KEY` | Alternative fuer DeepSeek |
| `OPENAI_API_KEY` | Alternative fuer OpenAI |
| `CRA_API_BASE_URL` | OpenAI-kompatible Base URL |
| `CRA_API_MODEL` | Modellname der API |
| `CRA_REVIEW_MAX_DIFF_LINES` | Schwelle fuer Chunking |
| `CRA_REVIEW_CHUNK_SIZE` | Zielgroesse pro Chunk |
| `CRA_LOG_LEVEL` | `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `GITHUB_TOKEN` | GitHub API-Token |
| `GITLAB_TOKEN` | GitLab API-Token |
| `WEBHOOK_SECRET` | Optionales Webhook-Secret |

## CLI

### Health Check

```bash
./code-review-agent health
```

Prueft den konfigurierten LLM-Provider und gibt die geladene Konfiguration ohne Secret-Werte aus.

### Lokalen Diff reviewen

```bash
./code-review-agent diff
```

Ohne Argumente werden unstaged und staged Changes im aktuellen Git-Repository reviewed.

### Diff-Datei reviewen

```bash
./code-review-agent diff changes.diff
```

### JSON-Ausgabe speichern

```bash
./code-review-agent diff --format json --output reports/review.json
```

### Branches vergleichen

```bash
./code-review-agent branch --target-branch main
./code-review-agent branch --target-branch main --source-branch feature/foo
```

### PR/MR reviewen

```bash
./code-review-agent mr --mr-id 42 --provider github
./code-review-agent mr --mr-id 42 --provider gitlab
```

## Webhook-Server

```bash
./code-review-agent webhook --port 8000
```

Endpunkte:

```text
POST /webhook/github
POST /webhook/gitlab
GET  /health
```

GitHub benoetigt `GITHUB_TOKEN`, GitLab benoetigt `GITLAB_TOKEN`. Optional kann `WEBHOOK_SECRET` gesetzt werden.

## Review-Verhalten

Der Review laeuft pro Datei:

1. Git-Diff wird geparst.
2. Geaenderte Dateien und Hunks werden strukturiert.
3. Kontext und betroffene Funktionen werden gesammelt.
4. Das LLM bekommt Diff, Kontext, erlaubte Kommentarzeilen und Regeln.
5. Die Antwort wird geparst.
6. Die Rule Engine validiert und kalibriert Findings.

Die Rule Engine verwirft oder reduziert unter anderem:

- Findings ausserhalb hinzugefuegter Diff-Zeilen
- Findings fuer nicht geaenderte Dateien
- doppelte Findings
- leere oder ungueltige Kategorien/Severities
- "keine Aenderung noetig"-Pseudo-Findings
- unsichere Vorschlaege wie `.env` per `source` auszufuehren
- spekulative `error`-Findings mit Woertern wie "koennte" oder "moeglicherweise"
- leere Secret-Platzhalter wie `api_key: ""`

## Nicht-Determinismus

LLM-Reviews sind nicht voll deterministisch. Zwei Laeufe ueber denselben Diff koennen unterschiedliche Roh-Findings erzeugen. Das ist besonders sichtbar bei:

- `temperature > 0`
- knappen `max_tokens`
- grossen Diffs mit Chunking
- freien Textantworten statt strikt validiertem JSON
- Remote-APIs, die keine reproduzierbare Seed-Steuerung anbieten

Fuer stabilere Ergebnisse:

- `temperature: 0.0` setzen
- `max_tokens` nicht zu knapp waehlen
- kleinere Diffs reviewen
- JSON-Ausgaben archivieren
- wichtige Findings durch Tests oder manuelle Pruefung bestaetigen

## Regeln

Regeln liegen unter `rules/`.

```text
rules/
  default.yaml
  security.yaml
```

Ein Regelprofil wird in `config.yaml` definiert:

```yaml
rules:
  enabled: true
  rules_dir: "./rules"
  profiles:
    default:
      - "default.yaml"
      - "security.yaml"
```

Regelbeispiel:

```yaml
rules:
  - id: "BUG-001"
    severity: "error"
    category: "bug"
    pattern: "Möglicher off-by-one-Fehler"
    message: "Überprüfe Schleifen- oder Indexgrenzen."
```

## Projektstruktur

```text
src/
  config.py             Konfiguration aus YAML und Env
  main.py               CLI mit Click und Rich
  llm_client.py         Ollama und OpenAI-kompatible API
  diff_analyzer.py      Git-Diff-Parser
  context_retriever.py  Kontext aus Repository
  review_prompt.py      System- und File-Prompts
  response_parser.py    LLM-Text zu ReviewFinding
  rule_engine.py        Validierung, Deduplizierung, Severity-Kalibrierung
  git_provider.py       Provider-Abstraktion
  github_client.py      GitHub-Integration
  gitlab_client.py      GitLab-Integration
  orchestrator.py       Ablaufsteuerung

rules/
  default.yaml
  security.yaml

examples/
  webhook_server.py

config.yaml
.env.example
code-review-agent
pyproject.toml
requirements.txt
```

## Bekannte Grenzen

- Der Agent kann halluzinierte LLM-Findings nicht vollstaendig verhindern.
- GitHub/GitLab Inline-Kommentare funktionieren nur auf gueltigen Diff-Zeilen.
- Remote-Kontext ist nur so gut wie der lokale Checkout bzw. Provider-Diff.
- Grosse Diffs werden gechunked; dadurch fehlt dem LLM eventuell globaler Kontext.
- Der Providername fuer OpenAI-kompatible APIs ist aktuell `openai`; DeepSeek wird ueber `api_base_url` abgebildet.
- Der Agent sollte Findings nicht ungeprueft als harte Merge-Blocker verwenden.

## Sicherheit

- Keine echten API-Keys in `config.yaml`, README oder Commits schreiben.
- `.env` bleibt lokal und ist ignoriert.
- `./code-review-agent` fuehrt `.env` nicht als Shell-Code aus.
- `health` gibt API-Keys redacted aus.
- Review-Kommentare koennen Codeausschnitte enthalten; bei privaten Repositories Provider und Logs entsprechend schuetzen.

## Beispielausgabe

```text
Review-Zusammenfassung
   3 Dateien geändert, 3 modified, 42 Einfügungen(+), 8 Löschungen(-)
   Score: 94/100
   Errors:   0
   Warnings: 1
   Infos:    1

WARNING | src/main.py:98
   Diese Zeile nutzt eine potentiell unsichere Operation.
   (security)
```

## Lizenz

MIT
