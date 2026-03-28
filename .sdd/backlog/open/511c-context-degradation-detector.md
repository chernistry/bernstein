# 511c — Context Degradation Detector
**Role:** backend **Priority:** 1 **Scope:** small

Monitor agent output quality over time. If quality drops (detectable via cross-model verifier): checkpoint progress → terminate → spawn fresh agent with summarized context. Turns Devin's main weakness into Bernstein's strength.
