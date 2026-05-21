# Team-hub convention paths

A team hub is a directory tree that ships shared agents, skills,
and rules across multiple repositories without symlinks. The
convention pins one manifest filename (`team-hub.yaml`) and one
sub-directory (`team/`) so the loader can detect a hub by inspection.

## Why it exists

Multi-repo projects accumulate the same role templates, skill
packs, and prompt rules in three places at once. Symlinking one
canonical copy into every repo works on Linux but breaks on
Windows checkouts and on shared CI runners that copy worktrees
between machines. A convention-driven hub solves both: the
manifest tells the loader exactly which paths to expose, and
every consumer mirrors the directory layout instead of resolving
links at runtime.

## Hub layout

```text
<hub-root>/
    team-hub.yaml          # required manifest (strict-validated)
    team/
        agents/<name>/      # role / agent templates
        skills/<name>/      # skill packs (SKILL.md inside)
        rules/<name>.md     # plain-text rules consumed by the planner
```

The manifest enumerates which entries the hub publishes. Three
buckets are recognised: `agents`, `skills`, `rules`. Each entry
points at a path inside `team/`; the loader resolves it on disk
and rejects entries that escape the hub root.

## How to use it

```python
from pathlib import Path
from bernstein.core.plugins_core.team_hub_loader import load_team_hub

hub = load_team_hub(Path("/path/to/hub-repo"))
if hub is None:
    # No hub installed - graceful degradation
    pass
else:
    for entry in hub.entries:
        print(entry.bucket, entry.relative, "->", entry.absolute)

    # Filter by bucket
    skill_packs = hub.by_bucket("skills")
```

Manifest example (`team-hub.yaml`):

```yaml
name: acme-platform-hub
version: "1"
ships:
  agents:
    - reviewer
    - release-manager
  skills:
    - ci-discipline
  rules:
    - prefer-typing.md
    - no-stale-todo.md
```

## Failure modes

- **No hub installed.** Missing hub root, missing `team-hub.yaml`,
  or missing `team/` directory yields `None`. This is the graceful
  no-op the loader is designed for: a consumer that calls
  `load_team_hub` on every spawn keeps working when the network is
  down or the hub has not been cloned yet.
- **Manifest broken.** A malformed manifest, a bucket entry that
  escapes the hub root, or an entry that points at a non-existent
  path raises a hard error so the operator knows the hub is broken
  before it silently disappears from the resolved graph.

## Limitations

- Read-only by design. Clone, pull, and resolution-path merging
  live in later slices, so this loader can be unit-tested against
  a fixture directory without touching git.
- Manifest size is capped at 64 KiB; a real `team-hub.yaml` is
  well under that. The cap prevents pathological YAML inputs from
  exhausting the loader.
- Bucket vocabulary is fixed at `agents`, `skills`, `rules` for
  this slice. Custom buckets are a follow-up.

## Related

- Loader: `src/bernstein/core/plugins_core/team_hub_loader.py`
- Manifest schema: `src/bernstein/core/plugins_core/team_hub_manifest.py`
- [Skill packs](../architecture/skills.md)
