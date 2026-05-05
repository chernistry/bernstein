"""Trivial FastAPI app loaded by the mTLS handshake integration test."""

from __future__ import annotations

from fastapi import FastAPI

app = FastAPI()


@app.get("/cluster/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
