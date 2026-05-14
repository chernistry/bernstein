[English](../../README.md) | [Español (Spanish)](README.es.md) | [中文 (Chinese)](README.zh.md) | [العربية (Arabic)](README.ar.md) | [Português (Portuguese)](README.pt.md) | [Bahasa Indonesia (Indonesian)](README.id.md) | [Français (French)](README.fr.md) | [日本語 (Japanese)](README.ja.md) | [Русский (Russian)](README.ru.md) | [Deutsch (German)](README.de.md) | **עברית (Hebrew)** | [יידיש (Yiddish)](README.yi.md)

<div dir="rtl">

<div align="center">

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="../../docs/assets/logo-dark.svg">
  <source media="(prefers-color-scheme: light)" srcset="../../docs/assets/logo-light.svg">
  <img alt="Bernstein" src="../../docs/assets/logo-light.svg" width="340">
</picture>

<br>

> *"כדי להשיג דברים גדולים, נדרשים שני דברים: תוכנית, ולא ממש מספיק זמן."* — Leonard Bernstein

### תזמרו כל סוכן קוד מבוסס בינה מלאכותית. כל מודל. בפקודה אחת.

[![CI](https://github.com/sipyourdrink-ltd/bernstein/actions/workflows/ci.yml/badge.svg)](https://github.com/sipyourdrink-ltd/bernstein/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/bernstein)](https://pypi.org/project/bernstein/)
[![Python 3.12+](https://img.shields.io/badge/python-3.12+-3776ab?logo=python&logoColor=white)](https://python.org)
[![License](https://img.shields.io/github/license/sipyourdrink-ltd/bernstein)](../../LICENSE)
[![MseeP.ai](https://img.shields.io/badge/MseeP.ai-verified-2496ed)](https://mseep.ai/app/chernistry-bernstein)

[אתר](https://bernstein.run?utm_source=github.com&utm_medium=readme&utm_campaign=bernstein-readme) &middot; [תיעוד](https://bernstein.readthedocs.io/) &middot; [צעדים ראשונים](../../docs/getting-started/GETTING_STARTED.md) &middot; [מילון מונחים](../../docs/reference/GLOSSARY.md) &middot; [מגבלות](../../docs/reference/KNOWN_LIMITATIONS.md)

</div>

---

Bernstein הוא מתזמן Python דטרמיניסטי שמריץ צוות של סוכני קוד CLI (Claude Code, Codex, Gemini CLI ועוד 40) על מטרה אחת במקביל בתוך worktrees של git, עם שרשרת ביקורת חתומה ב-HMAC על כל צעד.

### התקנה ב-30 שניות

```bash
pipx install bernstein
bernstein init
bernstein run -g "fix the failing test in tests/test_foo.py"
```

### לראות תוך 60 שניות

הקליפ הבא מכסה ריצה שלמה: המנהל מפרק את המטרה, שלושה סוכנים עובדים במקביל, שרשרת הביקורת רושמת כל handoff, ה-janitor מוודא, ונפתח PR.

<p align="center">
  <img alt="הדגמת Bernstein של 60 שניות: המנהל מפרק את המטרה, שלושה סוכנים רצים במקביל, שרשרת הביקורת רושמת כל handoff, ונפתח PR" src="../../docs/demo/demo.gif" width="800">
</p>

לאחר הריצה Bernstein מפרסם תגובה מובנית ב-PR עם עלות, תוצאות בדיקות, lineage ושרשרת hash הביקורת:

<p align="center">
  <img alt="תגובת Bernstein ב-PR: סקירה, עלות, Lineage, בדיקות, שרשרת ביקורת" src="../../docs/demo/screenshot-pr-comment.svg" width="720">
</p>

> ה-GIF נוצר מ-[`docs/demo/demo.tape`](../../docs/demo/demo.tape) באמצעות [vhs](https://github.com/charmbracelet/vhs); ניתן ליצור מחדש מקומית עם `vhs docs/demo/demo.tape`.

### השוואה

| תכונה                                        | Bernstein   | Archon   | LangGraph |
|----------------------------------------------|-------------|----------|-----------|
| צוות מרובה סוכנים (מתאמים מקבילים)            | כן          | אחד      | כן        |
| Lineage חתום / שרשרת ביקורת                  | כן          | לא       | לא        |
| פריסה אוויר־מנותקת (air-gap) / ריבונית        | כן          | חלקית    | לא        |
| YAML זרימת עבודה ויזואלית                    | כן [^yaml]  | כן       | לא        |
| לוח בקרה מתארח / SaaS                        | לא          | חלקית    | לא        |

[^yaml]: תמיכת YAML בזרימות עבודה נוחתת עם [PR #1108](https://github.com/sipyourdrink-ltd/bernstein/pull/1108) (במאצ'ר הזה). עד אז התוכניות נכתבות ב-Python או דרך `bernstein run plan.yaml` כנגד הסכמה הישנה.

מטריצת תכונות ארוכה יותר אל מול CrewAI, AutoGen, LangGraph וארבעת מתזמרי סוכני ה-CLI שבאותה קטגוריה כמו Bernstein גרה בקטע [השוואה מפורטת](#detailed-comparison) למטה.

---

### מה זה, בפסקה אחת

אתם אומרים ל-Bernstein מה אתם רוצים לבנות. הוא מפצל את העבודה בין כמה סוכני קוד מבוססי בינה מלאכותית, מריץ אותם במקביל בתוך worktrees מבודדים של git, רושם כל handoff בלוג ביקורת בשרשרת HMAC, מריץ את הבדיקות, וממזג את הקוד שעובר אותן בפועל. אתם חוזרים ל-PR ירוק.

Forward-deployed engineering, בצורת נחיל. תניחו את Bernstein על repo של לקוח, ויש לכם צוות מרובה סוכנים עם state בקבצים, היקף הרשאות לכל סוכן, ו-audit trail חתום, רץ מעל סוכני ה-CLI שהלקוח כבר סומך עליהם.

### דרכי התקנה נוספות

```bash
curl -fsSL https://bernstein.run/install.sh | sh        # שורה אחת ב-macOS / Linux
irm https://bernstein.run/install.ps1 | iex             # PowerShell של Windows
pip install bernstein                                   # pip
uv tool install bernstein                               # uv
brew tap chernistry/tap && brew install bernstein       # Homebrew
```

ראו [מטריצת התקנה](#install) המלאה ל-`dnf copr`, `npx`, extras אופציונליים ושביל wheelhouse לאתרים אוויר־מנותקים.

### למה המתזמן הוא Python רגיל

רוב מתזמרי הסוכנים משתמשים ב-LLM כדי להחליט מי עושה מה. זה לא דטרמיניסטי ושורף טוקנים על תזמון במקום על קוד. Bernstein מבצע קריאת LLM אחת כדי לפרק את המטרה שלכם, וכל היתר (הרצת סוכנים במקביל, בידוד ענפי git שלהם, הרצת בדיקות, ניתוב ניסיונות חוזרים) הוא Python רגיל. כל ריצה ניתנת לשחזור. כל שלב מתועד וניתן לחזרה.

אין מסגרת ללמוד. אין נעילת ספק. החליפו כל סוכן, כל מודל, כל ספק.

<img alt="Bernstein בפעולה: סוכני בינה מלאכותית מתוזמרים במקביל בזמן אמת" src="../../docs/assets/in-action-small.gif" width="700">

מה תראו בזמן הריצה:

```
$ bernstein -g "Add JWT auth"
[manager] decomposed into 4 tasks
[agent-1] claude-sonnet: src/auth/middleware.py  (done, 2m 14s)
[agent-2] codex:         tests/test_auth.py      (done, 1m 58s)
[verify]  all gates pass. merging to main.
```

## סוכנים נתמכים

Bernstein מזהה אוטומטית סוכני CLI מותקנים. ערבבו אותם באותה ריצה. מודלים מקומיים זולים לקוד שגרתי, מודלי ענן כבדים יותר לארכיטקטורה.

43 מתאמי סוכני CLI: 40 עטיפות לצד שלישי בתוספת עטיפה גנרית לכל דבר עם `--prompt`.

| Agent | Models | Install |
|-------|--------|---------|
| [Claude Code](https://docs.anthropic.com/en/docs/claude-code) | Opus 4, Sonnet 4.6, Haiku 4.5 | `npm install -g @anthropic-ai/claude-code` |
| [Codex CLI](https://github.com/openai/codex) | GPT-5, GPT-5 mini | `npm install -g @openai/codex` |
| [OpenAI Agents SDK v2](https://openai.github.io/openai-agents-python/) | GPT-5, GPT-5 mini, o4 | `pip install 'bernstein[openai]'` |
| [GitHub Copilot CLI](https://docs.github.com/en/copilot/github-copilot-in-the-cli) | Copilot-managed (GPT-5, Sonnet 4.6) | `npm install -g @github/copilot` |
| [Gemini CLI](https://github.com/google-gemini/gemini-cli) | Gemini 2.5 Pro, Gemini Flash | `npm install -g @google/gemini-cli` |
| [Cursor](https://www.cursor.com) | Sonnet 4.6, Opus 4, GPT-5 | [Cursor app](https://www.cursor.com) |
| [Aider](https://aider.chat) | Any OpenAI/Anthropic-compatible | `pip install aider-chat` |
| [Amp](https://ampcode.com) | Amp-managed | `npm install -g @sourcegraph/amp` |
| [Cody](https://sourcegraph.com/cody) | Sourcegraph-hosted | `npm install -g @sourcegraph/cody` |
| [Continue](https://continue.dev) | Any OpenAI/Anthropic-compatible | `npm install -g @continuedev/cli` (binary: `cn`) |
| [Goose](https://block.github.io/goose/) | Any provider Goose supports | See [Goose docs](https://block.github.io/goose/) |
| [IaC](https://www.terraform.io/) (Terraform/Pulumi) | Any provider the base agent uses | Built-in |
| [Kilo](https://kilo.dev) | Kilo-hosted | See [Kilo docs](https://kilo.dev) |
| [Kiro](https://kiro.dev) | Kiro-hosted | See [Kiro docs](https://kiro.dev) |
| [Ollama](https://ollama.ai) + Aider | Local models (offline) | `brew install ollama` |
| [OpenCode](https://opencode.ai) | Any provider OpenCode supports | See [OpenCode docs](https://opencode.ai) |
| [Qwen](https://github.com/QwenLM/qwen-code) | Qwen Code models | `npm install -g @qwen-code/qwen-code` |
| [Cloudflare Agents](https://developers.cloudflare.com/agents/) | Workers AI models | `bernstein cloud login` |
| [OpenHands](https://github.com/OpenHands/OpenHands) | Any LiteLLM-supported (Anthropic, OpenAI, ...) | `uv tool install openhands --python 3.12` |
| [Open Interpreter](https://github.com/OpenInterpreter/open-interpreter) | Any (LiteLLM-backed) | `pip install open-interpreter` |
| [gptme](https://github.com/gptme/gptme) | Anthropic, OpenAI, OpenRouter | `pipx install gptme` |
| [Plandex](https://github.com/plandex-ai/plandex) | Plandex Cloud or self-hosted models | `curl -sL https://plandex.ai/install.sh \| bash` |
| [AIChat](https://github.com/sigoden/aichat) | OpenAI, Anthropic, OpenRouter, Groq, Gemini | `cargo install aichat` |
| [Letta Code](https://github.com/letta-ai/letta-code) | Letta-routed (Anthropic, OpenAI) | `npm install -g @letta-ai/letta-code` |
| **Generic** | Any CLI with `--prompt` | Built-in |

#### האצלת תזמור (צומת עלה)

מחלקה נפרדת וקטנה יותר של מתאמים שעוטפים **מתזמרי CLI אחרים** כאילו היו סוכן יחיד. Bernstein מעביר לכלי העטוף הנחיה או תוכנית ורואה רק את קוד היציאה הסופי; עלויות תת-הסוכנים ושערי האיכות בתוך המתזמר העטוף אינם נראים ל-Bernstein. שימושי כשרוצים לשבץ זרימת עבודה קיימת שנבנתה על אחד מהכלים האלה כשלב בתוך תוכנית Bernstein גדולה יותר.

| Orchestrator | Wrapped as | Install |
|--------------|------------|---------|
| [Composio Agent Orchestrator](https://github.com/ComposioHQ/agent-orchestrator) (`@aoagents/ao`) | `composio` | `npm install -g @aoagents/ao` |
| [umputun/ralphex](https://github.com/umputun/ralphex) | `ralphex` | `go install github.com/umputun/ralphex/cmd/ralphex@latest` |

כל מתאם משמש גם כ-**LLM הפנימי של המתזמר**. הריצו את המחסנית כולה ללא ספק ספציפי כלשהו:

```yaml
internal_llm_provider: gemini            # or qwen, ollama, codex, goose, ...
internal_llm_model: gemini-3.1-pro
```

> [!TIP]
> הריצו `bernstein --headless` עבור צינורות CI. בלי TUI, פלט JSON מובנה, יציאה לא-אפסית בכשל.

## התחלה מהירה

```bash
cd your-project
bernstein init                    # creates .sdd/ workspace + bernstein.yaml
bernstein -g "Add rate limiting"  # agents spawn, work in parallel, verify, exit
bernstein live                    # watch progress in the TUI dashboard
bernstein stop                    # graceful shutdown with drain
```

לפרויקטים רב-שלביים, הגדירו תוכנית YAML:

```bash
bernstein run plan.yaml           # skips LLM planning, goes straight to execution
bernstein run --dry-run plan.yaml # preview tasks and estimated cost
```

## איך זה עובד

1. **פירוק**. המנהל מפרק את המטרה שלכם למשימות עם תפקידים, קבצים בבעלות וסימני השלמה.
2. **השריץ (Spawn)**. סוכנים מתחילים ב-worktrees מבודדים של git, אחד לכל משימה. ענף ה-main נשאר נקי.
3. **אימות**. ה-janitor בודק סימנים קונקרטיים: בדיקות עוברות, קבצים קיימים, lint נקי, טיפוסים נכונים.
4. **מיזוג**. עבודה מאומתת נוחתת ב-main. משימות שנכשלו עוברות ניסיון חוזר או מנותבות למודל אחר.

המתזמר הוא מתזמן Python, לא LLM. החלטות התזמון דטרמיניסטיות, ניתנות לביקורת וניתנות לשחזור.

## הרצה בענן (Cloudflare)

Bernstein יכול להריץ סוכנים על Cloudflare Workers במקום מקומית. ה-CLI של `bernstein cloud` מטפל בפריסה ובמחזור החיים.

- **Workers**. הרצת סוכנים על קצה Cloudflare, עם Durable Workflows למשימות רב-שלביות וניסיון חוזר אוטומטי.
- **בידוד ארגז חול V8**. כל סוכן רץ ב-isolate משלו, ללא תקורה של קונטיינר.
- **סנכרון מרחב עבודה ב-R2**. מצב ה-worktree המקומי מסונכרן לאחסון אובייקטים של R2 כדי שסוכני הענן יראו את אותם הקבצים.
- **Workers AI** (ניסיוני). השתמשו במודלים שמתארחים ב-Cloudflare כספק ה-LLM, ללא צורך במפתחות API חיצוניים.
- **אנליטיקה ב-D1**. מדדי משימות ונתוני עלות נשמרים ב-D1 לצורך שאילתות.
- **Vectorize**. מטמון סמנטי הנתמך על ידי מסד הנתונים הווקטורי של Cloudflare.
- **רינדור דפדפן**. Headless Chrome על Workers עבור סוכנים שצריכים לבחון פלט אינטרנט.
- **תעבורה מרוחקת של MCP**. חשיפה או צריכה של שרתי MCP מעל רשת Cloudflare.

```bash
bernstein cloud login      # authenticate with Bernstein Cloud
bernstein cloud deploy     # push agent workers
bernstein cloud run plan.yaml  # execute a plan on Cloudflare
```

## יכולות

**תזמור ליבה**. הרצה מקבילית, בידוד git worktree, אימות janitor, שערי איכות (lint, טיפוסים, סריקת PII), סקירת קוד צולבת בין מודלים, מאלץ נתיכים (circuit breaker) לסוכנים שמתנהגים לא כשורה, ניטור צמיחת טוקנים עם התערבות אוטומטית.

**אינטליגנציה**. נתב bandit הקשרי לבחירת מודל/מאמץ. גרף ידע לניתוח השפעת קוד. מטמון סמנטי חוסך טוקנים בדפוסים חוזרים. זיהוי חריגות עלות (התראות קצב שריפה). זיהוי חריגות התנהגות עם דגלי Z-score.

**ארגז חול**. פרוטוקול [`SandboxBackend`](../../docs/architecture/sandbox.md) הניתן לתוסף — הריצו סוכנים ב-git worktrees מקומיים (ברירת מחדל), קונטיינרים של Docker, microVMs של Firecracker מבית [E2B](https://e2b.dev), או קונטיינרים serverless של [Modal](https://modal.com) (עם GPU אופציונלי). מחברי תוספים יכולים לרשום backends מותאמים אישית דרך קבוצת נקודות הכניסה `bernstein.sandbox_backends`. בדקו backends מותקנים עם `bernstein agents sandbox-backends`.

**אחסון artifacts**. מצב `.sdd/` יכול לזרום ל-backends הניתנים לתוסף [`ArtifactSink`](../../docs/architecture/storage.md): מערכת קבצים מקומית (ברירת מחדל), S3, Google Cloud Storage, Azure Blob, או Cloudflare R2. `BufferedSink` משמר את חוזה בטיחות הקריסה של ה-WAL על ידי כתיבה מקומית עם fsync תחילה ושיקוף לרחוק באופן אסינכרוני.

**חבילות מיומנויות (Skill packs)**. [מיומנויות](../../docs/architecture/skills.md) בחשיפה הדרגתית (דפוס OpenAI Agents SDK): רק אינדקס מיומנויות קומפקטי נשלח בכל הנחיית מערכת של spawn, סוכנים מושכים את הגוף המלא דרך כלי MCP `load_skill` לפי דרישה. 17 חבילות תפקיד מובנות בתוספת נקודות כניסה `bernstein.skill_sources` של צד שלישי.

**בקרות**. יומני ביקורת משורשרים ב-HMAC, מנוע מדיניות, שיגור פלט PII, התאוששות מקריסה הנתמכת ב-WAL (בטיחות multi-worker ניסיונית), OAuth 2.0 PKCE.

**ניטור (Observability)**. `/metrics` של Prometheus, ערכות יצואן OTel מוכנות, לוחות מחוונים של Grafana. מעקב עלות לכל מודל (`bernstein cost`). TUI טרמינל ולוח מחוונים אינטרנטי. נראות תהליכי הסוכן ב-`ps`.

**אקוסיסטם**. מצב שרת MCP, תמיכה בפרוטוקול A2A, אינטגרציית GitHub App, מערכת תוספים מבוססת pluggy, מרחבי עבודה מרובי-מאגרים, מצב cluster להרצה מבוזרת, התפתחות עצמית דרך `--evolve` (ניסיוני).

מטריצת תכונות מלאה: [FEATURE_MATRIX.md](../../docs/reference/FEATURE_MATRIX.md) &middot; תכונות אחרונות: [מה חדש](../../docs/whats-new.md)

## מה חדש בגרסה v1.9

**גשר ACP** — `bernstein acp serve --stdio` חושף את Bernstein לכל עורך שמדבר Agent Communication Protocol (Zed וכו'). אין צורך בקוד תוסף בצד העורך.

**תיקון CI אוטונומי** — `bernstein autofix` עוקב אחר PR פתוחים של Bernstein, וכאשר ה-CI נצבע אדום, מפעיל סוכן מתקן באופן אוטומטי. ברגע שירוק, הוא דוחף את התיקון ומבקש סקירה מחדש.

**כספת אישורים** — `bernstein connect <provider>` כותב מפתחות API ל-keychain של מערכת ההפעלה; `bernstein creds` מציג ומחליף אותם. סוכנים יורשים אישורים מוגבלי-היקף בלי לגעת במשתני סביבה.

**מנהרות תצוגה מקדימה** — `bernstein preview start` מאתחל שרת פיתוח בארגז חול ומדפיס כתובת URL ציבורית. שימושי לשיתוף ענף רץ עם סוקר בלי לפרוס ל-staging.

יומן שינויים מלא: [docs/whats-new.md](../../docs/whats-new.md)

## פקודות אופרטור

פקודות שמסלקות את קוד הדבק שרוב הצוותים מוצאים את עצמם כותבים סביב ההרצות שלהם.

| Command | מה זה עושה |
|---------|--------------|
| `bernstein pr` | יוצר אוטומטית PR ב-GitHub מסשן שהושלם; הגוף נושא את תוצאות שערי ה-janitor ופירוט עלות בטוקנים/USD. |
| `bernstein from-ticket <url>` | מייבא כרטיס Linear / GitHub Issues / Jira כמשימת Bernstein. הסקת תפקיד ו-scope על בסיס תוויות. תומך ב-`--dry-run` וב-`--run`. |
| `bernstein ticket import <url>` | כינוי / צורת קבוצה של `from-ticket` עבור scripting. |
| `bernstein remote` | SSH sandbox backend. `remote test <host>`, `remote run <host> <path>`, `remote forget <host>`. שימוש חוזר בשקע ControlMaster לקריאות חוזרות מהירות. |
| `bernstein hooks` | hooks למחזור חיים עבור `pre_task`, `post_task`, `pre_merge`, `post_merge`, `pre_spawn`, `post_spawn` — סקריפטי shell או `@hookimpl`s של pluggy. `hooks list`, `hooks run <event>`, `hooks check`. |
| `bernstein chat serve --platform=telegram\|discord\|slack` | הפעילו ריצות מצ'אט עם `/run`, `/status`, `/approve`, `/reject`, `/switch`, `/stop`. |
| `bernstein approve-tool` / `bernstein reject-tool` | אישור אינטראקטיבי של קריאת כלי באמצע ריצה. `--latest`, `--id`, `--always`. |
| `bernstein tunnel start <port> [--provider auto\|cloudflared\|ngrok\|bore\|tailscale]` | עטיפה אחת סביב ארבעה ספקי מנהרה. גם `tunnel list`, `tunnel stop <name>\|--all`. שימוש חוזר בתהליך בסגנון ControlMaster. |
| `bernstein daemon install [--user\|--system] [--command="..."] [--env KEY=VAL]...` | מתקין יחידת systemd (Linux) או launchd (macOS) להפעלה אוטומטית. גם `daemon start/stop/restart/status/uninstall`. |
| `bernstein connect <provider>` / `bernstein creds` | מאחסן ומחליף אישורי API ב-keychain של מערכת ההפעלה. סוכנים יורשים מפתחות מוגבלי-היקף בכל ריצה. |
| `bernstein autofix` | Daemon שעוקב אחר PR פתוחים של Bernstein; מפעיל סוכן מתקן כשה-CI נכשל ודוחף את התיקון אוטומטית. |
| `bernstein preview start` | מתחיל שרת פיתוח בארגז חול עבור הענף הנוכחי ומדפיס כתובת מנהרה ציבורית הניתנת לשיתוף. |

## איך זה משתווה

| תכונה | Bernstein | CrewAI | AutoGen [^autogen] | LangGraph |
|---------|-----------|--------|---------|-----------|
| מתזמר | קוד דטרמיניסטי | מונע LLM (+ Flows בקוד) | מונע LLM | גרף + LLM |
| עובד עם | כל סוכן CLI (43 מתאמים) | מחלקות Python SDK | סוכני Python | צמתי LangChain |
| בידוד Git | Worktrees לכל סוכן | לא | לא | לא |
| ארגזי חול ניתנים לתוסף | Worktree, Docker, E2B, Modal | לא | לא | לא |
| אימות | Janitor + שערי איכות | Guardrails + פלט Pydantic | תנאי סיום | קצוות מותנים |
| מעקב עלות | מובנה | `usage_metrics` | `RequestUsage` | דרך LangSmith |
| מודל מצב | מבוסס קבצים (.sdd/) | בזיכרון + checkpoint של SQLite | בזיכרון | Checkpointer |
| Sinks מרוחקים ל-artifacts | S3, GCS, Azure Blob, R2 | לא | לא | לא |
| התפתחות עצמית | מובנה (ניסיוני) | לא | לא | לא |
| תוכניות הצהרתיות (YAML) | כן | כן (`agents.yaml`, `tasks.yaml`) | לא | חלקי (`langgraph.json`) |
| ניתוב מודל לכל משימה | כן | LLM לכל סוכן | `model_client` לכל סוכן | לכל צומת (ידני) |
| תמיכה ב-MCP | כן (לקוח + שרת) | כן | כן (לקוח + workbench) | כן (לקוח + שרת) |
| צ'אט בין סוכנים | לוח מודעות | כן (תהליך Crew) | כן (צ'אט קבוצתי) | כן (supervisor, swarm) |
| ממשק אינטרנט | TUI + לוח מחוונים אינטרנטי | CrewAI AMP | AutoGen Studio | LangGraph Studio + LangSmith |
| אפשרות אירוח בענן | כן (Cloudflare) | כן (CrewAI AMP) | לא | כן (LangGraph Cloud) |
| RAG/אחזור מובנה | כן (FTS5 של בסיס הקוד + BM25) | `crewai_tools` | `autogen_ext` retrievers | דרך LangChain |

*אומת לאחרונה: 2026-04-19. ראו [דפי השוואה מלאים](../../docs/compare/README.md) למטריצות תכונות מפורטות.*

הטבלה למעלה משווה את Bernstein למסגרות תזמור LLM (הן מתזמרות קריאות LLM). הטבלה למטה מכסה את הקטגוריה הקרובה יותר — כלים אחרים שמתזמרים **סוכני קוד CLI**:

| תכונה | Bernstein | [awslabs/cli-agent-orchestrator](https://github.com/awslabs/cli-agent-orchestrator) | [ComposioHQ/agent-orchestrator](https://github.com/ComposioHQ/agent-orchestrator) | [emdash](https://github.com/generalaction/emdash) | [umputun/ralphex](https://github.com/umputun/ralphex) |
|---------|-----------|-----------|-----------|-----------|-----------|
| צורה | Python CLI + ספרייה + שרת MCP | Python CLI + סשני tmux + ממשק אינטרנט | TypeScript CLI + לוח מחוונים מקומי | אפליקציית שולחן עבודה Electron | Go CLI |
| שפה ראשית | Python | Python | TypeScript | TypeScript | Go |
| התקנה | `pipx install bernstein` | `uv tool install cli-agent-orchestrator` | `npm install -g @aoagents/ao` | `.dmg` / `.msi` / `.AppImage` | `go install` / בינארי יחיד |
| מתאמי סוכנים | 43 | 5 (Kiro, Claude Code, Codex, Gemini, Kimi) | 3 (Claude Code, Codex, Aider) | 24 | 1 (Claude Code בלבד) |
| הרצה מקבילית רב-סוכנית | כן | כן (סשן tmux לכל סוכן) | כן | כן | לא (סשן עוקב יחיד) |
| Git worktree לכל סוכן | כן | לא (מתוכנן, [#100](https://github.com/awslabs/cli-agent-orchestrator/issues/100)) | כן | כן | דגל `--worktree` אופציונלי |
| מצב שרת MCP (חושף את עצמו כ-MCP) | כן (stdio + HTTP/SSE) | כן (תקשורת בין סוכנים) | לא | לא | לא |
| מתאם (Coordinator) | מתזמן Python דטרמיניסטי | מפקח LLM היררכי | מונע LLM | לא מתועד | מבצע תוכנית לינארי |
| הפעלה חוזרת של ביקורת משורשרת ב-HMAC | כן | לא | לא | לא | לא |
| מאמת בין-מודלים / שערי איכות | כן (רב-שלבי) | לא | לא | לא | סקירה רב-שלבית (Claude בלבד) |
| זרימת תיקון CI / PR אוטונומית | כן (`bernstein autofix`) | לא | כן | לא | לא |
| לוח מחוונים חזותי | TUI + אינטרנט | ממשק אינטרנט + tmux | אינטרנט | אפליקציית שולחן עבודה | אינטרנט (`--serve`) |
| Sinks להתראות | Telegram/Slack/Discord/Email/Webhook/Shell | — | לא | לא | Telegram / Email / Slack / Webhook |
| גיבוי | OSS עצמאי | AWS Labs | ממומן (Composio.dev) | YC W26 | OSS עצמאי |
| רישיון | Apache 2.0 | Apache 2.0 | MIT | Apache 2.0 | MIT |

הייחוד של Bernstein בקטגוריה הזו: **Python-native, MCP-server-first, כיסוי המתאמים הרחב ביותר, מקביליות רב-סוכנית אמיתית, מתזמן דטרמיניסטי ללא LLM בלולאת התיאום**. אם אתם רוצים בידוד סשני tmux מותאם AWS עם מפקח LLM היררכי, `cao` של AWS Labs מתאים יותר; אם המחסנית שלכם היא TypeScript ואתם רוצים מוצר עם לוח מחוונים, `@aoagents/ao` של Composio מתאים יותר; אם אתם רוצים ADE שולחני מלוטש, emdash הוא הבחירה; אם אתם משתמשים רק ב-Claude Code ורוצים בינארי Go יחיד שצועד דרך תוכנית מלמעלה למטה, ralphex הוא הבחירה. אם אתם רוצים פרימיטיב שמתייבא ל-Python, חושף את עצמו דרך MCP לכל לקוח, מריץ הרבה סוכנים במקביל, ומכסה את מלוא רוחב הסוכנים (כולל Qwen, Goose, Ollama, OpenAI Agents SDK, Cloudflare Agents ועוד) — Bernstein.

[^autogen]: AutoGen נמצא במצב תחזוקה; היורש הוא Microsoft Agent Framework 1.0.

## ניטור

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

## התקנה

| שיטה | פקודה |
|--------|---------|
| **שורה אחת (macOS / Linux)** | `curl -fsSL https://bernstein.run/install.sh \| sh` |
| **שורה אחת (Windows)** | `irm https://bernstein.run/install.ps1 \| iex` |
| **pip** | `pip install bernstein` |
| **pipx** | `pipx install bernstein` |
| **uv** | `uv tool install bernstein` |
| **Homebrew** | `brew tap chernistry/tap && brew install bernstein` |
| **Fedora / RHEL** | `sudo dnf copr enable alexchernysh/bernstein && sudo dnf install bernstein` |
| **npm** (עטיפה) | `npx bernstein-orchestrator` |

סקריפטי השורה האחת בודקים אם מותקן Python 3.12+, מאתחלים pipx כשהוא חסר, מתקנים PATH לסשן הנוכחי, ומתקינים (או משדרגים) את `bernstein`. הם מטפלים בסביבות macOS מנוהלות ב-brew ובפתרון נפילה לחלוצן Windows `py -3`. מקור הסקריפטים: [install.sh](../../scripts/install.sh) · [install.ps1](../../scripts/install.ps1).

### תוספים אופציונליים

SDKs של ספקים הם אופציונליים כדי שההתקנה הבסיסית תישאר רזה. בחרו את מה שאתם צריכים:

| תוסף | מאפשר |
|-------|---------|
| `bernstein[openai]` | מתאם OpenAI Agents SDK v2 (`openai_agents`) |
| `bernstein[docker]` | Docker sandbox backend |
| `bernstein[e2b]` | [E2B](https://e2b.dev) microVM sandbox backend (דורש `E2B_API_KEY`) |
| `bernstein[modal]` | [Modal](https://modal.com) sandbox backend, GPU אופציונלי (דורש `MODAL_TOKEN_ID` / `MODAL_TOKEN_SECRET`) |
| `bernstein[s3]` | S3 artifact sink (דרך `boto3`) |
| `bernstein[gcs]` | Google Cloud Storage artifact sink |
| `bernstein[azure]` | Azure Blob artifact sink |
| `bernstein[r2]` | Cloudflare R2 artifact sink (תואם S3 דרך `boto3`) |
| `bernstein[grpc]` | גשר gRPC |
| `bernstein[k8s]` | אינטגרציות Kubernetes |

שלבו תוספים בסוגריים, למשל `pip install 'bernstein[openai,docker,s3]'`.

תוספי עורך: [VS Marketplace](https://marketplace.visualstudio.com/items?itemName=alex-chernysh.bernstein) &middot; [Open VSX](https://open-vsx.org/extension/alex-chernysh/bernstein)

## תרומה

PRs מתקבלים בברכה. ראו [CONTRIBUTING.md](../../CONTRIBUTING.md) להגדרה וסגנון קוד.

## תמיכה

אם Bernstein חוסך לכם זמן: [GitHub Sponsors](https://github.com/sponsors/chernistry)

יצירת קשר: [forte@bernstein.run](mailto:forte@bernstein.run)

## הופיע ב

רשימות מובחרות, ניוזלטרים ופרויקטים עמיתים שאספו את Bernstein:

- [**Python Weekly #742**](https://www.pythonweekly.com/p/python-weekly-issue-742-april-23-2026) (23 באפריל 2026) — אזכור בניוזלטר.
- [**Augment Code — 9 Open-Source Agent Orchestrators for AI Coding (2026)**](https://www.augmentcode.com/tools/open-source-agent-orchestrators) — סקירה עריכתית; "הכלי המעניין ביותר מבחינה ארכיטקטונית בסקירה הזו."
- [**nibzard/awesome-agentic-patterns**](https://github.com/nibzard/awesome-agentic-patterns/blob/main/patterns/deterministic-zero-llm-orchestration.md) — Bernstein מצוטט כיישום הייצור של דפוס "תזמור דטרמיניסטי ללא LLM".
- [**Jenqyang/Awesome-AI-Agents**](https://github.com/Jenqyang/Awesome-AI-Agents)
- [**jamesmurdza/awesome-ai-devtools**](https://github.com/jamesmurdza/awesome-ai-devtools)
- [**jim-schwoebel/awesome_ai_agents**](https://github.com/jim-schwoebel/awesome_ai_agents)
- [**Piebald-AI/awesome-gemini-cli**](https://github.com/Piebald-AI/awesome-gemini-cli)
- [**ComposioHQ/awesome-codex-skills**](https://github.com/ComposioHQ/awesome-codex-skills)
- [**jxzhangjhu/Awesome-LLM-RAG**](https://github.com/jxzhangjhu/Awesome-LLM-RAG)
- [**rohitg00/awesome-claude-code-toolkit**](https://github.com/rohitg00/awesome-claude-code-toolkit)
- [**numtide/llm-agents.nix**](https://github.com/numtide/llm-agents.nix) — הפצת Nix flake.

<details>
<summary>רשימות awesome נוספות ואצירה קהילתית</summary>

- [andyrewlee/awesome-agent-orchestrators](https://github.com/andyrewlee/awesome-agent-orchestrators)
- [bradAGI/awesome-cli-coding-agents](https://github.com/bradAGI/awesome-cli-coding-agents)
- [milisp/awesome-codex-cli](https://github.com/milisp/awesome-codex-cli)
- [yaolifeng0629/Awesome-independent-tools](https://github.com/yaolifeng0629/Awesome-independent-tools) (中文 + EN)
- [caramaschiHG/awesome-ai-agents-2026](https://github.com/caramaschiHG/awesome-ai-agents-2026)
- [ai-for-developers/awesome-vibe-coding](https://github.com/ai-for-developers/awesome-vibe-coding)
- [killop/anything_about_game](https://github.com/killop/anything_about_game) (`AI.md`)
- [Glama MCP Catalog](https://glama.ai/mcp/servers/sipyourdrink-ltd/bernstein) — רישום עריכתי של שרת MCP.
- מראות: [icopy-site/awesome](https://github.com/icopy-site/awesome), [icopy-site/awesome-cn](https://github.com/icopy-site/awesome-cn), [trackawesomelist/trackawesomelist](https://github.com/trackawesomelist/trackawesomelist).

</details>

<details>
<summary>צוטט כתקדים על ידי פרויקטים עמיתים</summary>

- [**mkb23/overcode**](https://github.com/mkb23/overcode/blob/main/docs/design/bakeoffs/overcode-vs-bernstein.md) — bakeoff בפורמט ארוך שמתייחס ל-Bernstein כיישום הייחוס.
- [**Vintersong/NOVA-Cognition-Framework**](https://github.com/Vintersong/NOVA-Cognition-Framework) — `BERNSTEIN_PATTERNS.md`, "דפוסים ששווה להשאיל".
- [**AJV009/drupal-contrib-workbench**](https://github.com/AJV009/drupal-contrib-workbench) — הערות מחקר על הפיצול בין מנהל ל-janitor.
- [**danielvaughan/codex-blog**](https://github.com/danielvaughan/codex-blog/blob/main/_posts/2026-04-09-loki-mode-autonomous-execution.md) — מאמר השוואה הממקם את Bernstein בקצה הדטרמיניסטי.

</details>

## היסטוריית כוכבים

[![Star History Chart](https://api.star-history.com/svg?repos=sipyourdrink-ltd/bernstein&type=Date)](https://star-history.com/#sipyourdrink-ltd/bernstein&Date)

## רישיון

[Apache License 2.0](../../LICENSE)

---

נוצר באהבה על ידי [Alex Chernysh](https://alexchernysh.com) &middot; [GitHub](https://github.com/chernistry) &middot; [X](https://x.com/alex_chernysh) &middot; [bernstein.run](https://bernstein.run?utm_source=github.com&utm_medium=readme&utm_campaign=bernstein-readme)

<!-- mcp-name: io.github.sipyourdrink-ltd/bernstein -->

</div>
