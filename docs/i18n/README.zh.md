[English](../../README.md) | [Español (Spanish)](README.es.md) | **中文 (Chinese)** | [العربية (Arabic)](README.ar.md) | [Português (Portuguese)](README.pt.md) | [Bahasa Indonesia (Indonesian)](README.id.md) | [Français (French)](README.fr.md) | [日本語 (Japanese)](README.ja.md) | [Русский (Russian)](README.ru.md) | [Deutsch (German)](README.de.md) | [עברית (Hebrew)](README.he.md) | [יידיש (Yiddish)](README.yi.md)

<div align="center">

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="../../docs/assets/logo-dark.svg">
  <source media="(prefers-color-scheme: light)" srcset="../../docs/assets/logo-light.svg">
  <img alt="Bernstein" src="../../docs/assets/logo-light.svg" width="340">
</picture>

<br>

> *“要成就大事，需要两样东西：一个计划，以及不太够用的时间。”* — Leonard Bernstein

### 编排任意 AI 编码代理。任意模型。一条命令。

<img alt="Bernstein 实战：实时编排的并行 AI 代理" src="../../docs/assets/in-action-small.gif" width="700">

[![CI](https://github.com/sipyourdrink-ltd/bernstein/actions/workflows/ci.yml/badge.svg)](https://github.com/sipyourdrink-ltd/bernstein/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/bernstein)](https://pypi.org/project/bernstein/)
[![Python 3.12+](https://img.shields.io/badge/python-3.12+-3776ab?logo=python&logoColor=white)](https://python.org)
[![License](https://img.shields.io/github/license/sipyourdrink-ltd/bernstein)](../../LICENSE)
[![MseeP.ai](https://img.shields.io/badge/MseeP.ai-verified-2496ed)](https://mseep.ai/app/chernistry-bernstein)

[官网](https://bernstein.run) &middot; [文档](https://bernstein.readthedocs.io/) &middot; [快速入门](../../docs/getting-started/GETTING_STARTED.md) &middot; [术语表](../../docs/reference/GLOSSARY.md) &middot; [已知限制](../../docs/reference/KNOWN_LIMITATIONS.md)

</div>

---

**这是什么？** 你告诉它你想构建什么。它会把任务拆分给多个 AI 编码代理（Claude Code、Codex、Gemini CLI，以及另外 38 个），跑测试，再合并真正通过验证的代码。等你回来时，代码已经能跑了。

### 安装并运行

macOS / Linux 一行搞定：

```bash
curl -fsSL https://bernstein.run/install.sh | sh
```

Windows（PowerShell）：

```powershell
irm https://bernstein.run/install.ps1 | iex
```

然后切到你的项目里，设个目标：

```bash
cd your-project
bernstein init                          # 创建 .sdd/ 工作区
bernstein -g "Add JWT auth with refresh tokens, tests, and API docs"
```

运行时你会看到这样的输出：

```
$ bernstein -g "Add JWT auth"
[manager] decomposed into 4 tasks
[agent-1] claude-sonnet: src/auth/middleware.py  (done, 2m 14s)
[agent-2] codex:         tests/test_auth.py      (done, 1m 58s)
[verify]  all gates pass. merging to main.
```

### 它哪里不一样

大多数代理编排器靠 LLM 决定谁干什么。这种做法既不确定，又把 token 烧在调度而不是真正写代码上。Bernstein 只用一次 LLM 调用来拆解你的目标，剩下的——并行运行代理、隔离 git 分支、跑测试、路由重试——都是普通的 Python 代码。每一次运行都可复现。每一步都有日志、都能回放。

没有要学的框架。没有厂商锁定。任何代理、任何模型、任何提供商，随你换。

其他安装方式：`pipx install bernstein`、`pip install bernstein`、`uv tool install bernstein`、`brew`、`dnf copr`、`npx bernstein-orchestrator`。详见[安装方式](#install)。

## 支持的代理

Bernstein 会自动发现已安装的 CLI 代理。你可以在同一次运行中混用它们。便宜的本地模型干样板代码，更重的云端模型负责架构。

41 个 CLI 代理适配器：38 个第三方封装，加上一个适用于任何带 `--prompt` 的工具的通用封装。

| Agent | 模型 | 安装 |
|-------|--------|---------|
| [Claude Code](https://docs.anthropic.com/en/docs/claude-code) | Opus 4、Sonnet 4.6、Haiku 4.5 | `npm install -g @anthropic-ai/claude-code` |
| [Codex CLI](https://github.com/openai/codex) | GPT-5、GPT-5 mini | `npm install -g @openai/codex` |
| [OpenAI Agents SDK v2](https://openai.github.io/openai-agents-python/) | GPT-5、GPT-5 mini、o4 | `pip install 'bernstein[openai]'` |
| [GitHub Copilot CLI](https://docs.github.com/en/copilot/github-copilot-in-the-cli) | 由 Copilot 托管（GPT-5、Sonnet 4.6） | `npm install -g @github/copilot` |
| [Gemini CLI](https://github.com/google-gemini/gemini-cli) | Gemini 2.5 Pro、Gemini Flash | `npm install -g @google/gemini-cli` |
| [Cursor](https://www.cursor.com) | Sonnet 4.6、Opus 4、GPT-5 | [Cursor 应用](https://www.cursor.com) |
| [Aider](https://aider.chat) | 任意兼容 OpenAI / Anthropic 的模型 | `pip install aider-chat` |
| [Amp](https://ampcode.com) | 由 Amp 托管 | `npm install -g @sourcegraph/amp` |
| [Cody](https://sourcegraph.com/cody) | Sourcegraph 托管 | `npm install -g @sourcegraph/cody` |
| [Continue](https://continue.dev) | 任意兼容 OpenAI / Anthropic 的模型 | `npm install -g @continuedev/cli`（二进制：`cn`） |
| [Goose](https://block.github.io/goose/) | Goose 支持的任意提供商 | 见 [Goose 文档](https://block.github.io/goose/) |
| [IaC](https://www.terraform.io/)（Terraform / Pulumi） | 底层代理使用的任意提供商 | 内置 |
| [Kilo](https://kilo.dev) | Kilo 托管 | 见 [Kilo 文档](https://kilo.dev) |
| [Kiro](https://kiro.dev) | Kiro 托管 | 见 [Kiro 文档](https://kiro.dev) |
| [Ollama](https://ollama.ai) + Aider | 本地模型（离线） | `brew install ollama` |
| [OpenCode](https://opencode.ai) | OpenCode 支持的任意提供商 | 见 [OpenCode 文档](https://opencode.ai) |
| [Qwen](https://github.com/QwenLM/qwen-code) | Qwen Code 模型 | `npm install -g @qwen-code/qwen-code` |
| [Cloudflare Agents](https://developers.cloudflare.com/agents/) | Workers AI 模型 | `bernstein cloud login` |
| [OpenHands](https://github.com/OpenHands/OpenHands) | 任意 LiteLLM 支持的模型（Anthropic、OpenAI 等） | `uv tool install openhands --python 3.12` |
| [Open Interpreter](https://github.com/OpenInterpreter/open-interpreter) | 任意（基于 LiteLLM） | `pip install open-interpreter` |
| [gptme](https://github.com/gptme/gptme) | Anthropic、OpenAI、OpenRouter | `pipx install gptme` |
| [Plandex](https://github.com/plandex-ai/plandex) | Plandex Cloud 或自托管模型 | `curl -sL https://plandex.ai/install.sh \| bash` |
| [AIChat](https://github.com/sigoden/aichat) | OpenAI、Anthropic、OpenRouter、Groq、Gemini | `cargo install aichat` |
| [Letta Code](https://github.com/letta-ai/letta-code) | 经 Letta 路由（Anthropic、OpenAI） | `npm install -g @letta-ai/letta-code` |
| **Generic** | 任意带 `--prompt` 的 CLI | 内置 |

#### 编排器委派（叶子节点）

另有一类规模更小的适配器，它们把**其他 CLI 编排器**当作单个代理来封装。Bernstein 把提示或计划交给被封装的工具，只看最终的退出码；被封装编排器内部的子代理成本和质量门禁对 Bernstein 是不可见的。当你想把一段已有的、基于这些工具构建的工作流嵌入到更大的 Bernstein 计划中的某一步时，它就派上用场了。

| 编排器 | 封装为 | 安装 |
|--------------|------------|---------|
| [Composio Agent Orchestrator](https://github.com/ComposioHQ/agent-orchestrator)（`@aoagents/ao`） | `composio` | `npm install -g @aoagents/ao` |
| [umputun/ralphex](https://github.com/umputun/ralphex) | `ralphex` | `go install github.com/umputun/ralphex/cmd/ralphex@latest` |

任何适配器也都可以充当**内部调度 LLM**。你可以脱离任何特定提供商运行整套技术栈：

```yaml
internal_llm_provider: gemini            # 或 qwen、ollama、codex、goose……
internal_llm_model: gemini-3.1-pro
```

> [!TIP]
> CI 流水线请使用 `bernstein --headless`：无 TUI、结构化 JSON 输出、失败时返回非零退出码。

## 快速开始

```bash
cd your-project
bernstein init                    # 创建 .sdd/ 工作区与 bernstein.yaml
bernstein -g "Add rate limiting"  # 代理生成、并行工作、验证、退出
bernstein live                    # 在 TUI 仪表盘里观察进度
bernstein stop                    # 优雅关闭并排空
```

对于多阶段项目，可以用 YAML 定义计划：

```bash
bernstein run plan.yaml           # 跳过 LLM 规划阶段，直接进入执行
bernstein run --dry-run plan.yaml # 预览任务及预估成本
```

## 工作原理

1. **拆解**。manager 把你的目标拆成若干任务，分别带角色、归属文件和完成信号。
2. **派生**。代理在隔离的 git worktree 中启动，每个任务一个。main 分支保持干净。
3. **验证**。janitor 检查具体的信号：测试通过、文件存在、lint 干净、类型正确。
4. **合并**。通过验证的工作合入 main。失败的任务会重试或转交给另一个模型。

编排器是一个 Python 调度器，而不是 LLM。调度决策是确定性的、可审计的、可复现的。

## 云端执行（Cloudflare）

Bernstein 可以在 Cloudflare Workers 而不是本地运行代理。`bernstein cloud` 这条 CLI 负责处理部署和生命周期。

- **Workers**。代理在 Cloudflare 边缘上执行，用 Durable Workflows 处理多步任务，并自动重试。
- **V8 sandbox 隔离**。每个代理跑在自己的 isolate 里，没有容器开销。
- **R2 工作区同步**。本地的 worktree 状态会同步到 R2 对象存储，让云端代理看到同样的文件。
- **Workers AI**（实验性）。把 Cloudflare 托管的模型用作 LLM 提供商，无需外部 API key。
- **D1 分析**。任务指标和成本数据存到 D1，便于查询。
- **Vectorize**。基于 Cloudflare 向量数据库的语义缓存。
- **Browser rendering**。在 Workers 上跑无头 Chrome，供需要查看网页输出的代理使用。
- **MCP 远程传输**。通过 Cloudflare 网络对外暴露或对接 MCP 服务器。

```bash
bernstein cloud login      # 登录 Bernstein Cloud
bernstein cloud deploy     # 推送代理 worker
bernstein cloud run plan.yaml  # 在 Cloudflare 上执行计划
```

为 `wrangler.toml` 和绑定提供脚手架的 `bernstein cloud init` 已在规划中。

## 能力一览

**核心编排**。并行执行、git worktree 隔离、janitor 验证、质量门禁（lint、类型、PII 扫描）、跨模型代码评审、面向行为异常代理的熔断器、带自动干预的 token 增长监控。

**智能能力**。用于模型 / 努力级别选择的上下文老虎机路由器。用于代码库影响分析的知识图谱。语义缓存在重复模式上节省 token。成本异常检测（消耗速率告警）。基于 Z-score 标记的行为异常检测。

**沙箱化**。可插拔的 [`SandboxBackend`](../../docs/architecture/sandbox.md) 协议——可在本地 git worktree（默认）、Docker 容器、[E2B](https://e2b.dev) Firecracker 微型虚拟机或 [Modal](https://modal.com) 无服务器容器（可选 GPU）中运行代理。插件作者可以通过 `bernstein.sandbox_backends` 入口点组注册自定义后端。用 `bernstein agents sandbox-backends` 查看已安装的后端。

**产物存储**。`.sdd/` 状态可以流式输出到可插拔的 [`ArtifactSink`](../../docs/architecture/storage.md) 后端：本地文件系统（默认）、S3、Google Cloud Storage、Azure Blob 或 Cloudflare R2。`BufferedSink` 通过先在本地写入并 fsync、再异步镜像到远端的方式，保证 WAL（预写日志）的崩溃安全契约。

**技能包**。渐进式披露的 [skills](../../docs/architecture/skills.md)（OpenAI Agents SDK 模式）：每次派生的系统提示词中只携带紧凑的技能索引，代理按需通过 `load_skill` MCP 工具拉取完整内容。内置 17 个角色包，外加第三方的 `bernstein.skill_sources` 入口点。

**控制面**。HMAC 链式审计日志、策略引擎、PII 输出闸门、基于 WAL 的崩溃恢复（实验性的多 worker 安全机制）、OAuth 2.0 PKCE。SSO / SAML / OIDC 支持正在推进中。

**可观测性**。Prometheus 的 `/metrics`、OTel exporter 预设、Grafana 仪表盘。按模型 / 任务的成本追踪（`bernstein cost`）。终端 TUI 与 web 仪表盘。在 `ps` 中可见代理进程。

**生态系统**。MCP 服务器模式、A2A 协议支持、GitHub App 集成、基于 pluggy 的插件系统、多仓库工作区、用于分布式执行的集群模式、通过 `--evolve` 实现的自我演化（实验性）。

完整功能矩阵：[FEATURE_MATRIX.md](../../docs/reference/FEATURE_MATRIX.md) &middot; 近期新功能：[What's New](../../docs/whats-new.md)

## v1.9 新功能

**ACP 桥接**——`bernstein acp serve --stdio` 把 Bernstein 暴露给任何说 Agent Communication Protocol 的编辑器（Zed 等），编辑器一侧无需任何插件代码。

**自动化 CI 修复**——`bernstein autofix` 监视所有打开的 Bernstein PR，一旦 CI 变红，就自动派出一个修复代理。等到 CI 变绿，它会推送修复并重新请求评审。

**凭证保险库**——`bernstein connect <provider>` 把 API key 写入操作系统的 keychain；`bernstein creds` 列出并轮换它们。代理会继承范围受限的凭证，无需碰环境变量。

**预览隧道**——`bernstein preview start` 启动一个沙箱化的开发服务器并打印一个公网 URL。在不需要部署到 staging 的情况下，把当前分支的运行结果分享给评审者非常有用。

完整变更日志：[docs/whats-new.md](../../docs/whats-new.md)

## 运维命令

这些命令省掉了大多数团队最终都要自己写的、围绕运行流程的胶水代码。

| 命令 | 作用 |
|---------|--------------|
| `bernstein pr` | 从一次完成的会话中自动创建一个 GitHub PR；正文里携带 janitor 的门禁结果以及 token / 美元成本明细。 |
| `bernstein from-ticket <url>` | 把 Linear / GitHub Issues / Jira 上的工单导入为一个 Bernstein 任务。基于标签推断角色和范围。支持 `--dry-run` 与 `--run`。 |
| `bernstein ticket import <url>` | `from-ticket` 的别名 / 子命令组形式，便于在脚本里调用。 |
| `bernstein remote` | SSH 沙箱后端。`remote test <host>`、`remote run <host> <path>`、`remote forget <host>`。复用 ControlMaster socket，让重复调用更快。 |
| `bernstein hooks` | 生命周期钩子，覆盖 `pre_task`、`post_task`、`pre_merge`、`post_merge`、`pre_spawn`、`post_spawn`——可以是 shell 脚本，也可以是 pluggy 的 `@hookimpl`。`hooks list`、`hooks run <event>`、`hooks check`。 |
| `bernstein chat serve --platform=telegram\|discord\|slack` | 在聊天工具里通过 `/run`、`/status`、`/approve`、`/reject`、`/switch`、`/stop` 来驱动运行。 |
| `bernstein approve-tool` / `bernstein reject-tool` | 运行中途交互式的工具调用审批。`--latest`、`--id`、`--always`。 |
| `bernstein tunnel start <port> [--provider auto\|cloudflared\|ngrok\|bore\|tailscale]` | 一个统一封装四种隧道提供商的入口。还有 `tunnel list`、`tunnel stop <name>\|--all`。提供 ControlMaster 风格的进程复用。 |
| `bernstein daemon install [--user\|--system] [--command="..."] [--env KEY=VAL]...` | 安装 systemd（Linux）或 launchd（macOS）单元用于开机自启。还有 `daemon start/stop/restart/status/uninstall`。 |
| `bernstein connect <provider>` / `bernstein creds` | 在操作系统 keychain 中存储和轮换 API 凭证。代理在每次运行中继承范围受限的 key。 |
| `bernstein autofix` | 一个守护进程，监视打开的 Bernstein PR；CI 失败时派出一个修复代理，并自动推送修复。 |
| `bernstein preview start` | 为当前分支启动一个沙箱化的开发服务器，并打印一个可分享的公网隧道 URL。 |

## 与同类工具对比

| 功能 | Bernstein | CrewAI | AutoGen [^autogen] | LangGraph |
|---------|-----------|--------|---------|-----------|
| 编排器 | 确定性代码 | 由 LLM 驱动（外加 code Flows） | 由 LLM 驱动 | 图 + LLM |
| 兼容对象 | 任意 CLI 代理（41 个适配器） | Python SDK 类 | Python 代理 | LangChain 节点 |
| Git 隔离 | 每个代理独立 worktree | 否 | 否 | 否 |
| 可插拔沙箱 | Worktree、Docker、E2B、Modal | 否 | 否 | 否 |
| 验证机制 | Janitor + 质量门禁 | Guardrails + Pydantic 输出 | 终止条件 | 条件边 |
| 成本追踪 | 内置 | `usage_metrics` | `RequestUsage` | 通过 LangSmith |
| 状态模型 | 基于文件（.sdd/） | 内存 + SQLite checkpoint | 内存 | Checkpointer |
| 远端产物 sink | S3、GCS、Azure Blob、R2 | 否 | 否 | 否 |
| 自我演化 | 内置（实验性） | 否 | 否 | 否 |
| 声明式计划（YAML） | 是 | 是（`agents.yaml`、`tasks.yaml`） | 否 | 部分支持（`langgraph.json`） |
| 按任务路由模型 | 是 | 按代理设 LLM | 按代理设 `model_client` | 按节点（手动） |
| MCP 支持 | 是（客户端 + 服务端） | 是 | 是（客户端 + workbench） | 是（客户端 + 服务端） |
| 代理之间对话 | Bulletin board | 是（Crew process） | 是（group chat） | 是（supervisor、swarm） |
| Web UI | TUI + web 仪表盘 | CrewAI AMP | AutoGen Studio | LangGraph Studio + LangSmith |
| 云托管选项 | 是（Cloudflare） | 是（CrewAI AMP） | 否 | 是（LangGraph Cloud） |
| 内置 RAG / 检索 | 是（代码库 FTS5 + BM25） | `crewai_tools` | `autogen_ext` 的 retriever | 通过 LangChain |

*最近一次核对：2026-04-19。详细功能矩阵见[完整对比页](../../docs/compare/README.md)。*

上表是 Bernstein 与 LLM 编排框架（它们编排的是 LLM 调用）的对比。下表覆盖更接近的一类——其他编排 **CLI 编码代理** 的工具：

| 功能 | Bernstein | [awslabs/cli-agent-orchestrator](https://github.com/awslabs/cli-agent-orchestrator) | [ComposioHQ/agent-orchestrator](https://github.com/ComposioHQ/agent-orchestrator) | [emdash](https://github.com/generalaction/emdash) | [umputun/ralphex](https://github.com/umputun/ralphex) |
|---------|-----------|-----------|-----------|-----------|-----------|
| 形态 | Python CLI + 库 + MCP 服务器 | Python CLI + tmux 会话 + web UI | TypeScript CLI + 本地仪表盘 | Electron 桌面应用 | Go CLI |
| 主语言 | Python | Python | TypeScript | TypeScript | Go |
| 安装 | `pipx install bernstein` | `uv tool install cli-agent-orchestrator` | `npm install -g @aoagents/ao` | `.dmg` / `.msi` / `.AppImage` | `go install` / 单文件二进制 |
| 代理适配器数量 | 41 | 5（Kiro、Claude Code、Codex、Gemini、Kimi） | 3（Claude Code、Codex、Aider） | 24 | 1（仅 Claude Code） |
| 多代理并行执行 | 是 | 是（每个代理一个 tmux 会话） | 是 | 是 | 否（单一线性会话） |
| 每个代理独立 git worktree | 是 | 否（计划中，[#100](https://github.com/awslabs/cli-agent-orchestrator/issues/100)） | 是 | 是 | 可选的 `--worktree` 标志 |
| MCP 服务器模式（把自身暴露为 MCP） | 是（stdio + HTTP/SSE） | 是（用于代理间通信） | 否 | 否 | 否 |
| 协调者 | 确定性 Python 调度器 | 分层 LLM 监督者 | 由 LLM 驱动 | 未记录 | 线性计划执行器 |
| HMAC 链式审计回放 | 是 | 否 | 否 | 否 | 否 |
| 跨模型校验器 / 质量门禁 | 是（多阶段） | 否 | 否 | 否 | 多阶段评审（仅 Claude） |
| 自动 CI 修复 / PR 流程 | 是（`bernstein autofix`） | 否 | 是 | 否 | 否 |
| 可视化仪表盘 | TUI + web | Web UI + tmux | Web | 桌面应用 | Web（`--serve`） |
| 通知 sink | Telegram / Slack / Discord / Email / Webhook / Shell | — | 否 | 否 | Telegram / Email / Slack / Webhook |
| 背景 | 个人 OSS | AWS Labs | 受资助（Composio.dev） | YC W26 | 个人 OSS |
| 许可证 | Apache 2.0 | Apache 2.0 | MIT | Apache 2.0 | MIT |

Bernstein 在这个赛道上的切入点：**Python 原生、MCP 服务器优先、覆盖最广的适配器、真正的多代理并行、协调环节没有 LLM 参与的确定性调度器**。如果你想要 AWS 风格的 tmux 会话隔离 + 分层 LLM 监督者，AWS Labs 的 `cao` 更合适；如果你的技术栈是 TypeScript，又想要带仪表盘的产品，Composio 的 `@aoagents/ao` 更合适；如果你想要一个打磨精致的桌面 ADE，那就是 emdash；如果你只用 Claude Code，又只想要一个从上到下走完一份计划的单文件 Go 二进制，那就是 ralphex。如果你想要一个能 `import` 进 Python、通过 MCP 把自己暴露给任何客户端、能并行跑很多代理、并覆盖完整代理生态（包括 Qwen、Goose、Ollama、OpenAI Agents SDK、Cloudflare Agents 等等）的原语——那就是 Bernstein。

[^autogen]: AutoGen 已进入维护模式；继任者是 Microsoft Agent Framework 1.0。

## 监控

```bash
bernstein live       # TUI 仪表盘
bernstein dashboard  # web 仪表盘
bernstein status     # 任务概览
bernstein ps         # 正在运行的代理
bernstein cost       # 按模型 / 任务的开销
bernstein doctor     # 起飞前自检
bernstein recap      # 运行后的总结
bernstein trace <ID> # 代理决策的追踪信息
bernstein run-changelog --hours 48  # 由代理产出的 diff 生成 changelog
bernstein explain <cmd>  # 带示例的详细帮助
bernstein dry-run    # 不执行，只预览任务
bernstein dep-impact # API 破坏性变更 + 下游调用方影响
bernstein aliases    # 显示命令缩写
bernstein config-path    # 显示配置文件位置
bernstein init-wizard    # 交互式项目初始化
bernstein debug-bundle   # 收集日志、配置、状态以便提交 bug
bernstein skills list    # 可被发现的技能包（渐进式披露）
bernstein skills show <name>  # 打印某个技能的正文及其引用
```

```bash
bernstein fingerprint build --corpus-dir ~/oss-corpus  # 构建本地相似度索引
bernstein fingerprint check src/foo.py                 # 用索引检查生成的代码
```

## 安装

| 方式 | 命令 |
|--------|---------|
| **一行命令（macOS / Linux）** | `curl -fsSL https://bernstein.run/install.sh \| sh` |
| **一行命令（Windows）** | `irm https://bernstein.run/install.ps1 \| iex` |
| **pip** | `pip install bernstein` |
| **pipx** | `pipx install bernstein` |
| **uv** | `uv tool install bernstein` |
| **Homebrew** | `brew tap chernistry/tap && brew install bernstein` |
| **Fedora / RHEL** | `sudo dnf copr enable alexchernysh/bernstein && sudo dnf install bernstein` |
| **npm**（封装） | `npx bernstein-orchestrator` |

这些一行安装脚本会检查 Python 3.12+，缺少 pipx 时会自动 bootstrap，会修复当前会话的 PATH，然后安装（或升级）`bernstein`。它们能处理 brew 管理下的 macOS 环境，以及 Windows 上回退到 `py -3` 启动器的情况。脚本源码：[install.sh](../../scripts/install.sh) · [install.ps1](../../scripts/install.ps1)。

### 可选附加项

各家提供商的 SDK 都是可选的，让基础安装保持精简。按需挑选：

| Extra | 启用的功能 |
|-------|---------|
| `bernstein[openai]` | OpenAI Agents SDK v2 适配器（`openai_agents`） |
| `bernstein[docker]` | Docker 沙箱后端 |
| `bernstein[e2b]` | [E2B](https://e2b.dev) 微型虚拟机沙箱后端（需要 `E2B_API_KEY`） |
| `bernstein[modal]` | [Modal](https://modal.com) 沙箱后端，可选 GPU（需要 `MODAL_TOKEN_ID` / `MODAL_TOKEN_SECRET`） |
| `bernstein[s3]` | S3 产物 sink（基于 `boto3`） |
| `bernstein[gcs]` | Google Cloud Storage 产物 sink |
| `bernstein[azure]` | Azure Blob 产物 sink |
| `bernstein[r2]` | Cloudflare R2 产物 sink（S3 兼容的 `boto3`） |
| `bernstein[grpc]` | gRPC 桥接 |
| `bernstein[k8s]` | Kubernetes 集成 |

可以用方括号组合多个 extra，例如 `pip install 'bernstein[openai,docker,s3]'`。

编辑器扩展：[VS Marketplace](https://marketplace.visualstudio.com/items?itemName=alex-chernysh.bernstein) &middot; [Open VSX](https://open-vsx.org/extension/alex-chernysh/bernstein)

## 贡献

欢迎提 PR。环境搭建和代码风格请见 [CONTRIBUTING.md](../../CONTRIBUTING.md)。

## 支持

如果 Bernstein 帮你节省了时间：[GitHub Sponsors](https://github.com/sponsors/chernistry)

联系方式：[forte@bernstein.run](mailto:forte@bernstein.run)

## 被收录

精选列表、Newsletter 以及同行项目对 Bernstein 的提及：

- [**Python Weekly #742**](https://www.pythonweekly.com/p/python-weekly-issue-742-april-23-2026)（2026 年 4 月 23 日）——Newsletter 提名。
- [**Augment Code — 9 Open-Source Agent Orchestrators for AI Coding (2026)**](https://www.augmentcode.com/tools/open-source-agent-orchestrators)——编辑部综述；“本次综述中架构上最有趣的工具”。
- [**nibzard/awesome-agentic-patterns**](https://github.com/nibzard/awesome-agentic-patterns/blob/main/patterns/deterministic-zero-llm-orchestration.md)——把 Bernstein 列为“确定性零 LLM 编排”模式的生产实现。
- [**Jenqyang/Awesome-AI-Agents**](https://github.com/Jenqyang/Awesome-AI-Agents)
- [**jamesmurdza/awesome-ai-devtools**](https://github.com/jamesmurdza/awesome-ai-devtools)
- [**jim-schwoebel/awesome_ai_agents**](https://github.com/jim-schwoebel/awesome_ai_agents)
- [**Piebald-AI/awesome-gemini-cli**](https://github.com/Piebald-AI/awesome-gemini-cli)
- [**ComposioHQ/awesome-codex-skills**](https://github.com/ComposioHQ/awesome-codex-skills)
- [**jxzhangjhu/Awesome-LLM-RAG**](https://github.com/jxzhangjhu/Awesome-LLM-RAG)
- [**rohitg00/awesome-claude-code-toolkit**](https://github.com/rohitg00/awesome-claude-code-toolkit)
- [**numtide/llm-agents.nix**](https://github.com/numtide/llm-agents.nix)——Nix flake 发行。

<details>
<summary>更多精选列表与社区收录</summary>

- [andyrewlee/awesome-agent-orchestrators](https://github.com/andyrewlee/awesome-agent-orchestrators)
- [bradAGI/awesome-cli-coding-agents](https://github.com/bradAGI/awesome-cli-coding-agents)
- [milisp/awesome-codex-cli](https://github.com/milisp/awesome-codex-cli)
- [yaolifeng0629/Awesome-independent-tools](https://github.com/yaolifeng0629/Awesome-independent-tools)（中文 + EN）
- [caramaschiHG/awesome-ai-agents-2026](https://github.com/caramaschiHG/awesome-ai-agents-2026)
- [ai-for-developers/awesome-vibe-coding](https://github.com/ai-for-developers/awesome-vibe-coding)
- [killop/anything_about_game](https://github.com/killop/anything_about_game)（`AI.md`）
- [Glama MCP Catalog](https://glama.ai/mcp/servers/sipyourdrink-ltd/bernstein)——编辑部 MCP 服务器收录。
- 镜像站：[icopy-site/awesome](https://github.com/icopy-site/awesome)、[icopy-site/awesome-cn](https://github.com/icopy-site/awesome-cn)、[trackawesomelist/trackawesomelist](https://github.com/trackawesomelist/trackawesomelist)。

</details>

<details>
<summary>被同行项目作为前序工作引用</summary>

- [**mkb23/overcode**](https://github.com/mkb23/overcode/blob/main/docs/design/bakeoffs/overcode-vs-bernstein.md)——长篇 bakeoff，把 Bernstein 当作参考实现。
- [**Vintersong/NOVA-Cognition-Framework**](https://github.com/Vintersong/NOVA-Cognition-Framework)——`BERNSTEIN_PATTERNS.md`，“值得借鉴的模式”。
- [**AJV009/drupal-contrib-workbench**](https://github.com/AJV009/drupal-contrib-workbench)——关于 manager / janitor 拆分的研究笔记。
- [**danielvaughan/codex-blog**](https://github.com/danielvaughan/codex-blog/blob/main/_posts/2026-04-09-loki-mode-autonomous-execution.md)——把 Bernstein 定位在确定性这一端的对比文章。

</details>

## Star 历史

[![Star History Chart](https://api.star-history.com/svg?repos=sipyourdrink-ltd/bernstein&type=Date)](https://star-history.com/#sipyourdrink-ltd/bernstein&Date)

## 许可证

[Apache License 2.0](../../LICENSE)

---

由 [Alex Chernysh](https://alexchernysh.com) 倾心打造 &middot; [GitHub](https://github.com/chernistry) &middot; [X](https://x.com/alex_chernysh) &middot; [bernstein.run](https://bernstein.run)

<!-- mcp-name: io.github.sipyourdrink-ltd/bernstein -->
