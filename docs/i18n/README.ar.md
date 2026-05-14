[English](../../README.md) | [Español (Spanish)](README.es.md) | [中文 (Chinese)](README.zh.md) | **العربية (Arabic)** | [Português (Portuguese)](README.pt.md) | [Bahasa Indonesia (Indonesian)](README.id.md) | [Français (French)](README.fr.md) | [日本語 (Japanese)](README.ja.md) | [Русский (Russian)](README.ru.md) | [Deutsch (German)](README.de.md) | [עברית (Hebrew)](README.he.md) | [יידיש (Yiddish)](README.yi.md)

<div dir="rtl">

<div align="center">

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="../../docs/assets/logo-dark.svg">
  <source media="(prefers-color-scheme: light)" srcset="../../docs/assets/logo-light.svg">
  <img alt="Bernstein" src="../../docs/assets/logo-light.svg" width="340">
</picture>

<br>

> *"لتحقيق إنجازات عظيمة، يلزم أمران: خطة، ووقت لا يكفي تماماً."* — Leonard Bernstein

### نسِّق أي وكيل برمجة بالذكاء الاصطناعي. أي نموذج. بأمر واحد.

[![CI](https://github.com/sipyourdrink-ltd/bernstein/actions/workflows/ci.yml/badge.svg)](https://github.com/sipyourdrink-ltd/bernstein/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/bernstein)](https://pypi.org/project/bernstein/)
[![Python 3.12+](https://img.shields.io/badge/python-3.12+-3776ab?logo=python&logoColor=white)](https://python.org)
[![License](https://img.shields.io/github/license/sipyourdrink-ltd/bernstein)](../../LICENSE)
[![MseeP.ai](https://img.shields.io/badge/MseeP.ai-verified-2496ed)](https://mseep.ai/app/chernistry-bernstein)

[الموقع الإلكتروني](https://bernstein.run?utm_source=github.com&utm_medium=readme&utm_campaign=bernstein-readme) &middot; [التوثيق](https://bernstein.readthedocs.io/) &middot; [البدء](../../docs/getting-started/GETTING_STARTED.md) &middot; [المسرد](../../docs/reference/GLOSSARY.md) &middot; [القيود](../../docs/reference/KNOWN_LIMITATIONS.md)

</div>

---

Bernstein هو مُجدوِل حتمي مكتوب بـ Python يُشغِّل فريقاً من وكلاء البرمجة عبر سطر الأوامر (Claude Code وCodex وGemini CLI و40 وكيلاً آخر) لتحقيق هدف واحد بالتوازي داخل worktrees مستقلة في git، مع سلسلة تدقيق موقَّعة بـ HMAC على كل خطوة.

### ثبِّته في 30 ثانية

```bash
pipx install bernstein
bernstein init
bernstein run -g "fix the failing test in tests/test_foo.py"
```

### شاهده في 60 ثانية

يغطي المقطع التالي تشغيلاً كاملاً: المُدير يُفكِّك الهدف، وثلاثة وكلاء يعملون بالتوازي، وسلسلة التدقيق تُسجِّل كل تسليم بين الأطراف، والـ janitor يتحقق، ثم يُفتح PR.

<p align="center">
  <img alt="عرض Bernstein لمدة 60 ثانية: المدير يُفكِّك الهدف، وثلاثة وكلاء يعملون بالتوازي، وسلسلة التدقيق تُسجِّل كل تسليم، ثم يُفتح PR" src="../../docs/demo/demo.gif" width="800">
</p>

بعد التشغيل، ينشر Bernstein تعليقاً منظماً على الـ PR يتضمن التكلفة ونتائج الاختبارات والـ lineage وسلسلة هاش التدقيق:

<p align="center">
  <img alt="تعليق Bernstein على الـ PR: أقسام الملخص والتكلفة والـ Lineage والاختبارات وسلسلة التدقيق" src="../../docs/demo/screenshot-pr-comment.svg" width="720">
</p>

> يُنتَج الـ GIF من [`docs/demo/demo.tape`](../../docs/demo/demo.tape) بواسطة [vhs](https://github.com/charmbracelet/vhs)؛ أعِد توليده محلياً بـ `vhs docs/demo/demo.tape`.

### كيف يقارن نفسه

| الميزة                                | Bernstein   | Archon   | LangGraph |
|---------------------------------------|-------------|----------|-----------|
| فريق متعدد الوكلاء (محوِّلات متوازية)  | نعم         | واحد     | نعم       |
| Lineage موقَّع / سلسلة تدقيق           | نعم         | لا       | لا        |
| نشر Air-gap / سيادي                   | نعم         | جزئي     | لا        |
| YAML تدفق عمل بصري                    | نعم [^yaml] | نعم      | لا        |
| لوحة مُستضافة / SaaS                   | لا          | جزئي     | لا        |

[^yaml]: دعم YAML لتدفقات العمل ينزل مع [PR #1108](https://github.com/sipyourdrink-ltd/bernstein/pull/1108) (في الدفعة نفسها). حتى ذلك الحين، تُكتب الخطط بـ Python أو عبر `bernstein run plan.yaml` على المخطط القديم.

تجد مصفوفة ميزات أطول مقارنةً بـ CrewAI وAutoGen وLangGraph وأربعة من منسِّقات وكلاء سطر الأوامر التي تشترك مع Bernstein في الفئة نفسها في قسم [المقارنة المفصَّلة](#detailed-comparison) أدناه.

---

### ما هذا، في فقرة واحدة؟

أنت تخبر Bernstein بما تريد بناءه. يُقسِّم العمل بين عدة وكلاء برمجة بالذكاء الاصطناعي، ويُشغِّلهم بالتوازي داخل worktrees مستقلة في git، ويُسجِّل كل تسليم في سجل تدقيق مُسلسل بـ HMAC، ويُشغِّل الاختبارات، ويدمج الشيفرة التي تنجح فعلاً. وتعود لتجد PR أخضر.

Forward-deployed engineering، بأسلوب السرب. ضع Bernstein في مستودع عميل وستحصل على فريق متعدد الوكلاء بحالة على ملفات وعزل اعتمادات لكل وكيل وأثر تدقيق موقَّع، يعمل على وكلاء سطر الأوامر التي يثق بها العميل أصلاً.

### طرق تثبيت أخرى

```bash
curl -fsSL https://bernstein.run/install.sh | sh        # سطر واحد على macOS / Linux
irm https://bernstein.run/install.ps1 | iex             # PowerShell على Windows
pip install bernstein                                   # pip
uv tool install bernstein                               # uv
brew tap chernistry/tap && brew install bernstein       # Homebrew
```

انظر [مصفوفة التثبيت](#install) الكاملة لـ `dnf copr` و`npx` والإضافات الاختيارية ومسار الـ wheelhouse للمواقع المعزولة (air-gap).

### لماذا المُجدوِل Python خالص

تستخدم معظم منسِّقات الوكلاء نموذجاً لغوياً كبيراً ليقرر مَن يفعل ماذا. وهذا غير حتمي ويستهلك الرموز (tokens) في الجدولة بدلاً من الشيفرة. أما Bernstein فيُجري نداءً واحداً للنموذج اللغوي لتفكيك هدفك، وما تبقى (تشغيل الوكلاء بالتوازي، وعزل فروع git الخاصة بهم، وتشغيل الاختبارات، وتوجيه إعادات المحاولة) هو Python خالص. كل تشغيل قابل للاستنساخ. وكل خطوة مُسجَّلة وقابلة للإعادة.

لا إطار عمل عليك تعلُّمه. ولا تقييد بمزوِّد. بدِّل أي وكيل، أي نموذج، أي مزوِّد.

<img alt="Bernstein أثناء العمل: وكلاء ذكاء اصطناعي متوازون يُنسَّقون في الزمن الحقيقي" src="../../docs/assets/in-action-small.gif" width="700">

ما تراه أثناء التشغيل:

```
$ bernstein -g "Add JWT auth"
[manager] decomposed into 4 tasks
[agent-1] claude-sonnet: src/auth/middleware.py  (done, 2m 14s)
[agent-2] codex:         tests/test_auth.py      (done, 1m 58s)
[verify]  all gates pass. merging to main.
```

## الوكلاء المدعومون

يكتشف Bernstein تلقائياً وكلاء سطر الأوامر (CLI) المثبَّتين. اخلطهم في التشغيل نفسه. نماذج محلية رخيصة للأعمال الروتينية، ونماذج سحابية أثقل للهندسة المعمارية.

41 محوِّل وكيل CLI: 38 غلافاً لأطراف ثالثة بالإضافة إلى غلاف عام لأي شيء يدعم `--prompt`.

| الوكيل | النماذج | التثبيت |
|-------|--------|---------|
| [Claude Code](https://docs.anthropic.com/en/docs/claude-code) | Opus 4, Sonnet 4.6, Haiku 4.5 | `npm install -g @anthropic-ai/claude-code` |
| [Codex CLI](https://github.com/openai/codex) | GPT-5, GPT-5 mini | `npm install -g @openai/codex` |
| [OpenAI Agents SDK v2](https://openai.github.io/openai-agents-python/) | GPT-5, GPT-5 mini, o4 | `pip install 'bernstein[openai]'` |
| [GitHub Copilot CLI](https://docs.github.com/en/copilot/github-copilot-in-the-cli) | مُدارة بواسطة Copilot (GPT-5, Sonnet 4.6) | `npm install -g @github/copilot` |
| [Gemini CLI](https://github.com/google-gemini/gemini-cli) | Gemini 2.5 Pro, Gemini Flash | `npm install -g @google/gemini-cli` |
| [Cursor](https://www.cursor.com) | Sonnet 4.6, Opus 4, GPT-5 | [تطبيق Cursor](https://www.cursor.com) |
| [Aider](https://aider.chat) | أي نموذج متوافق مع OpenAI/Anthropic | `pip install aider-chat` |
| [Amp](https://ampcode.com) | مُدارة بواسطة Amp | `npm install -g @sourcegraph/amp` |
| [Cody](https://sourcegraph.com/cody) | مستضافة لدى Sourcegraph | `npm install -g @sourcegraph/cody` |
| [Continue](https://continue.dev) | أي نموذج متوافق مع OpenAI/Anthropic | `npm install -g @continuedev/cli` (الثنائي: `cn`) |
| [Goose](https://block.github.io/goose/) | أي مزوِّد يدعمه Goose | انظر [توثيق Goose](https://block.github.io/goose/) |
| [IaC](https://www.terraform.io/) (Terraform/Pulumi) | أي مزوِّد يستخدمه الوكيل الأساسي | مدمج |
| [Kilo](https://kilo.dev) | مستضافة لدى Kilo | انظر [توثيق Kilo](https://kilo.dev) |
| [Kiro](https://kiro.dev) | مستضافة لدى Kiro | انظر [توثيق Kiro](https://kiro.dev) |
| [Ollama](https://ollama.ai) + Aider | نماذج محلية (دون اتصال) | `brew install ollama` |
| [OpenCode](https://opencode.ai) | أي مزوِّد يدعمه OpenCode | انظر [توثيق OpenCode](https://opencode.ai) |
| [Qwen](https://github.com/QwenLM/qwen-code) | نماذج Qwen Code | `npm install -g @qwen-code/qwen-code` |
| [Cloudflare Agents](https://developers.cloudflare.com/agents/) | نماذج Workers AI | `bernstein cloud login` |
| [OpenHands](https://github.com/OpenHands/OpenHands) | أي نموذج يدعمه LiteLLM (Anthropic, OpenAI, ...) | `uv tool install openhands --python 3.12` |
| [Open Interpreter](https://github.com/OpenInterpreter/open-interpreter) | أي نموذج (مدعوم عبر LiteLLM) | `pip install open-interpreter` |
| [gptme](https://github.com/gptme/gptme) | Anthropic, OpenAI, OpenRouter | `pipx install gptme` |
| [Plandex](https://github.com/plandex-ai/plandex) | Plandex Cloud أو نماذج مستضافة ذاتياً | `curl -sL https://plandex.ai/install.sh \| bash` |
| [AIChat](https://github.com/sigoden/aichat) | OpenAI, Anthropic, OpenRouter, Groq, Gemini | `cargo install aichat` |
| [Letta Code](https://github.com/letta-ai/letta-code) | موجَّهة عبر Letta (Anthropic, OpenAI) | `npm install -g @letta-ai/letta-code` |
| **Generic** | أي CLI يدعم `--prompt` | مدمج |

#### تفويض المنسِّق (عقدة طرفية)

فئة منفصلة وأصغر من المحوِّلات تُغلِّف **منسِّقات CLI أخرى** كأنها وكلاء فرديون. يُسلِّم Bernstein الأداةَ المُغلَّفة موجِّهاً (prompt) أو خطةً، ولا يرى سوى رمز الخروج النهائي؛ تكاليف الوكلاء الفرعيين وبوابات الجودة داخل المنسِّق المُغلَّف غير مرئية لـ Bernstein. مفيد حين تريد إدراج سير عمل قائم على إحدى هذه الأدوات ضمن خطوة في خطة Bernstein أكبر.

| المنسِّق | مُغلَّف باسم | التثبيت |
|--------------|------------|---------|
| [Composio Agent Orchestrator](https://github.com/ComposioHQ/agent-orchestrator) (`@aoagents/ao`) | `composio` | `npm install -g @aoagents/ao` |
| [umputun/ralphex](https://github.com/umputun/ralphex) | `ralphex` | `go install github.com/umputun/ralphex/cmd/ralphex@latest` |

أي محوِّل يعمل أيضاً بوصفه **النموذج اللغوي الداخلي للجدولة**. شغِّل الحزمة بأكملها دون أي مزوِّد بعينه:

```yaml
internal_llm_provider: gemini            # or qwen, ollama, codex, goose, ...
internal_llm_model: gemini-3.1-pro
```

> [!TIP]
> شغِّل `bernstein --headless` لخطوط أنابيب CI. بلا واجهة طرفية (TUI)، ومخرجات JSON منظَّمة، ورمز خروج غير صفري عند الفشل.

## بداية سريعة

```bash
cd your-project
bernstein init                    # creates .sdd/ workspace + bernstein.yaml
bernstein -g "Add rate limiting"  # agents spawn, work in parallel, verify, exit
bernstein live                    # watch progress in the TUI dashboard
bernstein stop                    # graceful shutdown with drain
```

للمشاريع متعددة المراحل، عرِّف خطة YAML:

```bash
bernstein run plan.yaml           # skips LLM planning, goes straight to execution
bernstein run --dry-run plan.yaml # preview tasks and estimated cost
```

## كيف يعمل

1. **التفكيك**. يُقسِّم المدير هدفك إلى مهام بأدوار، وملفات مملوكة، وإشارات إنجاز.
2. **الإطلاق**. تنطلق الوكلاء في أشجار عمل git معزولة (worktrees)، واحدة لكل مهمة. ويظل الفرع الرئيسي نظيفاً.
3. **التحقُّق**. يفحص "البوّاب" (janitor) إشارات ملموسة: نجاح الاختبارات، وجود الملفات، نظافة فحص الأسلوب (lint)، صحة الأنواع.
4. **الدمج**. يُدمج العمل المُتحقَّق منه في الفرع الرئيسي. أما المهام الفاشلة فتُعاد محاولتها أو تُوجَّه إلى نموذج مختلف.

المنسِّق هو مُجدول Python، وليس نموذجاً لغوياً كبيراً. قرارات الجدولة حتمية وقابلة للتدقيق وقابلة للاستنساخ.

## التشغيل السحابي (Cloudflare)

يمكن لـ Bernstein تشغيل الوكلاء على Cloudflare Workers بدلاً من التشغيل محلياً. يتولى `bernstein cloud` CLI أمر النشر ودورة الحياة.

- **Workers**. تنفيذ الوكلاء على حافة شبكة Cloudflare، مع Durable Workflows للمهام متعددة الخطوات وإعادة المحاولة التلقائية.
- **عزل صندوق رمل V8**. يعمل كل وكيل في عزله الخاص (isolate)، دون نفقات حاويات.
- **مزامنة مساحة العمل R2**. تُزامَن حالة شجرة العمل المحلية إلى تخزين كائنات R2 ليرى الوكلاء السحابيون الملفات نفسها.
- **Workers AI** (تجريبي). استخدم النماذج المستضافة لدى Cloudflare بوصفها مزوِّد النموذج اللغوي، دون الحاجة إلى مفاتيح API خارجية.
- **تحليلات D1**. تُخزَّن مقاييس المهام وبيانات التكلفة في D1 للاستعلام.
- **Vectorize**. ذاكرة تخزين دلالية مدعومة بقاعدة بيانات المتجهات لدى Cloudflare.
- **عرض المتصفح**. Chrome بلا واجهة على Workers للوكلاء الذين يحتاجون إلى فحص مخرجات الويب.
- **نقل MCP عن بُعد**. اكشف خوادم MCP أو استهلكها عبر شبكة Cloudflare.

```bash
bernstein cloud login      # authenticate with Bernstein Cloud
bernstein cloud deploy     # push agent workers
bernstein cloud run plan.yaml  # execute a plan on Cloudflare
```

## القدرات

**التنسيق الجوهري**. تنفيذ متوازٍ، وعزل عبر git worktree، وتحقُّق "البوّاب"، وبوابات جودة (lint، الأنواع، فحص بيانات التعريف الشخصية PII)، ومراجعة شيفرة عبر النماذج المختلفة، وقاطع تيار (circuit breaker) للوكلاء المُسيئين، ومراقبة نمو الرموز (tokens) مع تدخُّل تلقائي.

**الذكاء**. موجِّه قطاع طرقي سياقي (contextual bandit) لاختيار النموذج/الجهد. رسم بياني للمعرفة لتحليل الأثر على قاعدة الشيفرة. التخزين الدلالي يوفِّر الرموز على الأنماط المتكررة. كشف شذوذ التكلفة (تنبيهات معدل الإنفاق). كشف شذوذ السلوك بإشارة Z-score.

**العزل في صناديق الرمل**. بروتوكول [`SandboxBackend`](../../docs/architecture/sandbox.md) قابل للتوصيل — شغِّل الوكلاء في git worktrees محلية (افتراضي)، أو حاويات Docker، أو أجهزة [E2B](https://e2b.dev) Firecracker الافتراضية الدقيقة (microVMs)، أو حاويات [Modal](https://modal.com) بلا خادم (مع GPU اختيارية). يستطيع مؤلِّفو الإضافات تسجيل خلفيات مخصَّصة عبر مجموعة نقاط الدخول `bernstein.sandbox_backends`. افحص الخلفيات المثبَّتة بـ `bernstein agents sandbox-backends`.

**تخزين القطع الأثرية (artifacts)**. يمكن تدفُّق حالة `.sdd/` إلى خلفيات [`ArtifactSink`](../../docs/architecture/storage.md) قابلة للتوصيل: نظام الملفات المحلي (افتراضي)، أو S3، أو Google Cloud Storage، أو Azure Blob، أو Cloudflare R2. يحفظ `BufferedSink` عقد سلامة WAL ضد الأعطال بالكتابة محلياً مع fsync أولاً، ثم النسخ إلى البعيد بشكل غير متزامن.

**حزم المهارات**. [مهارات](../../docs/architecture/skills.md) ذات إفصاح تدريجي (نمط OpenAI Agents SDK): يُشحن فهرس مهارات مُدمج فقط في موجِّه النظام لكل إطلاق، ويسحب الوكلاء الأجساد الكاملة عبر أداة MCP `load_skill` عند الطلب. 17 حزمة دور مدمجة بالإضافة إلى نقاط دخول `bernstein.skill_sources` لأطراف ثالثة.

**عناصر التحكم**. سجلات تدقيق متسلسلة بـ HMAC، ومحرِّك سياسات، وتصفية مخرجات بيانات التعريف الشخصية، واستعادة من الأعطال مدعومة بـ WAL (سلامة متعددة العمال تجريبية)، وOAuth 2.0 PKCE.

**القابلية للرصد**. `/metrics` لـ Prometheus، وإعدادات مسبقة لمصدِّر OTel، ولوحات Grafana. تتبُّع التكلفة لكل نموذج (`bernstein cost`). واجهة طرفية (TUI) ولوحة قيادة ويب. رؤية عملية الوكيل في `ps`.

**المنظومة**. وضع خادم MCP، ودعم بروتوكول A2A، وتكامل مع GitHub App، ونظام إضافات قائم على pluggy، ومساحات عمل متعددة المستودعات، ووضع عنقودي للتنفيذ الموزَّع، والتطوُّر الذاتي عبر `--evolve` (تجريبي).

مصفوفة الميزات الكاملة: [FEATURE_MATRIX.md](../../docs/reference/FEATURE_MATRIX.md) &middot; الميزات الأخيرة: [ما الجديد](../../docs/whats-new.md)

## ما الجديد في v1.9

**جسر ACP** — يعرض `bernstein acp serve --stdio` خدمة Bernstein لأي محرر يتحدث بروتوكول اتصال الوكلاء (Agent Communication Protocol) مثل Zed وغيره. لا حاجة لشيفرة إضافة على جانب المحرر.

**إصلاح CI الذاتي** — يراقب `bernstein autofix` طلبات السحب (PRs) المفتوحة لـ Bernstein، وحين يتحول CI إلى الأحمر، يُطلق وكيل إصلاح تلقائياً. وحين يصبح أخضر، يدفع الإصلاح ويعيد طلب المراجعة.

**خزنة الاعتمادات** — يكتب `bernstein connect <provider>` مفاتيح API إلى مفتاح نظام التشغيل (keychain)؛ ويُدرجها `bernstein creds` ويُدوِّرها. ترث الوكلاء اعتمادات بنطاقات محدَّدة دون لمس متغيرات البيئة.

**أنفاق المعاينة** — يُقلع `bernstein preview start` خادم تطوير معزولاً ويطبع رابطاً عاماً. مفيد لمشاركة فرع قيد التشغيل مع مراجِع دون النشر إلى التجهيز (staging).

سجل التغييرات الكامل: [docs/whats-new.md](../../docs/whats-new.md)

## أوامر المُشغِّل

أوامر تُلغي الشيفرة اللاصقة التي ينتهي بها الحال إلى كتابتها معظم الفرق حول تشغيلاتها.

| الأمر | ما يفعله |
|---------|--------------|
| `bernstein pr` | يُنشئ تلقائياً طلب سحب GitHub من جلسة منتهية؛ ويحمل المتن نتائج بوابات "البوّاب" وتفصيل تكلفة الرموز/الدولار. |
| `bernstein from-ticket <url>` | يستورد تذكرة Linear / GitHub Issues / Jira بوصفها مهمة Bernstein. استنتاج الدور والنطاق بناءً على التسميات (labels). يدعم `--dry-run` و`--run`. |
| `bernstein ticket import <url>` | اسم بديل / صيغة جماعية لـ `from-ticket` للبرمجة النصية. |
| `bernstein remote` | خلفية صندوق رمل عبر SSH. `remote test <host>`، `remote run <host> <path>`، `remote forget <host>`. إعادة استخدام مقبس ControlMaster لمكالمات متكررة سريعة. |
| `bernstein hooks` | خطافات دورة حياة لـ `pre_task` و`post_task` و`pre_merge` و`post_merge` و`pre_spawn` و`post_spawn` — برامج صدفة (shell scripts) أو `@hookimpl` عبر pluggy. `hooks list`، `hooks run <event>`، `hooks check`. |
| `bernstein chat serve --platform=telegram\|discord\|slack` | قُد التشغيلات من المحادثة بأوامر `/run`، `/status`، `/approve`، `/reject`، `/switch`، `/stop`. |
| `bernstein approve-tool` / `bernstein reject-tool` | موافقة تفاعلية على نداءات الأدوات في منتصف التشغيل. `--latest`، `--id`، `--always`. |
| `bernstein tunnel start <port> [--provider auto\|cloudflared\|ngrok\|bore\|tailscale]` | غلاف واحد حول أربعة مزوِّدي أنفاق. وأيضاً `tunnel list`، `tunnel stop <name>\|--all`. إعادة استخدام عمليات بأسلوب ControlMaster. |
| `bernstein daemon install [--user\|--system] [--command="..."] [--env KEY=VAL]...` | يُثبِّت وحدة systemd (Linux) أو launchd (macOS) للبدء التلقائي. وأيضاً `daemon start/stop/restart/status/uninstall`. |
| `bernstein connect <provider>` / `bernstein creds` | يُخزِّن اعتمادات API ويُدوِّرها في مفتاح نظام التشغيل. ترث الوكلاء مفاتيح بنطاقات محدَّدة لكل تشغيل. |
| `bernstein autofix` | عفريت (daemon) يراقب طلبات السحب المفتوحة لـ Bernstein؛ يُطلق وكيل إصلاح حين يفشل CI ويدفع الإصلاح تلقائياً. |
| `bernstein preview start` | يُشغِّل خادم تطوير معزولاً للفرع الحالي ويطبع رابط نفق عام قابلاً للمشاركة. |

## كيف يقارن

| الميزة | Bernstein | CrewAI | AutoGen [^autogen] | LangGraph |
|---------|-----------|--------|---------|-----------|
| المنسِّق | شيفرة حتمية | مُحرَّك بنموذج لغوي (+ Flows برمجية) | مُحرَّك بنموذج لغوي | رسم بياني + نموذج لغوي |
| يعمل مع | أي وكيل CLI (41 محوِّلاً) | أصناف Python SDK | وكلاء Python | عُقد LangChain |
| عزل git | Worktrees لكل وكيل | لا | لا | لا |
| صناديق رمل قابلة للتوصيل | Worktree, Docker, E2B, Modal | لا | لا | لا |
| التحقُّق | البوّاب + بوابات جودة | حواجز + مخرجات Pydantic | شروط إنهاء | حواف شرطية |
| تتبُّع التكلفة | مدمج | `usage_metrics` | `RequestUsage` | عبر LangSmith |
| نموذج الحالة | قائم على الملفات (.sdd/) | في الذاكرة + نقطة فحص SQLite | في الذاكرة | Checkpointer |
| منافذ قطع أثرية بعيدة | S3, GCS, Azure Blob, R2 | لا | لا | لا |
| تطوُّر ذاتي | مدمج (تجريبي) | لا | لا | لا |
| خطط تصريحية (YAML) | نعم | نعم (`agents.yaml`, `tasks.yaml`) | لا | جزئي (`langgraph.json`) |
| توجيه النموذج لكل مهمة | نعم | نموذج لغوي لكل وكيل | `model_client` لكل وكيل | لكل عقدة (يدوي) |
| دعم MCP | نعم (عميل + خادم) | نعم | نعم (عميل + workbench) | نعم (عميل + خادم) |
| محادثة من وكيل إلى وكيل | لوحة إعلانات (bulletin board) | نعم (عملية Crew) | نعم (محادثة جماعية) | نعم (مُشرِف، سرب) |
| واجهة ويب | TUI + لوحة قيادة ويب | CrewAI AMP | AutoGen Studio | LangGraph Studio + LangSmith |
| خيار استضافة سحابية | نعم (Cloudflare) | نعم (CrewAI AMP) | لا | نعم (LangGraph Cloud) |
| RAG/استرجاع مدمج | نعم (FTS5 لقاعدة الشيفرة + BM25) | `crewai_tools` | مسترجِعات `autogen_ext` | عبر LangChain |

*آخر تحقُّق: 2026-04-19. انظر [صفحات المقارنة الكاملة](../../docs/compare/README.md) للاطلاع على مصفوفات الميزات التفصيلية.*

يقارن الجدول أعلاه Bernstein بأطر تنسيق النماذج اللغوية (تُنسِّق نداءات النموذج اللغوي). أما الجدول التالي فيُغطي الفئة الأقرب — أدوات أخرى تُنسِّق **وكلاء برمجة CLI**:

| الميزة | Bernstein | [awslabs/cli-agent-orchestrator](https://github.com/awslabs/cli-agent-orchestrator) | [ComposioHQ/agent-orchestrator](https://github.com/ComposioHQ/agent-orchestrator) | [emdash](https://github.com/generalaction/emdash) | [umputun/ralphex](https://github.com/umputun/ralphex) |
|---------|-----------|-----------|-----------|-----------|-----------|
| الشكل | Python CLI + مكتبة + خادم MCP | Python CLI + جلسات tmux + واجهة ويب | TypeScript CLI + لوحة قيادة محلية | تطبيق سطح مكتب Electron | Go CLI |
| اللغة الأساسية | Python | Python | TypeScript | TypeScript | Go |
| التثبيت | `pipx install bernstein` | `uv tool install cli-agent-orchestrator` | `npm install -g @aoagents/ao` | `.dmg` / `.msi` / `.AppImage` | `go install` / ثنائي وحيد |
| محوِّلات الوكلاء | 41 | 5 (Kiro, Claude Code, Codex, Gemini, Kimi) | 3 (Claude Code, Codex, Aider) | 24 | 1 (Claude Code فقط) |
| تنفيذ متوازٍ متعدد الوكلاء | نعم | نعم (جلسة tmux لكل وكيل) | نعم | نعم | لا (جلسة تسلسلية واحدة) |
| Git worktree لكل وكيل | نعم | لا (مخطَّط، [#100](https://github.com/awslabs/cli-agent-orchestrator/issues/100)) | نعم | نعم | علم `--worktree` اختياري |
| وضع خادم MCP (يكشف ذاته بوصفه MCP) | نعم (stdio + HTTP/SSE) | نعم (تواصل بين الوكلاء) | لا | لا | لا |
| المُنسِّق | مُجدول Python حتمي | مُشرِف نموذج لغوي هرمي | مُحرَّك بنموذج لغوي | غير موثَّق | مُنفِّذ خطة خطية |
| إعادة تشغيل تدقيق متسلسلة بـ HMAC | نعم | لا | لا | لا | لا |
| مدقِّق عبر النماذج / بوابات جودة | نعم (متعدد المراحل) | لا | لا | لا | مراجعة متعددة المراحل (Claude فقط) |
| إصلاح CI ذاتي / تدفق PR | نعم (`bernstein autofix`) | لا | نعم | لا | لا |
| لوحة قيادة بصرية | TUI + ويب | واجهة ويب + tmux | ويب | تطبيق سطح مكتب | ويب (`--serve`) |
| منافذ الإشعارات | Telegram/Slack/Discord/Email/Webhook/Shell | — | لا | لا | Telegram / Email / Slack / Webhook |
| الجهة الداعمة | OSS فردي | AWS Labs | مُموَّل (Composio.dev) | YC W26 | OSS فردي |
| الترخيص | Apache 2.0 | Apache 2.0 | MIT | Apache 2.0 | MIT |

ميزة Bernstein التنافسية في هذه الفئة: **أصلي في Python، وخادم MCP أولاً، وأوسع تغطية للمحوِّلات، وتوازٍ حقيقي متعدد الوكلاء، ومُجدول حتمي بلا نموذج لغوي في حلقة التنسيق**. إن أردت عزل جلسات tmux متوافقاً مع AWS مع مُشرِف نموذج لغوي هرمي، فإن `cao` من AWS Labs أنسب؛ وإن كانت حزمتك TypeScript وأردت منتجاً مع لوحة قيادة، فإن `@aoagents/ao` من Composio أنسب؛ وإن أردت بيئة تطوير وكلاء (ADE) سطح مكتب أنيقة، فإن emdash هو الخيار؛ وإن كنت تستخدم Claude Code فقط وتريد ثنائي Go وحيداً يسير في خطة من أعلى إلى أسفل، فإن ralphex هو الأنسب. أما إن أردت لبنة تُستورَد إلى Python، وتكشف ذاتها عبر MCP لأي عميل، وتُشغِّل وكلاء عديدين بالتوازي، وتُغطي عرض الوكلاء الكامل (بما في ذلك Qwen، وGoose، وOllama، وOpenAI Agents SDK، وCloudflare Agents، وغيرها) — فهو Bernstein.

[^autogen]: AutoGen في وضع الصيانة؛ خَلَفُهُ Microsoft Agent Framework 1.0.

## المراقبة

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

## التثبيت

| الطريقة | الأمر |
|--------|---------|
| **سطر واحد (macOS / Linux)** | `curl -fsSL https://bernstein.run/install.sh \| sh` |
| **سطر واحد (Windows)** | `irm https://bernstein.run/install.ps1 \| iex` |
| **pip** | `pip install bernstein` |
| **pipx** | `pipx install bernstein` |
| **uv** | `uv tool install bernstein` |
| **Homebrew** | `brew tap chernistry/tap && brew install bernstein` |
| **Fedora / RHEL** | `sudo dnf copr enable alexchernysh/bernstein && sudo dnf install bernstein` |
| **npm** (غلاف) | `npx bernstein-orchestrator` |

تتحقَّق برامج السطر الواحد من وجود Python 3.12+، وتُهيِّئ pipx إن كان مفقوداً، وتُصلح PATH للجلسة الحالية، وتُثبِّت (أو تُحدِّث) `bernstein`. وتتعامل مع بيئات macOS التي تُديرها brew وآلية احتياط مُطلِق `py -3` في Windows. مصادر البرامج: [install.sh](../../scripts/install.sh) · [install.ps1](../../scripts/install.ps1).

### إضافات اختيارية

تُعدّ حزم تطوير المزوِّدين (SDKs) اختيارية ليبقى التثبيت الأساسي خفيفاً. اختر ما تحتاجه:

| الإضافة | تُفعِّل |
|-------|---------|
| `bernstein[openai]` | محوِّل OpenAI Agents SDK v2 (`openai_agents`) |
| `bernstein[docker]` | خلفية صندوق رمل Docker |
| `bernstein[e2b]` | خلفية صندوق رمل [E2B](https://e2b.dev) microVM (تتطلب `E2B_API_KEY`) |
| `bernstein[modal]` | خلفية صندوق رمل [Modal](https://modal.com)، GPU اختيارية (تتطلب `MODAL_TOKEN_ID` / `MODAL_TOKEN_SECRET`) |
| `bernstein[s3]` | منفذ قطع أثرية S3 (عبر `boto3`) |
| `bernstein[gcs]` | منفذ قطع أثرية Google Cloud Storage |
| `bernstein[azure]` | منفذ قطع أثرية Azure Blob |
| `bernstein[r2]` | منفذ قطع أثرية Cloudflare R2 (متوافق مع S3 عبر `boto3`) |
| `bernstein[grpc]` | جسر gRPC |
| `bernstein[k8s]` | تكاملات Kubernetes |

ادمج الإضافات بين قوسين مربَّعين، مثلاً `pip install 'bernstein[openai,docker,s3]'`.

إضافات المحرِّر: [VS Marketplace](https://marketplace.visualstudio.com/items?itemName=alex-chernysh.bernstein) &middot; [Open VSX](https://open-vsx.org/extension/alex-chernysh/bernstein)

## المساهمة

طلبات السحب مُرحَّب بها. انظر [CONTRIBUTING.md](../../CONTRIBUTING.md) للإعداد وأسلوب الشيفرة.

## الدعم

إن وفَّر لك Bernstein وقتاً: [GitHub Sponsors](https://github.com/sponsors/chernistry)

التواصل: [forte@bernstein.run](mailto:forte@bernstein.run)

## أُبرِز في

قوائم منتقاة، ونشرات إخبارية، ومشاريع نظيرة تبنَّت Bernstein:

- [**Python Weekly #742**](https://www.pythonweekly.com/p/python-weekly-issue-742-april-23-2026) (23 أبريل 2026) — إشارة في النشرة الإخبارية.
- [**Augment Code — 9 Open-Source Agent Orchestrators for AI Coding (2026)**](https://www.augmentcode.com/tools/open-source-agent-orchestrators) — تقرير تحريري؛ "الأداة الأكثر إثارةً للاهتمام معمارياً في هذا التقرير."
- [**nibzard/awesome-agentic-patterns**](https://github.com/nibzard/awesome-agentic-patterns/blob/main/patterns/deterministic-zero-llm-orchestration.md) — اسْتُشهِد بـ Bernstein بوصفه التنفيذ الإنتاجي لنمط "التنسيق الحتمي بصفر نموذج لغوي".
- [**Jenqyang/Awesome-AI-Agents**](https://github.com/Jenqyang/Awesome-AI-Agents)
- [**jamesmurdza/awesome-ai-devtools**](https://github.com/jamesmurdza/awesome-ai-devtools)
- [**jim-schwoebel/awesome_ai_agents**](https://github.com/jim-schwoebel/awesome_ai_agents)
- [**Piebald-AI/awesome-gemini-cli**](https://github.com/Piebald-AI/awesome-gemini-cli)
- [**ComposioHQ/awesome-codex-skills**](https://github.com/ComposioHQ/awesome-codex-skills)
- [**jxzhangjhu/Awesome-LLM-RAG**](https://github.com/jxzhangjhu/Awesome-LLM-RAG)
- [**rohitg00/awesome-claude-code-toolkit**](https://github.com/rohitg00/awesome-claude-code-toolkit)
- [**numtide/llm-agents.nix**](https://github.com/numtide/llm-agents.nix) — توزيع Nix flake.

<details>
<summary>المزيد من القوائم الرائعة وانتقاء المجتمع</summary>

- [andyrewlee/awesome-agent-orchestrators](https://github.com/andyrewlee/awesome-agent-orchestrators)
- [bradAGI/awesome-cli-coding-agents](https://github.com/bradAGI/awesome-cli-coding-agents)
- [milisp/awesome-codex-cli](https://github.com/milisp/awesome-codex-cli)
- [yaolifeng0629/Awesome-independent-tools](https://github.com/yaolifeng0629/Awesome-independent-tools) (中文 + EN)
- [caramaschiHG/awesome-ai-agents-2026](https://github.com/caramaschiHG/awesome-ai-agents-2026)
- [ai-for-developers/awesome-vibe-coding](https://github.com/ai-for-developers/awesome-vibe-coding)
- [killop/anything_about_game](https://github.com/killop/anything_about_game) (`AI.md`)
- [Glama MCP Catalog](https://glama.ai/mcp/servers/sipyourdrink-ltd/bernstein) — قائمة تحريرية لخوادم MCP.
- مرايا: [icopy-site/awesome](https://github.com/icopy-site/awesome)، و[icopy-site/awesome-cn](https://github.com/icopy-site/awesome-cn)، و[trackawesomelist/trackawesomelist](https://github.com/trackawesomelist/trackawesomelist).

</details>

<details>
<summary>اسْتُشهِد بوصفه فناً سابقاً من قِبل مشاريع نظيرة</summary>

- [**mkb23/overcode**](https://github.com/mkb23/overcode/blob/main/docs/design/bakeoffs/overcode-vs-bernstein.md) — مقارنة مطوَّلة تتعامل مع Bernstein بوصفه التنفيذ المرجعي.
- [**Vintersong/NOVA-Cognition-Framework**](https://github.com/Vintersong/NOVA-Cognition-Framework) — `BERNSTEIN_PATTERNS.md`، "أنماط جديرة بالاقتباس".
- [**AJV009/drupal-contrib-workbench**](https://github.com/AJV009/drupal-contrib-workbench) — ملاحظات بحثية حول الفصل بين المدير والبوّاب.
- [**danielvaughan/codex-blog**](https://github.com/danielvaughan/codex-blog/blob/main/_posts/2026-04-09-loki-mode-autonomous-execution.md) — مقال مقارنة يضع Bernstein على الطرف الحتمي.

</details>

## تاريخ النجوم

[![Star History Chart](https://api.star-history.com/svg?repos=sipyourdrink-ltd/bernstein&type=Date)](https://star-history.com/#sipyourdrink-ltd/bernstein&Date)

## الترخيص

[Apache License 2.0](../../LICENSE)

---

صُنع بحب بواسطة [Alex Chernysh](https://alexchernysh.com) &middot; [GitHub](https://github.com/chernistry) &middot; [X](https://x.com/alex_chernysh) &middot; [bernstein.run](https://bernstein.run?utm_source=github.com&utm_medium=readme&utm_campaign=bernstein-readme)

<!-- mcp-name: io.github.sipyourdrink-ltd/bernstein -->

</div>
