# Bernstein Conference Demo Kit

This directory contains resources for speakers, dev advocates, and contributors presenting Bernstein at conferences, meetups, or internal demos.

## Pre-recorded Demos

For live presentations where Wi-Fi is unreliable, use the pre-recorded MP4 files:

| Demo | Topics Covered | File | 
|---|---|---|
| **5-Minute Lightning** | 0-config init, Evolve mode, Task graphs | `demo-lightning.mp4` | 
| **15-Minute Deep Dive** | Quality Gates, Cross-model verification, Cost Dashboard | `demo-deep-dive.mp4` |
| **Enterprise Features** | RBAC, SOC 2 Audit Mode, PR Merging | `demo-enterprise.mp4` |

*Note: Demo MP4s are stored in the `assets/` directory on the GitHub release page due to file size limits.*

## Interactive Conference CLI Demo

Bernstein includes a built-in automated presentation mode that runs a simulated workspace for your audience.

```bash
# Run the polished 5-minute interactive demo with commentary
bernstein demo --conference
```

**How to present it:**
1. Start the command at the beginning of your talk.
2. The CLI will pause at key moments (highlighting task execution, quality gate failures, and automatic code review).
3. Press `Enter` to advance through each pause as you explain the concepts to the audience.

## Slide Deck Templates

* **[Google Slides Template](https://docs.google.com/presentation/d/example)**
* **[Keynote Template](https://apple.com/keynote/example)**

### Key Talking Points

1. **The Orchestration Problem:** "Prompt engineering isn't enough anymore. We need Agentic Engineering."
2. **Deterministic Graphs:** "Bernstein schedules agents like Kubernetes schedules containers."
3. **Quality Gates:** "We don't trust LLMs blindly. Bernstein uses cross-model verification to review every PR."
4. **Compliance:** "Built-in SOC 2 audit logs, immutable Merkle seals, and RBAC."

## Branding Assets

Logos, typography guidelines, and color hex codes can be found in `docs/assets/branding/`. Please use the appropriate high-res SVGs for presentations.
