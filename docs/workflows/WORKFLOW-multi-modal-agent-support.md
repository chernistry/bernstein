# WORKFLOW: Multi-Modal Agent Support
**Version**: 0.1
**Date**: 2026-04-11
**Author**: Workflow Architect
**Status**: Draft
**Implements**: road-184 — Multi-modal agent support (code + images + diagrams + architecture docs)

---

## Overview

Extends Bernstein's agent interface to accept multi-modal inputs — architecture diagrams, UI mockups, data flow diagrams, ERD schemas, and annotated screenshots — alongside text. When a task includes image attachments, the orchestrator validates model capabilities, preprocesses assets, constructs multi-modal prompts, and routes to vision-capable agents. Agents interpret visual inputs and produce code, configurations, or documentation that implements what the diagram describes. This keeps Bernstein relevant as foundation models become natively multi-modal.

---

## Actors

| Actor | Role in this workflow |
|---|---|
| User (or upstream system) | Attaches images/diagrams to task descriptions via CLI, plan YAML, or API |
| Task server | Stores task metadata including attachment references |
| Asset validator | Validates image format, dimensions, file size, and content safety |
| Asset preprocessor | Resizes, converts, and optimizes images for model consumption |
| Capability router | Checks which adapters/models support vision and routes accordingly |
| Spawner | Constructs multi-modal prompts (text + image references) and launches agents |
| CLI adapter | Passes image data to the agent CLI in the format it expects |
| Agent process | Interprets visual input and produces code/config output |
| Janitor/verifier | Validates that agent output structurally matches what the visual input described |

---

## Prerequisites

- At least one vision-capable model available (Claude Sonnet/Opus with vision, Gemini with vision, GPT-4o, etc.)
- Agent adapter supports passing image data (not all CLI tools accept images — see Capability Matrix)
- Image files accessible from the orchestrator's filesystem (local path or fetchable URL)
- Task description includes explicit instructions for what to produce from the visual input

---

## Trigger

**Task creation with attachments**: A task is created (via CLI, plan YAML, or POST /tasks) that includes one or more image attachments in its description or metadata.

**CLI entry point**: `bernstein add-task --title "Implement login page" --attach mockup.png --attach flow.svg`

**Plan YAML entry point**:
```yaml
steps:
  - goal: "Implement the authentication flow from the architecture diagram"
    role: backend
    attachments:
      - path: docs/diagrams/auth-flow.png
        type: architecture_diagram
        description: "Authentication flow showing OAuth2 + JWT token lifecycle"
      - path: docs/mockups/login-page.png
        type: ui_mockup
        description: "Login page design with email/password and SSO buttons"
```

**API entry point**: `POST /tasks` with `attachments` field in the request body.

---

## Attachment Schema

```yaml
# Per-attachment metadata
attachment:
  id: "string — auto-generated UUID"
  path: "string — local filesystem path or URL"
  type: "architecture_diagram | ui_mockup | data_flow | erd | screenshot | wireframe | other"
  description: "string — what this image shows and how the agent should use it"
  format: "png | jpg | jpeg | webp | svg | pdf"
  size_bytes: int
  dimensions: { width: int, height: int }  # null for SVG/PDF
  preprocessed_path: "string | null — path after optimization"
```

**Supported formats and limits**:

| Format | Max size | Max dimensions | Notes |
|---|---|---|---|
| PNG | 20 MB | 8192x8192 | Primary format; lossless |
| JPEG/JPG | 20 MB | 8192x8192 | Lossy OK for photos/screenshots |
| WebP | 20 MB | 8192x8192 | Preferred compressed format |
| SVG | 5 MB | N/A | Converted to PNG for model consumption |
| PDF | 10 MB (single page) | N/A | First page extracted as PNG; multi-page not supported |

---

## Capability Matrix

Not all models and adapters support vision. The router must check before assignment.

| Adapter | Vision support | Image passing mechanism | Max images per prompt | Notes |
|---|---|---|---|---|
| Claude Code | Yes (Sonnet/Opus) | Prompt includes image file paths; Claude reads them via Read tool | 10 | Agent reads images natively |
| Gemini CLI | Yes | Image passed via `--image` flag or inline in prompt | 5 | Check per-model limits |
| Codex CLI | No | N/A | 0 | Text-only; cannot route vision tasks here |
| Aider | Partial | `--read` flag for image files; model must support vision | 5 | Depends on underlying model |
| Ollama | Model-dependent | Via API; adapter must check model capabilities | Varies | llava, bakllava support vision |
| Cursor | Yes (via underlying model) | Image in prompt context | Varies | IDE-based; limited CLI control |
| Mock | Yes (test) | Stored in test fixture | 10 | For testing only |

**Fallback**: If a task has attachments but is routed to a non-vision adapter, the router must either (a) re-route to a vision-capable adapter, or (b) fall back to text-only mode using the attachment descriptions as context (degraded but functional).

---

## Workflow Tree

### STEP 1: Detect attachments in task
**Actor**: Task server (on task creation)
**Action**: Parse the task payload for `attachments` field. If present, set `task.has_attachments = true` and `task.attachment_types = [list of types]`. Validate that each attachment path is resolvable (file exists or URL is reachable). Store attachment metadata alongside the task.
**Timeout**: 5s per attachment (file stat or HEAD request)
**Input**: `{ "task": Task, "attachments": [Attachment] }`
**Output on SUCCESS**: task stored with attachment metadata -> GO TO STEP 2 (when task is claimed)
**Output on FAILURE**:
  - `FAILURE(attachment_not_found)`: File path does not exist or URL returns 404 -> return 400 + `{ "error": "Attachment not found: {path}", "code": "ATTACHMENT_NOT_FOUND", "retryable": false }`
  - `FAILURE(attachment_too_large)`: File exceeds format-specific size limit -> return 400 + `{ "error": "Attachment too large: {path} ({size_mb} MB, max {max_mb} MB)", "code": "ATTACHMENT_TOO_LARGE", "retryable": false }`
  - `FAILURE(unsupported_format)`: File extension not in supported list -> return 400 + `{ "error": "Unsupported attachment format: {ext}. Supported: png, jpg, jpeg, webp, svg, pdf", "code": "UNSUPPORTED_FORMAT", "retryable": false }`

**Observable states during this step**:
  - User sees: task creation success/failure response
  - Operator sees: task in task store with `has_attachments=true`
  - Database: task record with attachment metadata array
  - Logs: `[task-server] task={task_id} created with {n} attachments types={types}`

---

### STEP 2: Validate and preprocess attachments
**Actor**: Asset validator + asset preprocessor
**Action**: For each attachment: (a) Validate image integrity (not corrupt, decodable). (b) Check dimensions — if over 4096x4096, resize to fit within that box (maintain aspect ratio). (c) Convert SVG to PNG (rasterize at 2x resolution, min 1024px wide). (d) Extract first page from PDF as PNG. (e) Optimize file size (re-encode PNG with compression; JPEG at quality 85 if > 5 MB). (f) Write preprocessed file to `.sdd/runtime/attachments/{task_id}/{attachment_id}.{ext}`. (g) Update attachment metadata with `preprocessed_path` and final dimensions/size.
**Timeout**: 30s per attachment
**Input**: `{ "task_id": "string", "attachments": [Attachment] }`
**Output on SUCCESS**: `{ "preprocessed_attachments": [Attachment with preprocessed_path] }` -> GO TO STEP 3
**Output on FAILURE**:
  - `FAILURE(corrupt_image)`: Image cannot be decoded -> skip this attachment, log warning, continue with remaining attachments. If ALL attachments are corrupt -> fail task with `{ "error": "All attachments are corrupt or unreadable", "code": "ALL_ATTACHMENTS_CORRUPT", "retryable": false }`
  - `FAILURE(preprocessing_timeout)`: Single attachment took > 30s (likely very large PDF) -> skip, log warning, continue
  - `FAILURE(disk_full)`: Cannot write preprocessed file -> ABORT with `{ "error": "Disk full during attachment preprocessing", "code": "DISK_FULL", "retryable": true }`

**Observable states during this step**:
  - User sees: nothing (happens during orchestrator claim phase)
  - Operator sees: preprocessing pipeline running for task
  - Database: attachment records updated with preprocessed_path
  - Logs: `[preprocessor] task={task_id} attachment={att_id} resized={w}x{h} size={size_kb}KB`

---

### STEP 3: Route to vision-capable agent
**Actor**: Capability router (within orchestrator tick)
**Action**: When the orchestrator groups tasks for spawning: (a) Check if task has attachments. (b) If yes, filter available adapters to those with `vision_support=true` (see Capability Matrix). (c) Among vision-capable adapters, prefer the one matching the task's role. (d) If no vision-capable adapter matches the role, check if any adapter supports vision — use it with a role override note. (e) If NO vision-capable adapter is available at all, fall back to text-only mode: strip image references, use attachment descriptions as text context, and add a note to the prompt explaining that images were provided but the model cannot view them.
**Timeout**: N/A (part of orchestrator tick, not a separate HTTP call)
**Input**: `{ "task": Task, "available_adapters": [AdapterInfo], "preprocessed_attachments": [Attachment] }`
**Output on SUCCESS**: `{ "adapter": "string", "model": "string", "vision_mode": "native | text_fallback", "attachments_for_prompt": [Attachment] }` -> GO TO STEP 4
**Output on FAILURE**:
  - `FAILURE(no_adapters_available)`: No adapters available at all (all rate-limited or down) -> task stays in OPEN, retried next tick (standard orchestrator behavior)

**Observable states during this step**:
  - User sees: nothing (internal routing decision)
  - Operator sees: routing decision in orchestrator logs
  - Database: no change
  - Logs: `[router] task={task_id} routed to adapter={adapter} model={model} vision_mode={mode} attachments={n}`

---

### STEP 4: Construct multi-modal prompt
**Actor**: Spawner
**Action**: Build the agent prompt incorporating both text and image references. The prompt structure varies by adapter:

**For Claude Code adapter**:
```
## Task: {title}

{description}

## Visual References

The following image files are attached to this task. Read each one using the Read tool before starting implementation.

{for each attachment:}
### {attachment.type}: {attachment.description}
File: {attachment.preprocessed_path}

{end for}

## Instructions
1. Read all attached images first to understand the visual design/architecture
2. {task-specific instructions from description}
3. Implement what the diagrams describe
```

**For Gemini CLI adapter**:
Images passed via `--image` flag; text prompt references them by position.

**For text-fallback mode**:
```
## Task: {title}

{description}

## Visual References (text descriptions — images not viewable by this model)

{for each attachment:}
### {attachment.type}: {attachment.description}
(Original image at: {attachment.path} — not included in this prompt)

{end for}

Note: This task originally included {n} image attachments that describe the expected
output. The descriptions above summarize their content. Implement based on these
descriptions and the task instructions.
```

**Timeout**: 5s (prompt construction is CPU-only)
**Input**: `{ "task": Task, "adapter": "string", "vision_mode": "string", "preprocessed_attachments": [Attachment] }`
**Output on SUCCESS**: `{ "prompt": "string", "image_paths": ["string"], "token_estimate": int }` -> GO TO STEP 5
**Output on FAILURE**:
  - `FAILURE(prompt_too_large)`: Combined text + image token estimate exceeds model context window -> reduce images (drop lowest-priority attachments) and retry. If still too large after dropping all but one image -> log warning, proceed with single image.
  - `FAILURE(image_read_error)`: Preprocessed file missing or unreadable -> skip that attachment, log warning, proceed

**Observable states during this step**:
  - User sees: nothing
  - Operator sees: prompt construction logs
  - Database: no change
  - Logs: `[spawner] task={task_id} prompt constructed vision_mode={mode} images={n} estimated_tokens={tokens}`

---

### STEP 5: Spawn agent with multi-modal input
**Actor**: CLI adapter
**Action**: Launch the agent process with the multi-modal prompt. The adapter handles the mechanics of passing images to the specific CLI tool. The spawner writes image paths to the agent's working directory if needed. Standard timeout watchdog applies.
**Timeout**: standard task timeout (from ModelConfig or default 30 min)
**Input**: `{ "prompt": "string", "image_paths": ["string"], "workdir": Path, "model_config": ModelConfig, "session_id": "string" }`
**Output on SUCCESS**: `SpawnResult(pid, log_path, timeout_timer)` -> agent runs autonomously, completion via standard task lifecycle
**Output on FAILURE**:
  - `FAILURE(spawn_failed)`: Process failed to start -> standard retry via orchestrator
  - `FAILURE(adapter_image_error)`: Adapter could not pass images to CLI tool -> degrade to text-fallback, re-spawn

**Observable states during this step**:
  - User sees: agent status shows "working" in dashboard/status command
  - Operator sees: agent process running, heartbeats flowing
  - Database: task status = `claimed` -> `in_progress`
  - Logs: `[adapter:{name}] spawned session={session_id} pid={pid} vision={true/false} images={n}`

---

### STEP 6: Agent processes visual input
**Actor**: Agent process
**Action**: The agent reads the images (via Read tool for Claude, natively for Gemini, etc.), interprets the visual content, and produces code/configuration/documentation. This step is entirely within the agent — the orchestrator monitors via heartbeats. The agent is expected to:
  1. Read/view all attached images
  2. Describe what it sees (for traceability in logs)
  3. Map visual elements to code structures
  4. Implement the code
  5. Verify the implementation matches the visual input

**Timeout**: per task timeout
**Input**: agent reads from prompt + image files
**Output on SUCCESS**: task completed via `POST /tasks/{id}/complete` with `result_summary` describing what was implemented from the visual input
**Output on FAILURE**: standard agent failure modes (timeout, crash, task failure)

**Observable states during this step**:
  - User sees: agent progress updates via heartbeat
  - Operator sees: agent logs showing image interpretation and implementation steps
  - Database: task in_progress, heartbeat timestamps updating
  - Logs: `[agent:{session_id}] reading image {attachment_id} type={type}` (within agent's own log)

---

## State Transitions

```
Task lifecycle (standard + multi-modal additions):

[open] -> (orchestrator claims, STEP 2-3 runs) -> [claimed]
[claimed] -> (STEP 4-5: prompt built, agent spawned) -> [in_progress]
[in_progress] -> (agent completes) -> [done] -> (janitor verifies) -> [closed]
[in_progress] -> (agent fails) -> [failed] -> (retry?) -> [open]

Attachment lifecycle:

[raw] -> (STEP 1: validated) -> [validated]
[validated] -> (STEP 2: preprocessed) -> [preprocessed]
[preprocessed] -> (STEP 4: included in prompt) -> [consumed]
[consumed] -> (task closed + TTL expires) -> [cleaned_up]

Vision routing:

[task_has_attachments] -> (vision adapter available) -> [native_vision_mode]
[task_has_attachments] -> (no vision adapter) -> [text_fallback_mode]
```

---

## Handoff Contracts

### User -> Task Server (task creation with attachments)
**Endpoint**: `POST /tasks`
**Payload** (extended):
```json
{
  "title": "string",
  "description": "string",
  "role": "string",
  "attachments": [
    {
      "path": "string — local path or URL",
      "type": "architecture_diagram | ui_mockup | data_flow | erd | screenshot | wireframe | other",
      "description": "string — what this image shows"
    }
  ]
}
```
**Success response (201)**:
```json
{
  "id": "string",
  "status": "open",
  "has_attachments": true,
  "attachment_count": 2
}
```
**Failure response**:
```json
{
  "ok": false,
  "error": "string",
  "code": "ATTACHMENT_NOT_FOUND | ATTACHMENT_TOO_LARGE | UNSUPPORTED_FORMAT",
  "retryable": false
}
```

### Orchestrator -> Capability Router (internal)
**Function call**: `route_task(task, available_adapters)`
**Extended return**:
```python
@dataclass
class RoutingDecision:
    adapter: str
    model: str
    vision_mode: Literal["native", "text_fallback", "none"]
    attachments_for_prompt: list[Attachment]  # preprocessed, ordered by relevance
    fallback_reason: str | None  # why text_fallback was chosen, if applicable
```

### Spawner -> CLI Adapter (image passing)
**Extended CLIAdapter.spawn() signature**:
```python
def spawn(
    *,
    prompt: str,
    workdir: Path,
    model_config: ModelConfig,
    session_id: str,
    image_paths: list[Path] | None = None,  # NEW: preprocessed image files
    mcp_config: dict[str, Any] | None = None,
    timeout_seconds: int = 1800,
) -> SpawnResult:
```
**Contract**: Adapter MUST either pass images to the CLI tool in the tool's native format, or raise `AdapterImageError` if it cannot. The spawner catches this and retries in text-fallback mode.

---

## Cleanup Inventory

| Resource | Created at step | Destroyed by | Destroy method |
|---|---|---|---|
| Preprocessed image files in `.sdd/runtime/attachments/{task_id}/` | Step 2 | Cleanup after task closure | `rm -rf` directory after TTL (24h post-closure) |
| Attachment metadata in task record | Step 1 | Never (part of task audit trail) | Retained |
| Resized/converted temporary files | Step 2 | Immediate after preprocessing | Deleted within Step 2 on success |

---

## Adapter Implementation Guide

Each adapter that claims `vision_support=true` must implement these capabilities:

### Required adapter additions

```python
class CLIAdapter(ABC):
    @property
    @abstractmethod
    def supports_vision(self) -> bool:
        """Whether this adapter can pass images to the underlying CLI tool."""
        ...

    @property
    @abstractmethod
    def max_images_per_prompt(self) -> int:
        """Maximum number of images this adapter can include in a single prompt."""
        ...

    @property
    @abstractmethod
    def supported_image_formats(self) -> list[str]:
        """List of image formats this adapter accepts (e.g., ['png', 'jpg', 'webp'])."""
        ...
```

### Per-adapter image passing

| Adapter | How images are passed | Implementation notes |
|---|---|---|
| Claude Code | Images referenced by path in prompt text; agent uses Read tool to view them | No adapter changes needed for passing — Claude Code reads images natively. Prompt must instruct agent to Read the files. |
| Gemini CLI | `--image` flag per image file | Adapter builds command line with image flags |
| Aider | `--read` flag includes file in context | Only works if underlying model supports vision |
| Ollama | Via HTTP API `images` field (base64 encoded) | Adapter must base64-encode and include in API call |

---

## Test Cases

| Test | Trigger | Expected behavior |
|---|---|---|
| TC-01: Happy path — single PNG | Task with 1 architecture diagram PNG, Claude adapter | Image preprocessed, prompt includes Read instruction, agent produces code matching diagram |
| TC-02: Happy path — multiple images | Task with 3 attachments (mockup + ERD + flow) | All 3 preprocessed, included in prompt in order, agent references all |
| TC-03: SVG conversion | Task with SVG diagram | SVG converted to PNG, preprocessed PNG used in prompt |
| TC-04: PDF extraction | Task with single-page PDF | First page extracted as PNG, used in prompt |
| TC-05: Oversized image | 10000x8000 PNG | Resized to fit 4096x4096 box, aspect ratio preserved |
| TC-06: Corrupt image | Undecodable PNG file | Attachment skipped with warning, task proceeds with remaining attachments |
| TC-07: All attachments corrupt | 2 corrupt PNGs, no valid attachments | Task fails with ALL_ATTACHMENTS_CORRUPT |
| TC-08: Non-vision adapter | Task with images, only Codex available | Text-fallback mode: descriptions substituted for images, prompt notes degradation |
| TC-09: Unsupported format | Task with .bmp attachment | 400 error at task creation, lists supported formats |
| TC-10: Attachment file missing | Path references nonexistent file | 400 error at task creation |
| TC-11: Prompt too large | 10 high-res images exceed context window | Low-priority images dropped until prompt fits; warning logged |
| TC-12: Adapter image error | Gemini CLI rejects image flag | Adapter raises error, spawner retries in text-fallback mode |
| TC-13: Plan YAML with attachments | Plan file references image paths | Images resolved relative to plan file directory, preprocessed normally |
| TC-14: URL attachment | Attachment path is HTTPS URL | File fetched, validated, preprocessed same as local file |
| TC-15: Attachment cleanup | Task completed and closed for 24+ hours | Preprocessed files in .sdd/runtime/attachments/{task_id}/ deleted |

---

## Assumptions

| # | Assumption | Where verified | Risk if wrong |
|---|---|---|---|
| A1 | Claude Code's Read tool can natively render images when given a file path | Verified: Claude Code is multimodal and Read tool handles images | Low |
| A2 | Gemini CLI supports `--image` flag for passing images | Not verified — needs testing against current Gemini CLI version | Medium: if not supported, Gemini adapter cannot do native vision |
| A3 | SVG rasterization produces sufficient quality at 2x resolution for model interpretation | Not verified — depends on SVG complexity | Medium: complex SVGs with tiny text may lose detail |
| A4 | Single-page PDF extraction covers the majority of diagram use cases | Assumed based on typical architecture docs | Low: multi-page support can be added later as enhancement |
| A5 | 4096x4096 max dimension after preprocessing is within all vision models' limits | Claude supports up to 8192x8192; Gemini varies | Low: conservative limit covers all current models |
| A6 | Token cost of including images is predictable enough for budget forecasting | Not verified — image token costs vary by model and resolution | Medium: could blow budgets on image-heavy tasks |
| A7 | Attachment descriptions provided by users are accurate enough for text-fallback mode | Depends entirely on user input quality | High: poor descriptions make text-fallback nearly useless; consider adding description quality check |
| A8 | Agents will actually look at the images before coding (not just read the text description) | Not verified — depends on prompt quality | Medium: prompt engineering critical; may need explicit "describe what you see" step |

---

## Open Questions

- **Q1**: Should the preprocessor run an image classification/description step (e.g., via a cheap vision model) to auto-generate or augment user-provided descriptions? This would improve text-fallback quality and help the agent understand context.
- **Q2**: How should image token costs be estimated for budget tracking? Provider APIs charge differently for images (Claude uses tiles, Gemini uses a flat cost). The cost module needs per-provider image cost models.
- **Q3**: Should agents be required to output a "visual interpretation" step before coding — describing what they see in the image — to create an auditable link between visual input and code output?
- **Q4**: For UI mockups, should the workflow include a visual regression step after implementation (screenshot the output, compare to the mockup)? This is high-value but requires browser automation.
- **Q5**: Should Bernstein support image generation as output (not just input)? E.g., an agent generates an architecture diagram from code. This is the inverse workflow and may be a separate spec.
- **Q6**: How do we handle image attachments in the context collapse pipeline? Images are large in token terms — do we strip them first when context gets tight, or keep them as highest-priority context?

---

## Spec vs Reality Audit Log

| Date | Finding | Action taken |
|---|---|---|
| 2026-04-11 | Initial spec created | — |
| 2026-04-11 | Existing `CLIAdapter.spawn()` signature does not include `image_paths` parameter | Documented required interface extension in Handoff Contracts |
| 2026-04-11 | Existing `Task` dataclass has no `attachments` or `has_attachments` field | Documented required model extension |
| 2026-04-11 | Existing `RoutingDecision` in `router.py` has no `vision_mode` field | Documented required extension in Handoff Contracts |
| 2026-04-11 | Claude Code adapter's Read tool already supports image files natively | Confirmed A1 — lowest friction path for Claude |
| 2026-04-11 | Existing `context_collapse.py` has no awareness of image content — could strip images aggressively | Flagged as Q6; needs design decision |
| 2026-04-11 | Existing cost module (`cost.py`) has no image-token cost models | Flagged as Q2; blocks accurate budget tracking for vision tasks |
