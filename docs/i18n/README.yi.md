[English](../../README.md) | [Español (Spanish)](README.es.md) | [中文 (Chinese)](README.zh.md) | [العربية (Arabic)](README.ar.md) | [Português (Portuguese)](README.pt.md) | [Bahasa Indonesia (Indonesian)](README.id.md) | [Français (French)](README.fr.md) | [日本語 (Japanese)](README.ja.md) | [Русский (Russian)](README.ru.md) | [Deutsch (German)](README.de.md) | [עברית (Hebrew)](README.he.md) | **יידיש (Yiddish)**

<div dir="rtl">

<div align="center">

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="../../docs/assets/logo-dark.svg">
  <source media="(prefers-color-scheme: light)" srcset="../../docs/assets/logo-light.svg">
  <img alt="Bernstein" src="../../docs/assets/logo-light.svg" width="340">
</picture>

<br>

> *"כּדי אויסצופֿירן גרויסע זאַכן, דאַרף מען צוויי זאַכן: אַ פּלאַן און נישט אין גאַנצן גענוג צײַט."* — Leonard Bernstein

### דיריגיר אַבי וועלכן AI קאָדירונג־אַגענט. אַבי וועלכן מאָדעל. איין באַפֿעל.

[![CI](https://github.com/sipyourdrink-ltd/bernstein/actions/workflows/ci.yml/badge.svg)](https://github.com/sipyourdrink-ltd/bernstein/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/bernstein)](https://pypi.org/project/bernstein/)
[![Python 3.12+](https://img.shields.io/badge/python-3.12+-3776ab?logo=python&logoColor=white)](https://python.org)
[![License](https://img.shields.io/github/license/sipyourdrink-ltd/bernstein)](../../LICENSE)
[![MseeP.ai](https://img.shields.io/badge/MseeP.ai-verified-2496ed)](https://mseep.ai/app/chernistry-bernstein)

[וועבזײַטל](https://bernstein.run?utm_source=github.com&utm_medium=readme&utm_campaign=bernstein-readme) &middot; [דאָקומענטאַציע](https://bernstein.readthedocs.io/) &middot; [ווי אָנצוהייבן](../../docs/getting-started/GETTING_STARTED.md) &middot; [גלאָסאַר](../../docs/reference/GLOSSARY.md) &middot; [באַגרענעצונגען](../../docs/reference/KNOWN_LIMITATIONS.md)

</div>

---

Bernstein איז אַ דעטערמיניסטישער פּיתאָן־פּלאַנירער; ער לאָזט לויפֿן אַ קרו פֿון CLI קאָדירונג־אַגענטן (Claude Code, Codex, Gemini CLI, און נאָך 40) קעגן איין ציל פּאַראַלעל אין git־worktrees, מיט אַ HMAC־געחתמעטער ביקורת־קייט איבער יעדן טריט.

### אינסטאַלירן אין 30 סעקונדעס

```bash
pipx install bernstein
bernstein init
bernstein run -g "fix the failing test in tests/test_foo.py"
```

### אָנקוקן אין 60 סעקונדעס

דער קליפּ דעקט אַ פֿולן לויף: דער מענעדזשער צעלייגט דעם ציל, דרײַ אַגענטן אַרבעטן פּאַראַלעל, די ביקורת־קייט שרײַבט יעדן handoff, דער janitor באַשטעטיקט, און אַ PR ווערט עפֿנט.

<p align="center">
  <img alt="Bernstein 60־סעקונדן־דעמאָ: דער מענעדזשער צעלייגט דעם ציל, דרײַ אַגענטן לויפֿן פּאַראַלעל, די ביקורת־קייט שרײַבט יעדן handoff, אַ PR ווערט עפֿנט" src="../../docs/demo/demo.gif" width="800">
</p>

נאָך דעם לויף שטעלט Bernstein אַ סטרוקטורירטן קאָמענטאַר אין PR מיט קאָסטן, טעסט־רעזולטאַטן, lineage און די ביקורת־האַש־קייט:

<p align="center">
  <img alt="Bernstein PR קאָמענטאַר: סעקציעס איבערזיכט, קאָסטן, Lineage, טעסטן, ביקורת־קייט" src="../../docs/demo/screenshot-pr-comment.svg" width="720">
</p>

> דער GIF ווערט פּראָדוצירט פֿון [`docs/demo/demo.tape`](../../docs/demo/demo.tape) דורך [vhs](https://github.com/charmbracelet/vhs); רעגענערירט לאָקאַל מיט `vhs docs/demo/demo.tape`.

### ווי עס פֿאַרגלײַכט זיך

| פּראָפּערטיע                                  | Bernstein   | Archon   | LangGraph |
|---------------------------------------------|-------------|----------|-----------|
| מולטי־אַגענט קרו (פּאַראַלעלע אַדאַפּטערס)        | יאָ          | איינער   | יאָ        |
| געחתמעטער lineage / ביקורת־קייט             | יאָ          | ניין     | ניין      |
| Air-gap / סובערענע פּראָדוקציע                | יאָ          | טיילווײַז | ניין      |
| וויזועלע אַרבעט־פֿלוס YAML                    | יאָ [^yaml]  | יאָ       | ניין      |
| געהאָסטעטער דאַשבאָרד / SaaS                  | ניין        | טיילווײַז | ניין      |

[^yaml]: אַרבעט־פֿלוס YAML שטיצונג קומט מיט [PR #1108](https://github.com/sipyourdrink-ltd/bernstein/pull/1108) (אין דעם דאָזיקן באַטש). ביז דעמאָלט שרײַבט מען די פּלאַנען אין פּיתאָן אָדער דורך `bernstein run plan.yaml` קעגן דער אַלטער סכעמע.

אַ לענגערע פֿעיִקייט־מאַטריצע קעגן CrewAI, AutoGen, LangGraph, און די פֿיר CLI־אַגענט־דיריגענטן וואָס זײַנען אין דער זעלבער קאַטעגאָריע ווי Bernstein וווינט אין דעם אָפּשניט [פּרטימדיקער פֿאַרגלײַך](#detailed-comparison) למטה.

---

### וואָס איז דאָס, אין איין פּאַראַגראַף

איר זאָגט Bernstein וואָס איר ווילט זאָל געבויט ווערן. ער צעטיילט די אַרבעט צווישן עטלעכע AI קאָדירונג־אַגענטן, לאָזט זיי לויפֿן פּאַראַלעל אין איזאָלירטע git־worktrees, שרײַבט יעדן handoff אין אַ HMAC־קייט־ביקורת־לאָג, לויפֿט די טעסטן און פֿאַראייניקט דעם קאָד וואָס פֿאַקטיש גייט דורך. איר קומט צוריק צו אַ גרינעם PR.

Forward-deployed engineering, אין שוואָרעם־פֿאָרעם. שטעלט Bernstein אין אַ קונה־רעפּאָ און איר האָט אַ מולטי־אַגענט קרו מיט שטאַט אין פֿײַלן, אַגענט־ווײַזער קרעדענציאַל־סקאָוּפּ און אַ געחתמעטן audit trail, וואָס לויפֿט איבער די CLI־אַגענטן וואָס דער קונה האָט שוין געטרויט.

### אַנדערע אינסטאַלאַציע־אופֿנים

```bash
curl -fsSL https://bernstein.run/install.sh | sh        # macOS / Linux איין־שורה־אופֿן
irm https://bernstein.run/install.ps1 | iex             # Windows PowerShell
pip install bernstein                                   # pip
uv tool install bernstein                               # uv
brew tap chernistry/tap && brew install bernstein       # Homebrew
```

זעט די פֿולשטענדיקע [אינסטאַלאַציע־מאַטריצע](#install) פֿאַר `dnf copr`, `npx`, אָפּציאָנעלע extras, און דעם wheelhouse־וועג פֿאַר air-gap־פּלעצער.

### פֿאַר וואָס דער פּלאַנירער איז פּשוט פּיתאָן

די מערהייט פֿון אַגענט־דיריגענטן ניצן אַן LLM צו באַשליסן ווער טוט וואָס. דאָס איז נישט־דעטערמיניסטיש און פֿאַרברענט טאָקענס אויף פּלאַנירן אַנשטאָט אויף קאָד. Bernstein טוט איין LLM־רוף צו צעלייגן אײַער ציל, און דערנאָך דאָס איבעריקע (אָפּלויפֿן אַגענטן פּאַראַלעל, איזאָלירן זייערע git־צווײַגן, לויפֿן טעסטן, רוטירן ווידערהאָלונגען) איז פּשוט פּיתאָן. יעדער לויף איז ריפּראָדוצירבאַר. יעדער טריט איז פֿאַרשריבן און מע קען אים נאָכשפּילן.

קיין פֿרײַמווערק נישט צו לערנען. קיין פֿאַרקויפֿער־פֿאַרשפּערונג נישט. בײַט אַבי וועלכן אַגענט, אַבי וועלכן מאָדעל, אַבי וועלכן שפּײַזער.

<img alt="Bernstein בײַ דער אַרבעט: פּאַראַלעלע AI־אַגענטן דיריגירט אין עכט־צײַט" src="../../docs/assets/in-action-small.gif" width="700">

וואָס איר זעט בעת ער לויפֿט:

```
$ bernstein -g "Add JWT auth"
[manager] decomposed into 4 tasks
[agent-1] claude-sonnet: src/auth/middleware.py  (done, 2m 14s)
[agent-2] codex:         tests/test_auth.py      (done, 1m 58s)
[verify]  all gates pass. merging to main.
```

## געשטיצטע אַגענטן

Bernstein געפֿינט אויטאָמאַטיש די אינסטאַלירטע CLI־אַגענטן. מישט זיי אין דעם זעלביקן לויף. ביליקע לאָקאַלע מאָדעלן פֿאַר שאַבלאָנען, שווערערע וואָלקן־מאָדעלן פֿאַר אַרכיטעקטור.

43 CLI־אַגענט אַדאַפּטערס: 40 דריט־צד אַרומכאַפּערס פּלוס אַ גענערישער אַרומכאַפּער פֿאַר אַבי וואָס מיט `--prompt`.

| Agent | מאָדעלן | אינסטאַלאַציע |
|-------|--------|---------|
| [Claude Code](https://docs.anthropic.com/en/docs/claude-code) | Opus 4, Sonnet 4.6, Haiku 4.5 | `npm install -g @anthropic-ai/claude-code` |
| [Codex CLI](https://github.com/openai/codex) | GPT-5, GPT-5 mini | `npm install -g @openai/codex` |
| [OpenAI Agents SDK v2](https://openai.github.io/openai-agents-python/) | GPT-5, GPT-5 mini, o4 | `pip install 'bernstein[openai]'` |
| [GitHub Copilot CLI](https://docs.github.com/en/copilot/github-copilot-in-the-cli) | Copilot־געפֿירט (GPT-5, Sonnet 4.6) | `npm install -g @github/copilot` |
| [Gemini CLI](https://github.com/google-gemini/gemini-cli) | Gemini 2.5 Pro, Gemini Flash | `npm install -g @google/gemini-cli` |
| [Cursor](https://www.cursor.com) | Sonnet 4.6, Opus 4, GPT-5 | [Cursor אַפּ](https://www.cursor.com) |
| [Aider](https://aider.chat) | אַבי וואָס OpenAI/Anthropic־קאָמפּאַטיבל | `pip install aider-chat` |
| [Amp](https://ampcode.com) | Amp־געפֿירט | `npm install -g @sourcegraph/amp` |
| [Cody](https://sourcegraph.com/cody) | Sourcegraph־געהאָסטעט | `npm install -g @sourcegraph/cody` |
| [Continue](https://continue.dev) | אַבי וואָס OpenAI/Anthropic־קאָמפּאַטיבל | `npm install -g @continuedev/cli` (בינאַר: `cn`) |
| [Goose](https://block.github.io/goose/) | אַבי וועלכן שפּײַזער Goose שטיצט | זעט [Goose דאָקומענטאַציע](https://block.github.io/goose/) |
| [IaC](https://www.terraform.io/) (Terraform/Pulumi) | אַבי וועלכן שפּײַזער דער באַזיס־אַגענט ניצט | אײַנגעבויט |
| [Kilo](https://kilo.dev) | Kilo־געהאָסטעט | זעט [Kilo דאָקומענטאַציע](https://kilo.dev) |
| [Kiro](https://kiro.dev) | Kiro־געהאָסטעט | זעט [Kiro דאָקומענטאַציע](https://kiro.dev) |
| [Ollama](https://ollama.ai) + Aider | לאָקאַלע מאָדעלן (אָפֿלײַן) | `brew install ollama` |
| [OpenCode](https://opencode.ai) | אַבי וועלכן שפּײַזער OpenCode שטיצט | זעט [OpenCode דאָקומענטאַציע](https://opencode.ai) |
| [Qwen](https://github.com/QwenLM/qwen-code) | Qwen Code מאָדעלן | `npm install -g @qwen-code/qwen-code` |
| [Cloudflare Agents](https://developers.cloudflare.com/agents/) | Workers AI מאָדעלן | `bernstein cloud login` |
| [OpenHands](https://github.com/OpenHands/OpenHands) | אַבי וואָס LiteLLM־געשטיצט (Anthropic, OpenAI, ...) | `uv tool install openhands --python 3.12` |
| [Open Interpreter](https://github.com/OpenInterpreter/open-interpreter) | אַבי וואָס (LiteLLM־געשטיצט) | `pip install open-interpreter` |
| [gptme](https://github.com/gptme/gptme) | Anthropic, OpenAI, OpenRouter | `pipx install gptme` |
| [Plandex](https://github.com/plandex-ai/plandex) | Plandex Cloud אָדער זעלבסט־געהאָסטעטע מאָדעלן | `curl -sL https://plandex.ai/install.sh \| bash` |
| [AIChat](https://github.com/sigoden/aichat) | OpenAI, Anthropic, OpenRouter, Groq, Gemini | `cargo install aichat` |
| [Letta Code](https://github.com/letta-ai/letta-code) | Letta־רוטירט (Anthropic, OpenAI) | `npm install -g @letta-ai/letta-code` |
| **גענעריש** | אַבי וועלכער CLI מיט `--prompt` | אײַנגעבויט |

#### דעלעגאַציע פֿון אָרקעסטראַטאָר (בלאַט־קנופּ)

אַ באַזונדערע, קלענערע קלאַס פֿון אַדאַפּטערס וואָס אַרומכאַפּן **אַנדערע CLI אָרקעסטראַטאָרס** ווי ווען זיי וואָלטן געווען איינצלנע אַגענטן. Bernstein גיט דעם אַרומגעכאַפּטן געצײַג אַ פּראָמפּט אָדער פּלאַן און זעט בלויז דעם לעצטן ענד־קאָד; די קאָסטן און קוואַליטעט־טויערן פֿון די אונטער־אַגענטן אינעווייניק אינעם אַרומגעכאַפּטן אָרקעסטראַטאָר זענען נישט זעעוודיק פֿאַר Bernstein. ניצלעך ווען איר ווילט אַרײַנשטעלן אַן עקזיסטירנדיקן וואָרקפֿלאָו געבויט אויף איינעם פֿון די געצײַגן אין אַ טריט פֿון אַ גרעסערן Bernstein־פּלאַן.

| אָרקעסטראַטאָר | אַרומגעכאַפּט ווי | אינסטאַלאַציע |
|--------------|------------|---------|
| [Composio Agent Orchestrator](https://github.com/ComposioHQ/agent-orchestrator) (`@aoagents/ao`) | `composio` | `npm install -g @aoagents/ao` |
| [umputun/ralphex](https://github.com/umputun/ralphex) | `ralphex` | `go install github.com/umputun/ralphex/cmd/ralphex@latest` |

אַבי וועלכער אַדאַפּטער אַרבעט אויך ווי דער **אינערלעכער שעדולער־LLM**. לויפֿט דעם גאַנצן סטעק אָן אַבי וועלכן ספּעציפֿישן שפּײַזער:

```yaml
internal_llm_provider: gemini            # or qwen, ollama, codex, goose, ...
internal_llm_model: gemini-3.1-pro
```

> [!TIP]
> לויפֿט `bernstein --headless` פֿאַר CI־פּײַפּלײַנס. קיין TUI נישט, סטרוקטורירטער JSON־אויסגאַנג, נישט־נול ענד־קאָד אויף דורכפֿאַל.

## גיכער אָנהייב

```bash
cd your-project
bernstein init                    # שאַפֿט .sdd/ אַרבעטס־סבֿיבֿה + bernstein.yaml
bernstein -g "Add rate limiting"  # אַגענטן גײען אַרויס, אַרבעטן פּאַראַלעל, פֿאַרזיכערן, גײען אַרויס
bernstein live                    # קוקט אויפֿן פּראָגרעס אינעם TUI דאַשבאָרד
bernstein stop                    # שטילע אָפּשטעלונג מיט אויסליידיקונג
```

פֿאַר רב־סטאַדיע פּראָיעקטן, דעפֿינירט אַ YAML־פּלאַן:

```bash
bernstein run plan.yaml           # שפּרינגט איבער LLM־פּלאַנירן, גײט גלײַך צו אויספֿירן
bernstein run --dry-run plan.yaml # פֿאָרויס־קוקן אויף אויפֿגאַבן און אָפּגעשאַצטע קאָסטן
```

## ווי עס אַרבעט

1. **צעלייגן**. דער מענעדזשער צעלייגט אײַער ציל אין אויפֿגאַבן מיט ראָלן, באַזיצנדיקע פֿײַלן, און פֿאַרענדיקונג־סיגנאַלן.
2. **אַרויסגיין**. אַגענטן הייבן אָן אין איזאָלירטע git־וואָרקטריז, איינער פּער אויפֿגאַבע. די הויפּט־צווײַג בלײַבט ריין.
3. **פֿאַרזיכערן**. דער דזשאַניטאָר קוקט אויף קאָנקרעטע סיגנאַלן: טעסטן גײען דורך, פֿײַלן עקזיסטירן, lint ריין, טיפּן ריכטיק.
4. **פֿאַראייניקן**. פֿאַרזיכערטע אַרבעט לאַנדעט אין main. דורכגעפֿאַלענע אויפֿגאַבן ווערן ווידערגעטאָן אָדער רוטירט צו אַן אַנדער מאָדעל.

דער אָרקעסטראַטאָר איז אַ פּיתאָן־שעדולער, נישט קיין LLM. שעדול־באַשלוסן זענען דעטערמיניסטיש, אוידיטאַבל און ריפּראָדוצירבאַר.

## וואָלקן־אויספֿירונג (Cloudflare)

Bernstein קען לויפֿן אַגענטן אויף Cloudflare Workers אַנשטאָט לאָקאַל. די `bernstein cloud` CLI האַנדלט מיט אויסגעבונג און לעבן־ציקל.

- **Workers**. אַגענט־אויספֿירונג אויף Cloudflare'ס ראַנד, מיט Durable Workflows פֿאַר רב־טריט אויפֿגאַבן און אויטאָמאַטישע ווידערהאָלונג.
- **V8 סענדבאָקס־איזאָלאַציע**. יעדער אַגענט לויפֿט אין זײַן אייגענעם איזאָלאַט, אָן קאָנטיינער־איבערקאָפּ.
- **R2 אַרבעטס־סבֿיבֿה־סינכראָניזירונג**. לאָקאַלער וואָרקטרי־סטאַט סינכראָניזירט זיך מיט R2 אָבּיעקט־אָפּשפּאָר אַזוי אַז וואָלקן־אַגענטן זעען די זעלביקע פֿײַלן.
- **Workers AI** (עקספּערימענטאַל). ניצט Cloudflare־געהאָסטעטע מאָדעלן ווי דעם LLM־שפּײַזער, קיין דרויסנדיקע API־שליסלען נישט נייטיק.
- **D1 אַנאַליטיק**. אויפֿגאַבע־מעטריקן און קאָסט־דאַטן אויפֿגעהיט אין D1 פֿאַר אָנפֿרעגן.
- **Vectorize**. סעמאַנטישער קעש געשטיצט פֿון Cloudflare'ס וועקטאָר־דאַטנבאַזע.
- **בראָוזער־רענדערונג**. אַ headless Chrome אויף Workers פֿאַר אַגענטן וואָס דאַרפֿן באַטראַכטן וועב־אויסגאַנג.
- **MCP־ווײַטער טראַנספּאָרט**. אַרויסגעבן אָדער קאָנסומירן MCP־סערווערס איבער Cloudflare'ס נעץ.

```bash
bernstein cloud login      # אויטענטיפֿיצירן זיך מיט Bernstein Cloud
bernstein cloud deploy     # שטופּן אַגענט־וואָרקערס
bernstein cloud run plan.yaml  # אויספֿירן אַ פּלאַן אויף Cloudflare
```

## פֿעיִקייטן

**הויפּט־אָרקעסטראַציע**. פּאַראַלעלע אויספֿירונג, git־וואָרקטרי איזאָלאַציע, דזשאַניטאָר־פֿאַרזיכערונג, קוואַליטעט־טויערן (lint, טיפּן, PII־סקאַן), קרייץ־מאָדעל קאָד־רעצענזיע, סירקיט־ברעקער פֿאַר זיך נישט פֿירנדיקע אַגענטן, טאָקען־וווּקס מאָניטאָרינג מיט אויטאָמאַטישער אַרײַנמישונג.

**אינטעליגענץ**. קאָנטעקסטועלער באַנדיט־רוטער פֿאַר מאָדעל/אָנשטרענגונג־אויסקלײַב. וויסן־גראַף פֿאַר קאָד־באַזע השפּעה־אַנאַליז. סעמאַנטישער קעש שפּאָרט טאָקענס אויף ווידערהאָלטע מוסטערן. קאָסטן־אַנאָמאַליע־דעטעקציע (ברען־ראַטע אַלערטס). אויפֿפֿירונג־אַנאָמאַליע־דעטעקציע מיט Z־סקאָר־פֿאָנדל.

**סענדבאָקסירונג**. אַ אײַנשטעקבאַר [`SandboxBackend`](../../docs/architecture/sandbox.md) פּראָטאָקאָל — לויפֿט אַגענטן אין לאָקאַלע git־וואָרקטריז (פֿעלעריק), Docker־קאָנטיינערס, [E2B](https://e2b.dev) Firecracker מיקראָ־VMs, אָדער [Modal](https://modal.com) סערווערלעסע קאָנטיינערס (מיט אַ ברירה־GPU). פּלאַגין־מחברים קענען רעגיסטרירן אייגענע באַקענדס דורך דער `bernstein.sandbox_backends` אַרײַנגאַנג־פּונקט גרופּע. באַטראַכט אינסטאַלירטע באַקענדס מיט `bernstein agents sandbox-backends`.

**אַרטיפֿאַקט־אָפּשפּאָר**. `.sdd/` סטאַט קען שטראָמען צו אײַנשטעקבאַר [`ArtifactSink`](../../docs/architecture/storage.md) באַקענדס: לאָקאַלער פֿײַל־סיסטעם (פֿעלעריק), S3, Google Cloud Storage, Azure Blob, אָדער Cloudflare R2. `BufferedSink` האַלט דעם WAL־קראַש־זיכערקייט קאָנטראַקט דורך שרײַבן לאָקאַל מיט fsync ערשט און שפּיגלען צום ווײַטן אַסינכראָנאָוס.

**Skill Packs**. פּראָגרעסיווער־אַנטפּלעקונג [skills](../../docs/architecture/skills.md) (OpenAI Agents SDK מוסטער): בלויז אַ קאָמפּאַקטער skill־אינדעקס פֿאָרט אין יעדן אַרויסגאַנג'ס סיסטעם־פּראָמפּט, אַגענטן ציִען די פֿולע גופֿן דורך דעם `load_skill` MCP־געצײַג ווען מע פֿאַרלאַנגט. 17 אײַנגעבויטע ראָל־פּעקלעך פּלוס דריט־צד `bernstein.skill_sources` אַרײַנגאַנג־פּונקטן.

**קאָנטראָלן**. HMAC־קייטעוודיקע אוידיט־לאָגן, פּאָליסי־מאָטאָר, PII־אויסגאַנג־טויער, WAL־געשטיצטע קראַש־געווינונג (עקספּערימענטאַלע רב־וואָרקער זיכערקייט), OAuth 2.0 PKCE.

**באַאָבאַכטונג**. Prometheus `/metrics`, OTel־עקספּאָרטער פּרעסעטן, Grafana־דאַשבאָרדן. פּער־מאָדעל קאָסטן־אָנפֿירן (`bernstein cost`). טערמינאַל TUI און וועב־דאַשבאָרד. אַגענט־פּראָצעס זעעוודיקייט אין `ps`.

**עקאָסיסטעם**. MCP־סערווער מאָדע, A2A פּראָטאָקאָל־שטיצע, GitHub App אינטעגראַציע, pluggy־באַזירטער פּלאַגין־סיסטעם, רב־רעפּאָ אַרבעטס־סבֿיבֿות, קלאַסטער־מאָדע פֿאַר דיסטריבוטירטער אויספֿירונג, זעלבסט־עוואָלוציע דורך `--evolve` (עקספּערימענטאַל).

פֿולע פֿעיִקייט־מאַטריץ: [FEATURE_MATRIX.md](../../docs/reference/FEATURE_MATRIX.md) &middot; לעצטיקע פֿעיִקייטן: [וואָס איז נײַ](../../docs/whats-new.md)

## וואָס איז נײַ אין v1.9

**ACP־בריק** — `bernstein acp serve --stdio` שטעלט אַרויס Bernstein פֿאַר אַבי וועלכן רעדאַקטאָר וואָס רעדט דעם Agent Communication Protocol (Zed, אאַז''וו). קיין פּלאַגין־קאָד נישט נייטיק אויף דעם רעדאַקטאָר־צד.

**אויטאָנאָמע CI־רעפּאַראַטור** — `bernstein autofix` קוקט אויף אָפֿענע Bernstein־PR'ס און, ווען CI ווערט רויט, גיט אויטאָמאַטיש אַרויס אַ ריכטערס־אַגענט. ווען עס ווערט גרין, שטופּט עס דעם פֿיקס און בעט נאָך אַ רעצענזיע.

**אַקרעדיטיוו־קאַסע** — `bernstein connect <provider>` שרײַבט API־שליסלען צום OS־שליסל־קייט; `bernstein creds` רעכנט אויף און ראָטירט זיי. אַגענטן ירשענען באַגרענעצטע אַקרעדיטיוון אָן צו רירן סבֿיבֿה־וואַריאַבלען.

**פֿאָרויס־קוקן טונעלן** — `bernstein preview start` הייבט אָן אַ סענדבאָקסירטן דעוו־סערווער און דרוקט אַ פֿאַרעפֿנטלעכטן URL. ניצלעך פֿאַר טיילן אַ לויפֿנדיקע צווײַג מיט אַ רעצענזענט אָן צו דעפּלאָיען צו סטיידזשינג.

פֿולער טשיינדזשלאָג: [docs/whats-new.md](../../docs/whats-new.md)

## אָפּעראַטאָר־באַפֿעלן

באַפֿעלן וואָס עלימינירן דעם קלעב־קאָד וואָס די מערהייט מאַנשאַפֿטן ענדיקן זיך שרײַבן אַרום זייערע לויפֿן.

| באַפֿעל | וואָס עס טוט |
|---------|--------------|
| `bernstein pr` | שאַפֿט אויטאָמאַטיש אַ GitHub־PR פֿון אַ פֿאַרענדיקטער סעסיע; דער גוף טראָגט די דזשאַניטאָר־טויער־רעזולטאַטן און טאָקען/USD קאָסט־איבערשפּלוטערונג. |
| `bernstein from-ticket <url>` | אימפּאָרטירט אַ Linear / GitHub Issues / Jira טיקעט ווי אַ Bernstein־אויפֿגאַבע. לאַבעלאוו־באַזירטע ראָל + סקאָפּ אינפֿערענץ. שטיצט `--dry-run` און `--run`. |
| `bernstein ticket import <url>` | אַליאַס / גרופּע פֿאָרעם פֿון `from-ticket` פֿאַר סקריפּטעווען. |
| `bernstein remote` | SSH סענדבאָקס־באַקענד. `remote test <host>`, `remote run <host> <path>`, `remote forget <host>`. ControlMaster־סאָקעט־איבערגעניץ פֿאַר גיכע איבערחזרהדיקע רופֿן. |
| `bernstein hooks` | לעבן־ציקל הוקס פֿאַר `pre_task`, `post_task`, `pre_merge`, `post_merge`, `pre_spawn`, `post_spawn` — שעל־סקריפּטן אָדער pluggy `@hookimpl`'ן. `hooks list`, `hooks run <event>`, `hooks check`. |
| `bernstein chat serve --platform=telegram\|discord\|slack` | פֿירט לויפֿן פֿון טשאַט מיט `/run`, `/status`, `/approve`, `/reject`, `/switch`, `/stop`. |
| `bernstein approve-tool` / `bernstein reject-tool` | אינטעראַקטיווע מיטן־לויף געצײַג־רוף באַשטעטיקונג. `--latest`, `--id`, `--always`. |
| `bernstein tunnel start <port> [--provider auto\|cloudflared\|ngrok\|bore\|tailscale]` | איין אַרומכאַפּער אַרום פֿיר טונעל־שפּײַזערס. אויך `tunnel list`, `tunnel stop <name>\|--all`. ControlMaster־שטייגער פּראָצעס־איבערגעניץ. |
| `bernstein daemon install [--user\|--system] [--command="..."] [--env KEY=VAL]...` | אינסטאַלירט אַ systemd (Linux) אָדער launchd (macOS) אייניקייט פֿאַר אויטאָ־אָנהייב. אויך `daemon start/stop/restart/status/uninstall`. |
| `bernstein connect <provider>` / `bernstein creds` | אָפּשפּאָרט און ראָטירט API־אַקרעדיטיוון אינעם OS־שליסל־קייט. אַגענטן ירשענען באַגרענעצטע שליסלען פּער־לויף. |
| `bernstein autofix` | דעמאָן וואָס מאָניטאָרט אָפֿענע Bernstein־PR'ס; גיט אַרויס אַ ריכטערס־אַגענט ווען CI דורכפֿאַלט און שטופּט די רעפּאַראַטור אויטאָמאַטיש. |
| `bernstein preview start` | הייבט אָן אַ סענדבאָקסירטן דעוו־סערווער פֿאַר דער איצטיקער צווײַג און דרוקט אַ טיילבאַרן פֿאַרעפֿנטלעכטן טונעל־URL. |

## ווי עס פֿאַרגלײַכט זיך

| פֿעיִקייט | Bernstein | CrewAI | AutoGen [^autogen] | LangGraph |
|---------|-----------|--------|---------|-----------|
| אָרקעסטראַטאָר | דעטערמיניסטישער קאָד | LLM־געפֿירט (+ קאָד Flows) | LLM־געפֿירט | גראַף + LLM |
| אַרבעט מיט | אַבי וועלכן CLI־אַגענט (43 אַדאַפּטערס) | פּיתאָן SDK־קלאַסן | פּיתאָן־אַגענטן | LangChain־קנופּן |
| Git־איזאָלאַציע | וואָרקטריז פּער אַגענט | ניין | ניין | ניין |
| אײַנשטעקבאַרע סענדבאָקסעס | Worktree, Docker, E2B, Modal | ניין | ניין | ניין |
| פֿאַרזיכערונג | דזשאַניטאָר + קוואַליטעט־טויערן | Guardrails + Pydantic־אויסגאַנג | פֿאַרענדיקונג־באַדינגונגען | באַדינגלעכע קאַנטן |
| קאָסטן־אָנפֿירן | אײַנגעבויט | `usage_metrics` | `RequestUsage` | דורך LangSmith |
| סטאַט־מאָדעל | פֿײַל־באַזירט (.sdd/) | אין־זיכּרון + SQLite־טשעקפּאָינט | אין־זיכּרון | טשעקפּאָינטער |
| ווײַטע אַרטיפֿאַקט־אָפּשפּאָרן | S3, GCS, Azure Blob, R2 | ניין | ניין | ניין |
| זעלבסט־עוואָלוציע | אײַנגעבויט (עקספּערימענטאַל) | ניין | ניין | ניין |
| דעקלאַראַטיווע פּלענער (YAML) | יאָ | יאָ (`agents.yaml`, `tasks.yaml`) | ניין | טיילווײַז (`langgraph.json`) |
| מאָדעל־רוטירונג פּער אויפֿגאַבע | יאָ | פּער־אַגענט LLM | פּער־אַגענט `model_client` | פּער־קנופּ (האַנט) |
| MCP־שטיצע | יאָ (קליענט + סערווער) | יאָ | יאָ (קליענט + וואָרקבענטש) | יאָ (קליענט + סערווער) |
| אַגענט־צו־אַגענט טשאַט | בולעטין־ברעט | יאָ (Crew־פּראָצעס) | יאָ (גרופּע־טשאַט) | יאָ (סופּערווײַזער, סוואָרם) |
| וועב UI | TUI + וועב־דאַשבאָרד | CrewAI AMP | AutoGen Studio | LangGraph Studio + LangSmith |
| וואָלקן־געהאָסטעטע אָפּציע | יאָ (Cloudflare) | יאָ (CrewAI AMP) | ניין | יאָ (LangGraph Cloud) |
| אײַנגעבויטער RAG/אַרויסשלעפּונג | יאָ (קאָד־באַזע FTS5 + BM25) | `crewai_tools` | `autogen_ext` אַרויסשלעפּערס | דורך LangChain |

*לעצט פֿאַרזיכערט: 2026-04-19. זעט [פֿולע פֿאַרגלײַך־בלעטער](../../docs/compare/README.md) פֿאַר דעטאַלירטע פֿעיִקייט־מאַטריצן.*

די טאַבעלע אויבן פֿאַרגלײַכט Bernstein מיט LLM־אָרקעסטראַציע פֿרײַמווערקס (זיי אָרקעסטרירן LLM־רופֿן). די טאַבעלע אונטן דעקט די נעענטערע קאַטעגאָריע — אַנדערע געצײַגן וואָס אָרקעסטרירן **CLI קאָדירונג־אַגענטן**:

| פֿעיִקייט | Bernstein | [awslabs/cli-agent-orchestrator](https://github.com/awslabs/cli-agent-orchestrator) | [ComposioHQ/agent-orchestrator](https://github.com/ComposioHQ/agent-orchestrator) | [emdash](https://github.com/generalaction/emdash) | [umputun/ralphex](https://github.com/umputun/ralphex) |
|---------|-----------|-----------|-----------|-----------|-----------|
| פֿאָרעם | פּיתאָן CLI + ביבליאָטעק + MCP־סערווער | פּיתאָן CLI + tmux־סעסיעס + וועב UI | TypeScript CLI + לאָקאַלער דאַשבאָרד | Electron־דעסקטאָפּ אַפּ | Go CLI |
| הויפּט־שפּראַך | פּיתאָן | פּיתאָן | TypeScript | TypeScript | Go |
| אינסטאַלאַציע | `pipx install bernstein` | `uv tool install cli-agent-orchestrator` | `npm install -g @aoagents/ao` | `.dmg` / `.msi` / `.AppImage` | `go install` / איין בינאַר |
| אַגענט־אַדאַפּטערס | 43 | 5 (Kiro, Claude Code, Codex, Gemini, Kimi) | 3 (Claude Code, Codex, Aider) | 24 | 1 (Claude Code בלויז) |
| פּאַראַלעלע רב־אַגענט אויספֿירונג | יאָ | יאָ (tmux־סעסיע פּער אַגענט) | יאָ | יאָ | ניין (איינציקע סעקווענציעלע סעסיע) |
| Git־וואָרקטרי פּער אַגענט | יאָ | ניין (פּלאַנירט, [#100](https://github.com/awslabs/cli-agent-orchestrator/issues/100)) | יאָ | יאָ | ברירה־`--worktree` פֿלאַג |
| MCP־סערווער מאָדע (שטעלט אַרויס זיך ווי MCP) | יאָ (stdio + HTTP/SSE) | יאָ (אינטער־אַגענט קאָמסן) | ניין | ניין | ניין |
| קאָאָרדינאַטאָר | דעטערמיניסטישער פּיתאָן־שעדולער | היעראַרכישער LLM־סופּערווײַזער | LLM־געפֿירט | נישט דאָקומענטירט | ליניאַרער פּלאַן־עקזעקוטאָר |
| HMAC־קייטעוודיק אוידיט־ווידערשפּילן | יאָ | ניין | ניין | ניין | ניין |
| קרייץ־מאָדעל פֿאַרזיכערער / קוואַליטעט־טויערן | יאָ (רב־סטאַדיע) | ניין | ניין | ניין | רב־פֿאַזע רעצענזיע (Claude בלויז) |
| אויטאָנאָמער CI־פֿיקס / PR־פֿלאָו | יאָ (`bernstein autofix`) | ניין | יאָ | ניין | ניין |
| ווײַזעוודיקער דאַשבאָרד | TUI + וועב | וועב UI + tmux | וועב | דעסקטאָפּ אַפּ | וועב (`--serve`) |
| באַנאָכריכטיקונג־אָפּשפּאָרן | Telegram/Slack/Discord/Email/Webhook/Shell | — | ניין | ניין | Telegram / Email / Slack / Webhook |
| שטיצע | אַליין OSS | AWS Labs | פֿינאַנצירט (Composio.dev) | YC W26 | אַליין OSS |
| ליצענץ | Apache 2.0 | Apache 2.0 | MIT | Apache 2.0 | MIT |

Bernstein'ס שטעך אין דער קאַטעגאָריע: **פּיתאָן־געבוירן, MCP־סערווער־ערשט, ברייטסטע אַדאַפּטער־דעקונג, אמתע רב־אַגענט פּאַראַלעליזם, דעטערמיניסטישער שעדולער אָן אַן LLM אינעם קאָאָרדינאַציע־לופּ**. אויב איר ווילט AWS־אויסגעריכטעטע tmux־סעסיע איזאָלאַציע מיט אַ היעראַרכישן LLM־סופּערווײַזער, איז AWS Labs' `cao` אַ נעענטערער אָנפּאַס; אויב אײַער סטעק איז TypeScript און איר ווילט אַ פּראָדוקט מיט אַ דאַשבאָרד, איז Composio'ס `@aoagents/ao` אַ בעסערער אָנפּאַס; אויב איר ווילט אַ אויסגעפּוצטע דעסקטאָפּ ADE, איז עס emdash; אויב איר ניצט בלויז Claude Code און ווילט אַן איין Go־בינאַר וואָס גייט דורך אַ פּלאַן פֿון אויבן ביז אונטן, איז עס ralphex. אויב איר ווילט אַ פּרימיטיוו וואָס אימפּאָרטירט זיך אין פּיתאָן, שטעלט זיך אַרויס איבער MCP פֿאַר אַבי וועלכן קליענט, לויפֿט פֿיל אַגענטן פּאַראַלעל, און דעקט די פֿולע אַגענט־ברייט (אַרײַנגערעכנט Qwen, Goose, Ollama, OpenAI Agents SDK, Cloudflare Agents, און נאָך) — Bernstein.

[^autogen]: AutoGen איז אין אויסהאַלט־מאָדע; דער יורש איז Microsoft Agent Framework 1.0.

## מאָניטאָרינג

```bash
bernstein live       # TUI דאַשבאָרד
bernstein dashboard  # וועב דאַשבאָרד
bernstein status     # אויפֿגאַבע־קיצור
bernstein ps         # לויפֿנדיקע אַגענטן
bernstein cost       # אויסגאַבן לויט מאָדעל/אויפֿגאַבע
bernstein doctor     # פֿאָרויס־פֿלי טשעקס
bernstein recap      # נאָכן־לויף קיצור
bernstein trace <ID> # אַגענט־באַשלוס שפּור
bernstein run-changelog --hours 48  # טשיינדזשלאָג פֿון אַגענט־געשאַפֿענע diffs
bernstein explain <cmd>  # דעטאַלירטע הילף מיט בײַשפּילן
bernstein dry-run    # פֿאָרויס־קוקן אויף אויפֿגאַבן אָן אויספֿירן
bernstein dep-impact # API־ברעכונג + אַראָפּגעשטראָמטע רופֿער־השפּעה
bernstein aliases    # ווײַזט באַפֿעל־קורצוועגן
bernstein config-path    # ווײַזט קאָנפֿיג־פֿײַל אָרטן
bernstein init-wizard    # אינטעראַקטיווער פּראָיעקט־אויפֿשטעל
bernstein debug-bundle   # זאַמלט לאָגן, קאָנפֿיג, און סטאַט פֿאַר באַג־באַריכטן
bernstein skills list    # אויסגעפֿינסטליכע skill־פּעקלעך (פּראָגרעסיווער אַנטפּלעקונג)
bernstein skills show <name>  # דרוקט אַ skill־גוף מיט זײַנע רעפֿערענצן
```

```bash
bernstein fingerprint build --corpus-dir ~/oss-corpus  # בויט אַ לאָקאַלן ענלעכקייט־אינדעקס
bernstein fingerprint check src/foo.py                 # קוקט געשאַפֿענעם קאָד אַקעגן דעם אינדעקס
```

## אינסטאַלאַציע

| מעטאָד | באַפֿעל |
|--------|---------|
| **איין־שורה (macOS / Linux)** | `curl -fsSL https://bernstein.run/install.sh \| sh` |
| **איין־שורה (Windows)** | `irm https://bernstein.run/install.ps1 \| iex` |
| **pip** | `pip install bernstein` |
| **pipx** | `pipx install bernstein` |
| **uv** | `uv tool install bernstein` |
| **Homebrew** | `brew tap chernistry/tap && brew install bernstein` |
| **Fedora / RHEL** | `sudo dnf copr enable alexchernysh/bernstein && sudo dnf install bernstein` |
| **npm** (אַרומכאַפּער) | `npx bernstein-orchestrator` |

די איין־שורה־סקריפּטן קוקן נאָך פּיתאָן 3.12+, באָאָטסטראַפּן pipx ווען עס פֿעלט, פֿיקסן PATH פֿאַר דער איצטיקער סעסיע, און אינסטאַלירן (אָדער דערהייבן) `bernstein`. זיי האַנדלען מיט brew־געפֿירטע macOS־סבֿיבֿות און די Windows `py -3` לאָנטשער־פֿאַלבאַק. סקריפּט־מקורות: [install.sh](../../scripts/install.sh) · [install.ps1](../../scripts/install.ps1).

### ברירה־עקסטראַס

שפּײַזער SDK'ן זענען ברירה אַזוי אַז די באַזיס־אינסטאַלאַציע בלײַבט שלאַנק. קלײַבט אויס וואָס איר דאַרפֿט:

| עקסטרא | דערמעגלעכט |
|-------|---------|
| `bernstein[openai]` | OpenAI Agents SDK v2 אַדאַפּטער (`openai_agents`) |
| `bernstein[docker]` | Docker־סענדבאָקס באַקענד |
| `bernstein[e2b]` | [E2B](https://e2b.dev) מיקראָ־VM סענדבאָקס באַקענד (דאַרף `E2B_API_KEY`) |
| `bernstein[modal]` | [Modal](https://modal.com) סענדבאָקס באַקענד, ברירה־GPU (דאַרף `MODAL_TOKEN_ID` / `MODAL_TOKEN_SECRET`) |
| `bernstein[s3]` | S3 אַרטיפֿאַקט־אָפּשפּאָר (דורך `boto3`) |
| `bernstein[gcs]` | Google Cloud Storage אַרטיפֿאַקט־אָפּשפּאָר |
| `bernstein[azure]` | Azure Blob אַרטיפֿאַקט־אָפּשפּאָר |
| `bernstein[r2]` | Cloudflare R2 אַרטיפֿאַקט־אָפּשפּאָר (S3־קאָמפּאַטיבל `boto3`) |
| `bernstein[grpc]` | gRPC־בריק |
| `bernstein[k8s]` | Kubernetes אינטעגראַציעס |

קאָמבינירט עקסטראַס מיט קלאַמערן, למשל `pip install 'bernstein[openai,docker,s3]'`.

רעדאַקטאָר־ערווײַטערונגען: [VS Marketplace](https://marketplace.visualstudio.com/items?itemName=alex-chernysh.bernstein) &middot; [Open VSX](https://open-vsx.org/extension/alex-chernysh/bernstein)

## בײַשטײַערן

PR'ן זענען באַגריסט. זעט [CONTRIBUTING.md](../../CONTRIBUTING.md) פֿאַר אויפֿשטעל און קאָד־סטיל.

## שטיצע

אויב Bernstein שפּאָרט אײַך צײַט: [GitHub Sponsors](https://github.com/sponsors/chernistry)

קאָנטאַקט: [forte@bernstein.run](mailto:forte@bernstein.run)

## דערמאָנט אין

קוראַטירטע רשימות, נײַעס־בריוו, און חבֿר־פּראָיעקטן וואָס האָבן אויפֿגעכאַפּט Bernstein:

- [**Python Weekly #742**](https://www.pythonweekly.com/p/python-weekly-issue-742-april-23-2026) (23טן אַפּריל, 2026) — נײַעס־בריוו דערמאָנונג.
- [**Augment Code — 9 Open-Source Agent Orchestrators for AI Coding (2026)**](https://www.augmentcode.com/tools/open-source-agent-orchestrators) — רעדאַקציאָנעלער איבערבליק; "דאָס אַרכיטעקטוריש מערסט אינטערעסאַנטע געצײַג אין דעם איבערבליק."
- [**nibzard/awesome-agentic-patterns**](https://github.com/nibzard/awesome-agentic-patterns/blob/main/patterns/deterministic-zero-llm-orchestration.md) — Bernstein ציטירט ווי די פּראָדוקציע־אימפּלעמענטאַציע פֿונעם "דעטערמיניסטישע נול־LLM אָרקעסטראַציע" מוסטער.
- [**Jenqyang/Awesome-AI-Agents**](https://github.com/Jenqyang/Awesome-AI-Agents)
- [**jamesmurdza/awesome-ai-devtools**](https://github.com/jamesmurdza/awesome-ai-devtools)
- [**jim-schwoebel/awesome_ai_agents**](https://github.com/jim-schwoebel/awesome_ai_agents)
- [**Piebald-AI/awesome-gemini-cli**](https://github.com/Piebald-AI/awesome-gemini-cli)
- [**ComposioHQ/awesome-codex-skills**](https://github.com/ComposioHQ/awesome-codex-skills)
- [**jxzhangjhu/Awesome-LLM-RAG**](https://github.com/jxzhangjhu/Awesome-LLM-RAG)
- [**rohitg00/awesome-claude-code-toolkit**](https://github.com/rohitg00/awesome-claude-code-toolkit)
- [**numtide/llm-agents.nix**](https://github.com/numtide/llm-agents.nix) — Nix flake דיסטריבוציע.

<details>
<summary>נאָך אַוועסאַם־רשימות און קהילה־קוראַציע</summary>

- [andyrewlee/awesome-agent-orchestrators](https://github.com/andyrewlee/awesome-agent-orchestrators)
- [bradAGI/awesome-cli-coding-agents](https://github.com/bradAGI/awesome-cli-coding-agents)
- [milisp/awesome-codex-cli](https://github.com/milisp/awesome-codex-cli)
- [yaolifeng0629/Awesome-independent-tools](https://github.com/yaolifeng0629/Awesome-independent-tools) (中文 + EN)
- [caramaschiHG/awesome-ai-agents-2026](https://github.com/caramaschiHG/awesome-ai-agents-2026)
- [ai-for-developers/awesome-vibe-coding](https://github.com/ai-for-developers/awesome-vibe-coding)
- [killop/anything_about_game](https://github.com/killop/anything_about_game) (`AI.md`)
- [Glama MCP Catalog](https://glama.ai/mcp/servers/sipyourdrink-ltd/bernstein) — רעדאַקציאָנעלע MCP־סערווער רשימה.
- שפּיגלען: [icopy-site/awesome](https://github.com/icopy-site/awesome), [icopy-site/awesome-cn](https://github.com/icopy-site/awesome-cn), [trackawesomelist/trackawesomelist](https://github.com/trackawesomelist/trackawesomelist).

</details>

<details>
<summary>ציטירט ווי פֿריִערע קונסט פֿון חבֿר־פּראָיעקטן</summary>

- [**mkb23/overcode**](https://github.com/mkb23/overcode/blob/main/docs/design/bakeoffs/overcode-vs-bernstein.md) — לאַנג־פֿאָרעם פֿאַרגלײַך באַהאַנדלענדיק Bernstein ווי די רעפֿערענץ־אימפּלעמענטאַציע.
- [**Vintersong/NOVA-Cognition-Framework**](https://github.com/Vintersong/NOVA-Cognition-Framework) — `BERNSTEIN_PATTERNS.md`, "מוסטערן ווערט אויסצובאָרגן".
- [**AJV009/drupal-contrib-workbench**](https://github.com/AJV009/drupal-contrib-workbench) — פֿאָרשונג־נאָטיצן אויף דער מענעדזשער/דזשאַניטאָר טיילונג.
- [**danielvaughan/codex-blog**](https://github.com/danielvaughan/codex-blog/blob/main/_posts/2026-04-09-loki-mode-autonomous-execution.md) — פֿאַרגלײַך־אַרטיקל וואָס שטעלט Bernstein אויף דעם דעטערמיניסטישן עק.

</details>

## שטערן־געשיכטע

[![Star History Chart](https://api.star-history.com/svg?repos=sipyourdrink-ltd/bernstein&type=Date)](https://star-history.com/#sipyourdrink-ltd/bernstein&Date)

## ליצענץ

[Apache License 2.0](../../LICENSE)

---

געמאַכט מיט ליבע פֿון [Alex Chernysh](https://alexchernysh.com) &middot; [GitHub](https://github.com/chernistry) &middot; [X](https://x.com/alex_chernysh) &middot; [bernstein.run](https://bernstein.run?utm_source=github.com&utm_medium=readme&utm_campaign=bernstein-readme)

<!-- mcp-name: io.github.sipyourdrink-ltd/bernstein -->

</div>
