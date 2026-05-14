[English](../../README.md) | [Español (Spanish)](README.es.md) | [中文 (Chinese)](README.zh.md) | [العربية (Arabic)](README.ar.md) | [Português (Portuguese)](README.pt.md) | [Bahasa Indonesia (Indonesian)](README.id.md) | **Français (French)** | [日本語 (Japanese)](README.ja.md) | [Русский (Russian)](README.ru.md) | [Deutsch (German)](README.de.md) | [עברית (Hebrew)](README.he.md) | [יידיש (Yiddish)](README.yi.md)

<div align="center">

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="../../docs/assets/logo-dark.svg">
  <source media="(prefers-color-scheme: light)" srcset="../../docs/assets/logo-light.svg">
  <img alt="Bernstein" src="../../docs/assets/logo-light.svg" width="340">
</picture>

<br>

> *« Pour accomplir de grandes choses, il faut deux choses : un plan, et pas tout à fait assez de temps. »* — Leonard Bernstein

### Orchestrez n'importe quel agent de codage IA. N'importe quel modèle. Une seule commande.

[![CI](https://github.com/sipyourdrink-ltd/bernstein/actions/workflows/ci.yml/badge.svg)](https://github.com/sipyourdrink-ltd/bernstein/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/bernstein)](https://pypi.org/project/bernstein/)
[![Python 3.12+](https://img.shields.io/badge/python-3.12+-3776ab?logo=python&logoColor=white)](https://python.org)
[![License](https://img.shields.io/github/license/sipyourdrink-ltd/bernstein)](../../LICENSE)
[![MseeP.ai](https://img.shields.io/badge/MseeP.ai-verified-2496ed)](https://mseep.ai/app/chernistry-bernstein)

[Site web](https://bernstein.run?utm_source=github.com&utm_medium=readme&utm_campaign=bernstein-readme) &middot; [Documentation](https://bernstein.readthedocs.io/) &middot; [Premiers pas](../../docs/getting-started/GETTING_STARTED.md) &middot; [Glossaire](../../docs/reference/GLOSSARY.md) &middot; [Limitations](../../docs/reference/KNOWN_LIMITATIONS.md)

</div>

---

Bernstein est un planificateur Python déterministe qui pilote une équipe d'agents de codage CLI (Claude Code, Codex, Gemini CLI et 40 autres) sur un objectif unique, en parallèle dans des worktrees git, avec une chaîne d'audit signée HMAC sur chaque étape.

### Installation en 30 secondes

```bash
pipx install bernstein
bernstein init
bernstein run -g "fix the failing test in tests/test_foo.py"
```

### Démo en 60 secondes

Le clip ci-dessous couvre une exécution complète : le manager décompose l'objectif, trois agents travaillent en parallèle, la chaîne d'audit consigne chaque handoff, le janitor vérifie, puis une PR s'ouvre.

<p align="center">
  <img alt="Démo Bernstein de 60 secondes : le manager décompose l'objectif, trois agents tournent en parallèle, la chaîne d'audit consigne chaque handoff, une PR s'ouvre" src="../../docs/demo/demo.gif" width="800">
</p>

Après l'exécution, Bernstein poste sur la PR un commentaire structuré avec coût, résultats des tests, lineage et chaîne de hashs d'audit :

<p align="center">
  <img alt="Commentaire Bernstein sur la PR : sections Résumé, Coût, Lineage, Tests, Chaîne d'audit" src="../../docs/demo/screenshot-pr-comment.svg" width="720">
</p>

> Le GIF est généré depuis [`docs/demo/demo.tape`](../../docs/demo/demo.tape) avec [vhs](https://github.com/charmbracelet/vhs) ; régénérez-le localement avec `vhs docs/demo/demo.tape`.

### Comment il se compare

| Fonctionnalité                              | Bernstein   | Archon   | LangGraph |
|---------------------------------------------|-------------|----------|-----------|
| Équipe multi-agents (adapters parallèles)   | oui         | un       | oui       |
| Lineage signé / chaîne d'audit              | oui         | non      | non       |
| Déploiement air-gap / souverain             | oui         | partiel  | non       |
| Workflow YAML visuel                        | oui [^yaml] | oui      | non       |
| Tableau de bord hébergé / SaaS              | non         | partiel  | non       |

[^yaml]: Le support workflow YAML arrive avec la [PR #1108](https://github.com/sipyourdrink-ltd/bernstein/pull/1108) (dans ce lot). En attendant, les plans s'écrivent en Python ou via `bernstein run plan.yaml` sur l'ancien schéma.

Une matrice de fonctionnalités plus longue contre CrewAI, AutoGen, LangGraph et les quatre orchestrateurs d'agents CLI de la même catégorie que Bernstein vit dans la section [Comparaison détaillée](#detailed-comparison) plus bas.

---

### De quoi s'agit-il, en un paragraphe ?

Vous dites à Bernstein ce que vous voulez construire. Il répartit le travail entre plusieurs agents de codage IA, les exécute en parallèle dans des worktrees git isolés, consigne chaque handoff dans un journal d'audit chaîné par HMAC, lance les tests et fusionne le code qui passe vraiment. Vous revenez à une PR verte.

Forward-deployed engineering, en mode essaim. Posez Bernstein dans le repo d'un client et vous obtenez une équipe multi-agents avec état sur fichiers, scoping des credentials par agent et un audit trail signé, qui tourne sur les agents CLI auxquels le client fait déjà confiance.

### Autres méthodes d'installation

```bash
curl -fsSL https://bernstein.run/install.sh | sh        # Une ligne pour macOS / Linux
irm https://bernstein.run/install.ps1 | iex             # PowerShell sous Windows
pip install bernstein                                   # pip
uv tool install bernstein                               # uv
brew tap chernistry/tap && brew install bernstein       # Homebrew
```

Voir la [matrice d'installation](#install) complète pour `dnf copr`, `npx`, les extras optionnels et le chemin wheelhouse pour les sites air-gap.

### Pourquoi le scheduler est en Python ordinaire

La plupart des orchestrateurs d'agents utilisent un LLM pour décider qui fait quoi. C'est non déterministe et cela brûle des tokens en planification plutôt qu'en code. Bernstein effectue un seul appel LLM pour décomposer votre objectif, puis le reste (exécuter les agents en parallèle, isoler leurs branches git, lancer les tests, router les nouvelles tentatives) n'est que du Python ordinaire. Chaque exécution est reproductible. Chaque étape est journalisée et rejouable.

Pas de framework à apprendre. Pas de verrouillage fournisseur. Échangez n'importe quel agent, n'importe quel modèle, n'importe quel fournisseur.

<img alt="Bernstein en action : agents IA parallèles orchestrés en temps réel" src="../../docs/assets/in-action-small.gif" width="700">

Ce que vous voyez pendant l'exécution :

```
$ bernstein -g "Add JWT auth"
[manager] decomposed into 4 tasks
[agent-1] claude-sonnet: src/auth/middleware.py  (done, 2m 14s)
[agent-2] codex:         tests/test_auth.py      (done, 1m 58s)
[verify]  all gates pass. merging to main.
```

## Agents pris en charge

Bernstein détecte automatiquement les agents CLI installés. Mélangez-les dans une même exécution. Modèles locaux bon marché pour le code générique, modèles cloud plus puissants pour l'architecture.

43 adaptateurs d'agents CLI : 40 wrappers tiers plus un wrapper générique pour tout ce qui prend `--prompt`.

| Agent | Modèles | Installation |
|-------|---------|--------------|
| [Claude Code](https://docs.anthropic.com/en/docs/claude-code) | Opus 4, Sonnet 4.6, Haiku 4.5 | `npm install -g @anthropic-ai/claude-code` |
| [Codex CLI](https://github.com/openai/codex) | GPT-5, GPT-5 mini | `npm install -g @openai/codex` |
| [OpenAI Agents SDK v2](https://openai.github.io/openai-agents-python/) | GPT-5, GPT-5 mini, o4 | `pip install 'bernstein[openai]'` |
| [GitHub Copilot CLI](https://docs.github.com/en/copilot/github-copilot-in-the-cli) | Géré par Copilot (GPT-5, Sonnet 4.6) | `npm install -g @github/copilot` |
| [Gemini CLI](https://github.com/google-gemini/gemini-cli) | Gemini 2.5 Pro, Gemini Flash | `npm install -g @google/gemini-cli` |
| [Cursor](https://www.cursor.com) | Sonnet 4.6, Opus 4, GPT-5 | [Application Cursor](https://www.cursor.com) |
| [Aider](https://aider.chat) | Tout modèle compatible OpenAI/Anthropic | `pip install aider-chat` |
| [Amp](https://ampcode.com) | Géré par Amp | `npm install -g @sourcegraph/amp` |
| [Cody](https://sourcegraph.com/cody) | Hébergé par Sourcegraph | `npm install -g @sourcegraph/cody` |
| [Continue](https://continue.dev) | Tout modèle compatible OpenAI/Anthropic | `npm install -g @continuedev/cli` (binaire : `cn`) |
| [Goose](https://block.github.io/goose/) | Tout fournisseur pris en charge par Goose | Voir la [documentation Goose](https://block.github.io/goose/) |
| [IaC](https://www.terraform.io/) (Terraform/Pulumi) | Tout fournisseur utilisé par l'agent de base | Intégré |
| [Kilo](https://kilo.dev) | Hébergé par Kilo | Voir la [documentation Kilo](https://kilo.dev) |
| [Kiro](https://kiro.dev) | Hébergé par Kiro | Voir la [documentation Kiro](https://kiro.dev) |
| [Ollama](https://ollama.ai) + Aider | Modèles locaux (hors ligne) | `brew install ollama` |
| [OpenCode](https://opencode.ai) | Tout fournisseur pris en charge par OpenCode | Voir la [documentation OpenCode](https://opencode.ai) |
| [Qwen](https://github.com/QwenLM/qwen-code) | Modèles Qwen Code | `npm install -g @qwen-code/qwen-code` |
| [Cloudflare Agents](https://developers.cloudflare.com/agents/) | Modèles Workers AI | `bernstein cloud login` |
| [OpenHands](https://github.com/OpenHands/OpenHands) | Tout modèle pris en charge par LiteLLM (Anthropic, OpenAI, ...) | `uv tool install openhands --python 3.12` |
| [Open Interpreter](https://github.com/OpenInterpreter/open-interpreter) | Tout (via LiteLLM) | `pip install open-interpreter` |
| [gptme](https://github.com/gptme/gptme) | Anthropic, OpenAI, OpenRouter | `pipx install gptme` |
| [Plandex](https://github.com/plandex-ai/plandex) | Plandex Cloud ou modèles auto-hébergés | `curl -sL https://plandex.ai/install.sh \| bash` |
| [AIChat](https://github.com/sigoden/aichat) | OpenAI, Anthropic, OpenRouter, Groq, Gemini | `cargo install aichat` |
| [Letta Code](https://github.com/letta-ai/letta-code) | Routé par Letta (Anthropic, OpenAI) | `npm install -g @letta-ai/letta-code` |
| **Generic** | Toute CLI acceptant `--prompt` | Intégré |

#### Délégation à un orchestrateur (nœud feuille)

Une catégorie distincte et plus restreinte d'adaptateurs qui encapsulent **d'autres orchestrateurs CLI** comme s'ils étaient des agents uniques. Bernstein transmet à l'outil encapsulé un prompt ou un plan, puis ne voit que le code de sortie final ; les coûts des sous-agents et les contrôles qualité internes à l'orchestrateur encapsulé ne sont pas visibles pour Bernstein. Utile lorsque vous voulez intégrer un workflow existant bâti sur l'un de ces outils dans une étape d'un plan Bernstein plus large.

| Orchestrateur | Encapsulé sous | Installation |
|---------------|----------------|--------------|
| [Composio Agent Orchestrator](https://github.com/ComposioHQ/agent-orchestrator) (`@aoagents/ao`) | `composio` | `npm install -g @aoagents/ao` |
| [umputun/ralphex](https://github.com/umputun/ralphex) | `ralphex` | `go install github.com/umputun/ralphex/cmd/ralphex@latest` |

N'importe quel adaptateur fonctionne aussi comme **LLM interne du planificateur**. Exécutez toute la pile sans aucun fournisseur spécifique :

```yaml
internal_llm_provider: gemini            # or qwen, ollama, codex, goose, ...
internal_llm_model: gemini-3.1-pro
```

> [!TIP]
> Lancez `bernstein --headless` pour les pipelines CI. Pas de TUI, sortie JSON structurée, code de sortie non nul en cas d'échec.

## Démarrage rapide

```bash
cd your-project
bernstein init                    # creates .sdd/ workspace + bernstein.yaml
bernstein -g "Add rate limiting"  # agents spawn, work in parallel, verify, exit
bernstein live                    # watch progress in the TUI dashboard
bernstein stop                    # graceful shutdown with drain
```

Pour les projets multi-étapes, définissez un plan YAML :

```bash
bernstein run plan.yaml           # skips LLM planning, goes straight to execution
bernstein run --dry-run plan.yaml # preview tasks and estimated cost
```

## Comment ça marche

1. **Décomposer**. Le manager découpe votre objectif en tâches avec des rôles, les fichiers détenus et les signaux de complétion.
2. **Lancer**. Les agents démarrent dans des git worktrees isolés, un par tâche. La branche principale reste propre.
3. **Vérifier**. Le janitor contrôle des signaux concrets : tests qui passent, fichiers présents, lint propre, types corrects.
4. **Fusionner**. Le travail vérifié atterrit dans la branche principale. Les tâches échouées sont retentées ou routées vers un autre modèle.

L'orchestrateur est un planificateur Python, pas un LLM. Les décisions de planification sont déterministes, auditables et reproductibles.

## Exécution dans le cloud (Cloudflare)

Bernstein peut exécuter les agents sur Cloudflare Workers plutôt qu'en local. La CLI `bernstein cloud` gère le déploiement et le cycle de vie.

- **Workers**. Exécution des agents sur l'edge de Cloudflare, avec Durable Workflows pour les tâches multi-étapes et les nouvelles tentatives automatiques.
- **Isolation par sandbox V8**. Chaque agent s'exécute dans son propre isolate, sans surcharge de conteneur.
- **Synchronisation de l'espace de travail R2**. L'état du worktree local se synchronise avec le stockage objet R2 afin que les agents cloud voient les mêmes fichiers.
- **Workers AI** (expérimental). Utilisez les modèles hébergés par Cloudflare comme fournisseur LLM, sans clé d'API externe requise.
- **Analytique D1**. Les métriques de tâches et les données de coût sont stockées dans D1 pour interrogation.
- **Vectorize**. Cache sémantique adossé à la base de données vectorielle de Cloudflare.
- **Rendu de navigateur**. Chrome headless sur Workers pour les agents qui doivent inspecter une sortie web.
- **Transport distant MCP**. Exposez ou consommez des serveurs MCP via le réseau Cloudflare.

```bash
bernstein cloud login      # authenticate with Bernstein Cloud
bernstein cloud deploy     # push agent workers
bernstein cloud run plan.yaml  # execute a plan on Cloudflare
```

## Capacités

**Orchestration centrale**. Exécution parallèle, isolation par git worktree, vérification par le janitor, contrôles qualité (lint, types, scan PII), revue de code multi-modèles, disjoncteur pour les agents au comportement déviant, surveillance de la croissance des tokens avec intervention automatique.

**Intelligence**. Routeur à bandit contextuel pour la sélection du modèle/effort. Graphe de connaissances pour l'analyse d'impact sur la base de code. Cache sémantique qui économise des tokens sur les motifs répétés. Détection d'anomalies de coût (alertes de taux de consommation). Détection d'anomalies de comportement avec marquage par Z-score.

**Sandboxing**. Protocole [`SandboxBackend`](../../docs/architecture/sandbox.md) enfichable — exécutez les agents dans des git worktrees locaux (par défaut), des conteneurs Docker, des microVMs Firecracker [E2B](https://e2b.dev) ou des conteneurs serverless [Modal](https://modal.com) (avec GPU optionnel). Les auteurs de plugins peuvent enregistrer des backends personnalisés via le groupe d'entry-points `bernstein.sandbox_backends`. Inspectez les backends installés avec `bernstein agents sandbox-backends`.

**Stockage des artefacts**. L'état `.sdd/` peut être streamé vers des backends [`ArtifactSink`](../../docs/architecture/storage.md) enfichables : système de fichiers local (par défaut), S3, Google Cloud Storage, Azure Blob ou Cloudflare R2. `BufferedSink` préserve le contrat de robustesse aux pannes du WAL en écrivant d'abord en local avec fsync puis en mirroitant vers le distant de manière asynchrone.

**Packs de compétences**. [Compétences](../../docs/architecture/skills.md) à divulgation progressive (modèle OpenAI Agents SDK) : seul un index compact des compétences est embarqué dans le system prompt de chaque spawn, les agents récupèrent les corps complets via l'outil MCP `load_skill` à la demande. 17 packs de rôles intégrés plus des entry-points tiers `bernstein.skill_sources`.

**Contrôles**. Journaux d'audit chaînés HMAC, moteur de politiques, filtrage des sorties PII, reprise après crash adossée au WAL (sécurité multi-worker expérimentale), OAuth 2.0 PKCE.

**Observabilité**. Prometheus `/metrics`, presets d'exporteur OTel, tableaux de bord Grafana. Suivi des coûts par modèle (`bernstein cost`). TUI terminal et tableau de bord web. Visibilité des processus agents dans `ps`.

**Écosystème**. Mode serveur MCP, prise en charge du protocole A2A, intégration GitHub App, système de plugins basé sur pluggy, espaces de travail multi-dépôts, mode cluster pour exécution distribuée, auto-évolution via `--evolve` (expérimental).

Matrice complète des fonctionnalités : [FEATURE_MATRIX.md](../../docs/reference/FEATURE_MATRIX.md) &middot; Fonctionnalités récentes : [Quoi de neuf](../../docs/whats-new.md)

## Quoi de neuf en v1.9

**Pont ACP** — `bernstein acp serve --stdio` expose Bernstein à n'importe quel éditeur qui parle Agent Communication Protocol (Zed, etc.). Aucun code de plugin nécessaire côté éditeur.

**Réparation CI autonome** — `bernstein autofix` surveille les PR Bernstein ouvertes et, lorsque la CI passe au rouge, lance automatiquement un agent réparateur. Une fois au vert, il pousse le correctif et redemande la revue.

**Coffre-fort à identifiants** — `bernstein connect <provider>` écrit les clés d'API dans le keychain de l'OS ; `bernstein creds` les liste et les fait tourner. Les agents héritent d'identifiants à portée limitée sans toucher aux variables d'environnement.

**Tunnels de prévisualisation** — `bernstein preview start` démarre un serveur de développement en sandbox et imprime une URL publique. Utile pour partager une branche en cours d'exécution avec un relecteur sans déployer en staging.

Changelog complet : [docs/whats-new.md](../../docs/whats-new.md)

## Commandes pour opérateurs

Des commandes qui éliminent le code de glue que la plupart des équipes finissent par écrire autour de leurs exécutions.

| Commande | Ce qu'elle fait |
|----------|-----------------|
| `bernstein pr` | Crée automatiquement une PR GitHub à partir d'une session terminée ; le corps reprend les résultats des contrôles du janitor et la ventilation du coût en tokens/USD. |
| `bernstein from-ticket <url>` | Importe un ticket Linear / GitHub Issues / Jira en tant que tâche Bernstein. Inférence du rôle et du périmètre à partir des labels. Prend en charge `--dry-run` et `--run`. |
| `bernstein ticket import <url>` | Forme alias / groupe de `from-ticket` pour le scripting. |
| `bernstein remote` | Backend sandbox SSH. `remote test <host>`, `remote run <host> <path>`, `remote forget <host>`. Réutilisation du socket ControlMaster pour des appels répétés rapides. |
| `bernstein hooks` | Hooks de cycle de vie pour `pre_task`, `post_task`, `pre_merge`, `post_merge`, `pre_spawn`, `post_spawn` — scripts shell ou `@hookimpl`s pluggy. `hooks list`, `hooks run <event>`, `hooks check`. |
| `bernstein chat serve --platform=telegram\|discord\|slack` | Pilotez les exécutions depuis le chat avec `/run`, `/status`, `/approve`, `/reject`, `/switch`, `/stop`. |
| `bernstein approve-tool` / `bernstein reject-tool` | Approbation interactive des appels d'outils en cours d'exécution. `--latest`, `--id`, `--always`. |
| `bernstein tunnel start <port> [--provider auto\|cloudflared\|ngrok\|bore\|tailscale]` | Un seul wrapper autour de quatre fournisseurs de tunnels. Aussi `tunnel list`, `tunnel stop <name>\|--all`. Réutilisation de processus type ControlMaster. |
| `bernstein daemon install [--user\|--system] [--command="..."] [--env KEY=VAL]...` | Installe une unité systemd (Linux) ou launchd (macOS) pour un démarrage automatique. Aussi `daemon start/stop/restart/status/uninstall`. |
| `bernstein connect <provider>` / `bernstein creds` | Stocke et fait tourner les identifiants d'API dans le keychain de l'OS. Les agents héritent de clés à portée limitée par exécution. |
| `bernstein autofix` | Démon qui surveille les PR Bernstein ouvertes ; lance un agent réparateur lorsque la CI échoue et pousse la réparation automatiquement. |
| `bernstein preview start` | Démarre un serveur de développement en sandbox pour la branche courante et imprime une URL de tunnel public partageable. |

## Comparaison

| Fonctionnalité | Bernstein | CrewAI | AutoGen [^autogen] | LangGraph |
|----------------|-----------|--------|---------|-----------|
| Orchestrateur | Code déterministe | Piloté par LLM (+ Flows en code) | Piloté par LLM | Graphe + LLM |
| Fonctionne avec | Tout agent CLI (43 adaptateurs) | Classes du SDK Python | Agents Python | Nœuds LangChain |
| Isolation git | Worktrees par agent | Non | Non | Non |
| Sandboxes enfichables | Worktree, Docker, E2B, Modal | Non | Non | Non |
| Vérification | Janitor + contrôles qualité | Guardrails + sortie Pydantic | Conditions de terminaison | Arêtes conditionnelles |
| Suivi des coûts | Intégré | `usage_metrics` | `RequestUsage` | Via LangSmith |
| Modèle d'état | Basé sur fichiers (.sdd/) | En mémoire + checkpoint SQLite | En mémoire | Checkpointer |
| Sinks d'artefacts distants | S3, GCS, Azure Blob, R2 | Non | Non | Non |
| Auto-évolution | Intégrée (expérimentale) | Non | Non | Non |
| Plans déclaratifs (YAML) | Oui | Oui (`agents.yaml`, `tasks.yaml`) | Non | Partiel (`langgraph.json`) |
| Routage de modèle par tâche | Oui | LLM par agent | `model_client` par agent | Par nœud (manuel) |
| Support MCP | Oui (client + serveur) | Oui | Oui (client + workbench) | Oui (client + serveur) |
| Chat agent à agent | Tableau d'affichage | Oui (processus Crew) | Oui (chat de groupe) | Oui (superviseur, swarm) |
| Interface web | TUI + tableau de bord web | CrewAI AMP | AutoGen Studio | LangGraph Studio + LangSmith |
| Option hébergée dans le cloud | Oui (Cloudflare) | Oui (CrewAI AMP) | Non | Oui (LangGraph Cloud) |
| RAG/recherche intégré | Oui (FTS5 + BM25 sur la base de code) | `crewai_tools` | Récupérateurs `autogen_ext` | Via LangChain |

*Dernière vérification : 2026-04-19. Voir les [pages de comparaison complètes](../../docs/compare/README.md) pour des matrices de fonctionnalités détaillées.*

Le tableau ci-dessus compare Bernstein aux frameworks d'orchestration de LLM (qui orchestrent des appels LLM). Le tableau ci-dessous couvre la catégorie la plus proche — les autres outils qui orchestrent **des agents de codage CLI** :

| Fonctionnalité | Bernstein | [awslabs/cli-agent-orchestrator](https://github.com/awslabs/cli-agent-orchestrator) | [ComposioHQ/agent-orchestrator](https://github.com/ComposioHQ/agent-orchestrator) | [emdash](https://github.com/generalaction/emdash) | [umputun/ralphex](https://github.com/umputun/ralphex) |
|----------------|-----------|-----------|-----------|-----------|-----------|
| Forme | CLI Python + bibliothèque + serveur MCP | CLI Python + sessions tmux + interface web | CLI TypeScript + tableau de bord local | Application desktop Electron | CLI Go |
| Langage principal | Python | Python | TypeScript | TypeScript | Go |
| Installation | `pipx install bernstein` | `uv tool install cli-agent-orchestrator` | `npm install -g @aoagents/ao` | `.dmg` / `.msi` / `.AppImage` | `go install` / binaire unique |
| Adaptateurs d'agents | 43 | 5 (Kiro, Claude Code, Codex, Gemini, Kimi) | 3 (Claude Code, Codex, Aider) | 24 | 1 (Claude Code uniquement) |
| Exécution multi-agents en parallèle | Oui | Oui (session tmux par agent) | Oui | Oui | Non (session unique séquentielle) |
| Git worktree par agent | Oui | Non (prévu, [#100](https://github.com/awslabs/cli-agent-orchestrator/issues/100)) | Oui | Oui | Indicateur optionnel `--worktree` |
| Mode serveur MCP (s'expose en MCP) | Oui (stdio + HTTP/SSE) | Oui (communications inter-agents) | Non | Non | Non |
| Coordinateur | Planificateur Python déterministe | Superviseur LLM hiérarchique | Piloté par LLM | Non documenté | Exécuteur de plan linéaire |
| Replay d'audit chaîné HMAC | Oui | Non | Non | Non | Non |
| Vérificateur multi-modèles / contrôles qualité | Oui (multi-étapes) | Non | Non | Non | Revue multi-phases (Claude uniquement) |
| Réparation CI / flux PR autonome | Oui (`bernstein autofix`) | Non | Oui | Non | Non |
| Tableau de bord visuel | TUI + web | Interface web + tmux | Web | Application desktop | Web (`--serve`) |
| Sinks de notification | Telegram/Slack/Discord/Email/Webhook/Shell | — | Non | Non | Telegram / Email / Slack / Webhook |
| Soutien | OSS solo | AWS Labs | Financé (Composio.dev) | YC W26 | OSS solo |
| Licence | Apache 2.0 | Apache 2.0 | MIT | Apache 2.0 | MIT |

L'avantage de Bernstein dans cette catégorie : **natif Python, MCP-server-first, la couverture d'adaptateurs la plus large, vrai parallélisme multi-agents, planificateur déterministe sans LLM dans la boucle de coordination**. Si vous voulez une isolation par session tmux alignée AWS avec un superviseur LLM hiérarchique, le `cao` d'AWS Labs est plus adapté ; si votre stack est en TypeScript et que vous voulez un produit avec un tableau de bord, `@aoagents/ao` de Composio convient mieux ; si vous voulez un ADE desktop soigné, c'est emdash ; si vous n'utilisez que Claude Code et que vous voulez un binaire Go unique qui parcourt un plan de haut en bas, c'est ralphex. Si vous voulez une primitive qui s'importe en Python, s'expose via MCP à n'importe quel client, exécute de nombreux agents en parallèle et couvre toute l'étendue d'agents (y compris Qwen, Goose, Ollama, OpenAI Agents SDK, Cloudflare Agents et plus) — c'est Bernstein.

[^autogen]: AutoGen est en mode maintenance ; le successeur est Microsoft Agent Framework 1.0.

## Surveillance

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

## Installation

| Méthode | Commande |
|---------|----------|
| **One-liner (macOS / Linux)** | `curl -fsSL https://bernstein.run/install.sh \| sh` |
| **One-liner (Windows)** | `irm https://bernstein.run/install.ps1 \| iex` |
| **pip** | `pip install bernstein` |
| **pipx** | `pipx install bernstein` |
| **uv** | `uv tool install bernstein` |
| **Homebrew** | `brew tap chernistry/tap && brew install bernstein` |
| **Fedora / RHEL** | `sudo dnf copr enable alexchernysh/bernstein && sudo dnf install bernstein` |
| **npm** (wrapper) | `npx bernstein-orchestrator` |

Les scripts one-liner vérifient la présence de Python 3.12+, amorcent pipx s'il est manquant, corrigent le PATH pour la session courante et installent (ou mettent à jour) `bernstein`. Ils gèrent les environnements macOS administrés par brew et le repli sur le lanceur `py -3` sous Windows. Sources des scripts : [install.sh](../../scripts/install.sh) · [install.ps1](../../scripts/install.ps1).

### Extras optionnels

Les SDK des fournisseurs sont optionnels afin que l'installation de base reste légère. Choisissez ce dont vous avez besoin :

| Extra | Active |
|-------|--------|
| `bernstein[openai]` | Adaptateur OpenAI Agents SDK v2 (`openai_agents`) |
| `bernstein[docker]` | Backend sandbox Docker |
| `bernstein[e2b]` | Backend sandbox microVM [E2B](https://e2b.dev) (nécessite `E2B_API_KEY`) |
| `bernstein[modal]` | Backend sandbox [Modal](https://modal.com), GPU optionnel (nécessite `MODAL_TOKEN_ID` / `MODAL_TOKEN_SECRET`) |
| `bernstein[s3]` | Sink d'artefacts S3 (via `boto3`) |
| `bernstein[gcs]` | Sink d'artefacts Google Cloud Storage |
| `bernstein[azure]` | Sink d'artefacts Azure Blob |
| `bernstein[r2]` | Sink d'artefacts Cloudflare R2 (compatible S3 via `boto3`) |
| `bernstein[grpc]` | Pont gRPC |
| `bernstein[k8s]` | Intégrations Kubernetes |

Combinez les extras avec des crochets, par exemple `pip install 'bernstein[openai,docker,s3]'`.

Extensions pour éditeurs : [VS Marketplace](https://marketplace.visualstudio.com/items?itemName=alex-chernysh.bernstein) &middot; [Open VSX](https://open-vsx.org/extension/alex-chernysh/bernstein)

## Contribuer

Les PR sont les bienvenues. Voir [CONTRIBUTING.md](../../CONTRIBUTING.md) pour la configuration et le style de code.

## Soutien

Si Bernstein vous fait gagner du temps : [GitHub Sponsors](https://github.com/sponsors/chernistry)

Contact : [forte@bernstein.run](mailto:forte@bernstein.run)

## Mis en avant dans

Listes éditoriales, newsletters et projets pairs qui ont relayé Bernstein :

- [**Python Weekly #742**](https://www.pythonweekly.com/p/python-weekly-issue-742-april-23-2026) (23 avril 2026) — mention dans la newsletter.
- [**Augment Code — 9 Open-Source Agent Orchestrators for AI Coding (2026)**](https://www.augmentcode.com/tools/open-source-agent-orchestrators) — sélection éditoriale ; « l'outil le plus intéressant sur le plan architectural de cette sélection ».
- [**nibzard/awesome-agentic-patterns**](https://github.com/nibzard/awesome-agentic-patterns/blob/main/patterns/deterministic-zero-llm-orchestration.md) — Bernstein cité comme l'implémentation de production du modèle « orchestration déterministe sans LLM ».
- [**Jenqyang/Awesome-AI-Agents**](https://github.com/Jenqyang/Awesome-AI-Agents)
- [**jamesmurdza/awesome-ai-devtools**](https://github.com/jamesmurdza/awesome-ai-devtools)
- [**jim-schwoebel/awesome_ai_agents**](https://github.com/jim-schwoebel/awesome_ai_agents)
- [**Piebald-AI/awesome-gemini-cli**](https://github.com/Piebald-AI/awesome-gemini-cli)
- [**ComposioHQ/awesome-codex-skills**](https://github.com/ComposioHQ/awesome-codex-skills)
- [**jxzhangjhu/Awesome-LLM-RAG**](https://github.com/jxzhangjhu/Awesome-LLM-RAG)
- [**rohitg00/awesome-claude-code-toolkit**](https://github.com/rohitg00/awesome-claude-code-toolkit)
- [**numtide/llm-agents.nix**](https://github.com/numtide/llm-agents.nix) — distribution en flake Nix.

<details>
<summary>Plus de listes awesome et de sélections communautaires</summary>

- [andyrewlee/awesome-agent-orchestrators](https://github.com/andyrewlee/awesome-agent-orchestrators)
- [bradAGI/awesome-cli-coding-agents](https://github.com/bradAGI/awesome-cli-coding-agents)
- [milisp/awesome-codex-cli](https://github.com/milisp/awesome-codex-cli)
- [yaolifeng0629/Awesome-independent-tools](https://github.com/yaolifeng0629/Awesome-independent-tools) (中文 + EN)
- [caramaschiHG/awesome-ai-agents-2026](https://github.com/caramaschiHG/awesome-ai-agents-2026)
- [ai-for-developers/awesome-vibe-coding](https://github.com/ai-for-developers/awesome-vibe-coding)
- [killop/anything_about_game](https://github.com/killop/anything_about_game) (`AI.md`)
- [Glama MCP Catalog](https://glama.ai/mcp/servers/sipyourdrink-ltd/bernstein) — référencement éditorial de serveurs MCP.
- Miroirs : [icopy-site/awesome](https://github.com/icopy-site/awesome), [icopy-site/awesome-cn](https://github.com/icopy-site/awesome-cn), [trackawesomelist/trackawesomelist](https://github.com/trackawesomelist/trackawesomelist).

</details>

<details>
<summary>Cité comme antériorité par des projets pairs</summary>

- [**mkb23/overcode**](https://github.com/mkb23/overcode/blob/main/docs/design/bakeoffs/overcode-vs-bernstein.md) — comparatif au long cours traitant Bernstein comme l'implémentation de référence.
- [**Vintersong/NOVA-Cognition-Framework**](https://github.com/Vintersong/NOVA-Cognition-Framework) — `BERNSTEIN_PATTERNS.md`, « Patterns Worth Borrowing ».
- [**AJV009/drupal-contrib-workbench**](https://github.com/AJV009/drupal-contrib-workbench) — notes de recherche sur la séparation manager/janitor.
- [**danielvaughan/codex-blog**](https://github.com/danielvaughan/codex-blog/blob/main/_posts/2026-04-09-loki-mode-autonomous-execution.md) — article comparatif positionnant Bernstein du côté déterministe.

</details>

## Historique des étoiles

[![Star History Chart](https://api.star-history.com/svg?repos=sipyourdrink-ltd/bernstein&type=Date)](https://star-history.com/#sipyourdrink-ltd/bernstein&Date)

## Licence

[Apache License 2.0](../../LICENSE)

---

Fait avec amour par [Alex Chernysh](https://alexchernysh.com) &middot; [GitHub](https://github.com/chernistry) &middot; [X](https://x.com/alex_chernysh) &middot; [bernstein.run](https://bernstein.run?utm_source=github.com&utm_medium=readme&utm_campaign=bernstein-readme)

<!-- mcp-name: io.github.sipyourdrink-ltd/bernstein -->
