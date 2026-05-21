"""TLS-terminated fake NIM gateway used by the CLM Phase 2.5 mTLS test.

Spawned as a uvicorn subprocess (see
``test_adapter_clm_with_fake_nim.py``); kept tiny on purpose so the
subprocess starts in <1s on CI runners. The bearer-token check stays
in lockstep with the in-thread fake NIM so the two share a wire-format
contract - only the transport layer differs.
"""

from __future__ import annotations

from fastapi import FastAPI, Header, HTTPException

_FAKE_NIM_TOKEN = "scoped-jwt-fake-nim-mtls"
_FAKE_NIM_REPLY = "mtls handshake ok"

app = FastAPI()


@app.post("/v1/chat/completions")
async def chat(authorization: str = Header(default="")) -> dict[str, object]:
    if authorization != f"Bearer {_FAKE_NIM_TOKEN}":
        raise HTTPException(status_code=401, detail="bad token")
    # A non-streaming completion is enough - we're testing the TLS layer,
    # not SSE assembly (that's already covered by the plaintext test).
    return {
        "id": "chatcmpl-mtls-fake",
        "object": "chat.completion",
        "model": "clm-7b-instruct",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": _FAKE_NIM_REPLY},
                "finish_reason": "stop",
            }
        ],
    }
