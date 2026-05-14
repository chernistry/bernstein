[English](../../README.md) | **Español (Spanish)** | [中文 (Chinese)](README.zh.md) | [العربية (Arabic)](README.ar.md) | [Português (Portuguese)](README.pt.md) | [Bahasa Indonesia (Indonesian)](README.id.md) | [Français (French)](README.fr.md) | [日本語 (Japanese)](README.ja.md) | [Русский (Russian)](README.ru.md) | [Deutsch (German)](README.de.md) | [עברית (Hebrew)](README.he.md) | [יידיש (Yiddish)](README.yi.md)

<div align="center">

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="../../docs/assets/logo-dark.svg">
  <source media="(prefers-color-scheme: light)" srcset="../../docs/assets/logo-light.svg">
  <img alt="Bernstein" src="../../docs/assets/logo-light.svg" width="340">
</picture>

<br>

> *"Para lograr grandes cosas se necesitan dos cosas: un plan y no tener del todo suficiente tiempo."* — Leonard Bernstein

### Orquesta cualquier agente de codificación con IA. Cualquier modelo. Un solo comando.

[![CI](https://github.com/sipyourdrink-ltd/bernstein/actions/workflows/ci.yml/badge.svg)](https://github.com/sipyourdrink-ltd/bernstein/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/bernstein)](https://pypi.org/project/bernstein/)
[![Python 3.12+](https://img.shields.io/badge/python-3.12+-3776ab?logo=python&logoColor=white)](https://python.org)
[![License](https://img.shields.io/github/license/sipyourdrink-ltd/bernstein)](../../LICENSE)
[![MseeP.ai](https://img.shields.io/badge/MseeP.ai-verified-2496ed)](https://mseep.ai/app/chernistry-bernstein)

[Sitio web](https://bernstein.run?utm_source=github.com&utm_medium=readme&utm_campaign=bernstein-readme) &middot; [Documentación](https://bernstein.readthedocs.io/) &middot; [Primeros pasos](../../docs/getting-started/GETTING_STARTED.md) &middot; [Glosario](../../docs/reference/GLOSSARY.md) &middot; [Limitaciones](../../docs/reference/KNOWN_LIMITATIONS.md)

</div>

---

Bernstein es un planificador determinista en Python que coordina un equipo de agentes CLI de codificación (Claude Code, Codex, Gemini CLI y 40 más) sobre un único objetivo, en worktrees de git paralelos, con una cadena de auditoría firmada con HMAC sobre cada paso.

### Instala en 30 segundos

```bash
pipx install bernstein
bernstein init
bernstein run -g "fix the failing test in tests/test_foo.py"
```

### Míralo en 60 segundos

El clip cubre una ejecución completa: el manager descompone el objetivo, tres agentes trabajan en paralelo, la cadena de auditoría registra cada handoff, el janitor verifica y se abre un PR.

<p align="center">
  <img alt="Demo Bernstein de 60 segundos: el manager descompone el objetivo, tres agentes corren en paralelo, la cadena de auditoría registra cada handoff, se abre un PR" src="../../docs/demo/demo.gif" width="800">
</p>

Después de la ejecución, Bernstein publica un comentario estructurado en el PR con coste, resultados de pruebas, lineage y la cadena hash de auditoría:

<p align="center">
  <img alt="Comentario de PR de Bernstein: secciones Resumen, Coste, Lineage, Pruebas, Cadena de auditoría" src="../../docs/demo/screenshot-pr-comment.svg" width="720">
</p>

> El GIF se genera desde [`docs/demo/demo.tape`](../../docs/demo/demo.tape) con [vhs](https://github.com/charmbracelet/vhs); regenéralo localmente con `vhs docs/demo/demo.tape`.

### Cómo se compara

| Función                                  | Bernstein   | Archon   | LangGraph |
|------------------------------------------|-------------|----------|-----------|
| Equipo multiagente (adaptadores en paralelo) | sí       | uno      | sí        |
| Lineage firmado / cadena de auditoría    | sí          | no       | no        |
| Despliegue air-gap / soberano            | sí          | parcial  | no        |
| Workflow YAML visual                     | sí [^yaml]  | sí       | no        |
| Dashboard alojado / SaaS                 | no          | parcial  | no        |

[^yaml]: El soporte de workflow YAML llega con [PR #1108](https://github.com/sipyourdrink-ltd/bernstein/pull/1108) (en este lote). Hasta entonces, los planes se escriben en Python o vía `bernstein run plan.yaml` contra el esquema heredado.

Una matriz de funciones más larga frente a CrewAI, AutoGen, LangGraph y los cuatro orquestadores de agentes CLI de la misma categoría que Bernstein vive en la sección [Comparación detallada](#detailed-comparison) más abajo.

---

### ¿Qué es esto, en un párrafo?

Tú le dices a Bernstein qué quieres construir. Reparte el trabajo entre varios agentes de codificación con IA, los ejecuta en paralelo dentro de worktrees de git aislados, registra cada handoff en un log de auditoría encadenado con HMAC, corre las pruebas y fusiona el código que realmente pasa. Vuelves y te encuentras un PR en verde.

Forward-deployed engineering, sobre un enjambre. Suelta Bernstein en el repo de un cliente y obtienes un equipo multiagente con estado en archivos, scoping de credenciales por agente y un audit trail firmado, ejecutándose sobre los agentes CLI en los que el cliente ya confía.

### Otras formas de instalar

```bash
curl -fsSL https://bernstein.run/install.sh | sh        # Una línea para macOS / Linux
irm https://bernstein.run/install.ps1 | iex             # PowerShell de Windows
pip install bernstein                                   # pip
uv tool install bernstein                               # uv
brew tap chernistry/tap && brew install bernstein       # Homebrew
```

Consulta la [matriz de instalación](#install) completa para `dnf copr`, `npx`, extras opcionales y la ruta de wheelhouse para sitios air-gapped.

### Por qué el scheduler es Python puro

La mayoría de los orquestadores de agentes usan un LLM para decidir quién hace qué. Eso es no determinista y quema tokens en planificación en lugar de en código. Bernstein realiza una sola llamada al LLM para descomponer tu objetivo, y todo lo demás (ejecutar agentes en paralelo, aislar sus ramas de git, correr pruebas, encaminar reintentos) es Python puro. Cada ejecución es reproducible. Cada paso queda registrado y se puede reproducir.

Sin framework que aprender. Sin dependencia de un proveedor. Intercambia cualquier agente, cualquier modelo, cualquier proveedor.

<img alt="Bernstein en acción: agentes de IA en paralelo orquestados en tiempo real" src="../../docs/assets/in-action-small.gif" width="700">

Lo que verás durante la ejecución:

```
$ bernstein -g "Add JWT auth"
[manager] decomposed into 4 tasks
[agent-1] claude-sonnet: src/auth/middleware.py  (done, 2m 14s)
[agent-2] codex:         tests/test_auth.py      (done, 1m 58s)
[verify]  all gates pass. merging to main.
```

## Agentes compatibles

Bernstein detecta automáticamente los agentes CLI instalados. Combínalos en una misma ejecución. Modelos locales económicos para el código repetitivo, modelos en la nube más potentes para la arquitectura.

43 adaptadores de agentes CLI: 40 envoltorios de terceros más un envoltorio genérico para cualquier herramienta con `--prompt`.

| Agent | Modelos | Instalación |
|-------|---------|-------------|
| [Claude Code](https://docs.anthropic.com/en/docs/claude-code) | Opus 4, Sonnet 4.6, Haiku 4.5 | `npm install -g @anthropic-ai/claude-code` |
| [Codex CLI](https://github.com/openai/codex) | GPT-5, GPT-5 mini | `npm install -g @openai/codex` |
| [OpenAI Agents SDK v2](https://openai.github.io/openai-agents-python/) | GPT-5, GPT-5 mini, o4 | `pip install 'bernstein[openai]'` |
| [GitHub Copilot CLI](https://docs.github.com/en/copilot/github-copilot-in-the-cli) | Gestionado por Copilot (GPT-5, Sonnet 4.6) | `npm install -g @github/copilot` |
| [Gemini CLI](https://github.com/google-gemini/gemini-cli) | Gemini 2.5 Pro, Gemini Flash | `npm install -g @google/gemini-cli` |
| [Cursor](https://www.cursor.com) | Sonnet 4.6, Opus 4, GPT-5 | [Aplicación Cursor](https://www.cursor.com) |
| [Aider](https://aider.chat) | Cualquiera compatible con OpenAI/Anthropic | `pip install aider-chat` |
| [Amp](https://ampcode.com) | Gestionado por Amp | `npm install -g @sourcegraph/amp` |
| [Cody](https://sourcegraph.com/cody) | Alojado por Sourcegraph | `npm install -g @sourcegraph/cody` |
| [Continue](https://continue.dev) | Cualquiera compatible con OpenAI/Anthropic | `npm install -g @continuedev/cli` (binario: `cn`) |
| [Goose](https://block.github.io/goose/) | Cualquier proveedor compatible con Goose | Consulta la [documentación de Goose](https://block.github.io/goose/) |
| [IaC](https://www.terraform.io/) (Terraform/Pulumi) | Cualquier proveedor que use el agente base | Integrado |
| [Kilo](https://kilo.dev) | Alojado por Kilo | Consulta la [documentación de Kilo](https://kilo.dev) |
| [Kiro](https://kiro.dev) | Alojado por Kiro | Consulta la [documentación de Kiro](https://kiro.dev) |
| [Ollama](https://ollama.ai) + Aider | Modelos locales (offline) | `brew install ollama` |
| [OpenCode](https://opencode.ai) | Cualquier proveedor compatible con OpenCode | Consulta la [documentación de OpenCode](https://opencode.ai) |
| [Qwen](https://github.com/QwenLM/qwen-code) | Modelos Qwen Code | `npm install -g @qwen-code/qwen-code` |
| [Cloudflare Agents](https://developers.cloudflare.com/agents/) | Modelos Workers AI | `bernstein cloud login` |
| [OpenHands](https://github.com/OpenHands/OpenHands) | Cualquiera compatible con LiteLLM (Anthropic, OpenAI, ...) | `uv tool install openhands --python 3.12` |
| [Open Interpreter](https://github.com/OpenInterpreter/open-interpreter) | Cualquiera (vía LiteLLM) | `pip install open-interpreter` |
| [gptme](https://github.com/gptme/gptme) | Anthropic, OpenAI, OpenRouter | `pipx install gptme` |
| [Plandex](https://github.com/plandex-ai/plandex) | Plandex Cloud o modelos auto-alojados | `curl -sL https://plandex.ai/install.sh \| bash` |
| [AIChat](https://github.com/sigoden/aichat) | OpenAI, Anthropic, OpenRouter, Groq, Gemini | `cargo install aichat` |
| [Letta Code](https://github.com/letta-ai/letta-code) | Enrutado por Letta (Anthropic, OpenAI) | `npm install -g @letta-ai/letta-code` |
| **Genérico** | Cualquier CLI con `--prompt` | Integrado |

#### Delegación de orquestadores (nodo hoja)

Una clase aparte y más reducida de adaptadores que envuelven **otros orquestadores CLI** como si fueran agentes individuales. Bernstein le entrega a la herramienta envuelta un prompt o un plan y solo ve el código de salida final: los costes de los subagentes y las puertas de calidad internas del orquestador envuelto no son visibles para Bernstein. Útil cuando quieres incluir un flujo existente construido sobre alguna de estas herramientas como un paso dentro de un plan más amplio de Bernstein.

| Orquestador | Envuelto como | Instalación |
|-------------|---------------|-------------|
| [Composio Agent Orchestrator](https://github.com/ComposioHQ/agent-orchestrator) (`@aoagents/ao`) | `composio` | `npm install -g @aoagents/ao` |
| [umputun/ralphex](https://github.com/umputun/ralphex) | `ralphex` | `go install github.com/umputun/ralphex/cmd/ralphex@latest` |

Cualquier adaptador funciona también como **LLM planificador interno**. Ejecuta toda la pila sin depender de un proveedor concreto:

```yaml
internal_llm_provider: gemini            # or qwen, ollama, codex, goose, ...
internal_llm_model: gemini-3.1-pro
```

> [!TIP]
> Ejecuta `bernstein --headless` para pipelines de CI. Sin TUI, salida JSON estructurada y código de salida distinto de cero ante fallos.

## Inicio rápido

```bash
cd your-project
bernstein init                    # crea el workspace .sdd/ + bernstein.yaml
bernstein -g "Add rate limiting"  # los agentes arrancan, trabajan en paralelo, verifican y salen
bernstein live                    # observa el progreso en el panel TUI
bernstein stop                    # apagado controlado con drenado
```

Para proyectos de varias etapas, define un plan YAML:

```bash
bernstein run plan.yaml           # omite la planificación con LLM y pasa directo a la ejecución
bernstein run --dry-run plan.yaml # previsualiza tareas y coste estimado
```

## Cómo funciona

1. **Descomponer**. El manager divide tu objetivo en tareas con roles, archivos asignados y señales de finalización.
2. **Lanzar**. Los agentes arrancan en worktrees de git aislados, uno por tarea. La rama principal se mantiene limpia.
3. **Verificar**. El janitor comprueba señales concretas: las pruebas pasan, los archivos existen, el lint está limpio, los tipos son correctos.
4. **Fusionar**. El trabajo verificado entra en main. Las tareas fallidas se reintentan o se redirigen a otro modelo.

El orquestador es un planificador en Python, no un LLM. Las decisiones de planificación son deterministas, auditables y reproducibles.

## Ejecución en la nube (Cloudflare)

Bernstein puede ejecutar agentes en Cloudflare Workers en lugar de localmente. La CLI `bernstein cloud` se encarga del despliegue y del ciclo de vida.

- **Workers**. Ejecución de agentes en el edge de Cloudflare, con Durable Workflows para tareas multi-paso y reintento automático.
- **Aislamiento de sandbox V8**. Cada agente corre en su propio isolate, sin la sobrecarga de un contenedor.
- **Sincronización de workspace en R2**. El estado del worktree local se sincroniza con el almacenamiento de objetos R2 para que los agentes en la nube vean los mismos archivos.
- **Workers AI** (experimental). Usa modelos alojados por Cloudflare como proveedor de LLM, sin necesidad de claves de API externas.
- **Analítica en D1**. Las métricas de tareas y los datos de coste se almacenan en D1 para consultarlos.
- **Vectorize**. Caché semántica respaldada por la base de datos vectorial de Cloudflare.
- **Renderizado de navegador**. Chrome headless en Workers para los agentes que necesitan inspeccionar la salida web.
- **Transporte remoto MCP**. Expón o consume servidores MCP a través de la red de Cloudflare.

```bash
bernstein cloud login      # autenticarse con Bernstein Cloud
bernstein cloud deploy     # publica los workers de los agentes
bernstein cloud run plan.yaml  # ejecuta un plan en Cloudflare
```

## Capacidades

**Orquestación principal**. Ejecución en paralelo, aislamiento por git worktree, verificación con janitor, puertas de calidad (lint, tipos, escaneo de PII), revisión de código entre modelos, circuit breaker para agentes que se desvían, monitorización del crecimiento de tokens con intervención automática.

**Inteligencia**. Enrutador con bandit contextual para selección de modelo y nivel de esfuerzo. Grafo de conocimiento para análisis de impacto sobre el código. La caché semántica ahorra tokens en patrones repetidos. Detección de anomalías de coste (alertas por tasa de consumo). Detección de anomalías de comportamiento mediante puntuación Z.

**Sandboxing**. Protocolo [`SandboxBackend`](../../docs/architecture/sandbox.md) enchufable: ejecuta agentes en git worktrees locales (predeterminado), contenedores Docker, microVMs Firecracker de [E2B](https://e2b.dev) o contenedores serverless de [Modal](https://modal.com) (con GPU opcional). Los autores de plugins pueden registrar backends personalizados a través del grupo de entry-points `bernstein.sandbox_backends`. Inspecciona los backends instalados con `bernstein agents sandbox-backends`.

**Almacenamiento de artefactos**. El estado de `.sdd/` puede transmitirse a backends [`ArtifactSink`](../../docs/architecture/storage.md) enchufables: sistema de archivos local (predeterminado), S3, Google Cloud Storage, Azure Blob o Cloudflare R2. `BufferedSink` mantiene el contrato de seguridad ante fallos del WAL escribiendo localmente con fsync primero y replicando al remoto de forma asíncrona.

**Skill packs**. [Skills](../../docs/architecture/skills.md) de divulgación progresiva (patrón del OpenAI Agents SDK): solo un índice compacto de skills se incluye en el system prompt de cada spawn, y los agentes recuperan el contenido completo bajo demanda mediante la herramienta MCP `load_skill`. 17 paquetes de roles integrados, además de entry-points `bernstein.skill_sources` de terceros.

**Controles**. Logs de auditoría encadenados con HMAC, motor de políticas, filtrado de PII en la salida, recuperación ante fallos respaldada por WAL (seguridad multi-worker experimental), OAuth 2.0 PKCE.

**Observabilidad**. `/metrics` de Prometheus, presets de exportador OTel, dashboards de Grafana. Seguimiento de coste por modelo (`bernstein cost`). TUI en terminal y dashboard web. Visibilidad de los procesos de agente en `ps`.

**Ecosistema**. Modo servidor MCP, soporte del protocolo A2A, integración con GitHub App, sistema de plugins basado en pluggy, workspaces multi-repo, modo cluster para ejecución distribuida, autoevolución vía `--evolve` (experimental).

Matriz completa de funcionalidades: [FEATURE_MATRIX.md](../../docs/reference/FEATURE_MATRIX.md) &middot; Funcionalidades recientes: [Novedades](../../docs/whats-new.md)

## Novedades en v1.9

**Puente ACP** — `bernstein acp serve --stdio` expone Bernstein a cualquier editor que hable el Agent Communication Protocol (Zed, etc.). No hace falta ningún plugin del lado del editor.

**Reparación autónoma de CI** — `bernstein autofix` vigila los PRs abiertos de Bernstein y, cuando el CI se pone en rojo, lanza automáticamente un agente reparador. Una vez en verde, sube la corrección y vuelve a solicitar revisión.

**Bóveda de credenciales** — `bernstein connect <provider>` guarda claves de API en el llavero del sistema operativo; `bernstein creds` las lista y las rota. Los agentes heredan credenciales con alcance acotado sin tocar las variables de entorno.

**Túneles de previsualización** — `bernstein preview start` arranca un servidor de desarrollo en sandbox e imprime una URL pública. Útil para compartir una rama en ejecución con un revisor sin desplegar a staging.

Changelog completo: [docs/whats-new.md](../../docs/whats-new.md)

## Comandos para operadores

Comandos que eliminan el código pegamento que la mayoría de equipos terminan escribiendo alrededor de sus ejecuciones.

| Comando | Qué hace |
|---------|----------|
| `bernstein pr` | Crea automáticamente un PR de GitHub a partir de una sesión completada; el cuerpo incluye los resultados de las puertas del janitor y el desglose de coste en tokens/USD. |
| `bernstein from-ticket <url>` | Importa un ticket de Linear / GitHub Issues / Jira como tarea de Bernstein. Inferencia de rol y alcance basada en etiquetas. Soporta `--dry-run` y `--run`. |
| `bernstein ticket import <url>` | Forma de alias / agrupada de `from-ticket` para scripting. |
| `bernstein remote` | Backend de sandbox sobre SSH. `remote test <host>`, `remote run <host> <path>`, `remote forget <host>`. Reutilización del socket ControlMaster para llamadas repetidas rápidas. |
| `bernstein hooks` | Hooks de ciclo de vida para `pre_task`, `post_task`, `pre_merge`, `post_merge`, `pre_spawn`, `post_spawn`: scripts de shell o `@hookimpl` de pluggy. `hooks list`, `hooks run <event>`, `hooks check`. |
| `bernstein chat serve --platform=telegram\|discord\|slack` | Controla las ejecuciones desde un chat con `/run`, `/status`, `/approve`, `/reject`, `/switch`, `/stop`. |
| `bernstein approve-tool` / `bernstein reject-tool` | Aprobación interactiva de llamadas a herramientas en mitad de la ejecución. `--latest`, `--id`, `--always`. |
| `bernstein tunnel start <port> [--provider auto\|cloudflared\|ngrok\|bore\|tailscale]` | Un único envoltorio sobre cuatro proveedores de túneles. También `tunnel list`, `tunnel stop <name>\|--all`. Reutilización de proceso al estilo ControlMaster. |
| `bernstein daemon install [--user\|--system] [--command="..."] [--env KEY=VAL]...` | Instala una unit de systemd (Linux) o launchd (macOS) para el arranque automático. También `daemon start/stop/restart/status/uninstall`. |
| `bernstein connect <provider>` / `bernstein creds` | Almacena y rota credenciales de API en el llavero del sistema operativo. Los agentes heredan claves con alcance específico por ejecución. |
| `bernstein autofix` | Daemon que monitoriza los PRs abiertos de Bernstein; lanza un agente reparador cuando el CI falla y sube la corrección automáticamente. |
| `bernstein preview start` | Arranca un servidor de desarrollo en sandbox para la rama actual e imprime una URL pública de túnel para compartir. |

## Cómo se compara

| Funcionalidad | Bernstein | CrewAI | AutoGen [^autogen] | LangGraph |
|---------------|-----------|--------|--------------------|-----------|
| Orquestador | Código determinista | Dirigido por LLM (+ Flows en código) | Dirigido por LLM | Grafo + LLM |
| Funciona con | Cualquier agente CLI (43 adaptadores) | Clases del SDK de Python | Agentes en Python | Nodos de LangChain |
| Aislamiento de Git | Worktrees por agente | No | No | No |
| Sandboxes enchufables | Worktree, Docker, E2B, Modal | No | No | No |
| Verificación | Janitor + puertas de calidad | Guardrails + salida Pydantic | Condiciones de terminación | Aristas condicionales |
| Seguimiento de coste | Integrado | `usage_metrics` | `RequestUsage` | Vía LangSmith |
| Modelo de estado | Basado en archivos (.sdd/) | En memoria + checkpoint en SQLite | En memoria | Checkpointer |
| Sinks remotos de artefactos | S3, GCS, Azure Blob, R2 | No | No | No |
| Autoevolución | Integrada (experimental) | No | No | No |
| Planes declarativos (YAML) | Sí | Sí (`agents.yaml`, `tasks.yaml`) | No | Parcial (`langgraph.json`) |
| Enrutado de modelo por tarea | Sí | LLM por agente | `model_client` por agente | Por nodo (manual) |
| Soporte MCP | Sí (cliente + servidor) | Sí | Sí (cliente + workbench) | Sí (cliente + servidor) |
| Chat agente a agente | Tablón de anuncios | Sí (proceso Crew) | Sí (chat grupal) | Sí (supervisor, swarm) |
| UI web | TUI + dashboard web | CrewAI AMP | AutoGen Studio | LangGraph Studio + LangSmith |
| Opción alojada en la nube | Sí (Cloudflare) | Sí (CrewAI AMP) | No | Sí (LangGraph Cloud) |
| RAG/recuperación integrada | Sí (FTS5 + BM25 sobre el código) | `crewai_tools` | Recuperadores en `autogen_ext` | Vía LangChain |

*Última verificación: 2026-04-19. Consulta las [páginas de comparación completa](../../docs/compare/README.md) para ver matrices de funcionalidades detalladas.*

La tabla anterior compara Bernstein con frameworks de orquestación de LLM (que orquestan llamadas a LLMs). La siguiente cubre la categoría más cercana: otras herramientas que orquestan **agentes de codificación CLI**:

| Funcionalidad | Bernstein | [awslabs/cli-agent-orchestrator](https://github.com/awslabs/cli-agent-orchestrator) | [ComposioHQ/agent-orchestrator](https://github.com/ComposioHQ/agent-orchestrator) | [emdash](https://github.com/generalaction/emdash) | [umputun/ralphex](https://github.com/umputun/ralphex) |
|---------------|-----------|-----------|-----------|-----------|-----------|
| Forma | CLI + librería en Python + servidor MCP | CLI en Python + sesiones tmux + UI web | CLI en TypeScript + dashboard local | App de escritorio Electron | CLI en Go |
| Lenguaje principal | Python | Python | TypeScript | TypeScript | Go |
| Instalación | `pipx install bernstein` | `uv tool install cli-agent-orchestrator` | `npm install -g @aoagents/ao` | `.dmg` / `.msi` / `.AppImage` | `go install` / binario único |
| Adaptadores de agentes | 43 | 5 (Kiro, Claude Code, Codex, Gemini, Kimi) | 3 (Claude Code, Codex, Aider) | 24 | 1 (solo Claude Code) |
| Ejecución multi-agente en paralelo | Sí | Sí (sesión tmux por agente) | Sí | Sí | No (una única sesión secuencial) |
| Worktree de Git por agente | Sí | No (planeado, [#100](https://github.com/awslabs/cli-agent-orchestrator/issues/100)) | Sí | Sí | Flag opcional `--worktree` |
| Modo servidor MCP (se expone como MCP) | Sí (stdio + HTTP/SSE) | Sí (comunicación entre agentes) | No | No | No |
| Coordinador | Planificador determinista en Python | Supervisor LLM jerárquico | Dirigido por LLM | No documentado | Ejecutor lineal de planes |
| Replay de auditoría encadenada con HMAC | Sí | No | No | No | No |
| Verificador entre modelos / puertas de calidad | Sí (multi-etapa) | No | No | No | Revisión multifase (solo Claude) |
| Flujo autónomo de fix de CI / PR | Sí (`bernstein autofix`) | No | Sí | No | No |
| Dashboard visual | TUI + web | UI web + tmux | Web | App de escritorio | Web (`--serve`) |
| Sinks de notificaciones | Telegram/Slack/Discord/Email/Webhook/Shell | — | No | No | Telegram / Email / Slack / Webhook |
| Respaldo | OSS en solitario | AWS Labs | Financiado (Composio.dev) | YC W26 | OSS en solitario |
| Licencia | Apache 2.0 | Apache 2.0 | MIT | Apache 2.0 | MIT |

La cuña de Bernstein en esta categoría: **nativo de Python, MCP-server-first, la cobertura más amplia de adaptadores, paralelismo multi-agente real, planificador determinista sin LLM en el bucle de coordinación**. Si quieres aislamiento por sesión tmux alineado con AWS y un supervisor LLM jerárquico, `cao` de AWS Labs encaja mejor; si tu stack es TypeScript y quieres un producto con dashboard, `@aoagents/ao` de Composio es mejor opción; si quieres un ADE de escritorio pulido, esa es emdash; si solo usas Claude Code y quieres un único binario en Go que recorra un plan de arriba abajo, entonces ralphex. Si lo que quieres es una primitiva que se importe en Python, se exponga sobre MCP a cualquier cliente, ejecute muchos agentes en paralelo y cubra toda la amplitud de agentes (incluidos Qwen, Goose, Ollama, OpenAI Agents SDK, Cloudflare Agents y más): Bernstein.

[^autogen]: AutoGen está en modo de mantenimiento; su sucesor es Microsoft Agent Framework 1.0.

## Monitorización

```bash
bernstein live       # TUI dashboard
bernstein dashboard  # web dashboard
bernstein status     # task summary
bernstein ps         # running agents
bernstein cost       # spend by model/task
bernstein doctor     # pre-flight checks
bernstein recap      # post-run summary
bernstein trace <ID> # agent decision trace
bernstein run-changelog --hours 48  # changelog from agent-produced diffs
bernstein explain <cmd>  # detailed help with examples
bernstein dry-run    # preview tasks without executing
bernstein dep-impact # API breakage + downstream caller impact
bernstein aliases    # show command shortcuts
bernstein config-path    # show config file locations
bernstein init-wizard    # interactive project setup
bernstein debug-bundle   # collect logs, config, and state for bug reports
bernstein skills list    # discoverable skill packs (progressive disclosure)
bernstein skills show <name>  # print a skill body with its references
```

```bash
bernstein fingerprint build --corpus-dir ~/oss-corpus  # build local similarity index
bernstein fingerprint check src/foo.py                 # check generated code against the index
```

## Instalación

| Método | Comando |
|--------|---------|
| **Una línea (macOS / Linux)** | `curl -fsSL https://bernstein.run/install.sh \| sh` |
| **Una línea (Windows)** | `irm https://bernstein.run/install.ps1 \| iex` |
| **pip** | `pip install bernstein` |
| **pipx** | `pipx install bernstein` |
| **uv** | `uv tool install bernstein` |
| **Homebrew** | `brew tap chernistry/tap && brew install bernstein` |
| **Fedora / RHEL** | `sudo dnf copr enable alexchernysh/bernstein && sudo dnf install bernstein` |
| **npm** (envoltorio) | `npx bernstein-orchestrator` |

Los scripts de una sola línea comprueban Python 3.12+, hacen bootstrap de pipx si falta, arreglan el PATH para la sesión actual e instalan (o actualizan) `bernstein`. Manejan entornos de macOS gestionados por brew y el fallback con el lanzador `py -3` en Windows. Fuente de los scripts: [install.sh](../../scripts/install.sh) · [install.ps1](../../scripts/install.ps1).

### Extras opcionales

Los SDKs de los proveedores son opcionales para que la instalación base se mantenga ligera. Elige lo que necesites:

| Extra | Habilita |
|-------|----------|
| `bernstein[openai]` | Adaptador OpenAI Agents SDK v2 (`openai_agents`) |
| `bernstein[docker]` | Backend de sandbox con Docker |
| `bernstein[e2b]` | Backend de sandbox microVM con [E2B](https://e2b.dev) (requiere `E2B_API_KEY`) |
| `bernstein[modal]` | Backend de sandbox con [Modal](https://modal.com), GPU opcional (requiere `MODAL_TOKEN_ID` / `MODAL_TOKEN_SECRET`) |
| `bernstein[s3]` | Sink de artefactos en S3 (vía `boto3`) |
| `bernstein[gcs]` | Sink de artefactos en Google Cloud Storage |
| `bernstein[azure]` | Sink de artefactos en Azure Blob |
| `bernstein[r2]` | Sink de artefactos en Cloudflare R2 (compatible con S3 vía `boto3`) |
| `bernstein[grpc]` | Puente gRPC |
| `bernstein[k8s]` | Integraciones con Kubernetes |

Combina extras con corchetes, p. ej. `pip install 'bernstein[openai,docker,s3]'`.

Extensiones de editor: [VS Marketplace](https://marketplace.visualstudio.com/items?itemName=alex-chernysh.bernstein) &middot; [Open VSX](https://open-vsx.org/extension/alex-chernysh/bernstein)

## Contribuir

Se aceptan PRs. Consulta [CONTRIBUTING.md](../../CONTRIBUTING.md) para la configuración y el estilo de código.

## Apoyo

Si Bernstein te ahorra tiempo: [GitHub Sponsors](https://github.com/sponsors/chernistry)

Contacto: [forte@bernstein.run](mailto:forte@bernstein.run)

## Mencionado en

Listas curadas, newsletters y proyectos pares que han recogido a Bernstein:

- [**Python Weekly #742**](https://www.pythonweekly.com/p/python-weekly-issue-742-april-23-2026) (23 de abril de 2026): mención en la newsletter.
- [**Augment Code — 9 Open-Source Agent Orchestrators for AI Coding (2026)**](https://www.augmentcode.com/tools/open-source-agent-orchestrators): recopilación editorial; "la herramienta arquitectónicamente más interesante de esta selección".
- [**nibzard/awesome-agentic-patterns**](https://github.com/nibzard/awesome-agentic-patterns/blob/main/patterns/deterministic-zero-llm-orchestration.md): se cita a Bernstein como la implementación en producción del patrón "orquestación determinista sin LLM".
- [**Jenqyang/Awesome-AI-Agents**](https://github.com/Jenqyang/Awesome-AI-Agents)
- [**jamesmurdza/awesome-ai-devtools**](https://github.com/jamesmurdza/awesome-ai-devtools)
- [**jim-schwoebel/awesome_ai_agents**](https://github.com/jim-schwoebel/awesome_ai_agents)
- [**Piebald-AI/awesome-gemini-cli**](https://github.com/Piebald-AI/awesome-gemini-cli)
- [**ComposioHQ/awesome-codex-skills**](https://github.com/ComposioHQ/awesome-codex-skills)
- [**jxzhangjhu/Awesome-LLM-RAG**](https://github.com/jxzhangjhu/Awesome-LLM-RAG)
- [**rohitg00/awesome-claude-code-toolkit**](https://github.com/rohitg00/awesome-claude-code-toolkit)
- [**numtide/llm-agents.nix**](https://github.com/numtide/llm-agents.nix): distribución como flake de Nix.

<details>
<summary>Más listas awesome y curación comunitaria</summary>

- [andyrewlee/awesome-agent-orchestrators](https://github.com/andyrewlee/awesome-agent-orchestrators)
- [bradAGI/awesome-cli-coding-agents](https://github.com/bradAGI/awesome-cli-coding-agents)
- [milisp/awesome-codex-cli](https://github.com/milisp/awesome-codex-cli)
- [yaolifeng0629/Awesome-independent-tools](https://github.com/yaolifeng0629/Awesome-independent-tools) (中文 + EN)
- [caramaschiHG/awesome-ai-agents-2026](https://github.com/caramaschiHG/awesome-ai-agents-2026)
- [ai-for-developers/awesome-vibe-coding](https://github.com/ai-for-developers/awesome-vibe-coding)
- [killop/anything_about_game](https://github.com/killop/anything_about_game) (`AI.md`)
- [Glama MCP Catalog](https://glama.ai/mcp/servers/sipyourdrink-ltd/bernstein): listado editorial de servidores MCP.
- Espejos: [icopy-site/awesome](https://github.com/icopy-site/awesome), [icopy-site/awesome-cn](https://github.com/icopy-site/awesome-cn), [trackawesomelist/trackawesomelist](https://github.com/trackawesomelist/trackawesomelist).

</details>

<details>
<summary>Citado como precedente por proyectos pares</summary>

- [**mkb23/overcode**](https://github.com/mkb23/overcode/blob/main/docs/design/bakeoffs/overcode-vs-bernstein.md): comparativa extensa que toma a Bernstein como implementación de referencia.
- [**Vintersong/NOVA-Cognition-Framework**](https://github.com/Vintersong/NOVA-Cognition-Framework): `BERNSTEIN_PATTERNS.md`, "Patterns Worth Borrowing".
- [**AJV009/drupal-contrib-workbench**](https://github.com/AJV009/drupal-contrib-workbench): notas de investigación sobre la separación manager/janitor.
- [**danielvaughan/codex-blog**](https://github.com/danielvaughan/codex-blog/blob/main/_posts/2026-04-09-loki-mode-autonomous-execution.md): artículo comparativo que sitúa a Bernstein en el extremo determinista.

</details>

## Historial de estrellas

[![Star History Chart](https://api.star-history.com/svg?repos=sipyourdrink-ltd/bernstein&type=Date)](https://star-history.com/#sipyourdrink-ltd/bernstein&Date)

## Licencia

[Apache License 2.0](../../LICENSE)

---

Hecho con cariño por [Alex Chernysh](https://alexchernysh.com) &middot; [GitHub](https://github.com/chernistry) &middot; [X](https://x.com/alex_chernysh) &middot; [bernstein.run](https://bernstein.run?utm_source=github.com&utm_medium=readme&utm_campaign=bernstein-readme)

<!-- mcp-name: io.github.sipyourdrink-ltd/bernstein -->
