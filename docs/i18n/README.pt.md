<div align="center">

[English](../../README.md) | [Español (Spanish)](README.es.md) | [中文 (Chinese)](README.zh.md) | [العربية (Arabic)](README.ar.md) | **Português (Portuguese)** | [Bahasa Indonesia (Indonesian)](README.id.md) | [Français (French)](README.fr.md) | [日本語 (Japanese)](README.ja.md) | [Русский (Russian)](README.ru.md) | [Deutsch (German)](README.de.md) | [עברית (Hebrew)](README.he.md) | [יידיש (Yiddish)](README.yi.md)

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="../../docs/assets/logo-dark.svg">
  <source media="(prefers-color-scheme: light)" srcset="../../docs/assets/logo-light.svg">
  <img alt="Bernstein" src="../../docs/assets/logo-light.svg" width="340">
</picture>

<br>

> *"Para realizar grandes coisas, duas coisas são necessárias: um plano e tempo não exatamente suficiente."* — Leonard Bernstein

### Orquestre qualquer agente de codificação de IA. Qualquer modelo. Um único comando.

[![CI](https://github.com/sipyourdrink-ltd/bernstein/actions/workflows/ci.yml/badge.svg)](https://github.com/sipyourdrink-ltd/bernstein/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/bernstein)](https://pypi.org/project/bernstein/)
[![Python 3.12+](https://img.shields.io/badge/python-3.12+-3776ab?logo=python&logoColor=white)](https://python.org)
[![License](https://img.shields.io/github/license/sipyourdrink-ltd/bernstein)](../../LICENSE)
[![MseeP.ai](https://img.shields.io/badge/MseeP.ai-verified-2496ed)](https://mseep.ai/app/chernistry-bernstein)

[Site](https://bernstein.run?utm_source=github.com&utm_medium=readme&utm_campaign=bernstein-readme) &middot; [Documentação](https://bernstein.readthedocs.io/) &middot; [Primeiros Passos](../../docs/getting-started/GETTING_STARTED.md) &middot; [Glossário](../../docs/reference/GLOSSARY.md) &middot; [Limitações](../../docs/reference/KNOWN_LIMITATIONS.md)

</div>

---

O Bernstein é um escalonador determinístico em Python que executa uma equipe de agentes CLI de codificação (Claude Code, Codex, Gemini CLI e mais 40) sobre um único objetivo, em worktrees git paralelos, com uma cadeia de auditoria assinada por HMAC sobre cada passo.

### Instale em 30 segundos

```bash
pipx install bernstein
bernstein init
bernstein run -g "fix the failing test in tests/test_foo.py"
```

### Veja em 60 segundos

O clipe cobre uma execução completa: o manager decompõe o objetivo, três agentes trabalham em paralelo, a cadeia de auditoria registra cada handoff, o janitor verifica e um PR é aberto.

<p align="center">
  <img alt="Demo Bernstein de 60 segundos: o manager decompõe o objetivo, três agentes rodam em paralelo, a cadeia de auditoria registra cada handoff, abre-se um PR" src="../../docs/demo/demo.gif" width="800">
</p>

Após a execução, o Bernstein publica um comentário estruturado no PR com custo, resultados de testes, lineage e a cadeia hash de auditoria:

<p align="center">
  <img alt="Comentário do Bernstein no PR: seções Resumo, Custo, Lineage, Testes, Cadeia de auditoria" src="../../docs/demo/screenshot-pr-comment.svg" width="720">
</p>

> O GIF é gerado a partir de [`docs/demo/demo.tape`](../../docs/demo/demo.tape) com [vhs](https://github.com/charmbracelet/vhs); regenere localmente com `vhs docs/demo/demo.tape`.

### Como ele se compara

| Recurso                                    | Bernstein   | Archon   | LangGraph |
|--------------------------------------------|-------------|----------|-----------|
| Equipe multiagente (adaptadores em paralelo) | sim       | um       | sim       |
| Lineage assinado / cadeia de auditoria     | sim         | não      | não       |
| Implantação air-gap / soberana             | sim         | parcial  | não       |
| Workflow YAML visual                       | sim [^yaml] | sim      | não       |
| Painel hospedado / SaaS                    | não         | parcial  | não       |

[^yaml]: O suporte a workflow YAML chega com a [PR #1108](https://github.com/sipyourdrink-ltd/bernstein/pull/1108) (neste lote). Até lá, os planos são escritos em Python ou via `bernstein run plan.yaml` no schema antigo.

Uma matriz de recursos mais longa contra CrewAI, AutoGen, LangGraph e os quatro orquestradores de agentes CLI da mesma categoria do Bernstein vive na seção [Comparação detalhada](#detailed-comparison) abaixo.

---

### O que é isto, em um parágrafo?

Você diz ao Bernstein o que quer construir. Ele divide o trabalho entre vários agentes de codificação de IA, roda-os em paralelo dentro de worktrees git isolados, registra cada handoff em um log de auditoria encadeado por HMAC, executa os testes e mescla o código que de fato passa. Você volta para um PR verde.

Forward-deployed engineering, em formato de enxame. Solte o Bernstein em um repo de cliente e você terá uma equipe multiagente com estado em arquivos, escopo de credenciais por agente e um audit trail assinado, rodando sobre os agentes CLI nos quais o cliente já confia.

### Outros métodos de instalação

```bash
curl -fsSL https://bernstein.run/install.sh | sh        # Uma linha no macOS / Linux
irm https://bernstein.run/install.ps1 | iex             # PowerShell do Windows
pip install bernstein                                   # pip
uv tool install bernstein                               # uv
brew tap chernistry/tap && brew install bernstein       # Homebrew
```

Veja a [matriz de instalação](#install) completa para `dnf copr`, `npx`, extras opcionais e o caminho de wheelhouse para sites air-gapped.

### Por que o escalonador é Python puro

A maioria dos orquestradores de agentes usa um LLM para decidir quem faz o quê. Isso é não determinístico e queima tokens em escalonamento em vez de em código. O Bernstein faz uma única chamada ao LLM para decompor seu objetivo, e o restante (executar agentes em paralelo, isolar suas branches git, rodar testes, rotear retentativas) é Python puro. Cada execução é reproduzível. Cada passo é registrado e pode ser reproduzido novamente.

Sem framework para aprender. Sem aprisionamento a fornecedores. Troque qualquer agente, qualquer modelo, qualquer provedor.

<img alt="Bernstein em ação: agentes de IA paralelos orquestrados em tempo real" src="../../docs/assets/in-action-small.gif" width="700">

O que você vê durante a execução:

```
$ bernstein -g "Add JWT auth"
[manager] decomposed into 4 tasks
[agent-1] claude-sonnet: src/auth/middleware.py  (done, 2m 14s)
[agent-2] codex:         tests/test_auth.py      (done, 1m 58s)
[verify]  all gates pass. merging to main.
```

## Agentes suportados

O Bernstein descobre automaticamente os agentes CLI instalados. Combine-os na mesma execução. Modelos locais baratos para boilerplate, modelos em nuvem mais robustos para arquitetura.

43 adaptadores de agentes CLI: 40 wrappers de terceiros mais um wrapper genérico para qualquer coisa com `--prompt`.

| Agente | Modelos | Instalação |
|-------|--------|---------|
| [Claude Code](https://docs.anthropic.com/en/docs/claude-code) | Opus 4, Sonnet 4.6, Haiku 4.5 | `npm install -g @anthropic-ai/claude-code` |
| [Codex CLI](https://github.com/openai/codex) | GPT-5, GPT-5 mini | `npm install -g @openai/codex` |
| [OpenAI Agents SDK v2](https://openai.github.io/openai-agents-python/) | GPT-5, GPT-5 mini, o4 | `pip install 'bernstein[openai]'` |
| [GitHub Copilot CLI](https://docs.github.com/en/copilot/github-copilot-in-the-cli) | gerenciado pelo Copilot (GPT-5, Sonnet 4.6) | `npm install -g @github/copilot` |
| [Gemini CLI](https://github.com/google-gemini/gemini-cli) | Gemini 2.5 Pro, Gemini Flash | `npm install -g @google/gemini-cli` |
| [Cursor](https://www.cursor.com) | Sonnet 4.6, Opus 4, GPT-5 | [App do Cursor](https://www.cursor.com) |
| [Aider](https://aider.chat) | Qualquer compatível com OpenAI/Anthropic | `pip install aider-chat` |
| [Amp](https://ampcode.com) | gerenciado pelo Amp | `npm install -g @sourcegraph/amp` |
| [Cody](https://sourcegraph.com/cody) | hospedado pelo Sourcegraph | `npm install -g @sourcegraph/cody` |
| [Continue](https://continue.dev) | Qualquer compatível com OpenAI/Anthropic | `npm install -g @continuedev/cli` (binário: `cn`) |
| [Goose](https://block.github.io/goose/) | Qualquer provedor suportado pelo Goose | Veja a [documentação do Goose](https://block.github.io/goose/) |
| [IaC](https://www.terraform.io/) (Terraform/Pulumi) | Qualquer provedor que o agente base utilize | Integrado |
| [Kilo](https://kilo.dev) | hospedado pelo Kilo | Veja a [documentação do Kilo](https://kilo.dev) |
| [Kiro](https://kiro.dev) | hospedado pelo Kiro | Veja a [documentação do Kiro](https://kiro.dev) |
| [Ollama](https://ollama.ai) + Aider | Modelos locais (offline) | `brew install ollama` |
| [OpenCode](https://opencode.ai) | Qualquer provedor suportado pelo OpenCode | Veja a [documentação do OpenCode](https://opencode.ai) |
| [Qwen](https://github.com/QwenLM/qwen-code) | Modelos Qwen Code | `npm install -g @qwen-code/qwen-code` |
| [Cloudflare Agents](https://developers.cloudflare.com/agents/) | Modelos do Workers AI | `bernstein cloud login` |
| [OpenHands](https://github.com/OpenHands/OpenHands) | Qualquer suportado por LiteLLM (Anthropic, OpenAI, ...) | `uv tool install openhands --python 3.12` |
| [Open Interpreter](https://github.com/OpenInterpreter/open-interpreter) | Qualquer (apoiado pelo LiteLLM) | `pip install open-interpreter` |
| [gptme](https://github.com/gptme/gptme) | Anthropic, OpenAI, OpenRouter | `pipx install gptme` |
| [Plandex](https://github.com/plandex-ai/plandex) | Plandex Cloud ou modelos auto-hospedados | `curl -sL https://plandex.ai/install.sh \| bash` |
| [AIChat](https://github.com/sigoden/aichat) | OpenAI, Anthropic, OpenRouter, Groq, Gemini | `cargo install aichat` |
| [Letta Code](https://github.com/letta-ai/letta-code) | Roteado pelo Letta (Anthropic, OpenAI) | `npm install -g @letta-ai/letta-code` |
| **Genérico** | Qualquer CLI com `--prompt` | Integrado |

#### Delegação de orquestrador (nó-folha)

Uma classe separada e menor de adaptadores que envolvem **outros orquestradores de CLI** como se fossem agentes individuais. O Bernstein entrega à ferramenta encapsulada um prompt ou plano e enxerga apenas o código de saída final; custos de subagentes e os portões de qualidade dentro do orquestrador encapsulado não são visíveis para o Bernstein. Útil quando você quer encaixar um fluxo de trabalho existente, construído sobre uma dessas ferramentas, em uma etapa de um plano maior do Bernstein.

| Orquestrador | Encapsulado como | Instalação |
|--------------|------------|---------|
| [Composio Agent Orchestrator](https://github.com/ComposioHQ/agent-orchestrator) (`@aoagents/ao`) | `composio` | `npm install -g @aoagents/ao` |
| [umputun/ralphex](https://github.com/umputun/ralphex) | `ralphex` | `go install github.com/umputun/ralphex/cmd/ralphex@latest` |

Qualquer adaptador também funciona como o **LLM agendador interno**. Execute toda a stack sem nenhum provedor específico:

```yaml
internal_llm_provider: gemini            # or qwen, ollama, codex, goose, ...
internal_llm_model: gemini-3.1-pro
```

> [!TIP]
> Execute `bernstein --headless` para pipelines de CI. Sem TUI, saída JSON estruturada, código de saída diferente de zero em caso de falha.

## Início rápido

```bash
cd your-project
bernstein init                    # creates .sdd/ workspace + bernstein.yaml
bernstein -g "Add rate limiting"  # agents spawn, work in parallel, verify, exit
bernstein live                    # watch progress in the TUI dashboard
bernstein stop                    # graceful shutdown with drain
```

Para projetos com múltiplas etapas, defina um plano em YAML:

```bash
bernstein run plan.yaml           # skips LLM planning, goes straight to execution
bernstein run --dry-run plan.yaml # preview tasks and estimated cost
```

## Como funciona

1. **Decompor**. O gerente quebra seu objetivo em tarefas com papéis, arquivos sob responsabilidade e sinais de conclusão.
2. **Iniciar**. Os agentes começam em worktrees git isoladas, uma por tarefa. A branch principal permanece limpa.
3. **Verificar**. O zelador verifica sinais concretos: testes passam, arquivos existem, lint limpo, tipos corretos.
4. **Mesclar**. O trabalho verificado entra na main. Tarefas falhas são repetidas ou roteadas para um modelo diferente.

O orquestrador é um agendador em Python, não um LLM. As decisões de agendamento são determinísticas, auditáveis e reproduzíveis.

## Execução em nuvem (Cloudflare)

O Bernstein pode executar agentes no Cloudflare Workers em vez de localmente. A CLI `bernstein cloud` cuida do deploy e do ciclo de vida.

- **Workers**. Execução de agentes na borda da Cloudflare, com Durable Workflows para tarefas multi-etapa e retry automático.
- **Isolamento via sandbox V8**. Cada agente roda em seu próprio isolate, sem sobrecarga de container.
- **Sincronização de workspace via R2**. O estado da worktree local sincroniza com o object storage R2 para que os agentes na nuvem vejam os mesmos arquivos.
- **Workers AI** (experimental). Use modelos hospedados pela Cloudflare como provedor de LLM, sem necessidade de chaves de API externas.
- **Analytics em D1**. Métricas de tarefas e dados de custo armazenados em D1 para consulta.
- **Vectorize**. Cache semântico apoiado pelo banco de dados vetorial da Cloudflare.
- **Renderização de navegador**. Chrome headless em Workers para agentes que precisam inspecionar saída web.
- **Transporte remoto MCP**. Exponha ou consuma servidores MCP através da rede da Cloudflare.

```bash
bernstein cloud login      # authenticate with Bernstein Cloud
bernstein cloud deploy     # push agent workers
bernstein cloud run plan.yaml  # execute a plan on Cloudflare
```

## Capacidades

**Orquestração principal**. Execução paralela, isolamento de worktree git, verificação por zelador, portões de qualidade (lint, tipos, varredura de PII), revisão de código entre modelos, circuit breaker para agentes mal-comportados, monitoramento de crescimento de tokens com auto-intervenção.

**Inteligência**. Roteador de bandit contextual para seleção de modelo/esforço. Grafo de conhecimento para análise de impacto na base de código. Cache semântico economiza tokens em padrões repetidos. Detecção de anomalias de custo (alertas de taxa de queima). Detecção de anomalias de comportamento com sinalização por Z-score.

**Sandboxing**. Protocolo plugável [`SandboxBackend`](../../docs/architecture/sandbox.md) — execute agentes em worktrees git locais (padrão), containers Docker, microVMs Firecracker do [E2B](https://e2b.dev), ou containers serverless do [Modal](https://modal.com) (com GPU opcional). Autores de plugins podem registrar backends personalizados pelo entry-point group `bernstein.sandbox_backends`. Inspecione os backends instalados com `bernstein agents sandbox-backends`.

**Armazenamento de artefatos**. O estado em `.sdd/` pode ser transmitido para backends plugáveis [`ArtifactSink`](../../docs/architecture/storage.md): sistema de arquivos local (padrão), S3, Google Cloud Storage, Azure Blob ou Cloudflare R2. O `BufferedSink` mantém o contrato de segurança contra falhas do WAL escrevendo localmente com fsync primeiro e espelhando para o remoto de forma assíncrona.

**Skill packs**. [Skills](../../docs/architecture/skills.md) com divulgação progressiva (padrão do OpenAI Agents SDK): apenas um índice compacto de skills é incluído no system prompt de cada spawn; os agentes carregam o conteúdo completo via a ferramenta MCP `load_skill` sob demanda. 17 packs de papéis integrados, mais entry-points `bernstein.skill_sources` de terceiros.

**Controles**. Logs de auditoria encadeados por HMAC, motor de políticas, gating de saída de PII, recuperação de falhas apoiada por WAL (segurança experimental para múltiplos workers), OAuth 2.0 PKCE.

**Observabilidade**. Prometheus `/metrics`, presets de exporter OTel, dashboards Grafana. Rastreamento de custo por modelo (`bernstein cost`). TUI no terminal e dashboard web. Visibilidade de processos de agentes em `ps`.

**Ecossistema**. Modo de servidor MCP, suporte ao protocolo A2A, integração com GitHub App, sistema de plugins baseado em pluggy, workspaces multi-repo, modo cluster para execução distribuída, auto-evolução via `--evolve` (experimental).

Matriz completa de funcionalidades: [FEATURE_MATRIX.md](../../docs/reference/FEATURE_MATRIX.md) &middot; Funcionalidades recentes: [Novidades](../../docs/whats-new.md)

## Novidades na v1.9

**Ponte ACP** — `bernstein acp serve --stdio` expõe o Bernstein para qualquer editor que fale o Agent Communication Protocol (Zed, etc.). Sem necessidade de código de plugin no lado do editor.

**Reparo autônomo de CI** — `bernstein autofix` observa PRs abertos do Bernstein e, quando o CI fica vermelho, dispara automaticamente um agente reparador. Quando volta a verde, ele faz push da correção e solicita revisão novamente.

**Cofre de credenciais** — `bernstein connect <provider>` grava chaves de API no chaveiro do SO; `bernstein creds` lista e rotaciona elas. Os agentes herdam credenciais com escopo sem mexer em variáveis de ambiente.

**Túneis de preview** — `bernstein preview start` inicializa um servidor de desenvolvimento em sandbox e imprime uma URL pública. Útil para compartilhar uma branch em execução com um revisor sem fazer deploy para staging.

Changelog completo: [docs/whats-new.md](../../docs/whats-new.md)

## Comandos do operador

Comandos que eliminam o código-cola que a maioria dos times acaba escrevendo em torno de suas execuções.

| Comando | O que faz |
|---------|--------------|
| `bernstein pr` | Cria automaticamente um PR no GitHub a partir de uma sessão concluída; o corpo carrega os resultados dos portões do zelador e o detalhamento de custo em tokens/USD. |
| `bernstein from-ticket <url>` | Importa um ticket do Linear / GitHub Issues / Jira como uma tarefa do Bernstein. Inferência de papel + escopo baseada em labels. Suporta `--dry-run` e `--run`. |
| `bernstein ticket import <url>` | Forma alias / agrupada de `from-ticket` para uso em scripts. |
| `bernstein remote` | Backend de sandbox via SSH. `remote test <host>`, `remote run <host> <path>`, `remote forget <host>`. Reuso de socket ControlMaster para chamadas repetidas rápidas. |
| `bernstein hooks` | Hooks de ciclo de vida para `pre_task`, `post_task`, `pre_merge`, `post_merge`, `pre_spawn`, `post_spawn` — scripts shell ou `@hookimpl`s do pluggy. `hooks list`, `hooks run <event>`, `hooks check`. |
| `bernstein chat serve --platform=telegram\|discord\|slack` | Conduza execuções a partir do chat com `/run`, `/status`, `/approve`, `/reject`, `/switch`, `/stop`. |
| `bernstein approve-tool` / `bernstein reject-tool` | Aprovação interativa de chamadas de ferramentas no meio da execução. `--latest`, `--id`, `--always`. |
| `bernstein tunnel start <port> [--provider auto\|cloudflared\|ngrok\|bore\|tailscale]` | Um único wrapper para quatro provedores de túnel. Também `tunnel list`, `tunnel stop <name>\|--all`. Reuso de processo no estilo ControlMaster. |
| `bernstein daemon install [--user\|--system] [--command="..."] [--env KEY=VAL]...` | Instala uma unit do systemd (Linux) ou launchd (macOS) para auto-start. Também `daemon start/stop/restart/status/uninstall`. |
| `bernstein connect <provider>` / `bernstein creds` | Armazena e rotaciona credenciais de API no chaveiro do SO. Os agentes herdam chaves com escopo por execução. |
| `bernstein autofix` | Daemon que monitora PRs abertos do Bernstein; dispara um agente reparador quando o CI falha e faz push da correção automaticamente. |
| `bernstein preview start` | Inicia um servidor de desenvolvimento em sandbox para a branch atual e imprime uma URL pública de túnel compartilhável. |

## Como se compara

| Funcionalidade | Bernstein | CrewAI | AutoGen [^autogen] | LangGraph |
|---------|-----------|--------|---------|-----------|
| Orquestrador | Código determinístico | Conduzido por LLM (+ Flows em código) | Conduzido por LLM | Grafo + LLM |
| Funciona com | Qualquer agente CLI (43 adaptadores) | Classes do Python SDK | Agentes Python | Nós LangChain |
| Isolamento via git | Worktrees por agente | Não | Não | Não |
| Sandboxes plugáveis | Worktree, Docker, E2B, Modal | Não | Não | Não |
| Verificação | Zelador + portões de qualidade | Guardrails + saída Pydantic | Condições de término | Arestas condicionais |
| Rastreamento de custo | Integrado | `usage_metrics` | `RequestUsage` | Via LangSmith |
| Modelo de estado | Baseado em arquivo (.sdd/) | Em memória + checkpoint SQLite | Em memória | Checkpointer |
| Sinks remotos de artefatos | S3, GCS, Azure Blob, R2 | Não | Não | Não |
| Auto-evolução | Integrada (experimental) | Não | Não | Não |
| Planos declarativos (YAML) | Sim | Sim (`agents.yaml`, `tasks.yaml`) | Não | Parcial (`langgraph.json`) |
| Roteamento de modelo por tarefa | Sim | LLM por agente | `model_client` por agente | Por nó (manual) |
| Suporte a MCP | Sim (cliente + servidor) | Sim | Sim (cliente + workbench) | Sim (cliente + servidor) |
| Chat agente-a-agente | Bulletin board | Sim (processo Crew) | Sim (group chat) | Sim (supervisor, swarm) |
| Web UI | TUI + dashboard web | CrewAI AMP | AutoGen Studio | LangGraph Studio + LangSmith |
| Opção hospedada em nuvem | Sim (Cloudflare) | Sim (CrewAI AMP) | Não | Sim (LangGraph Cloud) |
| RAG/recuperação integrado | Sim (FTS5 + BM25 na base de código) | `crewai_tools` | Recuperadores `autogen_ext` | Via LangChain |

*Última verificação: 2026-04-19. Veja as [páginas de comparação completas](../../docs/compare/README.md) para matrizes de funcionalidades detalhadas.*

A tabela acima compara o Bernstein com frameworks de orquestração de LLMs (eles orquestram chamadas de LLM). A tabela abaixo cobre a categoria mais próxima — outras ferramentas que orquestram **agentes CLI de codificação**:

| Funcionalidade | Bernstein | [awslabs/cli-agent-orchestrator](https://github.com/awslabs/cli-agent-orchestrator) | [ComposioHQ/agent-orchestrator](https://github.com/ComposioHQ/agent-orchestrator) | [emdash](https://github.com/generalaction/emdash) | [umputun/ralphex](https://github.com/umputun/ralphex) |
|---------|-----------|-----------|-----------|-----------|-----------|
| Forma | Python CLI + biblioteca + servidor MCP | Python CLI + sessões tmux + web UI | TypeScript CLI + dashboard local | Aplicativo desktop Electron | Go CLI |
| Linguagem principal | Python | Python | TypeScript | TypeScript | Go |
| Instalação | `pipx install bernstein` | `uv tool install cli-agent-orchestrator` | `npm install -g @aoagents/ao` | `.dmg` / `.msi` / `.AppImage` | `go install` / binário único |
| Adaptadores de agentes | 43 | 5 (Kiro, Claude Code, Codex, Gemini, Kimi) | 3 (Claude Code, Codex, Aider) | 24 | 1 (apenas Claude Code) |
| Execução paralela multi-agente | Sim | Sim (sessão tmux por agente) | Sim | Sim | Não (sessão única sequencial) |
| Worktree git por agente | Sim | Não (planejado, [#100](https://github.com/awslabs/cli-agent-orchestrator/issues/100)) | Sim | Sim | Flag `--worktree` opcional |
| Modo de servidor MCP (expõe-se como MCP) | Sim (stdio + HTTP/SSE) | Sim (comunicação inter-agente) | Não | Não | Não |
| Coordenador | Agendador determinístico em Python | Supervisor LLM hierárquico | Conduzido por LLM | Não documentado | Executor de plano linear |
| Replay de auditoria encadeada por HMAC | Sim | Não | Não | Não | Não |
| Verificador entre modelos / portões de qualidade | Sim (multi-estágio) | Não | Não | Não | Revisão multi-fase (apenas Claude) |
| Fluxo autônomo de correção de CI / PR | Sim (`bernstein autofix`) | Não | Sim | Não | Não |
| Dashboard visual | TUI + web | Web UI + tmux | Web | App desktop | Web (`--serve`) |
| Sinks de notificação | Telegram/Slack/Discord/Email/Webhook/Shell | — | Não | Não | Telegram / Email / Slack / Webhook |
| Apoio | OSS solo | AWS Labs | Financiado (Composio.dev) | YC W26 | OSS solo |
| Licença | Apache 2.0 | Apache 2.0 | MIT | Apache 2.0 | MIT |

A vantagem do Bernstein nesta categoria: **Python-nativo, MCP-server-first, maior cobertura de adaptadores, paralelismo multi-agente verdadeiro, agendador determinístico sem LLM no laço de coordenação**. Se você quer isolamento por sessão tmux alinhado à AWS com supervisor LLM hierárquico, o `cao` da AWS Labs é uma escolha mais adequada; se sua stack é TypeScript e você quer um produto com dashboard, o `@aoagents/ao` da Composio é a melhor opção; se você quer um ADE desktop polido, o emdash é; se você só usa Claude Code e quer um único binário Go que percorre um plano de cima a baixo, o ralphex é. Se você quer uma primitiva que seja importável em Python, exponha-se via MCP para qualquer cliente, execute muitos agentes em paralelo e cubra toda a amplitude de agentes (incluindo Qwen, Goose, Ollama, OpenAI Agents SDK, Cloudflare Agents e mais) — Bernstein.

[^autogen]: O AutoGen está em modo de manutenção; o sucessor é o Microsoft Agent Framework 1.0.

## Monitoramento

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

## Instalação

| Método | Comando |
|--------|---------|
| **One-liner (macOS / Linux)** | `curl -fsSL https://bernstein.run/install.sh \| sh` |
| **One-liner (Windows)** | `irm https://bernstein.run/install.ps1 \| iex` |
| **pip** | `pip install bernstein` |
| **pipx** | `pipx install bernstein` |
| **uv** | `uv tool install bernstein` |
| **Homebrew** | `brew tap chernistry/tap && brew install bernstein` |
| **Fedora / RHEL** | `sudo dnf copr enable alexchernysh/bernstein && sudo dnf install bernstein` |
| **npm** (wrapper) | `npx bernstein-orchestrator` |

Os scripts de uma linha verificam a presença de Python 3.12+, fazem bootstrap do pipx quando ele estiver ausente, corrigem o PATH para a sessão atual e instalam (ou atualizam) o `bernstein`. Eles lidam com ambientes macOS gerenciados pelo brew e com o fallback do launcher `py -3` no Windows. Fontes dos scripts: [install.sh](../../scripts/install.sh) · [install.ps1](../../scripts/install.ps1).

### Extras opcionais

Os SDKs de provedores são opcionais para que a instalação base permaneça enxuta. Escolha o que precisar:

| Extra | Habilita |
|-------|---------|
| `bernstein[openai]` | Adaptador do OpenAI Agents SDK v2 (`openai_agents`) |
| `bernstein[docker]` | Backend de sandbox Docker |
| `bernstein[e2b]` | Backend de sandbox microVM [E2B](https://e2b.dev) (requer `E2B_API_KEY`) |
| `bernstein[modal]` | Backend de sandbox [Modal](https://modal.com), GPU opcional (requer `MODAL_TOKEN_ID` / `MODAL_TOKEN_SECRET`) |
| `bernstein[s3]` | Sink de artefatos S3 (via `boto3`) |
| `bernstein[gcs]` | Sink de artefatos do Google Cloud Storage |
| `bernstein[azure]` | Sink de artefatos do Azure Blob |
| `bernstein[r2]` | Sink de artefatos do Cloudflare R2 (`boto3` compatível com S3) |
| `bernstein[grpc]` | Ponte gRPC |
| `bernstein[k8s]` | Integrações com Kubernetes |

Combine extras com colchetes, p. ex. `pip install 'bernstein[openai,docker,s3]'`.

Extensões para editor: [VS Marketplace](https://marketplace.visualstudio.com/items?itemName=alex-chernysh.bernstein) &middot; [Open VSX](https://open-vsx.org/extension/alex-chernysh/bernstein)

## Contribuindo

PRs são bem-vindos. Veja [CONTRIBUTING.md](../../CONTRIBUTING.md) para configuração e estilo de código.

## Suporte

Se o Bernstein economiza seu tempo: [GitHub Sponsors](https://github.com/sponsors/chernistry)

Contato: [forte@bernstein.run](mailto:forte@bernstein.run)

## Onde aparecemos

Listas curadas, newsletters e projetos parceiros que adotaram o Bernstein:

- [**Python Weekly #742**](https://www.pythonweekly.com/p/python-weekly-issue-742-april-23-2026) (23 de abril de 2026) — menção em newsletter.
- [**Augment Code — 9 Open-Source Agent Orchestrators for AI Coding (2026)**](https://www.augmentcode.com/tools/open-source-agent-orchestrators) — seleção editorial; "a ferramenta mais arquiteturalmente interessante deste roundup."
- [**nibzard/awesome-agentic-patterns**](https://github.com/nibzard/awesome-agentic-patterns/blob/main/patterns/deterministic-zero-llm-orchestration.md) — Bernstein citado como a implementação em produção do padrão "deterministic zero-LLM orchestration".
- [**Jenqyang/Awesome-AI-Agents**](https://github.com/Jenqyang/Awesome-AI-Agents)
- [**jamesmurdza/awesome-ai-devtools**](https://github.com/jamesmurdza/awesome-ai-devtools)
- [**jim-schwoebel/awesome_ai_agents**](https://github.com/jim-schwoebel/awesome_ai_agents)
- [**Piebald-AI/awesome-gemini-cli**](https://github.com/Piebald-AI/awesome-gemini-cli)
- [**ComposioHQ/awesome-codex-skills**](https://github.com/ComposioHQ/awesome-codex-skills)
- [**jxzhangjhu/Awesome-LLM-RAG**](https://github.com/jxzhangjhu/Awesome-LLM-RAG)
- [**rohitg00/awesome-claude-code-toolkit**](https://github.com/rohitg00/awesome-claude-code-toolkit)
- [**numtide/llm-agents.nix**](https://github.com/numtide/llm-agents.nix) — distribuição em flake do Nix.

<details>
<summary>Mais listas awesome e curadoria da comunidade</summary>

- [andyrewlee/awesome-agent-orchestrators](https://github.com/andyrewlee/awesome-agent-orchestrators)
- [bradAGI/awesome-cli-coding-agents](https://github.com/bradAGI/awesome-cli-coding-agents)
- [milisp/awesome-codex-cli](https://github.com/milisp/awesome-codex-cli)
- [yaolifeng0629/Awesome-independent-tools](https://github.com/yaolifeng0629/Awesome-independent-tools) (中文 + EN)
- [caramaschiHG/awesome-ai-agents-2026](https://github.com/caramaschiHG/awesome-ai-agents-2026)
- [ai-for-developers/awesome-vibe-coding](https://github.com/ai-for-developers/awesome-vibe-coding)
- [killop/anything_about_game](https://github.com/killop/anything_about_game) (`AI.md`)
- [Glama MCP Catalog](https://glama.ai/mcp/servers/sipyourdrink-ltd/bernstein) — listagem editorial de servidores MCP.
- Espelhos: [icopy-site/awesome](https://github.com/icopy-site/awesome), [icopy-site/awesome-cn](https://github.com/icopy-site/awesome-cn), [trackawesomelist/trackawesomelist](https://github.com/trackawesomelist/trackawesomelist).

</details>

<details>
<summary>Citado como prior art por projetos parceiros</summary>

- [**mkb23/overcode**](https://github.com/mkb23/overcode/blob/main/docs/design/bakeoffs/overcode-vs-bernstein.md) — bakeoff em formato longo tratando o Bernstein como a implementação de referência.
- [**Vintersong/NOVA-Cognition-Framework**](https://github.com/Vintersong/NOVA-Cognition-Framework) — `BERNSTEIN_PATTERNS.md`, "Patterns Worth Borrowing".
- [**AJV009/drupal-contrib-workbench**](https://github.com/AJV009/drupal-contrib-workbench) — notas de pesquisa sobre a divisão gerente/zelador.
- [**danielvaughan/codex-blog**](https://github.com/danielvaughan/codex-blog/blob/main/_posts/2026-04-09-loki-mode-autonomous-execution.md) — artigo de comparação posicionando o Bernstein no extremo determinístico.

</details>

## Histórico de estrelas

[![Star History Chart](https://api.star-history.com/svg?repos=sipyourdrink-ltd/bernstein&type=Date)](https://star-history.com/#sipyourdrink-ltd/bernstein&Date)

## Licença

[Apache License 2.0](../../LICENSE)

---

Feito com amor por [Alex Chernysh](https://alexchernysh.com) &middot; [GitHub](https://github.com/chernistry) &middot; [X](https://x.com/alex_chernysh) &middot; [bernstein.run](https://bernstein.run?utm_source=github.com&utm_medium=readme&utm_campaign=bernstein-readme)

<!-- mcp-name: io.github.sipyourdrink-ltd/bernstein -->
