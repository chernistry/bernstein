# AGENTS.md — Task Rules

## Always Required

1. **Read the complete function before modifying it.** Use offset/limit or Grep to LOCATE
   the function, then read the entire body before writing any changes.

2. **No console.log / console.debug / console.info.** Remove them if already in existing code.

## Forbidden patterns — automatic review failure

**Error details in HTTP responses**
```python
# ❌ WRONG
return jsonify({"error": str(e)}), 500
return jsonify({"error": f"Failed: {e}"}), 500

# ✅ CORRECT
return jsonify({"error": "An error occurred. Please try again."}), 500
```

**No print() in production code**
```python
# ❌ WRONG
print(f"Debug: {data}")

# ✅ CORRECT
import logging; logging.error("Debug: %s", data)
```

**Complete function bodies — no truncation**
```python
# ❌ WRONG
# ... rest of function unchanged

# ✅ Write every line of the modified function
```

## Acceptance Criteria (must ALL be addressed)

- Generate self-contained HTML report with inline CSS only
- Include run summary, task timeline, and cost breakdown sections
- Implement --format md flag to output Markdown
- Add repository footer link and enforce 500KB file size limit
