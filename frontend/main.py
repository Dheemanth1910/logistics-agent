"""Minimal FastAPI proxy for a deployed A2A agent (Agent Runtime, agents-cli 1.1.0+).

The browser talks ONLY to this proxy (same origin, no CORS, no GCP creds in the
browser). The proxy authenticates with Application Default Credentials and
forwards chat to the deployed agent over the A2A protocol, returning replies as
structured parts the chat UI knows how to show:

  * {"kind": "text", "text": ...}  -> a normal chat bubble
  * {"kind": "a2ui", "data": ...}  -> one A2UI message (beginRendering /
    surfaceUpdate); static/index.html renders these as a card.

Why A2A: agents-cli 1.1.0 (GA) deploys ADK agents to Agent Runtime as A2A agents
and no longer registers the reasoning-engine operation schema the old
`agent_engines.get(...).stream_query()` path relied on (operation_schemas() comes
back empty). The container serves the A2A protocol over the Agent Engine HTTP
passthrough, so this proxy fetches the agent's card and sends messages with the
a2a-sdk client (the same path `agents-cli run --mode a2a` uses). This works for
both A2A and plain ADK 1.1.0 deployments (the container serves A2A either way).

Run:
  pip install -r requirements.txt
  export AGENT_ENGINE_RESOURCE_NAME="projects/.../locations/.../reasoningEngines/..."
  export AGENT_DIRECTORY="app"   # your agent's app directory (agents-cli-manifest.yaml)
  python main.py                 # -> http://localhost:8080
"""

import os
import uuid

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
import google.auth
import google.auth.transport.requests
import google.protobuf.json_format as json_format
import httpx

load_dotenv()

RESOURCE = os.environ.get(
    "AGENT_ENGINE_RESOURCE_NAME",
    "projects/875763921497/locations/us-east1/reasoningEngines/8705789032707129344",
)
# The agent's app directory (matches agent_directory in agents-cli-manifest.yaml).
AGENT_DIRECTORY = os.environ.get("AGENT_DIRECTORY", "app")
# Location is embedded in the resource name: projects/<p>/locations/<loc>/reasoningEngines/<id>.
LOCATION = (
    RESOURCE.split("/locations/")[1].split("/")[0]
    if "/locations/" in RESOURCE
    else "us-east1"
)

# A2A endpoint for an Agent Runtime deployment, via the Agent Engine HTTP
# passthrough. The card lives at the well-known path under this base.
A2A_BASE = (
    f"https://{LOCATION}-aiplatform.googleapis.com/reasoningEngines/v1/"
    f"{RESOURCE}/api/a2a/{AGENT_DIRECTORY}"
)
A2A_CARD_URL = f"{A2A_BASE}/.well-known/agent-card.json"

# The agent tags its A2UI data parts with this mime type.
_A2UI_MIME = "application/json+a2ui"

# One set of ADC credentials, refreshed per request (access tokens expire ~1h).
_creds, _ = google.auth.default(
    scopes=["https://www.googleapis.com/auth/cloud-platform"]
)


def _auth_headers() -> dict[str, str]:
    _creds.refresh(google.auth.transport.requests.Request())
    return {
        "Authorization": f"Bearer {_creds.token}",
        "Content-Type": "application/json",
    }


app = FastAPI()


@app.exception_handler(Exception)
async def _json_errors(request: Request, exc: Exception):
    # Always return JSON so the browser never receives a plain-text 500 page.
    return JSONResponse(
        status_code=200,
        content={
            "parts": [{"kind": "text", "text": f"Error: {type(exc).__name__}: {exc}"}]
        },
    )


# Reuse ONE A2A context per user so the agent remembers the conversation.
_contexts: dict[str, str] = {}
# Cache the agent card after the first fetch.
_card = None


async def _get_card(client: httpx.AsyncClient):
    global _card
    if _card is None:
        resp = await client.get(A2A_CARD_URL)
        resp.raise_for_status()
        data = resp.json()
        from a2a.types import AgentCard

        try:
            card = json_format.ParseDict(data, AgentCard(), ignore_unknown_fields=True)
            if hasattr(card, "supported_interfaces"):
                for interface in card.supported_interfaces:
                    interface.url = A2A_BASE
            if hasattr(card, "url"):
                card.url = A2A_BASE
        except Exception:
            card = AgentCard(**data)
            card.url = A2A_BASE
        _card = card
    return _card


def _extract_parts(parts: list) -> list[dict]:
    """Turn A2A response parts into structured parts for the chat UI."""
    out: list[dict] = []
    for p in parts:
        root = getattr(p, "root", p)
        text = getattr(root, "text", None)
        if text:
            out.append({"kind": "text", "text": text})
            continue
        data = getattr(root, "data", None)
        if data is not None:
            meta = getattr(root, "metadata", None) or {}
            mime = (
                meta.get("mimeType")
                if isinstance(meta, dict)
                else getattr(meta, "mime_type", None)
            )
            if mime == _A2UI_MIME or getattr(root, "media_type", None) == _A2UI_MIME:
                out.append({"kind": "a2ui", "data": data})
                continue
        url = getattr(root, "url", None)
        if url:
            out.append({"kind": "text", "text": url})
            continue
        file_uri = getattr(getattr(root, "file", None), "uri", None)
        if file_uri:
            out.append({"kind": "text", "text": file_uri})
    return out


@app.post("/chat")
async def chat(req: Request):
    body = await req.json()
    message = body.get("message", "")
    user_id = body.get("user_id") or "web-user"
    parts: list[dict] = []

    async with httpx.AsyncClient(headers=_auth_headers(), timeout=120) as client:
        card = await _get_card(client)
        from a2a.client import ClientConfig, ClientFactory

        try:
            from a2a.types import TransportProtocol

            factory = ClientFactory(
                ClientConfig(
                    supported_transports=[
                        TransportProtocol.jsonrpc,
                        TransportProtocol.http_json,
                    ],
                    httpx_client=client,
                )
            )
        except Exception:
            factory = ClientFactory(ClientConfig(httpx_client=client))

        a2a_client = factory.create(card)

        from a2a.types import Message, Part, Role

        user_role = getattr(Role, "ROLE_USER", getattr(Role, "user", 1))

        try:
            from a2a.types import TextPart

            part = Part(root=TextPart(text=message))
        except Exception:
            part = Part(text=message)

        msg_kwargs = {
            "message_id": str(uuid.uuid4()),
            "role": user_role,
            "parts": [part],
        }
        if _contexts.get(user_id):
            msg_kwargs["context_id"] = _contexts[user_id]

        msg = Message(**msg_kwargs)

        try:
            from a2a.types import SendMessageRequest

            send_arg = SendMessageRequest(message=msg)
        except Exception:
            send_arg = msg

        last_task = None
        got_artifact_update = False

        async for event in a2a_client.send_message(send_arg):
            if isinstance(event, tuple):
                task, update = event
                if task is not None:
                    last_task = task
                    if getattr(task, "context_id", None):
                        _contexts[user_id] = task.context_id
                if update is not None and hasattr(update, "artifact"):
                    got_artifact_update = True
                    parts.extend(_extract_parts(update.artifact.parts))
            elif hasattr(event, "DESCRIPTOR"):
                if event.HasField("task"):
                    last_task = event.task
                    if event.task.context_id:
                        _contexts[user_id] = event.task.context_id
                if event.HasField("artifact_update"):
                    got_artifact_update = True
                    parts.extend(_extract_parts(event.artifact_update.artifact.parts))
                elif event.HasField("message"):
                    parts.extend(_extract_parts(event.message.parts))

        # Non-streaming fallback
        if not got_artifact_update and last_task is not None:
            for artifact in getattr(last_task, "artifacts", None) or []:
                parts.extend(_extract_parts(artifact.parts))

    if not parts:
        parts = [{"kind": "text", "text": "(The agent didn't return a reply.)"}]
    return JSONResponse({"parts": parts})


# Serve the chat UI (keep this mount last so /chat wins).
app.mount("/", StaticFiles(directory="frontend/static" if os.path.isdir("frontend/static") else "static", html=True), name="static")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
