<div align="center">

[English](../../README.md) | [Español (Spanish)](README.es.md) | [中文 (Chinese)](README.zh.md) | [العربية (Arabic)](README.ar.md) | [Português (Portuguese)](README.pt.md) | [Bahasa Indonesia (Indonesian)](README.id.md) | [Français (French)](README.fr.md) | [日本語 (Japanese)](README.ja.md) | [Русский (Russian)](README.ru.md) | **Deutsch (German)** | [עברית (Hebrew)](README.he.md) | [יידיש (Yiddish)](README.yi.md)

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="../../docs/assets/logo-dark.svg">
  <source media="(prefers-color-scheme: light)" srcset="../../docs/assets/logo-light.svg">
  <img alt="Bernstein" src="../../docs/assets/logo-light.svg" width="340">
</picture>

<br>

> *„Um Großes zu erreichen, braucht es zwei Dinge: einen Plan und nicht ganz genug Zeit."* — Leonard Bernstein

### Orchestriere jeden KI-Coding-Agenten. Jedes Modell. Mit einem einzigen Befehl.

<img alt="Bernstein im Einsatz: parallele KI-Agenten in Echtzeit orchestriert" src="../../docs/assets/in-action-small.gif" width="700">

[![CI](https://github.com/sipyourdrink-ltd/bernstein/actions/workflows/ci.yml/badge.svg)](https://github.com/sipyourdrink-ltd/bernstein/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/bernstein)](https://pypi.org/project/bernstein/)
[![Python 3.12+](https://img.shields.io/badge/python-3.12+-3776ab?logo=python&logoColor=white)](https://python.org)
[![License](https://img.shields.io/github/license/sipyourdrink-ltd/bernstein)](../../LICENSE)
[![MseeP.ai](https://img.shields.io/badge/MseeP.ai-verified-2496ed)](https://mseep.ai/app/chernistry-bernstein)

[Website](https://bernstein.run) &middot; [Dokumentation](https://bernstein.readthedocs.io/) &middot; [Erste Schritte](../../docs/getting-started/GETTING_STARTED.md) &middot; [Glossar](../../docs/reference/GLOSSARY.md) &middot; [Einschränkungen](../../docs/reference/KNOWN_LIMITATIONS.md)

</div>

---

**Was ist das?** Du sagst Bernstein, was gebaut werden soll. Es verteilt die Arbeit auf mehrere KI-Coding-Agenten (Claude Code, Codex, Gemini CLI und 38 weitere), führt die Tests aus und merged genau den Code, der wirklich besteht. Du kommst zurück zu funktionierendem Code.

### Installation und Ausführung

Eine Zeile auf macOS / Linux:

```bash
curl -fsSL https://bernstein.run/install.sh | sh
```

Windows (PowerShell):

```powershell
irm https://bernstein.run/install.ps1 | iex
```

Anschließend auf dein Projekt zeigen und ein Ziel setzen:

```bash
cd your-project
bernstein init                          # erstellt einen .sdd/-Workspace
bernstein -g "Add JWT auth with refresh tokens, tests, and API docs"
```

Das siehst du während des Laufs:

```
$ bernstein -g "Add JWT auth"
[manager] decomposed into 4 tasks
[agent-1] claude-sonnet: src/auth/middleware.py  (done, 2m 14s)
[agent-2] codex:         tests/test_auth.py      (done, 1m 58s)
[verify]  all gates pass. merging to main.
```

### Was Bernstein anders macht

Die meisten Agenten-Orchestratoren lassen ein LLM entscheiden, wer was tut. Das ist nicht-deterministisch und verbrennt Tokens für Scheduling statt für Code. Bernstein macht genau einen LLM-Aufruf, um dein Ziel zu zerlegen — der Rest (Agenten parallel ausführen, ihre Git-Branches isolieren, Tests laufen lassen, Retries routen) ist schlichtes Python. Jeder Lauf ist reproduzierbar. Jeder Schritt wird protokolliert und ist erneut abspielbar.

Kein Framework zu lernen. Kein Vendor-Lock-in. Tausche jeden Agenten, jedes Modell, jeden Provider beliebig aus.

Weitere Installationsoptionen: `pipx install bernstein`, `pip install bernstein`, `uv tool install bernstein`, `brew`, `dnf copr`, `npx bernstein-orchestrator`. Siehe [Installationsoptionen](#installation).

## Unterstützte Agenten

Bernstein erkennt installierte CLI-Agenten automatisch. Mische sie im selben Lauf. Günstige lokale Modelle für Boilerplate, schwerere Cloud-Modelle für Architektur.

41 CLI-Agent-Adapter: 38 Wrapper für Drittanbieter plus ein generischer Wrapper für alles, was `--prompt` versteht.

| Agent | Modelle | Installation |
|-------|---------|--------------|
| [Claude Code](https://docs.anthropic.com/en/docs/claude-code) | Opus 4, Sonnet 4.6, Haiku 4.5 | `npm install -g @anthropic-ai/claude-code` |
| [Codex CLI](https://github.com/openai/codex) | GPT-5, GPT-5 mini | `npm install -g @openai/codex` |
| [OpenAI Agents SDK v2](https://openai.github.io/openai-agents-python/) | GPT-5, GPT-5 mini, o4 | `pip install 'bernstein[openai]'` |
| [GitHub Copilot CLI](https://docs.github.com/en/copilot/github-copilot-in-the-cli) | Copilot-verwaltet (GPT-5, Sonnet 4.6) | `npm install -g @github/copilot` |
| [Gemini CLI](https://github.com/google-gemini/gemini-cli) | Gemini 2.5 Pro, Gemini Flash | `npm install -g @google/gemini-cli` |
| [Cursor](https://www.cursor.com) | Sonnet 4.6, Opus 4, GPT-5 | [Cursor-App](https://www.cursor.com) |
| [Aider](https://aider.chat) | Beliebige OpenAI/Anthropic-kompatible | `pip install aider-chat` |
| [Amp](https://ampcode.com) | Amp-verwaltet | `npm install -g @sourcegraph/amp` |
| [Cody](https://sourcegraph.com/cody) | Sourcegraph-gehostet | `npm install -g @sourcegraph/cody` |
| [Continue](https://continue.dev) | Beliebige OpenAI/Anthropic-kompatible | `npm install -g @continuedev/cli` (Binary: `cn`) |
| [Goose](https://block.github.io/goose/) | Jeder von Goose unterstützte Anbieter | Siehe [Goose-Doku](https://block.github.io/goose/) |
| [IaC](https://www.terraform.io/) (Terraform/Pulumi) | Jeder Anbieter, den der Basisagent nutzt | Eingebaut |
| [Kilo](https://kilo.dev) | Kilo-gehostet | Siehe [Kilo-Doku](https://kilo.dev) |
| [Kiro](https://kiro.dev) | Kiro-gehostet | Siehe [Kiro-Doku](https://kiro.dev) |
| [Ollama](https://ollama.ai) + Aider | Lokale Modelle (offline) | `brew install ollama` |
| [OpenCode](https://opencode.ai) | Jeder von OpenCode unterstützte Anbieter | Siehe [OpenCode-Doku](https://opencode.ai) |
| [Qwen](https://github.com/QwenLM/qwen-code) | Qwen-Code-Modelle | `npm install -g @qwen-code/qwen-code` |
| [Cloudflare Agents](https://developers.cloudflare.com/agents/) | Workers-AI-Modelle | `bernstein cloud login` |
| [OpenHands](https://github.com/OpenHands/OpenHands) | Alle LiteLLM-unterstützten (Anthropic, OpenAI, ...) | `uv tool install openhands --python 3.12` |
| [Open Interpreter](https://github.com/OpenInterpreter/open-interpreter) | Beliebig (LiteLLM-basiert) | `pip install open-interpreter` |
| [gptme](https://github.com/gptme/gptme) | Anthropic, OpenAI, OpenRouter | `pipx install gptme` |
| [Plandex](https://github.com/plandex-ai/plandex) | Plandex Cloud oder selbst gehostete Modelle | `curl -sL https://plandex.ai/install.sh \| bash` |
| [AIChat](https://github.com/sigoden/aichat) | OpenAI, Anthropic, OpenRouter, Groq, Gemini | `cargo install aichat` |
| [Letta Code](https://github.com/letta-ai/letta-code) | Letta-geroutet (Anthropic, OpenAI) | `npm install -g @letta-ai/letta-code` |
| **Generic** | Jede CLI mit `--prompt` | Eingebaut |

#### Orchestrator-Delegation (Leaf-Node)

Eine separate, kleinere Klasse von Adaptern, die **andere CLI-Orchestratoren** so verpacken, als wären sie einzelne Agenten. Bernstein übergibt dem gewrappten Tool einen Prompt oder Plan und sieht nur den finalen Exit-Code; Sub-Agent-Kosten und Quality Gates innerhalb des gewrappten Orchestrators bleiben für Bernstein unsichtbar. Nützlich, wenn du einen bestehenden, auf einem dieser Tools basierenden Workflow als einzelnen Schritt in einen größeren Bernstein-Plan einbinden willst.

| Orchestrator | Verpackt als | Installation |
|--------------|--------------|--------------|
| [Composio Agent Orchestrator](https://github.com/ComposioHQ/agent-orchestrator) (`@aoagents/ao`) | `composio` | `npm install -g @aoagents/ao` |
| [umputun/ralphex](https://github.com/umputun/ralphex) | `ralphex` | `go install github.com/umputun/ralphex/cmd/ralphex@latest` |

Jeder Adapter funktioniert auch als **internes Scheduler-LLM**. Betreibe den gesamten Stack ohne einen bestimmten Anbieter:

```yaml
internal_llm_provider: gemini            # oder qwen, ollama, codex, goose, ...
internal_llm_model: gemini-3.1-pro
```

> [!TIP]
> Führe `bernstein --headless` für CI-Pipelines aus. Keine TUI, strukturierte JSON-Ausgabe, Exit-Code ungleich null bei Fehler.

## Schnellstart

```bash
cd your-project
bernstein init                    # erstellt .sdd/-Workspace + bernstein.yaml
bernstein -g "Add rate limiting"  # Agenten starten, arbeiten parallel, verifizieren, beenden
bernstein live                    # Fortschritt im TUI-Dashboard verfolgen
bernstein stop                    # geordnetes Herunterfahren mit Drain
```

Für mehrstufige Projekte definierst du einen YAML-Plan:

```bash
bernstein run plan.yaml           # überspringt LLM-Planung, geht direkt in die Ausführung
bernstein run --dry-run plan.yaml # Vorschau auf Tasks und geschätzte Kosten
```

## So funktioniert es

1. **Zerlegen.** Der Manager bricht dein Ziel in Tasks mit Rollen, zugeordneten Dateien und Abschluss-Signalen herunter.
2. **Spawnen.** Agenten starten in isolierten Git-Worktrees, einer pro Task. Der Main-Branch bleibt sauber.
3. **Verifizieren.** Der Janitor prüft konkrete Signale: Tests bestehen, Dateien existieren, Lint sauber, Typen korrekt.
4. **Mergen.** Verifizierte Arbeit landet in main. Fehlgeschlagene Tasks werden erneut versucht oder an ein anderes Modell geroutet.

Der Orchestrator ist ein Python-Scheduler, kein LLM. Scheduling-Entscheidungen sind deterministisch, auditierbar und reproduzierbar.

## Cloud-Ausführung (Cloudflare)

Bernstein kann Agenten statt lokal auf Cloudflare Workers ausführen. Die `bernstein cloud`-CLI übernimmt Deployment und Lifecycle.

- **Workers.** Agentenausführung am Cloudflare-Edge mit Durable Workflows für mehrstufige Tasks und automatischem Retry.
- **V8-Sandbox-Isolation.** Jeder Agent läuft in seiner eigenen Isolate, ohne Container-Overhead.
- **R2-Workspace-Sync.** Lokaler Worktree-State wird mit R2-Object-Storage synchronisiert, damit Cloud-Agenten dieselben Dateien sehen.
- **Workers AI** (experimentell). Cloudflare-gehostete Modelle als LLM-Provider nutzen, ohne externe API-Keys.
- **D1-Analytics.** Task-Metriken und Kostendaten werden in D1 für Abfragen gespeichert.
- **Vectorize.** Semantischer Cache, gestützt auf Cloudflares Vektor-Datenbank.
- **Browser Rendering.** Headless-Chrome auf Workers für Agenten, die Web-Output inspizieren müssen.
- **MCP Remote Transport.** MCP-Server über das Cloudflare-Netzwerk bereitstellen oder konsumieren.

```bash
bernstein cloud login      # bei Bernstein Cloud authentifizieren
bernstein cloud deploy     # Agenten-Worker pushen
bernstein cloud run plan.yaml  # Plan auf Cloudflare ausführen
```

Ein `bernstein cloud init`-Scaffold für `wrangler.toml` und Bindings ist geplant.

## Funktionen

**Kern-Orchestrierung.** Parallele Ausführung, Git-Worktree-Isolation, Janitor-Verifizierung, Quality Gates (Lint, Typen, PII-Scan), Cross-Modell-Code-Review, Circuit Breaker für fehlerhafte Agenten, Token-Wachstums-Monitoring mit Auto-Intervention.

**Intelligenz.** Contextual-Bandit-Router für Modell-/Aufwandsauswahl. Knowledge Graph für Codebase-Impact-Analyse. Semantisches Caching spart Tokens bei wiederkehrenden Mustern. Kosten-Anomalieerkennung (Burn-Rate-Alerts). Verhaltens-Anomalieerkennung mit Z-Score-Markierung.

**Sandboxing.** Steckbares [`SandboxBackend`](../../docs/architecture/sandbox.md)-Protokoll — Agenten laufen in lokalen Git-Worktrees (Standard), Docker-Containern, [E2B](https://e2b.dev) Firecracker-MicroVMs oder [Modal](https://modal.com) Serverless-Containern (optional mit GPU). Plugin-Autoren können eigene Backends über die Entry-Point-Gruppe `bernstein.sandbox_backends` registrieren. Installierte Backends prüfst du mit `bernstein agents sandbox-backends`.

**Artefakt-Speicherung.** Der `.sdd/`-State kann an steckbare [`ArtifactSink`](../../docs/architecture/storage.md)-Backends gestreamt werden: lokales Dateisystem (Standard), S3, Google Cloud Storage, Azure Blob oder Cloudflare R2. `BufferedSink` wahrt den WAL-Crash-Safety-Vertrag, indem zuerst lokal mit fsync geschrieben und asynchron in die Ferne gespiegelt wird.

**Skill-Packs.** Progressive-Disclosure-[Skills](../../docs/architecture/skills.md) (OpenAI-Agents-SDK-Pattern): Im System-Prompt jedes Spawns landet nur ein kompakter Skill-Index, Agenten ziehen die vollständigen Inhalte bei Bedarf über das `load_skill`-MCP-Tool nach. 17 eingebaute Rollen-Packs plus Drittanbieter-`bernstein.skill_sources`-Entry-Points.

**Kontrolle.** HMAC-verkettete Audit-Logs, Policy-Engine, PII-Output-Gating, WAL-gestützte Crash-Recovery (experimentelle Multi-Worker-Sicherheit), OAuth 2.0 PKCE. SSO/SAML/OIDC-Support ist in Arbeit.

**Observability.** Prometheus-`/metrics`, OTel-Exporter-Presets, Grafana-Dashboards. Pro-Modell-Kostentracking (`bernstein cost`). Terminal-TUI und Web-Dashboard. Agentenprozesse sind in `ps` sichtbar.

**Ökosystem.** MCP-Server-Modus, A2A-Protokoll-Support, GitHub-App-Integration, pluggy-basiertes Plugin-System, Multi-Repo-Workspaces, Cluster-Modus für verteilte Ausführung, Selbst-Evolution via `--evolve` (experimentell).

Vollständige Feature-Matrix: [FEATURE_MATRIX.md](../../docs/reference/FEATURE_MATRIX.md) &middot; Aktuelle Funktionen: [What's New](../../docs/whats-new.md)

## Neu in v1.9

**ACP-Bridge** — `bernstein acp serve --stdio` macht Bernstein für jeden Editor verfügbar, der das Agent Communication Protocol spricht (Zed etc.). Auf Editor-Seite ist kein Plugin-Code nötig.

**Autonome CI-Reparatur** — `bernstein autofix` beobachtet offene Bernstein-PRs und spawnt automatisch einen Fixer-Agent, sobald CI rot wird. Sobald wieder grün, pusht es den Fix und fordert das Review erneut an.

**Credential-Vault** — `bernstein connect <provider>` schreibt API-Keys in den Schlüsselbund des Betriebssystems; `bernstein creds` listet sie auf und rotiert sie. Agenten erben gescopte Credentials, ohne Umgebungsvariablen anzufassen.

**Preview-Tunnel** — `bernstein preview start` startet einen sandboxed Dev-Server und gibt eine öffentliche URL aus. Praktisch, um einen laufenden Branch mit einem Reviewer zu teilen, ohne nach Staging deployen zu müssen.

Vollständiges Changelog: [docs/whats-new.md](../../docs/whats-new.md)

## Operator-Befehle

Befehle, die den Glue-Code überflüssig machen, den die meisten Teams sonst um ihre Läufe herum schreiben.

| Befehl | Was er tut |
|--------|------------|
| `bernstein pr` | Erzeugt automatisch einen GitHub-PR aus einer abgeschlossenen Session; der Body enthält die Gate-Ergebnisse des Janitors sowie die Token-/USD-Kostenaufschlüsselung. |
| `bernstein from-ticket <url>` | Importiert ein Linear- / GitHub-Issues- / Jira-Ticket als Bernstein-Task. Label-basierte Rollen- und Scope-Inferenz. Unterstützt `--dry-run` und `--run`. |
| `bernstein ticket import <url>` | Alias-/Gruppenform von `from-ticket` für Skripting. |
| `bernstein remote` | SSH-Sandbox-Backend. `remote test <host>`, `remote run <host> <path>`, `remote forget <host>`. ControlMaster-Socket-Wiederverwendung für schnelle Folgeaufrufe. |
| `bernstein hooks` | Lifecycle-Hooks für `pre_task`, `post_task`, `pre_merge`, `post_merge`, `pre_spawn`, `post_spawn` — Shell-Skripte oder pluggy-`@hookimpl`s. `hooks list`, `hooks run <event>`, `hooks check`. |
| `bernstein chat serve --platform=telegram\|discord\|slack` | Steuere Läufe per Chat mit `/run`, `/status`, `/approve`, `/reject`, `/switch`, `/stop`. |
| `bernstein approve-tool` / `bernstein reject-tool` | Interaktive Tool-Call-Freigabe mitten im Lauf. `--latest`, `--id`, `--always`. |
| `bernstein tunnel start <port> [--provider auto\|cloudflared\|ngrok\|bore\|tailscale]` | Ein Wrapper über vier Tunnel-Anbieter. Außerdem `tunnel list`, `tunnel stop <name>\|--all`. ControlMaster-artige Prozess-Wiederverwendung. |
| `bernstein daemon install [--user\|--system] [--command="..."] [--env KEY=VAL]...` | Installiert eine systemd- (Linux) oder launchd- (macOS) Unit für den Auto-Start. Außerdem `daemon start/stop/restart/status/uninstall`. |
| `bernstein connect <provider>` / `bernstein creds` | Speichert und rotiert API-Credentials im OS-Schlüsselbund. Agenten erben gescopte Keys pro Lauf. |
| `bernstein autofix` | Daemon, der offene Bernstein-PRs überwacht; spawnt einen Fixer-Agent, wenn CI fehlschlägt, und pusht die Reparatur automatisch. |
| `bernstein preview start` | Startet einen sandboxed Dev-Server für den aktuellen Branch und gibt eine teilbare öffentliche Tunnel-URL aus. |

## Im Vergleich

| Funktion | Bernstein | CrewAI | AutoGen [^autogen] | LangGraph |
|----------|-----------|--------|--------------------|-----------|
| Orchestrator | Deterministischer Code | LLM-getrieben (+ Code-Flows) | LLM-getrieben | Graph + LLM |
| Funktioniert mit | Beliebigem CLI-Agenten (41 Adapter) | Python-SDK-Klassen | Python-Agenten | LangChain-Knoten |
| Git-Isolation | Worktrees pro Agent | Nein | Nein | Nein |
| Steckbare Sandboxes | Worktree, Docker, E2B, Modal | Nein | Nein | Nein |
| Verifizierung | Janitor + Quality Gates | Guardrails + Pydantic-Output | Termination Conditions | Conditional Edges |
| Kostentracking | Eingebaut | `usage_metrics` | `RequestUsage` | Über LangSmith |
| State-Modell | Dateibasiert (.sdd/) | In-Memory + SQLite-Checkpoint | In-Memory | Checkpointer |
| Remote-Artefakt-Sinks | S3, GCS, Azure Blob, R2 | Nein | Nein | Nein |
| Selbst-Evolution | Eingebaut (experimentell) | Nein | Nein | Nein |
| Deklarative Pläne (YAML) | Ja | Ja (`agents.yaml`, `tasks.yaml`) | Nein | Teilweise (`langgraph.json`) |
| Modell-Routing pro Task | Ja | Pro-Agent-LLM | Pro-Agent-`model_client` | Pro-Knoten (manuell) |
| MCP-Support | Ja (Client + Server) | Ja | Ja (Client + Workbench) | Ja (Client + Server) |
| Agent-zu-Agent-Chat | Bulletin Board | Ja (Crew-Prozess) | Ja (Group Chat) | Ja (Supervisor, Swarm) |
| Web-UI | TUI + Web-Dashboard | CrewAI AMP | AutoGen Studio | LangGraph Studio + LangSmith |
| Cloud-Hosted-Option | Ja (Cloudflare) | Ja (CrewAI AMP) | Nein | Ja (LangGraph Cloud) |
| Eingebautes RAG/Retrieval | Ja (Codebase FTS5 + BM25) | `crewai_tools` | `autogen_ext`-Retriever | Über LangChain |

*Zuletzt verifiziert: 2026-04-19. Detaillierte Feature-Matrizen siehe [vollständige Vergleichsseiten](../../docs/compare/README.md).*

Die Tabelle oben vergleicht Bernstein mit LLM-Orchestrierungs-Frameworks (die LLM-Aufrufe orchestrieren). Die Tabelle unten deckt die nähere Kategorie ab — andere Tools, die **CLI-Coding-Agenten** orchestrieren:

| Funktion | Bernstein | [awslabs/cli-agent-orchestrator](https://github.com/awslabs/cli-agent-orchestrator) | [ComposioHQ/agent-orchestrator](https://github.com/ComposioHQ/agent-orchestrator) | [emdash](https://github.com/generalaction/emdash) | [umputun/ralphex](https://github.com/umputun/ralphex) |
|----------|-----------|-----------|-----------|-----------|-----------|
| Form | Python-CLI + Bibliothek + MCP-Server | Python-CLI + tmux-Sessions + Web-UI | TypeScript-CLI + lokales Dashboard | Electron-Desktop-App | Go-CLI |
| Hauptsprache | Python | Python | TypeScript | TypeScript | Go |
| Installation | `pipx install bernstein` | `uv tool install cli-agent-orchestrator` | `npm install -g @aoagents/ao` | `.dmg` / `.msi` / `.AppImage` | `go install` / Single-Binary |
| Agent-Adapter | 41 | 5 (Kiro, Claude Code, Codex, Gemini, Kimi) | 3 (Claude Code, Codex, Aider) | 24 | 1 (nur Claude Code) |
| Parallele Multi-Agent-Ausführung | Ja | Ja (tmux-Session pro Agent) | Ja | Ja | Nein (einzelne sequentielle Session) |
| Git-Worktree pro Agent | Ja | Nein (geplant, [#100](https://github.com/awslabs/cli-agent-orchestrator/issues/100)) | Ja | Ja | Optionales `--worktree`-Flag |
| MCP-Server-Modus (sich selbst als MCP exponieren) | Ja (stdio + HTTP/SSE) | Ja (Inter-Agent-Comms) | Nein | Nein | Nein |
| Koordinator | Deterministischer Python-Scheduler | Hierarchischer LLM-Supervisor | LLM-getrieben | Nicht dokumentiert | Linearer Plan-Executor |
| HMAC-verketteter Audit-Replay | Ja | Nein | Nein | Nein | Nein |
| Cross-Modell-Verifier / Quality Gates | Ja (mehrstufig) | Nein | Nein | Nein | Mehrphasiges Review (nur Claude) |
| Autonomer CI-Fix-/PR-Flow | Ja (`bernstein autofix`) | Nein | Ja | Nein | Nein |
| Visuelles Dashboard | TUI + Web | Web-UI + tmux | Web | Desktop-App | Web (`--serve`) |
| Notification-Sinks | Telegram/Slack/Discord/E-Mail/Webhook/Shell | — | Nein | Nein | Telegram / E-Mail / Slack / Webhook |
| Trägerschaft | Solo-OSS | AWS Labs | Finanziert (Composio.dev) | YC W26 | Solo-OSS |
| Lizenz | Apache 2.0 | Apache 2.0 | MIT | Apache 2.0 | MIT |

Bernsteins Wedge in dieser Kategorie: **Python-nativ, MCP-Server-first, breiteste Adapter-Abdeckung, echte Multi-Agent-Parallelität, deterministischer Scheduler ohne LLM in der Koordinationsschleife.** Wenn du AWS-konforme tmux-Session-Isolation mit hierarchischem LLM-Supervisor willst, passt AWS Labs' `cao` besser; wenn dein Stack TypeScript ist und du ein Produkt mit Dashboard möchtest, ist Composios `@aoagents/ao` die bessere Wahl; wenn du eine polierte Desktop-ADE willst, ist es emdash; wenn du ausschließlich Claude Code nutzt und ein einzelnes Go-Binary willst, das einen Plan von oben nach unten abarbeitet, dann ralphex. Wenn du eine Primitive willst, die sich in Python importieren lässt, sich über MCP gegenüber jedem Client exponiert, viele Agenten parallel laufen lässt und die volle Agenten-Breite (inklusive Qwen, Goose, Ollama, OpenAI Agents SDK, Cloudflare Agents und mehr) abdeckt — dann Bernstein.

[^autogen]: AutoGen ist im Wartungsmodus; Nachfolger ist Microsoft Agent Framework 1.0.

## Monitoring

```bash
bernstein live       # TUI-Dashboard
bernstein dashboard  # Web-Dashboard
bernstein status     # Task-Übersicht
bernstein ps         # laufende Agenten
bernstein cost       # Ausgaben pro Modell/Task
bernstein doctor     # Pre-Flight-Checks
bernstein recap      # Zusammenfassung nach dem Lauf
bernstein trace <ID> # Entscheidungs-Trace eines Agenten
bernstein run-changelog --hours 48  # Changelog aus Agent-erzeugten Diffs
bernstein explain <cmd>  # ausführliche Hilfe mit Beispielen
bernstein dry-run    # Tasks vorab anzeigen, ohne sie auszuführen
bernstein dep-impact # API-Brüche + Auswirkungen auf nachgelagerte Aufrufer
bernstein aliases    # Befehls-Kurzformen anzeigen
bernstein config-path    # Speicherorte der Konfigurationsdateien anzeigen
bernstein init-wizard    # interaktives Projekt-Setup
bernstein debug-bundle   # Logs, Konfiguration und State für Bug-Reports einsammeln
bernstein skills list    # auffindbare Skill-Packs (Progressive Disclosure)
bernstein skills show <name>  # Skill-Inhalt mit Referenzen ausgeben
```

```bash
bernstein fingerprint build --corpus-dir ~/oss-corpus  # lokalen Ähnlichkeitsindex bauen
bernstein fingerprint check src/foo.py                 # generierten Code gegen den Index prüfen
```

## Installation

| Methode | Befehl |
|---------|--------|
| **Einzeiler (macOS / Linux)** | `curl -fsSL https://bernstein.run/install.sh \| sh` |
| **Einzeiler (Windows)** | `irm https://bernstein.run/install.ps1 \| iex` |
| **pip** | `pip install bernstein` |
| **pipx** | `pipx install bernstein` |
| **uv** | `uv tool install bernstein` |
| **Homebrew** | `brew tap chernistry/tap && brew install bernstein` |
| **Fedora / RHEL** | `sudo dnf copr enable alexchernysh/bernstein && sudo dnf install bernstein` |
| **npm** (Wrapper) | `npx bernstein-orchestrator` |

Die Einzeiler-Skripte prüfen auf Python 3.12+, bootstrappen pipx, falls es fehlt, korrigieren den PATH für die aktuelle Session und installieren (oder aktualisieren) `bernstein`. Sie behandeln brew-verwaltete macOS-Umgebungen sowie das Fallback auf den Windows-`py -3`-Launcher. Skript-Quellen: [install.sh](../../scripts/install.sh) · [install.ps1](../../scripts/install.ps1).

### Optionale Extras

Provider-SDKs sind optional, damit die Basis-Installation schlank bleibt. Wähle, was du brauchst:

| Extra | Aktiviert |
|-------|-----------|
| `bernstein[openai]` | OpenAI-Agents-SDK-v2-Adapter (`openai_agents`) |
| `bernstein[docker]` | Docker-Sandbox-Backend |
| `bernstein[e2b]` | [E2B](https://e2b.dev)-MicroVM-Sandbox-Backend (benötigt `E2B_API_KEY`) |
| `bernstein[modal]` | [Modal](https://modal.com)-Sandbox-Backend, optional GPU (benötigt `MODAL_TOKEN_ID` / `MODAL_TOKEN_SECRET`) |
| `bernstein[s3]` | S3-Artefakt-Sink (über `boto3`) |
| `bernstein[gcs]` | Google-Cloud-Storage-Artefakt-Sink |
| `bernstein[azure]` | Azure-Blob-Artefakt-Sink |
| `bernstein[r2]` | Cloudflare-R2-Artefakt-Sink (S3-kompatibles `boto3`) |
| `bernstein[grpc]` | gRPC-Bridge |
| `bernstein[k8s]` | Kubernetes-Integrationen |

Extras lassen sich in eckigen Klammern kombinieren, z. B. `pip install 'bernstein[openai,docker,s3]'`.

Editor-Erweiterungen: [VS Marketplace](https://marketplace.visualstudio.com/items?itemName=alex-chernysh.bernstein) &middot; [Open VSX](https://open-vsx.org/extension/alex-chernysh/bernstein)

## Mitwirken

PRs sind willkommen. Setup und Code-Style siehe [CONTRIBUTING.md](../../CONTRIBUTING.md).

## Unterstützung

Wenn dir Bernstein Zeit spart: [GitHub Sponsors](https://github.com/sponsors/chernistry)

Kontakt: [forte@bernstein.run](mailto:forte@bernstein.run)

## Erwähnt in

Kuratierte Listen, Newsletter und Peer-Projekte, die Bernstein aufgegriffen haben:

- [**Python Weekly #742**](https://www.pythonweekly.com/p/python-weekly-issue-742-april-23-2026) (23. April 2026) — Newsletter-Erwähnung.
- [**Augment Code — 9 Open-Source Agent Orchestrators for AI Coding (2026)**](https://www.augmentcode.com/tools/open-source-agent-orchestrators) — redaktionelles Roundup; „das architektonisch interessanteste Tool in diesem Roundup."
- [**nibzard/awesome-agentic-patterns**](https://github.com/nibzard/awesome-agentic-patterns/blob/main/patterns/deterministic-zero-llm-orchestration.md) — Bernstein als Produktiv-Implementierung des Patterns „deterministic zero-LLM orchestration" zitiert.
- [**Jenqyang/Awesome-AI-Agents**](https://github.com/Jenqyang/Awesome-AI-Agents)
- [**jamesmurdza/awesome-ai-devtools**](https://github.com/jamesmurdza/awesome-ai-devtools)
- [**jim-schwoebel/awesome_ai_agents**](https://github.com/jim-schwoebel/awesome_ai_agents)
- [**Piebald-AI/awesome-gemini-cli**](https://github.com/Piebald-AI/awesome-gemini-cli)
- [**ComposioHQ/awesome-codex-skills**](https://github.com/ComposioHQ/awesome-codex-skills)
- [**jxzhangjhu/Awesome-LLM-RAG**](https://github.com/jxzhangjhu/Awesome-LLM-RAG)
- [**rohitg00/awesome-claude-code-toolkit**](https://github.com/rohitg00/awesome-claude-code-toolkit)
- [**numtide/llm-agents.nix**](https://github.com/numtide/llm-agents.nix) — Nix-Flake-Distribution.

<details>
<summary>Weitere Awesome-Listen und Community-Kuratierung</summary>

- [andyrewlee/awesome-agent-orchestrators](https://github.com/andyrewlee/awesome-agent-orchestrators)
- [bradAGI/awesome-cli-coding-agents](https://github.com/bradAGI/awesome-cli-coding-agents)
- [milisp/awesome-codex-cli](https://github.com/milisp/awesome-codex-cli)
- [yaolifeng0629/Awesome-independent-tools](https://github.com/yaolifeng0629/Awesome-independent-tools) (中文 + EN)
- [caramaschiHG/awesome-ai-agents-2026](https://github.com/caramaschiHG/awesome-ai-agents-2026)
- [ai-for-developers/awesome-vibe-coding](https://github.com/ai-for-developers/awesome-vibe-coding)
- [killop/anything_about_game](https://github.com/killop/anything_about_game) (`AI.md`)
- [Glama MCP Catalog](https://glama.ai/mcp/servers/sipyourdrink-ltd/bernstein) — redaktionelles MCP-Server-Listing.
- Mirrors: [icopy-site/awesome](https://github.com/icopy-site/awesome), [icopy-site/awesome-cn](https://github.com/icopy-site/awesome-cn), [trackawesomelist/trackawesomelist](https://github.com/trackawesomelist/trackawesomelist).

</details>

<details>
<summary>Als Vorarbeit von Peer-Projekten zitiert</summary>

- [**mkb23/overcode**](https://github.com/mkb23/overcode/blob/main/docs/design/bakeoffs/overcode-vs-bernstein.md) — ausführlicher Bake-off, der Bernstein als Referenzimplementierung behandelt.
- [**Vintersong/NOVA-Cognition-Framework**](https://github.com/Vintersong/NOVA-Cognition-Framework) — `BERNSTEIN_PATTERNS.md`, „Patterns Worth Borrowing".
- [**AJV009/drupal-contrib-workbench**](https://github.com/AJV009/drupal-contrib-workbench) — Forschungsnotizen zur Manager-/Janitor-Trennung.
- [**danielvaughan/codex-blog**](https://github.com/danielvaughan/codex-blog/blob/main/_posts/2026-04-09-loki-mode-autonomous-execution.md) — Vergleichsartikel, der Bernstein am deterministischen Ende einordnet.

</details>

## Star-History

[![Star History Chart](https://api.star-history.com/svg?repos=sipyourdrink-ltd/bernstein&type=Date)](https://star-history.com/#sipyourdrink-ltd/bernstein&Date)

## Lizenz

[Apache License 2.0](../../LICENSE)

---

Mit Liebe gemacht von [Alex Chernysh](https://alexchernysh.com) &middot; [GitHub](https://github.com/chernistry) &middot; [X](https://x.com/alex_chernysh) &middot; [bernstein.run](https://bernstein.run)

<!-- mcp-name: io.github.sipyourdrink-ltd/bernstein -->
