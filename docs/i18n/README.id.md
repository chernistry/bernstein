<div align="center">

[English](../../README.md) | [Español (Spanish)](README.es.md) | [中文 (Chinese)](README.zh.md) | [العربية (Arabic)](README.ar.md) | [Português (Portuguese)](README.pt.md) | **Bahasa Indonesia (Indonesian)** | [Français (French)](README.fr.md) | [日本語 (Japanese)](README.ja.md) | [Русский (Russian)](README.ru.md) | [Deutsch (German)](README.de.md) | [עברית (Hebrew)](README.he.md) | [יידיש (Yiddish)](README.yi.md)

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="../../docs/assets/logo-dark.svg">
  <source media="(prefers-color-scheme: light)" srcset="../../docs/assets/logo-light.svg">
  <img alt="Bernstein" src="../../docs/assets/logo-light.svg" width="340">
</picture>

<br>

> *"Untuk mencapai hal-hal besar, dua hal dibutuhkan: sebuah rencana dan waktu yang tidak cukup."* — Leonard Bernstein

### Orkestrasi semua AI coding agent. Model apa pun. Satu perintah.

[![CI](https://github.com/sipyourdrink-ltd/bernstein/actions/workflows/ci.yml/badge.svg)](https://github.com/sipyourdrink-ltd/bernstein/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/bernstein)](https://pypi.org/project/bernstein/)
[![Python 3.12+](https://img.shields.io/badge/python-3.12+-3776ab?logo=python&logoColor=white)](https://python.org)
[![License](https://img.shields.io/github/license/sipyourdrink-ltd/bernstein)](../../LICENSE)
[![MseeP.ai](https://img.shields.io/badge/MseeP.ai-verified-2496ed)](https://mseep.ai/app/chernistry-bernstein)

[Website](https://bernstein.run?utm_source=github.com&utm_medium=readme&utm_campaign=bernstein-readme) &middot; [Dokumentasi](https://bernstein.readthedocs.io/) &middot; [Memulai](../../docs/getting-started/GETTING_STARTED.md) &middot; [Glosarium](../../docs/reference/GLOSSARY.md) &middot; [Keterbatasan](../../docs/reference/KNOWN_LIMITATIONS.md)

</div>

---

Bernstein adalah scheduler Python yang deterministik; ia menjalankan kru CLI coding agent (Claude Code, Codex, Gemini CLI, dan 40 lainnya) atas satu tujuan secara paralel di worktree git, dengan rantai audit yang ditandatangani HMAC pada setiap langkah.

### Pasang dalam 30 detik

```bash
pipx install bernstein
bernstein init
bernstein run -g "fix the failing test in tests/test_foo.py"
```

### Lihat dalam 60 detik

Klip berikut mencakup satu run penuh: manager mendekomposisi tujuan, tiga agen bekerja paralel, rantai audit mencatat setiap handoff, janitor memverifikasi, lalu PR dibuka.

<p align="center">
  <img alt="Demo Bernstein 60 detik: manager mendekomposisi tujuan, tiga agen jalan paralel, rantai audit mencatat setiap handoff, PR dibuka" src="../../docs/demo/demo.gif" width="800">
</p>

Setelah run, Bernstein memposting komentar terstruktur di PR dengan biaya, hasil tes, lineage, dan rantai hash audit:

<p align="center">
  <img alt="Komentar PR Bernstein: bagian Ringkasan, Biaya, Lineage, Tes, Rantai audit" src="../../docs/demo/screenshot-pr-comment.svg" width="720">
</p>

> GIF dibangkitkan dari [`docs/demo/demo.tape`](../../docs/demo/demo.tape) dengan [vhs](https://github.com/charmbracelet/vhs); regenerasi lokal dengan `vhs docs/demo/demo.tape`.

### Bagaimana perbandingannya

| Fitur                                       | Bernstein   | Archon   | LangGraph |
|---------------------------------------------|-------------|----------|-----------|
| Kru multiagent (adapter paralel)            | ya          | satu     | ya        |
| Lineage bertanda tangan / rantai audit      | ya          | tidak    | tidak     |
| Deploy air-gap / berdaulat                  | ya          | parsial  | tidak     |
| Workflow YAML visual                        | ya [^yaml]  | ya       | tidak     |
| Dashboard hosted / SaaS                     | tidak       | parsial  | tidak     |

[^yaml]: Dukungan workflow YAML mendarat bersama [PR #1108](https://github.com/sipyourdrink-ltd/bernstein/pull/1108) (di batch ini). Sampai itu mendarat, plan ditulis dengan Python atau via `bernstein run plan.yaml` di skema lama.

Matriks fitur yang lebih panjang melawan CrewAI, AutoGen, LangGraph, dan empat orkestrator CLI agent yang berada di kategori sama dengan Bernstein ada di bagian [Perbandingan rinci](#detailed-comparison) di bawah.

---

### Apa ini, dalam satu paragraf?

Anda beri tahu Bernstein apa yang ingin dibangun. Ia membagi pekerjaan ke beberapa AI coding agent, menjalankan mereka paralel di dalam worktree git terisolasi, mencatat setiap handoff di log audit ber-rantai HMAC, menjalankan tes, lalu menggabungkan kode yang benar-benar lulus. Anda kembali ke PR yang sudah hijau.

Forward-deployed engineering, dalam bentuk swarm. Letakkan Bernstein di repo klien dan Anda mendapat kru multiagent dengan state berbasis file, scoping kredensial per agen, dan audit trail bertanda tangan, berjalan di atas CLI agent yang sudah dipercaya klien.

### Metode pemasangan lain

```bash
curl -fsSL https://bernstein.run/install.sh | sh        # Satu baris di macOS / Linux
irm https://bernstein.run/install.ps1 | iex             # PowerShell di Windows
pip install bernstein                                   # pip
uv tool install bernstein                               # uv
brew tap chernistry/tap && brew install bernstein       # Homebrew
```

Lihat [matriks pemasangan](#install) lengkap untuk `dnf copr`, `npx`, ekstra opsional, dan jalur wheelhouse untuk site air-gapped.

### Mengapa scheduler-nya Python biasa

Kebanyakan orkestrator agen menggunakan LLM untuk memutuskan siapa mengerjakan apa. Itu tidak deterministik dan menghabiskan token untuk penjadwalan, bukan untuk kode. Bernstein melakukan satu panggilan LLM untuk memecah tujuan Anda, lalu sisanya (menjalankan agen secara paralel, mengisolasi cabang git mereka, menjalankan tes, mengarahkan retry) adalah Python biasa. Setiap run dapat direproduksi. Setiap langkah dicatat dan dapat diputar ulang.

Tidak ada framework yang harus dipelajari. Tidak ada vendor lock-in. Tukar agen apa pun, model apa pun, provider apa pun.

<img alt="Bernstein beraksi: agen AI paralel diorkestrasi secara real time" src="../../docs/assets/in-action-small.gif" width="700">

Apa yang Anda lihat saat berjalan:

```
$ bernstein -g "Add JWT auth"
[manager] decomposed into 4 tasks
[agent-1] claude-sonnet: src/auth/middleware.py  (done, 2m 14s)
[agent-2] codex:         tests/test_auth.py      (done, 1m 58s)
[verify]  all gates pass. merging to main.
```

## Agen yang didukung

Bernstein secara otomatis mendeteksi CLI agent yang terpasang. Campurkan mereka dalam run yang sama. Model lokal yang murah untuk boilerplate, model cloud yang lebih berat untuk arsitektur.

43 adapter CLI agent: 40 wrapper pihak ketiga ditambah satu wrapper generik untuk apa pun yang punya `--prompt`.

| Agent | Models | Install |
|-------|--------|---------|
| [Claude Code](https://docs.anthropic.com/en/docs/claude-code) | Opus 4, Sonnet 4.6, Haiku 4.5 | `npm install -g @anthropic-ai/claude-code` |
| [Codex CLI](https://github.com/openai/codex) | GPT-5, GPT-5 mini | `npm install -g @openai/codex` |
| [OpenAI Agents SDK v2](https://openai.github.io/openai-agents-python/) | GPT-5, GPT-5 mini, o4 | `pip install 'bernstein[openai]'` |
| [GitHub Copilot CLI](https://docs.github.com/en/copilot/github-copilot-in-the-cli) | Copilot-managed (GPT-5, Sonnet 4.6) | `npm install -g @github/copilot` |
| [Gemini CLI](https://github.com/google-gemini/gemini-cli) | Gemini 2.5 Pro, Gemini Flash | `npm install -g @google/gemini-cli` |
| [Cursor](https://www.cursor.com) | Sonnet 4.6, Opus 4, GPT-5 | [Aplikasi Cursor](https://www.cursor.com) |
| [Aider](https://aider.chat) | Apa pun yang kompatibel dengan OpenAI/Anthropic | `pip install aider-chat` |
| [Amp](https://ampcode.com) | Amp-managed | `npm install -g @sourcegraph/amp` |
| [Cody](https://sourcegraph.com/cody) | Sourcegraph-hosted | `npm install -g @sourcegraph/cody` |
| [Continue](https://continue.dev) | Apa pun yang kompatibel dengan OpenAI/Anthropic | `npm install -g @continuedev/cli` (binary: `cn`) |
| [Goose](https://block.github.io/goose/) | Provider apa pun yang didukung Goose | Lihat [dokumentasi Goose](https://block.github.io/goose/) |
| [IaC](https://www.terraform.io/) (Terraform/Pulumi) | Provider apa pun yang dipakai agen dasar | Bawaan |
| [Kilo](https://kilo.dev) | Kilo-hosted | Lihat [dokumentasi Kilo](https://kilo.dev) |
| [Kiro](https://kiro.dev) | Kiro-hosted | Lihat [dokumentasi Kiro](https://kiro.dev) |
| [Ollama](https://ollama.ai) + Aider | Model lokal (offline) | `brew install ollama` |
| [OpenCode](https://opencode.ai) | Provider apa pun yang didukung OpenCode | Lihat [dokumentasi OpenCode](https://opencode.ai) |
| [Qwen](https://github.com/QwenLM/qwen-code) | Model Qwen Code | `npm install -g @qwen-code/qwen-code` |
| [Cloudflare Agents](https://developers.cloudflare.com/agents/) | Model Workers AI | `bernstein cloud login` |
| [OpenHands](https://github.com/OpenHands/OpenHands) | Apa pun yang didukung LiteLLM (Anthropic, OpenAI, ...) | `uv tool install openhands --python 3.12` |
| [Open Interpreter](https://github.com/OpenInterpreter/open-interpreter) | Apa pun (didukung LiteLLM) | `pip install open-interpreter` |
| [gptme](https://github.com/gptme/gptme) | Anthropic, OpenAI, OpenRouter | `pipx install gptme` |
| [Plandex](https://github.com/plandex-ai/plandex) | Plandex Cloud atau model self-hosted | `curl -sL https://plandex.ai/install.sh \| bash` |
| [AIChat](https://github.com/sigoden/aichat) | OpenAI, Anthropic, OpenRouter, Groq, Gemini | `cargo install aichat` |
| [Letta Code](https://github.com/letta-ai/letta-code) | Letta-routed (Anthropic, OpenAI) | `npm install -g @letta-ai/letta-code` |
| **Generic** | CLI apa pun yang punya `--prompt` | Bawaan |

#### Delegasi orkestrator (leaf-node)

Kelas adapter terpisah yang lebih kecil yang membungkus **orkestrator CLI lain** seakan-akan mereka adalah satu agen tunggal. Bernstein menyerahkan prompt atau rencana ke alat yang dibungkus dan hanya melihat exit code akhir; biaya sub-agen dan quality gates di dalam orkestrator yang dibungkus tidak terlihat oleh Bernstein. Berguna ketika Anda ingin memasukkan workflow yang sudah ada yang dibangun di atas salah satu alat ini ke dalam langkah dari rencana Bernstein yang lebih besar.

| Orchestrator | Wrapped as | Install |
|--------------|------------|---------|
| [Composio Agent Orchestrator](https://github.com/ComposioHQ/agent-orchestrator) (`@aoagents/ao`) | `composio` | `npm install -g @aoagents/ao` |
| [umputun/ralphex](https://github.com/umputun/ralphex) | `ralphex` | `go install github.com/umputun/ralphex/cmd/ralphex@latest` |

Adapter mana pun juga berfungsi sebagai **scheduler LLM internal**. Jalankan seluruh stack tanpa provider tertentu:

```yaml
internal_llm_provider: gemini            # or qwen, ollama, codex, goose, ...
internal_llm_model: gemini-3.1-pro
```

> [!TIP]
> Jalankan `bernstein --headless` untuk pipeline CI. Tanpa TUI, output JSON terstruktur, exit non-zero saat gagal.

## Mulai cepat

```bash
cd your-project
bernstein init                    # creates .sdd/ workspace + bernstein.yaml
bernstein -g "Add rate limiting"  # agents spawn, work in parallel, verify, exit
bernstein live                    # watch progress in the TUI dashboard
bernstein stop                    # graceful shutdown with drain
```

Untuk proyek multi-tahap, definisikan rencana YAML:

```bash
bernstein run plan.yaml           # skips LLM planning, goes straight to execution
bernstein run --dry-run plan.yaml # preview tasks and estimated cost
```

## Cara kerjanya

1. **Decompose**. Manajer memecah tujuan Anda menjadi tugas-tugas dengan peran, file yang dimiliki, dan sinyal penyelesaian.
2. **Spawn**. Agen mulai di git worktree yang terisolasi, satu per tugas. Cabang main tetap bersih.
3. **Verify**. Janitor memeriksa sinyal konkret: tes lulus, file ada, lint bersih, tipe benar.
4. **Merge**. Pekerjaan yang terverifikasi masuk ke main. Tugas yang gagal di-retry atau diarahkan ke model lain.

Orkestrator adalah scheduler Python, bukan LLM. Keputusan penjadwalan bersifat deterministik, dapat diaudit, dan dapat direproduksi.

## Eksekusi cloud (Cloudflare)

Bernstein dapat menjalankan agen di Cloudflare Workers, bukan secara lokal. CLI `bernstein cloud` menangani deployment dan siklus hidup.

- **Workers**. Eksekusi agen di edge Cloudflare, dengan Durable Workflows untuk tugas multi-langkah dan retry otomatis.
- **Isolasi sandbox V8**. Setiap agen berjalan di isolate-nya sendiri, tanpa overhead container.
- **Sinkronisasi workspace R2**. State worktree lokal disinkronkan ke object storage R2 sehingga agen cloud melihat file yang sama.
- **Workers AI** (eksperimental). Gunakan model yang di-host Cloudflare sebagai provider LLM, tanpa perlu API key eksternal.
- **Analitik D1**. Metrik tugas dan data biaya disimpan di D1 untuk querying.
- **Vectorize**. Cache semantik yang didukung database vektor Cloudflare.
- **Browser rendering**. Headless Chrome di Workers untuk agen yang perlu memeriksa output web.
- **MCP remote transport**. Mengekspos atau mengonsumsi server MCP melalui jaringan Cloudflare.

```bash
bernstein cloud login      # authenticate with Bernstein Cloud
bernstein cloud deploy     # push agent workers
bernstein cloud run plan.yaml  # execute a plan on Cloudflare
```

## Kapabilitas

**Orkestrasi inti**. Eksekusi paralel, isolasi git worktree, verifikasi janitor, quality gates (lint, tipe, pemindaian PII), code review lintas-model, circuit breaker untuk agen yang berperilaku buruk, monitoring pertumbuhan token dengan auto-intervention.

**Kecerdasan**. Contextual bandit router untuk pemilihan model/effort. Knowledge graph untuk analisis dampak codebase. Caching semantik menghemat token pada pola berulang. Deteksi anomali biaya (peringatan burn-rate). Deteksi anomali perilaku dengan flagging Z-score.

**Sandboxing**. Protokol [`SandboxBackend`](../../docs/architecture/sandbox.md) yang pluggable — jalankan agen di git worktree lokal (default), Docker container, microVM Firecracker [E2B](https://e2b.dev), atau container serverless [Modal](https://modal.com) (dengan GPU opsional). Penulis plugin dapat mendaftarkan backend kustom melalui entry-point group `bernstein.sandbox_backends`. Inspeksi backend yang terpasang dengan `bernstein agents sandbox-backends`.

**Penyimpanan artefak**. State `.sdd/` dapat dialirkan ke backend [`ArtifactSink`](../../docs/architecture/storage.md) yang pluggable: filesystem lokal (default), S3, Google Cloud Storage, Azure Blob, atau Cloudflare R2. `BufferedSink` menjaga kontrak crash-safety WAL dengan menulis secara lokal dengan fsync terlebih dahulu lalu mencerminkannya ke remote secara asinkron.

**Skill packs**. [Skill](../../docs/architecture/skills.md) dengan progressive-disclosure (pola OpenAI Agents SDK): hanya indeks skill yang ringkas yang disertakan dalam system prompt setiap spawn, agen menarik isi lengkap melalui tool MCP `load_skill` saat dibutuhkan. 17 role pack bawaan ditambah entry-point `bernstein.skill_sources` pihak ketiga.

**Kontrol**. Audit log berantai HMAC, policy engine, gating output PII, pemulihan crash didukung WAL (keamanan multi-worker eksperimental), OAuth 2.0 PKCE.

**Observabilitas**. Prometheus `/metrics`, preset eksporter OTel, dashboard Grafana. Pelacakan biaya per-model (`bernstein cost`). TUI terminal dan dashboard web. Visibilitas proses agen di `ps`.

**Ekosistem**. Mode server MCP, dukungan protokol A2A, integrasi GitHub App, sistem plugin berbasis pluggy, workspace multi-repo, mode cluster untuk eksekusi terdistribusi, evolusi diri melalui `--evolve` (eksperimental).

Matriks fitur lengkap: [FEATURE_MATRIX.md](../../docs/reference/FEATURE_MATRIX.md) &middot; Fitur terbaru: [What's New](../../docs/whats-new.md)

## Yang baru di v1.9

**ACP bridge** — `bernstein acp serve --stdio` mengekspos Bernstein ke editor mana pun yang berbicara Agent Communication Protocol (Zed, dll.). Tidak perlu kode plugin di sisi editor.

**Perbaikan CI otonom** — `bernstein autofix` memantau PR Bernstein yang terbuka dan, ketika CI menjadi merah, secara otomatis memunculkan agen perbaik. Setelah hijau, ia mendorong perbaikan dan meminta review ulang.

**Credential vault** — `bernstein connect <provider>` menulis API key ke keychain OS; `bernstein creds` mendaftar dan merotasinya. Agen mewarisi kredensial ber-cakupan tanpa menyentuh environment variable.

**Preview tunnels** — `bernstein preview start` menjalankan dev server tersandbox dan mencetak URL publik. Berguna untuk membagikan cabang yang sedang berjalan ke reviewer tanpa men-deploy ke staging.

Changelog lengkap: [docs/whats-new.md](../../docs/whats-new.md)

## Perintah operator

Perintah-perintah yang menghilangkan kode "lem" yang biasanya ditulis kebanyakan tim di sekitar run mereka.

| Command | What it does |
|---------|--------------|
| `bernstein pr` | Membuat PR GitHub secara otomatis dari sesi yang selesai; body memuat hasil gate dari janitor dan rincian biaya token/USD. |
| `bernstein from-ticket <url>` | Mengimpor tiket Linear / GitHub Issues / Jira sebagai tugas Bernstein. Inferensi peran + cakupan berbasis label. Mendukung `--dry-run` dan `--run`. |
| `bernstein ticket import <url>` | Bentuk alias / group dari `from-ticket` untuk scripting. |
| `bernstein remote` | Backend sandbox SSH. `remote test <host>`, `remote run <host> <path>`, `remote forget <host>`. Penggunaan ulang socket ControlMaster untuk panggilan berulang yang cepat. |
| `bernstein hooks` | Hook siklus hidup untuk `pre_task`, `post_task`, `pre_merge`, `post_merge`, `pre_spawn`, `post_spawn` — skrip shell atau pluggy `@hookimpl`. `hooks list`, `hooks run <event>`, `hooks check`. |
| `bernstein chat serve --platform=telegram\|discord\|slack` | Jalankan run dari chat dengan `/run`, `/status`, `/approve`, `/reject`, `/switch`, `/stop`. |
| `bernstein approve-tool` / `bernstein reject-tool` | Persetujuan tool-call interaktif di tengah run. `--latest`, `--id`, `--always`. |
| `bernstein tunnel start <port> [--provider auto\|cloudflared\|ngrok\|bore\|tailscale]` | Satu wrapper untuk empat provider tunnel. Juga `tunnel list`, `tunnel stop <name>\|--all`. Penggunaan ulang proses bergaya ControlMaster. |
| `bernstein daemon install [--user\|--system] [--command="..."] [--env KEY=VAL]...` | Memasang unit systemd (Linux) atau launchd (macOS) untuk auto-start. Juga `daemon start/stop/restart/status/uninstall`. |
| `bernstein connect <provider>` / `bernstein creds` | Menyimpan dan merotasi kredensial API di keychain OS. Agen mewarisi key ber-cakupan per-run. |
| `bernstein autofix` | Daemon yang memantau PR Bernstein yang terbuka; memunculkan agen perbaik saat CI gagal dan secara otomatis mendorong perbaikan. |
| `bernstein preview start` | Memulai dev server tersandbox untuk cabang saat ini dan mencetak URL tunnel publik yang dapat dibagikan. |

## Bagaimana perbandingannya

| Feature | Bernstein | CrewAI | AutoGen [^autogen] | LangGraph |
|---------|-----------|--------|---------|-----------|
| Orkestrator | Kode deterministik | Digerakkan LLM (+ Flows kode) | Digerakkan LLM | Graph + LLM |
| Bekerja dengan | CLI agent apa pun (43 adapter) | Kelas Python SDK | Agen Python | Node LangChain |
| Isolasi git | Worktree per agen | Tidak | Tidak | Tidak |
| Sandbox pluggable | Worktree, Docker, E2B, Modal | Tidak | Tidak | Tidak |
| Verifikasi | Janitor + quality gates | Guardrails + output Pydantic | Kondisi terminasi | Edge bersyarat |
| Pelacakan biaya | Bawaan | `usage_metrics` | `RequestUsage` | Via LangSmith |
| Model state | Berbasis file (.sdd/) | In-memory + checkpoint SQLite | In-memory | Checkpointer |
| Sink artefak remote | S3, GCS, Azure Blob, R2 | Tidak | Tidak | Tidak |
| Self-evolution | Bawaan (eksperimental) | Tidak | Tidak | Tidak |
| Rencana deklaratif (YAML) | Ya | Ya (`agents.yaml`, `tasks.yaml`) | Tidak | Sebagian (`langgraph.json`) |
| Routing model per tugas | Ya | LLM per-agen | `model_client` per-agen | Per-node (manual) |
| Dukungan MCP | Ya (client + server) | Ya | Ya (client + workbench) | Ya (client + server) |
| Chat antar-agen | Bulletin board | Ya (proses Crew) | Ya (group chat) | Ya (supervisor, swarm) |
| UI Web | TUI + dashboard web | CrewAI AMP | AutoGen Studio | LangGraph Studio + LangSmith |
| Opsi cloud hosted | Ya (Cloudflare) | Ya (CrewAI AMP) | Tidak | Ya (LangGraph Cloud) |
| RAG/retrieval bawaan | Ya (codebase FTS5 + BM25) | `crewai_tools` | Retriever `autogen_ext` | Via LangChain |

*Terakhir diverifikasi: 2026-04-19. Lihat [halaman perbandingan lengkap](../../docs/compare/README.md) untuk matriks fitur yang lebih rinci.*

Tabel di atas membandingkan Bernstein dengan framework orkestrasi LLM (mereka mengorkestrasi panggilan LLM). Tabel di bawah ini mencakup kategori yang lebih dekat — alat lain yang mengorkestrasi **CLI coding agent**:

| Feature | Bernstein | [awslabs/cli-agent-orchestrator](https://github.com/awslabs/cli-agent-orchestrator) | [ComposioHQ/agent-orchestrator](https://github.com/ComposioHQ/agent-orchestrator) | [emdash](https://github.com/generalaction/emdash) | [umputun/ralphex](https://github.com/umputun/ralphex) |
|---------|-----------|-----------|-----------|-----------|-----------|
| Bentuk | CLI Python + library + server MCP | CLI Python + sesi tmux + UI web | CLI TypeScript + dashboard lokal | Aplikasi desktop Electron | CLI Go |
| Bahasa utama | Python | Python | TypeScript | TypeScript | Go |
| Pemasangan | `pipx install bernstein` | `uv tool install cli-agent-orchestrator` | `npm install -g @aoagents/ao` | `.dmg` / `.msi` / `.AppImage` | `go install` / single binary |
| Adapter agen | 43 | 5 (Kiro, Claude Code, Codex, Gemini, Kimi) | 3 (Claude Code, Codex, Aider) | 24 | 1 (Claude Code saja) |
| Eksekusi multi-agen paralel | Ya | Ya (sesi tmux per agen) | Ya | Ya | Tidak (sesi sekuensial tunggal) |
| Git worktree per agen | Ya | Tidak (direncanakan, [#100](https://github.com/awslabs/cli-agent-orchestrator/issues/100)) | Ya | Ya | Flag `--worktree` opsional |
| Mode server MCP (mengekspos diri sebagai MCP) | Ya (stdio + HTTP/SSE) | Ya (komunikasi antar-agen) | Tidak | Tidak | Tidak |
| Koordinator | Scheduler Python deterministik | Supervisor LLM hierarkis | Digerakkan LLM | Tidak terdokumentasi | Eksekutor rencana linier |
| Replay audit berantai HMAC | Ya | Tidak | Tidak | Tidak | Tidak |
| Verifier lintas-model / quality gates | Ya (multi-tahap) | Tidak | Tidak | Tidak | Review multi-fase (Claude saja) |
| Alur perbaikan CI / PR otonom | Ya (`bernstein autofix`) | Tidak | Ya | Tidak | Tidak |
| Dashboard visual | TUI + web | UI web + tmux | Web | Aplikasi desktop | Web (`--serve`) |
| Sink notifikasi | Telegram/Slack/Discord/Email/Webhook/Shell | — | Tidak | Tidak | Telegram / Email / Slack / Webhook |
| Dukungan | OSS solo | AWS Labs | Berdana (Composio.dev) | YC W26 | OSS solo |
| Lisensi | Apache 2.0 | Apache 2.0 | MIT | Apache 2.0 | MIT |

Keunggulan Bernstein dalam kategori ini: **native Python, MCP-server-first, cakupan adapter terluas, paralelisme multi-agen sejati, scheduler deterministik tanpa LLM dalam loop koordinasi**. Jika Anda menginginkan isolasi sesi tmux yang selaras AWS dengan supervisor LLM hierarkis, `cao` dari AWS Labs lebih cocok; jika stack Anda TypeScript dan Anda menginginkan produk dengan dashboard, `@aoagents/ao` dari Composio lebih cocok; jika Anda menginginkan ADE desktop yang halus, emdash adalah pilihannya; jika Anda hanya menggunakan Claude Code dan menginginkan satu binary Go yang menjalankan rencana dari atas ke bawah, ralphex adalah jawabannya. Jika Anda menginginkan primitif yang dapat diimpor ke Python, mengekspos dirinya melalui MCP ke client mana pun, menjalankan banyak agen secara paralel, dan mencakup keluasan agen secara penuh (termasuk Qwen, Goose, Ollama, OpenAI Agents SDK, Cloudflare Agents, dan lainnya) — Bernstein.

[^autogen]: AutoGen berada dalam mode pemeliharaan; penerusnya adalah Microsoft Agent Framework 1.0.

## Pemantauan

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

## Install

| Method | Command |
|--------|---------|
| **One-liner (macOS / Linux)** | `curl -fsSL https://bernstein.run/install.sh \| sh` |
| **One-liner (Windows)** | `irm https://bernstein.run/install.ps1 \| iex` |
| **pip** | `pip install bernstein` |
| **pipx** | `pipx install bernstein` |
| **uv** | `uv tool install bernstein` |
| **Homebrew** | `brew tap chernistry/tap && brew install bernstein` |
| **Fedora / RHEL** | `sudo dnf copr enable alexchernysh/bernstein && sudo dnf install bernstein` |
| **npm** (wrapper) | `npx bernstein-orchestrator` |

Skrip one-liner memeriksa Python 3.12+, mem-bootstrap pipx jika belum ada, memperbaiki PATH untuk sesi saat ini, dan memasang (atau meng-upgrade) `bernstein`. Skrip ini menangani lingkungan macOS yang dikelola brew dan fallback launcher Windows `py -3`. Sumber skrip: [install.sh](../../scripts/install.sh) · [install.ps1](../../scripts/install.ps1).

### Ekstra opsional

SDK provider bersifat opsional agar pemasangan dasar tetap ringan. Pilih yang Anda butuhkan:

| Extra | Enables |
|-------|---------|
| `bernstein[openai]` | Adapter OpenAI Agents SDK v2 (`openai_agents`) |
| `bernstein[docker]` | Backend sandbox Docker |
| `bernstein[e2b]` | Backend sandbox microVM [E2B](https://e2b.dev) (membutuhkan `E2B_API_KEY`) |
| `bernstein[modal]` | Backend sandbox [Modal](https://modal.com), GPU opsional (membutuhkan `MODAL_TOKEN_ID` / `MODAL_TOKEN_SECRET`) |
| `bernstein[s3]` | Sink artefak S3 (via `boto3`) |
| `bernstein[gcs]` | Sink artefak Google Cloud Storage |
| `bernstein[azure]` | Sink artefak Azure Blob |
| `bernstein[r2]` | Sink artefak Cloudflare R2 (kompatibel S3, `boto3`) |
| `bernstein[grpc]` | Bridge gRPC |
| `bernstein[k8s]` | Integrasi Kubernetes |

Gabungkan extra dengan tanda kurung, mis. `pip install 'bernstein[openai,docker,s3]'`.

Ekstensi editor: [VS Marketplace](https://marketplace.visualstudio.com/items?itemName=alex-chernysh.bernstein) &middot; [Open VSX](https://open-vsx.org/extension/alex-chernysh/bernstein)

## Berkontribusi

PR dipersilakan. Lihat [CONTRIBUTING.md](../../CONTRIBUTING.md) untuk setup dan gaya kode.

## Dukungan

Jika Bernstein menghemat waktu Anda: [GitHub Sponsors](https://github.com/sponsors/chernistry)

Kontak: [forte@bernstein.run](mailto:forte@bernstein.run)

## Tampil di

Daftar terkurasi, newsletter, dan proyek sejawat yang menyorot Bernstein:

- [**Python Weekly #742**](https://www.pythonweekly.com/p/python-weekly-issue-742-april-23-2026) (23 April 2026) — penyebutan dalam newsletter.
- [**Augment Code — 9 Open-Source Agent Orchestrators for AI Coding (2026)**](https://www.augmentcode.com/tools/open-source-agent-orchestrators) — rangkuman editorial; "alat yang paling menarik secara arsitektural dalam rangkuman ini."
- [**nibzard/awesome-agentic-patterns**](https://github.com/nibzard/awesome-agentic-patterns/blob/main/patterns/deterministic-zero-llm-orchestration.md) — Bernstein dikutip sebagai implementasi produksi dari pola "deterministic zero-LLM orchestration".
- [**Jenqyang/Awesome-AI-Agents**](https://github.com/Jenqyang/Awesome-AI-Agents)
- [**jamesmurdza/awesome-ai-devtools**](https://github.com/jamesmurdza/awesome-ai-devtools)
- [**jim-schwoebel/awesome_ai_agents**](https://github.com/jim-schwoebel/awesome_ai_agents)
- [**Piebald-AI/awesome-gemini-cli**](https://github.com/Piebald-AI/awesome-gemini-cli)
- [**ComposioHQ/awesome-codex-skills**](https://github.com/ComposioHQ/awesome-codex-skills)
- [**jxzhangjhu/Awesome-LLM-RAG**](https://github.com/jxzhangjhu/Awesome-LLM-RAG)
- [**rohitg00/awesome-claude-code-toolkit**](https://github.com/rohitg00/awesome-claude-code-toolkit)
- [**numtide/llm-agents.nix**](https://github.com/numtide/llm-agents.nix) — distribusi Nix flake.

<details>
<summary>Lebih banyak awesome list & kurasi komunitas</summary>

- [andyrewlee/awesome-agent-orchestrators](https://github.com/andyrewlee/awesome-agent-orchestrators)
- [bradAGI/awesome-cli-coding-agents](https://github.com/bradAGI/awesome-cli-coding-agents)
- [milisp/awesome-codex-cli](https://github.com/milisp/awesome-codex-cli)
- [yaolifeng0629/Awesome-independent-tools](https://github.com/yaolifeng0629/Awesome-independent-tools) (中文 + EN)
- [caramaschiHG/awesome-ai-agents-2026](https://github.com/caramaschiHG/awesome-ai-agents-2026)
- [ai-for-developers/awesome-vibe-coding](https://github.com/ai-for-developers/awesome-vibe-coding)
- [killop/anything_about_game](https://github.com/killop/anything_about_game) (`AI.md`)
- [Glama MCP Catalog](https://glama.ai/mcp/servers/sipyourdrink-ltd/bernstein) — daftar server MCP editorial.
- Mirror: [icopy-site/awesome](https://github.com/icopy-site/awesome), [icopy-site/awesome-cn](https://github.com/icopy-site/awesome-cn), [trackawesomelist/trackawesomelist](https://github.com/trackawesomelist/trackawesomelist).

</details>

<details>
<summary>Dikutip sebagai prior art oleh proyek sejawat</summary>

- [**mkb23/overcode**](https://github.com/mkb23/overcode/blob/main/docs/design/bakeoffs/overcode-vs-bernstein.md) — bakeoff bentuk panjang yang memperlakukan Bernstein sebagai implementasi referensi.
- [**Vintersong/NOVA-Cognition-Framework**](https://github.com/Vintersong/NOVA-Cognition-Framework) — `BERNSTEIN_PATTERNS.md`, "Patterns Worth Borrowing".
- [**AJV009/drupal-contrib-workbench**](https://github.com/AJV009/drupal-contrib-workbench) — catatan riset tentang pemisahan manajer/janitor.
- [**danielvaughan/codex-blog**](https://github.com/danielvaughan/codex-blog/blob/main/_posts/2026-04-09-loki-mode-autonomous-execution.md) — artikel perbandingan yang menempatkan Bernstein di sisi deterministik.

</details>

## Star History

[![Star History Chart](https://api.star-history.com/svg?repos=sipyourdrink-ltd/bernstein&type=Date)](https://star-history.com/#sipyourdrink-ltd/bernstein&Date)

## Lisensi

[Apache License 2.0](../../LICENSE)

---

Dibuat dengan cinta oleh [Alex Chernysh](https://alexchernysh.com) &middot; [GitHub](https://github.com/chernistry) &middot; [X](https://x.com/alex_chernysh) &middot; [bernstein.run](https://bernstein.run?utm_source=github.com&utm_medium=readme&utm_campaign=bernstein-readme)

<!-- mcp-name: io.github.sipyourdrink-ltd/bernstein -->
