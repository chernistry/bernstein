---
name: bernstein-signal-check
description: Check for orchestrator signals during work
whenToUse: Periodically while working, approximately every 60 seconds, or before starting a long-running operation
---

Check for orchestrator signals:

```bash
cat .sdd/runtime/signals/{{SESSION_ID}}/WAKEUP 2>/dev/null
cat .sdd/runtime/signals/{{SESSION_ID}}/SHUTDOWN 2>/dev/null
```

If **SHUTDOWN** exists: commit any in-progress work and exit immediately:

```bash
git add -A && git commit -m "[WIP] work in progress" 2>/dev/null || true
exit 0
```

If **WAKEUP** exists: read its contents, address the concern described, then continue your work.
