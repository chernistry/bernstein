<div align="center">

[English](../../README.md) | [Español (Spanish)](README.es.md) | [中文 (Chinese)](README.zh.md) | [العربية (Arabic)](README.ar.md) | [Português (Portuguese)](README.pt.md) | [Bahasa Indonesia (Indonesian)](README.id.md) | [Français (French)](README.fr.md) | **日本語 (Japanese)** | [Русский (Russian)](README.ru.md) | [Deutsch (German)](README.de.md) | [עברית (Hebrew)](README.he.md) | [יידיש (Yiddish)](README.yi.md)

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="../../docs/assets/logo-dark.svg">
  <source media="(prefers-color-scheme: light)" srcset="../../docs/assets/logo-light.svg">
  <img alt="Bernstein" src="../../docs/assets/logo-light.svg" width="340">
</picture>

<br>

> *「偉大なことを成し遂げるには、二つのものが必要だ。計画と、少しだけ足りない時間である。」* — Leonard Bernstein

### あらゆる AI コーディングエージェントを統制する。あらゆるモデルを。たった一つのコマンドで。

[![CI](https://github.com/sipyourdrink-ltd/bernstein/actions/workflows/ci.yml/badge.svg)](https://github.com/sipyourdrink-ltd/bernstein/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/bernstein)](https://pypi.org/project/bernstein/)
[![Python 3.12+](https://img.shields.io/badge/python-3.12+-3776ab?logo=python&logoColor=white)](https://python.org)
[![License](https://img.shields.io/github/license/sipyourdrink-ltd/bernstein)](../../LICENSE)
[![MseeP.ai](https://img.shields.io/badge/MseeP.ai-verified-2496ed)](https://mseep.ai/app/chernistry-bernstein)

[Website](https://bernstein.run?utm_source=github.com&utm_medium=readme&utm_campaign=bernstein-readme) &middot; [ドキュメント](https://bernstein.readthedocs.io/) &middot; [はじめに](../../docs/getting-started/GETTING_STARTED.md) &middot; [用語集](../../docs/reference/GLOSSARY.md) &middot; [既知の制限](../../docs/reference/KNOWN_LIMITATIONS.md)

</div>

---

Bernstein は決定的な Python スケジューラーです。CLI コーディングエージェント (Claude Code、Codex、Gemini CLI、ほか 40 種類) のチームを並列の git worktree で 1 つのゴールに対して走らせ、すべての操作に HMAC 署名された監査チェーンを付与します。

### 30 秒でインストール

```bash
pipx install bernstein
bernstein init
bernstein run -g "fix the failing test in tests/test_foo.py"
```

### 60 秒で見る

以下のクリップは 1 回の実行を通しで収めています。マネージャーがゴールを分解し、3 つのエージェントが並列で作業し、監査チェーンが各 handoff を記録し、janitor が検証し、PR が開きます。

<p align="center">
  <img alt="Bernstein 60 秒デモ: マネージャーがゴールを分解し、3 つのエージェントが並列実行し、監査チェーンが各 handoff を記録し、PR が開く" src="../../docs/demo/demo.gif" width="800">
</p>

実行後、Bernstein はコスト・テスト結果・lineage・監査ハッシュチェーンを含む構造化コメントを PR に投稿します。

<p align="center">
  <img alt="Bernstein の PR コメント: サマリー、コスト、Lineage、テスト、監査チェーンの各セクション" src="../../docs/demo/screenshot-pr-comment.svg" width="720">
</p>

> GIF は [`docs/demo/demo.tape`](../../docs/demo/demo.tape) から [vhs](https://github.com/charmbracelet/vhs) で生成しています。`vhs docs/demo/demo.tape` でローカル再生成できます。

### 比較

| 機能                                       | Bernstein   | Archon   | LangGraph |
|--------------------------------------------|-------------|----------|-----------|
| マルチエージェント (並列アダプタ)          | あり        | 1 つ     | あり      |
| 署名付き lineage / 監査チェーン            | あり        | なし     | なし      |
| エアギャップ / 主権デプロイ                | あり        | 一部     | なし      |
| ビジュアルワークフロー YAML                | あり [^yaml]| あり     | なし      |
| ホスト型ダッシュボード / SaaS              | なし        | 一部     | なし      |

[^yaml]: ワークフロー YAML サポートは [PR #1108](https://github.com/sipyourdrink-ltd/bernstein/pull/1108) (この回) で着地します。それまでは Python で書くか、`bernstein run plan.yaml` で旧スキーマに対して動かしてください。

CrewAI、AutoGen、LangGraph、および Bernstein と同じカテゴリの 4 つの CLI エージェントオーケストレーターに対するさらに詳細な機能マトリクスは、下の [詳細比較](#detailed-comparison) セクションにあります。

---

### これは何ですか、一段落で

作りたいものを Bernstein に伝えると、複数の AI コーディングエージェントに作業を分担させ、隔離された git worktree で並列に走らせ、各 handoff を HMAC でチェーンされた監査ログに記録し、テストを実行し、実際に通ったコードをマージします。戻ってきたときには、グリーンの PR が手元にあります。

Forward-deployed engineering を、スウォーム形式で。クライアントのリポジトリに Bernstein を投入すれば、ファイルベースの状態、エージェントごとの認証スコープ、署名付き audit trail を備えたマルチエージェントチームを、クライアントが既に信頼している CLI エージェント上で動かせます。

### その他のインストール方法

```bash
curl -fsSL https://bernstein.run/install.sh | sh        # macOS / Linux のワンライナー
irm https://bernstein.run/install.ps1 | iex             # Windows PowerShell
pip install bernstein                                   # pip
uv tool install bernstein                               # uv
brew tap chernistry/tap && brew install bernstein       # Homebrew
```

`dnf copr`、`npx`、オプションの extras、エアギャップサイト向けの wheelhouse パスは [インストールマトリクス](#install) を参照してください。

### スケジューラーが素の Python である理由

ほとんどのエージェントオーケストレーターは、誰が何をやるかを LLM に判断させます。これは非決定的で、コードではなくスケジューリングのためにトークンを消費します。Bernstein はゴールを分解するために LLM を一度だけ呼び出し、それ以降 (エージェントの並列実行、Git ブランチの隔離、テスト実行、リトライのルーティング) はすべて素の Python で行います。すべての実行は再現可能です。すべてのステップはログに記録され、再生可能です。

学ぶべきフレームワークはありません。ベンダーロックインもありません。エージェントもモデルもプロバイダも、自由に差し替えられます。

<img alt="Bernstein 動作中: 並列 AI エージェントをリアルタイムにオーケストレーション" src="../../docs/assets/in-action-small.gif" width="700">

実行中に表示される様子:

```
$ bernstein -g "Add JWT auth"
[manager] decomposed into 4 tasks
[agent-1] claude-sonnet: src/auth/middleware.py  (done, 2m 14s)
[agent-2] codex:         tests/test_auth.py      (done, 1m 58s)
[verify]  all gates pass. merging to main.
```

## 対応エージェント

Bernstein はインストール済みの CLI エージェントを自動で検出します。同じ実行内で混在させることもできます。定型処理には安価なローカルモデル、アーキテクチャ設計には強力なクラウドモデルといった使い分けが可能です。

43 個の CLI エージェントアダプター: 40 個のサードパーティラッパーに加え、`--prompt` を受け付けるあらゆるツール用の汎用ラッパーが付属しています。

| Agent | Models | Install |
|-------|--------|---------|
| [Claude Code](https://docs.anthropic.com/en/docs/claude-code) | Opus 4, Sonnet 4.6, Haiku 4.5 | `npm install -g @anthropic-ai/claude-code` |
| [Codex CLI](https://github.com/openai/codex) | GPT-5, GPT-5 mini | `npm install -g @openai/codex` |
| [OpenAI Agents SDK v2](https://openai.github.io/openai-agents-python/) | GPT-5, GPT-5 mini, o4 | `pip install 'bernstein[openai]'` |
| [GitHub Copilot CLI](https://docs.github.com/en/copilot/github-copilot-in-the-cli) | Copilot-managed (GPT-5, Sonnet 4.6) | `npm install -g @github/copilot` |
| [Gemini CLI](https://github.com/google-gemini/gemini-cli) | Gemini 2.5 Pro, Gemini Flash | `npm install -g @google/gemini-cli` |
| [Cursor](https://www.cursor.com) | Sonnet 4.6, Opus 4, GPT-5 | [Cursor app](https://www.cursor.com) |
| [Aider](https://aider.chat) | OpenAI/Anthropic 互換のあらゆるモデル | `pip install aider-chat` |
| [Amp](https://ampcode.com) | Amp が管理 | `npm install -g @sourcegraph/amp` |
| [Cody](https://sourcegraph.com/cody) | Sourcegraph がホスト | `npm install -g @sourcegraph/cody` |
| [Continue](https://continue.dev) | OpenAI/Anthropic 互換のあらゆるモデル | `npm install -g @continuedev/cli` (バイナリ名: `cn`) |
| [Goose](https://block.github.io/goose/) | Goose が対応する任意のプロバイダ | [Goose docs](https://block.github.io/goose/) を参照 |
| [IaC](https://www.terraform.io/) (Terraform/Pulumi) | ベースエージェントが利用する任意のプロバイダ | 組み込み |
| [Kilo](https://kilo.dev) | Kilo がホスト | [Kilo docs](https://kilo.dev) を参照 |
| [Kiro](https://kiro.dev) | Kiro がホスト | [Kiro docs](https://kiro.dev) を参照 |
| [Ollama](https://ollama.ai) + Aider | ローカルモデル (オフライン) | `brew install ollama` |
| [OpenCode](https://opencode.ai) | OpenCode が対応する任意のプロバイダ | [OpenCode docs](https://opencode.ai) を参照 |
| [Qwen](https://github.com/QwenLM/qwen-code) | Qwen Code モデル | `npm install -g @qwen-code/qwen-code` |
| [Cloudflare Agents](https://developers.cloudflare.com/agents/) | Workers AI モデル | `bernstein cloud login` |
| [OpenHands](https://github.com/OpenHands/OpenHands) | LiteLLM 対応の任意のプロバイダ (Anthropic、OpenAI、…) | `uv tool install openhands --python 3.12` |
| [Open Interpreter](https://github.com/OpenInterpreter/open-interpreter) | 任意 (LiteLLM ベース) | `pip install open-interpreter` |
| [gptme](https://github.com/gptme/gptme) | Anthropic、OpenAI、OpenRouter | `pipx install gptme` |
| [Plandex](https://github.com/plandex-ai/plandex) | Plandex Cloud またはセルフホストモデル | `curl -sL https://plandex.ai/install.sh \| bash` |
| [AIChat](https://github.com/sigoden/aichat) | OpenAI、Anthropic、OpenRouter、Groq、Gemini | `cargo install aichat` |
| [Letta Code](https://github.com/letta-ai/letta-code) | Letta 経由 (Anthropic、OpenAI) | `npm install -g @letta-ai/letta-code` |
| **Generic** | `--prompt` を持つ任意の CLI | 組み込み |

#### オーケストレーター委任 (リーフノード)

別カテゴリに属する、より小さなアダプター群で、**他の CLI オーケストレーター** を単一エージェントのようにラップします。Bernstein はラップ対象のツールにプロンプトやプランを渡し、最終的な終了コードのみを受け取ります。ラップされたオーケストレーター内部のサブエージェントのコストや品質ゲートは Bernstein からは見えません。これらのツールで構築済みの既存ワークフローを、より大きな Bernstein プランの 1 ステップとして組み込みたいときに便利です。

| Orchestrator | Wrapped as | Install |
|--------------|------------|---------|
| [Composio Agent Orchestrator](https://github.com/ComposioHQ/agent-orchestrator) (`@aoagents/ao`) | `composio` | `npm install -g @aoagents/ao` |
| [umputun/ralphex](https://github.com/umputun/ralphex) | `ralphex` | `go install github.com/umputun/ralphex/cmd/ralphex@latest` |

どのアダプターも **内部スケジューラ LLM** として動作させられます。特定のプロバイダに依存せずスタック全体を実行できます:

```yaml
internal_llm_provider: gemini            # or qwen, ollama, codex, goose, ...
internal_llm_model: gemini-3.1-pro
```

> [!TIP]
> CI パイプラインでは `bernstein --headless` を使ってください。TUI なし、構造化された JSON 出力、失敗時は非ゼロ終了コードを返します。

## クイックスタート

```bash
cd your-project
bernstein init                    # creates .sdd/ workspace + bernstein.yaml
bernstein -g "Add rate limiting"  # agents spawn, work in parallel, verify, exit
bernstein live                    # watch progress in the TUI dashboard
bernstein stop                    # graceful shutdown with drain
```

複数ステージのプロジェクトでは、YAML プランを定義します:

```bash
bernstein run plan.yaml           # skips LLM planning, goes straight to execution
bernstein run --dry-run plan.yaml # preview tasks and estimated cost
```

## 仕組み

1. **分解**。マネージャがゴールを役割・所有ファイル・完了シグナル付きのタスクへ分解します。
2. **起動**。エージェントは隔離された Git worktree でタスクごとに起動します。main ブランチはクリーンに保たれます。
3. **検証**。ジャニターが具体的なシグナル — テスト合格、ファイル存在、Lint クリーン、型整合 — を確認します。
4. **マージ**。検証済みの成果物は main に取り込まれます。失敗したタスクは再試行されるか、別のモデルへ振り直されます。

オーケストレーターは LLM ではなく Python のスケジューラです。スケジューリングは決定的で、監査可能で、再現可能です。

## クラウド実行 (Cloudflare)

Bernstein はエージェントをローカルではなく Cloudflare Workers 上で実行できます。デプロイとライフサイクルは `bernstein cloud` CLI が扱います。

- **Workers**。Cloudflare のエッジでエージェントを実行。多段タスク向けの Durable Workflows と自動リトライに対応します。
- **V8 サンドボックス分離**。各エージェントは独自の isolate で動作し、コンテナのオーバーヘッドはありません。
- **R2 ワークスペース同期**。ローカル worktree の状態を R2 オブジェクトストレージへ同期し、クラウドエージェントが同じファイルを参照できます。
- **Workers AI** (実験的)。Cloudflare がホストするモデルを LLM プロバイダとして利用でき、外部 API キーは不要です。
- **D1 アナリティクス**。タスクメトリクスとコストデータを D1 に保存して問い合わせできます。
- **Vectorize**。Cloudflare のベクターデータベースに支えられたセマンティックキャッシュ。
- **ブラウザレンダリング**。Web 出力を確認したいエージェント向けに、Workers 上のヘッドレス Chrome を提供します。
- **MCP リモートトランスポート**。Cloudflare ネットワーク越しに MCP サーバーを公開・利用できます。

```bash
bernstein cloud login      # authenticate with Bernstein Cloud
bernstein cloud deploy     # push agent workers
bernstein cloud run plan.yaml  # execute a plan on Cloudflare
```

## 機能

**コアオーケストレーション**。並列実行、Git worktree 分離、ジャニターによる検証、品質ゲート (Lint、型、PII スキャン)、クロスモデルコードレビュー、挙動が怪しいエージェント向けのサーキットブレーカー、自動介入付きのトークン増加監視。

**インテリジェンス**。モデル/努力レベル選択のための文脈的バンディットルーター。コードベースへの影響分析のための知識グラフ。繰り返しパターンでトークンを節約するセマンティックキャッシュ。コスト異常検知 (バーンレートアラート)。Z スコアによる挙動異常検知。

**サンドボックス**。プラガブルな [`SandboxBackend`](../../docs/architecture/sandbox.md) プロトコル — エージェントをローカル Git worktree (デフォルト)、Docker コンテナ、[E2B](https://e2b.dev) Firecracker microVM、または [Modal](https://modal.com) サーバーレスコンテナ (オプションで GPU) で実行できます。プラグイン作成者は `bernstein.sandbox_backends` エントリーポイントグループ経由で独自バックエンドを登録できます。`bernstein agents sandbox-backends` でインストール済みバックエンドを確認できます。

**アーティファクトストレージ**。`.sdd/` の状態はプラガブルな [`ArtifactSink`](../../docs/architecture/storage.md) バックエンドへ流せます: ローカルファイルシステム (デフォルト)、S3、Google Cloud Storage、Azure Blob、Cloudflare R2。`BufferedSink` はまずローカルへ fsync 付きで書き、その後リモートへ非同期にミラーすることで WAL のクラッシュ安全性契約を維持します。

**スキルパック**。漸進開示型の [スキル](../../docs/architecture/skills.md) (OpenAI Agents SDK パターン): すべての起動時のシステムプロンプトにはコンパクトなスキルインデックスのみが載り、エージェントは必要に応じて `load_skill` MCP ツールで本体を取得します。組み込みのロールパック 17 個に加え、サードパーティの `bernstein.skill_sources` エントリーポイントもあります。

**コントロール**。HMAC でチェーンされた監査ログ、ポリシーエンジン、PII 出力ゲーティング、WAL によるクラッシュ復旧 (実験的なマルチワーカー安全性)、OAuth 2.0 PKCE。

**可観測性**。Prometheus `/metrics`、OTel エクスポーターのプリセット、Grafana ダッシュボード。モデル別のコストトラッキング (`bernstein cost`)。ターミナル TUI と Web ダッシュボード。`ps` 上でのエージェントプロセス可視化。

**エコシステム**。MCP サーバーモード、A2A プロトコル対応、GitHub App 連携、pluggy ベースのプラグインシステム、複数リポジトリのワークスペース、分散実行用のクラスタモード、`--evolve` (実験的) による自己進化。

機能マトリクスの全量: [FEATURE_MATRIX.md](../../docs/reference/FEATURE_MATRIX.md) &middot; 直近の機能: [What's New](../../docs/whats-new.md)

## v1.9 の新機能

**ACP ブリッジ** — `bernstein acp serve --stdio` により、Agent Communication Protocol を話す任意のエディタ (Zed など) から Bernstein を利用できるようになりました。エディタ側にプラグインコードは不要です。

**自律的な CI 修復** — `bernstein autofix` が公開中の Bernstein PR を監視し、CI が赤くなったときに自動でフィクサーエージェントを起動します。緑になり次第、修正をプッシュしてレビューを再依頼します。

**クレデンシャルボールト** — `bernstein connect <provider>` が API キーを OS のキーチェーンへ書き込み、`bernstein creds` で一覧表示・ローテーションできます。エージェントは環境変数を介さずスコープ付きの認証情報を継承します。

**プレビュートンネル** — `bernstein preview start` がサンドボックス化された開発サーバーを起動し、公開 URL を表示します。ステージングへデプロイせずに動作中のブランチをレビュアーへ共有したいときに便利です。

完全な変更履歴: [docs/whats-new.md](../../docs/whats-new.md)

## オペレーターコマンド

ほとんどのチームが実行回りで自前で書いてしまう「のり付けコード」を不要にするコマンド群です。

| Command | What it does |
|---------|--------------|
| `bernstein pr` | 完了したセッションから GitHub PR を自動作成。本文にはジャニターのゲート結果と、トークン/USD のコスト内訳が含まれます。 |
| `bernstein from-ticket <url>` | Linear / GitHub Issues / Jira のチケットを Bernstein タスクとして取り込みます。ラベルベースで役割とスコープを推論。`--dry-run` と `--run` をサポート。 |
| `bernstein ticket import <url>` | スクリプト用途向けの `from-ticket` のエイリアス/グループ形式。 |
| `bernstein remote` | SSH サンドボックスバックエンド。`remote test <host>`、`remote run <host> <path>`、`remote forget <host>`。連続呼び出しを高速化するため ControlMaster ソケットを再利用します。 |
| `bernstein hooks` | `pre_task`、`post_task`、`pre_merge`、`post_merge`、`pre_spawn`、`post_spawn` のライフサイクルフック — シェルスクリプトまたは pluggy の `@hookimpl`。`hooks list`、`hooks run <event>`、`hooks check`。 |
| `bernstein chat serve --platform=telegram\|discord\|slack` | チャットから `/run`、`/status`、`/approve`、`/reject`、`/switch`、`/stop` で実行を操作します。 |
| `bernstein approve-tool` / `bernstein reject-tool` | 実行中のツール呼び出しを対話的に承認。`--latest`、`--id`、`--always`。 |
| `bernstein tunnel start <port> [--provider auto\|cloudflared\|ngrok\|bore\|tailscale]` | 4 種のトンネルプロバイダを 1 つでラップ。`tunnel list`、`tunnel stop <name>\|--all` も。ControlMaster 風のプロセス再利用。 |
| `bernstein daemon install [--user\|--system] [--command="..."] [--env KEY=VAL]...` | 自動起動用に systemd (Linux) / launchd (macOS) ユニットをインストール。`daemon start/stop/restart/status/uninstall` も。 |
| `bernstein connect <provider>` / `bernstein creds` | OS キーチェーンに API クレデンシャルを保存・ローテーション。エージェントは実行ごとにスコープ付きキーを継承します。 |
| `bernstein autofix` | 公開中の Bernstein PR を監視するデーモン。CI 失敗時にフィクサーエージェントを起動して、修正を自動プッシュします。 |
| `bernstein preview start` | 現在のブランチでサンドボックス化された開発サーバーを起動し、共有可能な公開トンネル URL を表示します。 |

## 比較

| Feature | Bernstein | CrewAI | AutoGen [^autogen] | LangGraph |
|---------|-----------|--------|---------|-----------|
| オーケストレーター | 決定的なコード | LLM 主導 (+ コードフロー) | LLM 主導 | グラフ + LLM |
| 連携対象 | あらゆる CLI エージェント (43 アダプター) | Python SDK クラス | Python エージェント | LangChain ノード |
| Git 分離 | エージェントごとの Worktree | 非対応 | 非対応 | 非対応 |
| プラガブルなサンドボックス | Worktree、Docker、E2B、Modal | 非対応 | 非対応 | 非対応 |
| 検証 | ジャニター + 品質ゲート | ガードレール + Pydantic 出力 | 終了条件 | 条件付きエッジ |
| コストトラッキング | 組み込み | `usage_metrics` | `RequestUsage` | LangSmith 経由 |
| 状態モデル | ファイルベース (.sdd/) | インメモリ + SQLite チェックポイント | インメモリ | Checkpointer |
| リモートアーティファクトシンク | S3、GCS、Azure Blob、R2 | 非対応 | 非対応 | 非対応 |
| 自己進化 | 組み込み (実験的) | 非対応 | 非対応 | 非対応 |
| 宣言的プラン (YAML) | 対応 | 対応 (`agents.yaml`、`tasks.yaml`) | 非対応 | 部分対応 (`langgraph.json`) |
| タスク単位のモデルルーティング | 対応 | エージェント単位の LLM | エージェント単位の `model_client` | ノード単位 (手動) |
| MCP サポート | 対応 (クライアント + サーバー) | 対応 | 対応 (クライアント + workbench) | 対応 (クライアント + サーバー) |
| エージェント間チャット | 掲示板方式 | 対応 (Crew プロセス) | 対応 (グループチャット) | 対応 (supervisor、swarm) |
| Web UI | TUI + Web ダッシュボード | CrewAI AMP | AutoGen Studio | LangGraph Studio + LangSmith |
| クラウドホスト版 | あり (Cloudflare) | あり (CrewAI AMP) | なし | あり (LangGraph Cloud) |
| 組み込み RAG/検索 | 対応 (コードベース FTS5 + BM25) | `crewai_tools` | `autogen_ext` retriever | LangChain 経由 |

*最終確認: 2026-04-19。詳細な機能マトリクスは [比較ページ全文](../../docs/compare/README.md) を参照。*

上記の表は、LLM オーケストレーションフレームワーク (LLM 呼び出しをオーケストレーションするもの) と Bernstein を比較しています。下記の表はより近いカテゴリ — **CLI コーディングエージェント** をオーケストレーションする他ツールとの比較です:

| Feature | Bernstein | [awslabs/cli-agent-orchestrator](https://github.com/awslabs/cli-agent-orchestrator) | [ComposioHQ/agent-orchestrator](https://github.com/ComposioHQ/agent-orchestrator) | [emdash](https://github.com/generalaction/emdash) | [umputun/ralphex](https://github.com/umputun/ralphex) |
|---------|-----------|-----------|-----------|-----------|-----------|
| 形態 | Python CLI + ライブラリ + MCP サーバー | Python CLI + tmux セッション + Web UI | TypeScript CLI + ローカルダッシュボード | Electron デスクトップアプリ | Go CLI |
| 主要言語 | Python | Python | TypeScript | TypeScript | Go |
| インストール | `pipx install bernstein` | `uv tool install cli-agent-orchestrator` | `npm install -g @aoagents/ao` | `.dmg` / `.msi` / `.AppImage` | `go install` / 単一バイナリ |
| エージェントアダプター | 43 | 5 (Kiro、Claude Code、Codex、Gemini、Kimi) | 3 (Claude Code、Codex、Aider) | 24 | 1 (Claude Code のみ) |
| 並列マルチエージェント実行 | 対応 | 対応 (エージェントごとの tmux セッション) | 対応 | 対応 | 非対応 (単一の逐次セッション) |
| エージェントごとの Git worktree | 対応 | 非対応 (計画中、[#100](https://github.com/awslabs/cli-agent-orchestrator/issues/100)) | 対応 | 対応 | オプション (`--worktree` フラグ) |
| MCP サーバーモード (自身を MCP として公開) | 対応 (stdio + HTTP/SSE) | 対応 (エージェント間通信) | 非対応 | 非対応 | 非対応 |
| コーディネータ | 決定的な Python スケジューラ | 階層的 LLM スーパーバイザー | LLM 主導 | ドキュメント未記載 | 線形プラン実行 |
| HMAC チェーン監査リプレイ | 対応 | 非対応 | 非対応 | 非対応 | 非対応 |
| クロスモデル検証 / 品質ゲート | 対応 (多段) | 非対応 | 非対応 | 非対応 | 多フェーズレビュー (Claude 限定) |
| 自律 CI 修復 / PR フロー | 対応 (`bernstein autofix`) | 非対応 | 対応 | 非対応 | 非対応 |
| ビジュアルダッシュボード | TUI + Web | Web UI + tmux | Web | デスクトップアプリ | Web (`--serve`) |
| 通知シンク | Telegram/Slack/Discord/メール/Webhook/シェル | — | 非対応 | 非対応 | Telegram / メール / Slack / Webhook |
| バッキング | 個人 OSS | AWS Labs | 出資あり (Composio.dev) | YC W26 | 個人 OSS |
| ライセンス | Apache 2.0 | Apache 2.0 | MIT | Apache 2.0 | MIT |

このカテゴリにおける Bernstein の強み: **Python ネイティブ、MCP サーバーファースト、最も広いアダプターカバレッジ、真のマルチエージェント並列性、コーディネーションループに LLM を持たない決定的スケジューラ**。AWS と整合した tmux セッション分離と階層的 LLM スーパーバイザーが欲しいなら AWS Labs の `cao` が近い選択肢です。スタックが TypeScript でダッシュボード付きの製品が欲しいなら Composio の `@aoagents/ao` が向いています。洗練されたデスクトップ ADE が欲しいなら emdash、Claude Code だけを使いプランを上から下へ歩く単一 Go バイナリが欲しいなら ralphex が適しています。Python に取り込めるプリミティブで、MCP 経由で任意のクライアントへ自身を公開し、多数のエージェントを並列実行し、全エージェント (Qwen、Goose、Ollama、OpenAI Agents SDK、Cloudflare Agents、ほか) を網羅したいなら — Bernstein です。

[^autogen]: AutoGen はメンテナンスモードで、後継は Microsoft Agent Framework 1.0 です。

## モニタリング

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

## インストール

| Method | Command |
|--------|---------|
| **ワンライナー (macOS / Linux)** | `curl -fsSL https://bernstein.run/install.sh \| sh` |
| **ワンライナー (Windows)** | `irm https://bernstein.run/install.ps1 \| iex` |
| **pip** | `pip install bernstein` |
| **pipx** | `pipx install bernstein` |
| **uv** | `uv tool install bernstein` |
| **Homebrew** | `brew tap chernistry/tap && brew install bernstein` |
| **Fedora / RHEL** | `sudo dnf copr enable alexchernysh/bernstein && sudo dnf install bernstein` |
| **npm** (ラッパー) | `npx bernstein-orchestrator` |

ワンライナースクリプトは Python 3.12+ を確認し、pipx が無ければブートストラップし、現在のセッションの PATH を整え、`bernstein` をインストール (またはアップグレード) します。brew 管理の macOS 環境や Windows の `py -3` ランチャーへのフォールバックも処理します。スクリプトのソース: [install.sh](../../scripts/install.sh) · [install.ps1](../../scripts/install.ps1)。

### オプションのエクストラ

ベースインストールを軽量に保つため、プロバイダの SDK はオプションです。必要なものだけ選んでください:

| Extra | Enables |
|-------|---------|
| `bernstein[openai]` | OpenAI Agents SDK v2 アダプター (`openai_agents`) |
| `bernstein[docker]` | Docker サンドボックスバックエンド |
| `bernstein[e2b]` | [E2B](https://e2b.dev) microVM サンドボックスバックエンド (`E2B_API_KEY` が必要) |
| `bernstein[modal]` | [Modal](https://modal.com) サンドボックスバックエンド、オプションで GPU (`MODAL_TOKEN_ID` / `MODAL_TOKEN_SECRET` が必要) |
| `bernstein[s3]` | S3 アーティファクトシンク (`boto3` 経由) |
| `bernstein[gcs]` | Google Cloud Storage アーティファクトシンク |
| `bernstein[azure]` | Azure Blob アーティファクトシンク |
| `bernstein[r2]` | Cloudflare R2 アーティファクトシンク (S3 互換 `boto3`) |
| `bernstein[grpc]` | gRPC ブリッジ |
| `bernstein[k8s]` | Kubernetes 連携 |

エクストラはブラケットで結合できます。例: `pip install 'bernstein[openai,docker,s3]'`。

エディタ拡張: [VS Marketplace](https://marketplace.visualstudio.com/items?itemName=alex-chernysh.bernstein) &middot; [Open VSX](https://open-vsx.org/extension/alex-chernysh/bernstein)

## コントリビューション

PR を歓迎します。セットアップとコードスタイルは [CONTRIBUTING.md](../../CONTRIBUTING.md) を参照してください。

## サポート

Bernstein で時間が節約できたら: [GitHub Sponsors](https://github.com/sponsors/chernistry)

連絡先: [forte@bernstein.run](mailto:forte@bernstein.run)

## 掲載・参照

Bernstein を取り上げたキュレーションリスト、ニュースレター、関連プロジェクト:

- [**Python Weekly #742**](https://www.pythonweekly.com/p/python-weekly-issue-742-april-23-2026) (2026 年 4 月 23 日) — ニュースレター掲載。
- [**Augment Code — 9 Open-Source Agent Orchestrators for AI Coding (2026)**](https://www.augmentcode.com/tools/open-source-agent-orchestrators) — 編集部によるまとめ記事。「このまとめの中で最もアーキテクチャ的に興味深いツール」と評価。
- [**nibzard/awesome-agentic-patterns**](https://github.com/nibzard/awesome-agentic-patterns/blob/main/patterns/deterministic-zero-llm-orchestration.md) — Bernstein は「決定的ゼロ LLM オーケストレーション」パターンの実運用実装として引用されています。
- [**Jenqyang/Awesome-AI-Agents**](https://github.com/Jenqyang/Awesome-AI-Agents)
- [**jamesmurdza/awesome-ai-devtools**](https://github.com/jamesmurdza/awesome-ai-devtools)
- [**jim-schwoebel/awesome_ai_agents**](https://github.com/jim-schwoebel/awesome_ai_agents)
- [**Piebald-AI/awesome-gemini-cli**](https://github.com/Piebald-AI/awesome-gemini-cli)
- [**ComposioHQ/awesome-codex-skills**](https://github.com/ComposioHQ/awesome-codex-skills)
- [**jxzhangjhu/Awesome-LLM-RAG**](https://github.com/jxzhangjhu/Awesome-LLM-RAG)
- [**rohitg00/awesome-claude-code-toolkit**](https://github.com/rohitg00/awesome-claude-code-toolkit)
- [**numtide/llm-agents.nix**](https://github.com/numtide/llm-agents.nix) — Nix flake 配布。

<details>
<summary>その他の awesome リスト・コミュニティキュレーション</summary>

- [andyrewlee/awesome-agent-orchestrators](https://github.com/andyrewlee/awesome-agent-orchestrators)
- [bradAGI/awesome-cli-coding-agents](https://github.com/bradAGI/awesome-cli-coding-agents)
- [milisp/awesome-codex-cli](https://github.com/milisp/awesome-codex-cli)
- [yaolifeng0629/Awesome-independent-tools](https://github.com/yaolifeng0629/Awesome-independent-tools) (中文 + EN)
- [caramaschiHG/awesome-ai-agents-2026](https://github.com/caramaschiHG/awesome-ai-agents-2026)
- [ai-for-developers/awesome-vibe-coding](https://github.com/ai-for-developers/awesome-vibe-coding)
- [killop/anything_about_game](https://github.com/killop/anything_about_game) (`AI.md`)
- [Glama MCP Catalog](https://glama.ai/mcp/servers/sipyourdrink-ltd/bernstein) — 編集部による MCP サーバー紹介。
- ミラー: [icopy-site/awesome](https://github.com/icopy-site/awesome)、[icopy-site/awesome-cn](https://github.com/icopy-site/awesome-cn)、[trackawesomelist/trackawesomelist](https://github.com/trackawesomelist/trackawesomelist)。

</details>

<details>
<summary>関連プロジェクトから先行事例として引用</summary>

- [**mkb23/overcode**](https://github.com/mkb23/overcode/blob/main/docs/design/bakeoffs/overcode-vs-bernstein.md) — Bernstein をリファレンス実装として扱う詳細な比較記事。
- [**Vintersong/NOVA-Cognition-Framework**](https://github.com/Vintersong/NOVA-Cognition-Framework) — `BERNSTEIN_PATTERNS.md`、「借用に値するパターン」。
- [**AJV009/drupal-contrib-workbench**](https://github.com/AJV009/drupal-contrib-workbench) — マネージャ/ジャニター分離に関する研究ノート。
- [**danielvaughan/codex-blog**](https://github.com/danielvaughan/codex-blog/blob/main/_posts/2026-04-09-loki-mode-autonomous-execution.md) — Bernstein を決定的サイドに位置付ける比較記事。

</details>

## スター履歴

[![Star History Chart](https://api.star-history.com/svg?repos=sipyourdrink-ltd/bernstein&type=Date)](https://star-history.com/#sipyourdrink-ltd/bernstein&Date)

## ライセンス

[Apache License 2.0](../../LICENSE)

---

Made with love by [Alex Chernysh](https://alexchernysh.com) &middot; [GitHub](https://github.com/chernistry) &middot; [X](https://x.com/alex_chernysh) &middot; [bernstein.run](https://bernstein.run?utm_source=github.com&utm_medium=readme&utm_campaign=bernstein-readme)

<!-- mcp-name: io.github.sipyourdrink-ltd/bernstein -->
