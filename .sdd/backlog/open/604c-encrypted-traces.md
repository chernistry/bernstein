# 604c — Encrypted Trace Storage with Key Rotation
**Role:** backend **Priority:** 2 **Scope:** medium

AES-256 encryption for all trace files. Customer-managed keys via env var or Vault. Automated key rotation with re-encryption. SOC 2 + HIPAA at-rest requirement.
