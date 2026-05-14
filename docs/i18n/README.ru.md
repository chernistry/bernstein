<div align="center">

[English](../../README.md) | [Español (Spanish)](README.es.md) | [中文 (Chinese)](README.zh.md) | [العربية (Arabic)](README.ar.md) | [Português (Portuguese)](README.pt.md) | [Bahasa Indonesia (Indonesian)](README.id.md) | [Français (French)](README.fr.md) | [日本語 (Japanese)](README.ja.md) | **Русский (Russian)** | [Deutsch (German)](README.de.md) | [עברית (Hebrew)](README.he.md) | [יידיש (Yiddish)](README.yi.md)

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="../../docs/assets/logo-dark.svg">
  <source media="(prefers-color-scheme: light)" srcset="../../docs/assets/logo-light.svg">
  <img alt="Bernstein" src="../../docs/assets/logo-light.svg" width="340">
</picture>

<br>

> *«Чтобы добиться великого, нужны две вещи: план и не совсем достаточно времени.»* — Леонард Бернштейн

### Оркеструйте любого ИИ-агента для кода. Любую модель. Одной командой.

[![CI](https://github.com/sipyourdrink-ltd/bernstein/actions/workflows/ci.yml/badge.svg)](https://github.com/sipyourdrink-ltd/bernstein/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/bernstein)](https://pypi.org/project/bernstein/)
[![Python 3.12+](https://img.shields.io/badge/python-3.12+-3776ab?logo=python&logoColor=white)](https://python.org)
[![License](https://img.shields.io/github/license/sipyourdrink-ltd/bernstein)](../../LICENSE)
[![MseeP.ai](https://img.shields.io/badge/MseeP.ai-verified-2496ed)](https://mseep.ai/app/chernistry-bernstein)

[Сайт](https://bernstein.run?utm_source=github.com&utm_medium=readme&utm_campaign=bernstein-readme) &middot; [Документация](https://bernstein.readthedocs.io/) &middot; [Быстрый старт](../../docs/getting-started/GETTING_STARTED.md) &middot; [Глоссарий](../../docs/reference/GLOSSARY.md) &middot; [Ограничения](../../docs/reference/KNOWN_LIMITATIONS.md)

</div>

---

Bernstein — это детерминированный планировщик на Python: он запускает команду CLI-агентов для кода (Claude Code, Codex, Gemini CLI и ещё 40) на одну цель в параллельных git worktree, с HMAC-подписанной цепочкой аудита на каждом шаге.

### Установка за 30 секунд

```bash
pipx install bernstein
bernstein init
bernstein run -g "fix the failing test in tests/test_foo.py"
```

### Демо за 60 секунд

Ролик ниже охватывает один прогон целиком: менеджер декомпозирует цель, три агента работают параллельно, цепочка аудита фиксирует каждый handoff, janitor проверяет, и открывается PR.

<p align="center">
  <img alt="60-секундное демо Bernstein: менеджер декомпозирует цель, три агента работают параллельно, цепочка аудита фиксирует каждый handoff, открывается PR" src="../../docs/demo/demo.gif" width="800">
</p>

После прогона Bernstein постит структурированный комментарий в PR со стоимостью, результатами тестов, lineage и хеш-цепочкой аудита:

<p align="center">
  <img alt="Комментарий Bernstein в PR: разделы Сводка, Стоимость, Lineage, Тесты, Цепочка аудита" src="../../docs/demo/screenshot-pr-comment.svg" width="720">
</p>

> GIF собирается из [`docs/demo/demo.tape`](../../docs/demo/demo.tape) с помощью [vhs](https://github.com/charmbracelet/vhs); локально перегенерировать — `vhs docs/demo/demo.tape`.

### Сравнение

| Возможность                                    | Bernstein   | Archon   | LangGraph |
|------------------------------------------------|-------------|----------|-----------|
| Команда мульти-агентов (параллельные адаптеры) | да          | один     | да        |
| Подписанный lineage / цепочка аудита           | да          | нет      | нет       |
| Air-gap / суверенный деплой                    | да          | частично | нет       |
| Визуальный workflow YAML                       | да [^yaml]  | да       | нет       |
| Хостовый дашборд / SaaS                        | нет         | частично | нет       |

[^yaml]: Поддержка workflow YAML приедет с [PR #1108](https://github.com/sipyourdrink-ltd/bernstein/pull/1108) (в этой же партии). До этого момента планы пишут на Python или подают через `bernstein run plan.yaml` под старую схему.

Более длинная матрица возможностей против CrewAI, AutoGen, LangGraph и четырёх оркестраторов CLI-агентов того же класса, что и Bernstein, лежит в разделе [Подробное сравнение](#detailed-comparison) ниже.

---

### Что это, в одном абзаце

Вы говорите Bernstein, что хотите получить. Он распределяет работу между несколькими ИИ-агентами для кода, запускает их параллельно в изолированных git worktree, фиксирует каждый handoff в HMAC-цепочечном логе аудита, прогоняет тесты и мерджит тот код, который реально проходит. Вы возвращаетесь к зелёному PR.

Forward-deployed engineering в формате роя. Положите Bernstein в репозиторий клиента — и у вас есть мульти-агентная команда с состоянием в файлах, ограничением учётных данных по агентам и подписанным audit trail, работающая поверх тех CLI-агентов, которым клиент уже доверяет.

### Другие способы установки

```bash
curl -fsSL https://bernstein.run/install.sh | sh        # Одна строка для macOS / Linux
irm https://bernstein.run/install.ps1 | iex             # PowerShell в Windows
pip install bernstein                                   # pip
uv tool install bernstein                               # uv
brew tap chernistry/tap && brew install bernstein       # Homebrew
```

Полная [матрица установки](#install) покрывает `dnf copr`, `npx`, опциональные extras и путь wheelhouse для air-gap-площадок.

### Почему планировщик — обычный Python

Большинство оркестраторов агентов используют LLM, чтобы решать, кто что делает. Это недетерминированно и сжигает токены на планирование вместо кода. Bernstein делает один LLM-вызов, чтобы декомпозировать вашу цель, а всё остальное (параллельный запуск агентов, изоляция их git-веток, прогон тестов, маршрутизация повторных попыток) — это обычный Python. Каждый запуск воспроизводим. Каждый шаг логируется и поддаётся повтору.

Никакого фреймворка для изучения. Никакой привязки к вендору. Меняйте любого агента, любую модель, любого провайдера.

<img alt="Bernstein в действии: параллельные ИИ-агенты, оркестрируемые в реальном времени" src="../../docs/assets/in-action-small.gif" width="700">

Что вы видите во время работы:

```
$ bernstein -g "Add JWT auth"
[manager] decomposed into 4 tasks
[agent-1] claude-sonnet: src/auth/middleware.py  (done, 2m 14s)
[agent-2] codex:         tests/test_auth.py      (done, 1m 58s)
[verify]  all gates pass. merging to main.
```

## Поддерживаемые агенты

Bernstein автоматически обнаруживает установленные CLI-агенты. Смешивайте их в одном запуске. Дешёвые локальные модели — для рутины, более тяжёлые облачные — для архитектуры.

43 адаптера CLI-агентов: 40 обёрток для сторонних инструментов плюс универсальная обёртка для всего, что принимает `--prompt`.

| Агент | Модели | Установка |
|-------|--------|-----------|
| [Claude Code](https://docs.anthropic.com/en/docs/claude-code) | Opus 4, Sonnet 4.6, Haiku 4.5 | `npm install -g @anthropic-ai/claude-code` |
| [Codex CLI](https://github.com/openai/codex) | GPT-5, GPT-5 mini | `npm install -g @openai/codex` |
| [OpenAI Agents SDK v2](https://openai.github.io/openai-agents-python/) | GPT-5, GPT-5 mini, o4 | `pip install 'bernstein[openai]'` |
| [GitHub Copilot CLI](https://docs.github.com/en/copilot/github-copilot-in-the-cli) | Управляются Copilot (GPT-5, Sonnet 4.6) | `npm install -g @github/copilot` |
| [Gemini CLI](https://github.com/google-gemini/gemini-cli) | Gemini 2.5 Pro, Gemini Flash | `npm install -g @google/gemini-cli` |
| [Cursor](https://www.cursor.com) | Sonnet 4.6, Opus 4, GPT-5 | [Приложение Cursor](https://www.cursor.com) |
| [Aider](https://aider.chat) | Любая, совместимая с OpenAI/Anthropic | `pip install aider-chat` |
| [Amp](https://ampcode.com) | Управляются Amp | `npm install -g @sourcegraph/amp` |
| [Cody](https://sourcegraph.com/cody) | Хостятся Sourcegraph | `npm install -g @sourcegraph/cody` |
| [Continue](https://continue.dev) | Любая, совместимая с OpenAI/Anthropic | `npm install -g @continuedev/cli` (бинарь: `cn`) |
| [Goose](https://block.github.io/goose/) | Любой провайдер, поддерживаемый Goose | См. [документацию Goose](https://block.github.io/goose/) |
| [IaC](https://www.terraform.io/) (Terraform/Pulumi) | Любой провайдер, который использует базовый агент | Встроенный |
| [Kilo](https://kilo.dev) | Хостятся Kilo | См. [документацию Kilo](https://kilo.dev) |
| [Kiro](https://kiro.dev) | Хостятся Kiro | См. [документацию Kiro](https://kiro.dev) |
| [Ollama](https://ollama.ai) + Aider | Локальные модели (офлайн) | `brew install ollama` |
| [OpenCode](https://opencode.ai) | Любой провайдер, поддерживаемый OpenCode | См. [документацию OpenCode](https://opencode.ai) |
| [Qwen](https://github.com/QwenLM/qwen-code) | Модели Qwen Code | `npm install -g @qwen-code/qwen-code` |
| [Cloudflare Agents](https://developers.cloudflare.com/agents/) | Модели Workers AI | `bernstein cloud login` |
| [OpenHands](https://github.com/OpenHands/OpenHands) | Любая, поддерживаемая LiteLLM (Anthropic, OpenAI, ...) | `uv tool install openhands --python 3.12` |
| [Open Interpreter](https://github.com/OpenInterpreter/open-interpreter) | Любая (через LiteLLM) | `pip install open-interpreter` |
| [gptme](https://github.com/gptme/gptme) | Anthropic, OpenAI, OpenRouter | `pipx install gptme` |
| [Plandex](https://github.com/plandex-ai/plandex) | Plandex Cloud или self-hosted модели | `curl -sL https://plandex.ai/install.sh \| bash` |
| [AIChat](https://github.com/sigoden/aichat) | OpenAI, Anthropic, OpenRouter, Groq, Gemini | `cargo install aichat` |
| [Letta Code](https://github.com/letta-ai/letta-code) | Маршрутизируются Letta (Anthropic, OpenAI) | `npm install -g @letta-ai/letta-code` |
| **Generic** | Любой CLI с `--prompt` | Встроенный |

#### Делегирование оркестраторам (leaf-node)

Отдельный, более узкий класс адаптеров, которые оборачивают **другие CLI-оркестраторы**, как если бы они были одиночными агентами. Bernstein передаёт обёрнутому инструменту промпт или план и видит только итоговый exit code; затраты под-агентов и quality gates внутри обёрнутого оркестратора для Bernstein невидимы. Полезно, когда вы хотите вставить уже готовый workflow на одном из этих инструментов как шаг более крупного плана Bernstein.

| Оркестратор | Обёрнут как | Установка |
|-------------|-------------|-----------|
| [Composio Agent Orchestrator](https://github.com/ComposioHQ/agent-orchestrator) (`@aoagents/ao`) | `composio` | `npm install -g @aoagents/ao` |
| [umputun/ralphex](https://github.com/umputun/ralphex) | `ralphex` | `go install github.com/umputun/ralphex/cmd/ralphex@latest` |

Любой адаптер также работает как **внутренний LLM-планировщик**. Запускайте весь стек без какого-либо конкретного провайдера:

```yaml
internal_llm_provider: gemini            # или qwen, ollama, codex, goose, ...
internal_llm_model: gemini-3.1-pro
```

> [!TIP]
> Запускайте `bernstein --headless` для CI-пайплайнов. Никакого TUI, структурированный JSON-вывод, ненулевой exit code при сбое.

## Быстрый старт

```bash
cd your-project
bernstein init                    # создаёт .sdd/ + bernstein.yaml
bernstein -g "Add rate limiting"  # агенты стартуют, работают параллельно, верифицируют, выходят
bernstein live                    # наблюдайте прогресс в TUI-дашборде
bernstein stop                    # корректное завершение с дрейном
```

Для многоэтапных проектов опишите план в YAML:

```bash
bernstein run plan.yaml           # пропускает LLM-планирование, сразу к выполнению
bernstein run --dry-run plan.yaml # предпросмотр задач и оценочной стоимости
```

## Как это работает

1. **Декомпозиция.** Менеджер разбивает вашу цель на задачи с ролями, закреплёнными за ними файлами и сигналами завершения.
2. **Запуск.** Агенты стартуют в изолированных git worktrees, по одному на задачу. Основная ветка остаётся чистой.
3. **Верификация.** Janitor проверяет конкретные сигналы: тесты проходят, файлы существуют, линт чист, типы корректны.
4. **Мердж.** Проверенная работа попадает в main. Проваленные задачи перезапускаются или маршрутизируются на другую модель.

Оркестратор — это Python-планировщик, а не LLM. Решения о планировании детерминированы, проверяемы и воспроизводимы.

## Облачное выполнение (Cloudflare)

Bernstein умеет запускать агентов на Cloudflare Workers вместо локального запуска. CLI `bernstein cloud` берёт на себя деплой и жизненный цикл.

- **Workers.** Выполнение агентов на edge Cloudflare с Durable Workflows для многошаговых задач и автоматическим повтором.
- **Изоляция в V8-песочнице.** Каждый агент работает в собственном изоляте, без накладных расходов на контейнер.
- **Синхронизация рабочего пространства через R2.** Состояние локального worktree синхронизируется с объектным хранилищем R2, чтобы облачные агенты видели те же файлы.
- **Workers AI** (экспериментально). Используйте модели, хостимые Cloudflare, в качестве LLM-провайдера — внешние API-ключи не требуются.
- **Аналитика в D1.** Метрики задач и данные о стоимости хранятся в D1 для запросов.
- **Vectorize.** Семантический кеш на базе векторной БД Cloudflare.
- **Browser rendering.** Headless Chrome на Workers для агентов, которым нужно инспектировать веб-вывод.
- **Удалённый транспорт MCP.** Публикуйте или потребляйте MCP-серверы через сеть Cloudflare.

```bash
bernstein cloud login      # аутентификация в Bernstein Cloud
bernstein cloud deploy     # пушит worker'ы агентов
bernstein cloud run plan.yaml  # выполняет план на Cloudflare
```

## Возможности

**Базовая оркестрация.** Параллельное выполнение, изоляция через git worktree, верификация janitor'ом, quality gates (линт, типы, PII-сканирование), кросс-модельный код-ревью, circuit breaker для вышедших из строя агентов, мониторинг роста токенов с авто-вмешательством.

**Интеллект.** Контекстный bandit-роутер для выбора модели/уровня усилий. Граф знаний для анализа влияния изменений на кодовую базу. Семантическое кеширование экономит токены на повторяющихся паттернах. Детектор аномалий по затратам (алерты по burn-rate). Детектор поведенческих аномалий с маркировкой по Z-score.

**Sandboxing.** Подключаемый протокол [`SandboxBackend`](../../docs/architecture/sandbox.md) — запускайте агентов в локальных git worktrees (по умолчанию), Docker-контейнерах, Firecracker-микро-VM от [E2B](https://e2b.dev) или serverless-контейнерах [Modal](https://modal.com) (с опциональным GPU). Авторы плагинов могут регистрировать собственные бэкенды через entry-point группу `bernstein.sandbox_backends`. Установленные бэкенды смотрите через `bernstein agents sandbox-backends`.

**Хранилище артефактов.** Состояние `.sdd/` может стримиться в подключаемые бэкенды [`ArtifactSink`](../../docs/architecture/storage.md): локальная файловая система (по умолчанию), S3, Google Cloud Storage, Azure Blob или Cloudflare R2. `BufferedSink` сохраняет crash-safety-контракт WAL: сначала пишет локально с fsync, затем асинхронно зеркалит в удалённое хранилище.

**Skill packs.** [Скиллы](../../docs/architecture/skills.md) с прогрессивным раскрытием (паттерн OpenAI Agents SDK): в системный промпт каждого спавна попадает только компактный индекс скиллов, а сами тела агенты подтягивают по требованию через MCP-инструмент `load_skill`. 17 встроенных ролевых паков плюс сторонние entry-point'ы `bernstein.skill_sources`.

**Контроль.** Аудит-логи с цепочкой HMAC, движок политик, гейтинг PII в выводе, восстановление после краша на базе WAL (экспериментальная безопасность многопроцессного режима), OAuth 2.0 PKCE.

**Наблюдаемость.** `/metrics` для Prometheus, пресеты экспортёров OTel, дашборды Grafana. Учёт затрат по моделям (`bernstein cost`). Терминальный TUI и веб-дашборд. Видимость процессов агентов в `ps`.

**Экосистема.** Режим MCP-сервера, поддержка протокола A2A, интеграция с GitHub App, плагин-система на pluggy, мульти-репо рабочие пространства, кластерный режим для распределённого выполнения, само-эволюция через `--evolve` (экспериментально).

Полная матрица возможностей: [FEATURE_MATRIX.md](../../docs/reference/FEATURE_MATRIX.md) &middot; Свежие фичи: [What's New](../../docs/whats-new.md)

## Что нового в v1.9

**Мост ACP** — `bernstein acp serve --stdio` отдаёт Bernstein любому редактору, говорящему по Agent Communication Protocol (Zed и др.). Плагинный код на стороне редактора не нужен.

**Автономный ремонт CI** — `bernstein autofix` следит за открытыми PR'ами Bernstein и, когда CI краснеет, автоматически спавнит агента-ремонтника. После того как сборка станет зелёной, он пушит фикс и заново запрашивает ревью.

**Хранилище учётных данных** — `bernstein connect <provider>` пишет API-ключи в keychain ОС; `bernstein creds` отображает и ротирует их. Агенты наследуют ограниченные по области credentials, не трогая переменные окружения.

**Preview-туннели** — `bernstein preview start` поднимает sandboxed dev-сервер и печатает публичный URL. Удобно, чтобы поделиться запущенной веткой с ревьюером, не деплоя в staging.

Полный changelog: [docs/whats-new.md](../../docs/whats-new.md)

## Команды для оператора

Команды, которые избавляют от glue-кода, который большинство команд в итоге пишет вокруг своих запусков.

| Команда | Что делает |
|---------|------------|
| `bernstein pr` | Автоматически создаёт PR в GitHub из завершённой сессии; в теле — результаты гейтов janitor'а и разбивка стоимости в токенах/USD. |
| `bernstein from-ticket <url>` | Импортирует тикет из Linear / GitHub Issues / Jira как задачу Bernstein. По меткам определяется роль и scope. Поддерживает `--dry-run` и `--run`. |
| `bernstein ticket import <url>` | Алиас / групповая форма `from-ticket` для скриптинга. |
| `bernstein remote` | SSH-бэкенд для песочницы. `remote test <host>`, `remote run <host> <path>`, `remote forget <host>`. Переиспользование сокета ControlMaster для быстрых повторных вызовов. |
| `bernstein hooks` | Lifecycle-хуки `pre_task`, `post_task`, `pre_merge`, `post_merge`, `pre_spawn`, `post_spawn` — shell-скрипты или pluggy `@hookimpl`. `hooks list`, `hooks run <event>`, `hooks check`. |
| `bernstein chat serve --platform=telegram\|discord\|slack` | Управляйте запусками из чата командами `/run`, `/status`, `/approve`, `/reject`, `/switch`, `/stop`. |
| `bernstein approve-tool` / `bernstein reject-tool` | Интерактивное согласование вызовов инструментов посреди запуска. `--latest`, `--id`, `--always`. |
| `bernstein tunnel start <port> [--provider auto\|cloudflared\|ngrok\|bore\|tailscale]` | Единая обёртка вокруг четырёх провайдеров туннелей. Также `tunnel list`, `tunnel stop <name>\|--all`. Переиспользование процессов в стиле ControlMaster. |
| `bernstein daemon install [--user\|--system] [--command="..."] [--env KEY=VAL]...` | Устанавливает unit systemd (Linux) или launchd (macOS) для авто-старта. Также `daemon start/stop/restart/status/uninstall`. |
| `bernstein connect <provider>` / `bernstein creds` | Хранит и ротирует API-ключи в keychain ОС. Агенты получают ограниченные ключи на каждый запуск. |
| `bernstein autofix` | Демон, отслеживающий открытые PR'ы Bernstein; спавнит агента-ремонтника при падении CI и автоматически пушит фикс. |
| `bernstein preview start` | Поднимает sandboxed dev-сервер для текущей ветки и выводит публичный URL расшариваемого туннеля. |

## Сравнение

| Возможность | Bernstein | CrewAI | AutoGen [^autogen] | LangGraph |
|-------------|-----------|--------|---------|-----------|
| Оркестратор | Детерминированный код | На базе LLM (+ code Flows) | На базе LLM | Граф + LLM |
| Работает с | Любым CLI-агентом (43 адаптера) | Классами Python SDK | Python-агентами | Узлами LangChain |
| Изоляция через git | Worktree на каждого агента | Нет | Нет | Нет |
| Подключаемые песочницы | Worktree, Docker, E2B, Modal | Нет | Нет | Нет |
| Верификация | Janitor + quality gates | Guardrails + Pydantic-вывод | Условия завершения | Условные рёбра |
| Учёт затрат | Встроен | `usage_metrics` | `RequestUsage` | Через LangSmith |
| Модель состояния | Файловая (.sdd/) | In-memory + чекпоинт SQLite | In-memory | Checkpointer |
| Удалённые artifact sink'и | S3, GCS, Azure Blob, R2 | Нет | Нет | Нет |
| Само-эволюция | Встроена (экспериментально) | Нет | Нет | Нет |
| Декларативные планы (YAML) | Да | Да (`agents.yaml`, `tasks.yaml`) | Нет | Частично (`langgraph.json`) |
| Маршрутизация модели по задаче | Да | LLM на каждого агента | `model_client` на каждого агента | По узлу (вручную) |
| Поддержка MCP | Да (клиент + сервер) | Да | Да (клиент + workbench) | Да (клиент + сервер) |
| Чат между агентами | Доска объявлений | Да (процесс Crew) | Да (групповой чат) | Да (supervisor, swarm) |
| Веб-UI | TUI + веб-дашборд | CrewAI AMP | AutoGen Studio | LangGraph Studio + LangSmith |
| Облачный хостинг | Да (Cloudflare) | Да (CrewAI AMP) | Нет | Да (LangGraph Cloud) |
| Встроенный RAG/поиск | Да (FTS5 + BM25 по кодовой базе) | `crewai_tools` | Ретриверы `autogen_ext` | Через LangChain |

*Дата последней проверки: 2026-04-19. См. [страницы полного сравнения](../../docs/compare/README.md) с детальными матрицами возможностей.*

Таблица выше сравнивает Bernstein с фреймворками для оркестрации LLM (они оркеструют LLM-вызовы). Таблица ниже покрывает более близкую категорию — другие инструменты, оркестрирующие **CLI-агентов для кода**:

| Возможность | Bernstein | [awslabs/cli-agent-orchestrator](https://github.com/awslabs/cli-agent-orchestrator) | [ComposioHQ/agent-orchestrator](https://github.com/ComposioHQ/agent-orchestrator) | [emdash](https://github.com/generalaction/emdash) | [umputun/ralphex](https://github.com/umputun/ralphex) |
|-------------|-----------|-----------|-----------|-----------|-----------|
| Форма | Python CLI + библиотека + MCP-сервер | Python CLI + tmux-сессии + веб-UI | TypeScript CLI + локальный дашборд | Electron-приложение | Go CLI |
| Основной язык | Python | Python | TypeScript | TypeScript | Go |
| Установка | `pipx install bernstein` | `uv tool install cli-agent-orchestrator` | `npm install -g @aoagents/ao` | `.dmg` / `.msi` / `.AppImage` | `go install` / одиночный бинарь |
| Адаптеры агентов | 43 | 5 (Kiro, Claude Code, Codex, Gemini, Kimi) | 3 (Claude Code, Codex, Aider) | 24 | 1 (только Claude Code) |
| Параллельное выполнение нескольких агентов | Да | Да (tmux-сессия на агента) | Да | Да | Нет (одна последовательная сессия) |
| Git worktree на агента | Да | Нет (запланировано, [#100](https://github.com/awslabs/cli-agent-orchestrator/issues/100)) | Да | Да | Опциональный флаг `--worktree` |
| Режим MCP-сервера (отдаёт сам себя как MCP) | Да (stdio + HTTP/SSE) | Да (межагентные коммуникации) | Нет | Нет | Нет |
| Координатор | Детерминированный планировщик на Python | Иерархический LLM-супервизор | На базе LLM | Не задокументировано | Линейный исполнитель плана |
| Аудит-реплей с цепочкой HMAC | Да | Нет | Нет | Нет | Нет |
| Кросс-модельная верификация / quality gates | Да (многоступенчатая) | Нет | Нет | Нет | Многофазный ревью (только Claude) |
| Автономный CI-fix / PR-флоу | Да (`bernstein autofix`) | Нет | Да | Нет | Нет |
| Визуальный дашборд | TUI + веб | Веб-UI + tmux | Веб | Десктоп-приложение | Веб (`--serve`) |
| Каналы уведомлений | Telegram/Slack/Discord/Email/Webhook/Shell | — | Нет | Нет | Telegram / Email / Slack / Webhook |
| Поддержка | Соло-OSS | AWS Labs | С финансированием (Composio.dev) | YC W26 | Соло-OSS |
| Лицензия | Apache 2.0 | Apache 2.0 | MIT | Apache 2.0 | MIT |

Преимущество Bernstein в этой категории: **Python-нативный, MCP-сервер по умолчанию, самое широкое покрытие адаптеров, настоящий многоагентный параллелизм, детерминированный планировщик без LLM в петле координации.** Если вам нужна изоляция через tmux-сессии в стиле AWS с иерархическим LLM-супервизором — `cao` от AWS Labs ближе; если у вас стек на TypeScript и нужен продукт с дашбордом — лучше подойдёт `@aoagents/ao` от Composio; если хочется отполированную десктоп-ADE — это emdash; если вы используете только Claude Code и хотите один Go-бинарь, который проходит план сверху вниз — это ralphex. Если же нужен примитив, который импортируется в Python, отдаёт себя через MCP любому клиенту, запускает много агентов параллельно и покрывает всю широту агентов (включая Qwen, Goose, Ollama, OpenAI Agents SDK, Cloudflare Agents и другие) — это Bernstein.

[^autogen]: AutoGen в режиме сопровождения; преемник — Microsoft Agent Framework 1.0.

## Мониторинг

```bash
bernstein live       # TUI-дашборд
bernstein dashboard  # веб-дашборд
bernstein status     # сводка по задачам
bernstein ps         # запущенные агенты
bernstein cost       # затраты по модели/задаче
bernstein doctor     # пред-полётные проверки
bernstein recap      # итоговая сводка после запуска
bernstein trace <ID> # трасса решений агента
bernstein run-changelog --hours 48  # changelog из диффов, произведённых агентами
bernstein explain <cmd>  # подробная справка с примерами
bernstein dry-run    # предпросмотр задач без выполнения
bernstein dep-impact # поломки API + влияние на upstream-вызывающих
bernstein aliases    # показать сокращения команд
bernstein config-path    # показать пути к конфигам
bernstein init-wizard    # интерактивная настройка проекта
bernstein debug-bundle   # собрать логи, конфиг и состояние для bug-репорта
bernstein skills list    # обнаруживаемые skill packs (прогрессивное раскрытие)
bernstein skills show <name>  # вывести тело скилла со ссылками
```

```bash
bernstein fingerprint build --corpus-dir ~/oss-corpus  # построить локальный индекс схожести
bernstein fingerprint check src/foo.py                 # проверить сгенерированный код по индексу
```

## Установка

| Способ | Команда |
|--------|---------|
| **Один лайнер (macOS / Linux)** | `curl -fsSL https://bernstein.run/install.sh \| sh` |
| **Один лайнер (Windows)** | `irm https://bernstein.run/install.ps1 \| iex` |
| **pip** | `pip install bernstein` |
| **pipx** | `pipx install bernstein` |
| **uv** | `uv tool install bernstein` |
| **Homebrew** | `brew tap chernistry/tap && brew install bernstein` |
| **Fedora / RHEL** | `sudo dnf copr enable alexchernysh/bernstein && sudo dnf install bernstein` |
| **npm** (обёртка) | `npx bernstein-orchestrator` |

Скрипты-однострочники проверяют наличие Python 3.12+, поднимают pipx, если его нет, чинят PATH в текущей сессии и устанавливают (или обновляют) `bernstein`. Они корректно обрабатывают macOS-окружения под управлением brew и фолбэк на лаунчер `py -3` в Windows. Исходники скриптов: [install.sh](../../scripts/install.sh) · [install.ps1](../../scripts/install.ps1).

### Опциональные extras

SDK провайдеров вынесены в опции, чтобы базовая установка оставалась лёгкой. Выберите, что нужно:

| Extra | Что включает |
|-------|--------------|
| `bernstein[openai]` | Адаптер OpenAI Agents SDK v2 (`openai_agents`) |
| `bernstein[docker]` | Песочница на Docker |
| `bernstein[e2b]` | Песочница на микро-VM [E2B](https://e2b.dev) (нужен `E2B_API_KEY`) |
| `bernstein[modal]` | Песочница [Modal](https://modal.com), опциональный GPU (нужны `MODAL_TOKEN_ID` / `MODAL_TOKEN_SECRET`) |
| `bernstein[s3]` | Artifact sink в S3 (через `boto3`) |
| `bernstein[gcs]` | Artifact sink в Google Cloud Storage |
| `bernstein[azure]` | Artifact sink в Azure Blob |
| `bernstein[r2]` | Artifact sink в Cloudflare R2 (S3-совместимый `boto3`) |
| `bernstein[grpc]` | Мост gRPC |
| `bernstein[k8s]` | Интеграции с Kubernetes |

Комбинируйте extras в скобках, например: `pip install 'bernstein[openai,docker,s3]'`.

Расширения для редакторов: [VS Marketplace](https://marketplace.visualstudio.com/items?itemName=alex-chernysh.bernstein) &middot; [Open VSX](https://open-vsx.org/extension/alex-chernysh/bernstein)

## Контрибьюция

PR'ы приветствуются. См. [CONTRIBUTING.md](../../CONTRIBUTING.md) — там настройка и code style.

## Поддержка

Если Bernstein экономит вам время: [GitHub Sponsors](https://github.com/sponsors/chernistry)

Контакт: [forte@bernstein.run](mailto:forte@bernstein.run)

## Где упомянули

Кураторские списки, рассылки и соседние проекты, подхватившие Bernstein:

- [**Python Weekly #742**](https://www.pythonweekly.com/p/python-weekly-issue-742-april-23-2026) (23 апреля 2026) — упоминание в рассылке.
- [**Augment Code — 9 Open-Source Agent Orchestrators for AI Coding (2026)**](https://www.augmentcode.com/tools/open-source-agent-orchestrators) — редакционный обзор; «архитектурно самый интересный инструмент в подборке».
- [**nibzard/awesome-agentic-patterns**](https://github.com/nibzard/awesome-agentic-patterns/blob/main/patterns/deterministic-zero-LLM-orchestration.md) — Bernstein упомянут как production-реализация паттерна «детерминированная оркестрация без LLM».
- [**Jenqyang/Awesome-AI-Agents**](https://github.com/Jenqyang/Awesome-AI-Agents)
- [**jamesmurdza/awesome-ai-devtools**](https://github.com/jamesmurdza/awesome-ai-devtools)
- [**jim-schwoebel/awesome_ai_agents**](https://github.com/jim-schwoebel/awesome_ai_agents)
- [**Piebald-AI/awesome-gemini-cli**](https://github.com/Piebald-AI/awesome-gemini-cli)
- [**ComposioHQ/awesome-codex-skills**](https://github.com/ComposioHQ/awesome-codex-skills)
- [**jxzhangjhu/Awesome-LLM-RAG**](https://github.com/jxzhangjhu/Awesome-LLM-RAG)
- [**rohitg00/awesome-claude-code-toolkit**](https://github.com/rohitg00/awesome-claude-code-toolkit)
- [**numtide/llm-agents.nix**](https://github.com/numtide/llm-agents.nix) — дистрибуция в виде Nix flake.

<details>
<summary>Ещё awesome-списки и кураторские подборки сообщества</summary>

- [andyrewlee/awesome-agent-orchestrators](https://github.com/andyrewlee/awesome-agent-orchestrators)
- [bradAGI/awesome-cli-coding-agents](https://github.com/bradAGI/awesome-cli-coding-agents)
- [milisp/awesome-codex-cli](https://github.com/milisp/awesome-codex-cli)
- [yaolifeng0629/Awesome-independent-tools](https://github.com/yaolifeng0629/Awesome-independent-tools) (中文 + EN)
- [caramaschiHG/awesome-ai-agents-2026](https://github.com/caramaschiHG/awesome-ai-agents-2026)
- [ai-for-developers/awesome-vibe-coding](https://github.com/ai-for-developers/awesome-vibe-coding)
- [killop/anything_about_game](https://github.com/killop/anything_about_game) (`AI.md`)
- [Glama MCP Catalog](https://glama.ai/mcp/servers/sipyourdrink-ltd/bernstein) — редакционный листинг MCP-серверов.
- Зеркала: [icopy-site/awesome](https://github.com/icopy-site/awesome), [icopy-site/awesome-cn](https://github.com/icopy-site/awesome-cn), [trackawesomelist/trackawesomelist](https://github.com/trackawesomelist/trackawesomelist).

</details>

<details>
<summary>Цитируется как prior art в смежных проектах</summary>

- [**mkb23/overcode**](https://github.com/mkb23/overcode/blob/main/docs/design/bakeoffs/overcode-vs-bernstein.md) — развёрнутый bakeoff, рассматривающий Bernstein как референсную реализацию.
- [**Vintersong/NOVA-Cognition-Framework**](https://github.com/Vintersong/NOVA-Cognition-Framework) — `BERNSTEIN_PATTERNS.md`, «Patterns Worth Borrowing».
- [**AJV009/drupal-contrib-workbench**](https://github.com/AJV009/drupal-contrib-workbench) — заметки об исследовании разделения на manager/janitor.
- [**danielvaughan/codex-blog**](https://github.com/danielvaughan/codex-blog/blob/main/_posts/2026-04-09-loki-mode-autonomous-execution.md) — сравнительная статья, помещающая Bernstein на детерминированный конец спектра.

</details>

## История звёзд

[![Star History Chart](https://api.star-history.com/svg?repos=sipyourdrink-ltd/bernstein&type=Date)](https://star-history.com/#sipyourdrink-ltd/bernstein&Date)

## Лицензия

[Apache License 2.0](../../LICENSE)

---

Сделано с любовью — [Alex Chernysh](https://alexchernysh.com) &middot; [GitHub](https://github.com/chernistry) &middot; [X](https://x.com/alex_chernysh) &middot; [bernstein.run](https://bernstein.run?utm_source=github.com&utm_medium=readme&utm_campaign=bernstein-readme)

<!-- mcp-name: io.github.sipyourdrink-ltd/bernstein -->
