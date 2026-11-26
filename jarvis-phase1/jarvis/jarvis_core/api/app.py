from __future__ import annotations

import hmac
import json
import secrets
from typing import Literal

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from .. import __version__
from ..claude import ClaudeNotConfiguredError, ClaudeService, ClaudeUnavailableError
from ..config import Settings
from ..db.session import create_db_engine, init_db, make_session_factory
from ..memory import (ClaudeSafeContextBuilder, Classification, ImportError_, MemoryCreate, MemoryNotFound,
                      MemoryPrivacyPolicy, MemoryRepository, MemoryService, MemoryUpdate)
from ..memory.crypto import FieldCipher
from ..security.secure_storage import SecureStorage, get_secure_storage

API_KEY_SECRET = "anthropic_api_key"
API_TOKEN_SECRET = "api_token"
MEMORY_KEY_SECRET = "memory_key"


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=50_000)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=50_000)
    history: list[ChatMessage] = Field(default_factory=list, max_length=100)  # client-held; never persisted
    model: Literal["sonnet", "opus"] = "sonnet"
    use_memory: bool = True


def create_app(settings: Settings | None = None, storage: SecureStorage | None = None,
               claude: ClaudeService | None = None) -> FastAPI:
    settings = settings or Settings()
    settings.ensure_dirs()
    storage = storage or get_secure_storage(settings.data_dir)

    engine = create_db_engine(settings.resolved_database_url())
    init_db(engine)
    policy = MemoryPrivacyPolicy()
    cipher = FieldCipher(storage.get_or_create(MEMORY_KEY_SECRET, FieldCipher.generate_key))
    repo = MemoryRepository(make_session_factory(engine), cipher, policy)
    memory = MemoryService(repo, policy)
    context = ClaudeSafeContextBuilder(repo, policy)
    claude = claude or ClaudeService(settings, lambda: storage.get(API_KEY_SECRET))
    token = storage.get_or_create(API_TOKEN_SECRET, lambda: secrets.token_urlsafe(32))

    async def require_auth(authorization: str | None = Header(default=None)) -> None:
        scheme, _, supplied = (authorization or "").partition(" ")
        if scheme.lower() != "bearer" or not hmac.compare_digest(supplied.encode(), token.encode()):
            raise HTTPException(401, "Authentication required", headers={"WWW-Authenticate": "Bearer"})

    app = FastAPI(title="JARVIS Core", version=__version__, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.memory, app.state.claude = memory, claude
    r = APIRouter(dependencies=[Depends(require_auth)])

    @r.get("/health")
    async def health():
        return {"status": "ok", "version": __version__,
                "services": {"MemoryService": memory.health(), "ClaudeService": claude.health()},
                "secure_storage": storage.backend}

    # ---- memory ----
    @r.post("/memory", status_code=201)
    async def create_memory(body: MemoryCreate):
        return memory.create(body)

    @r.get("/memory")
    async def list_memory(classification: Classification | None = None, include_deleted: bool = False):
        return memory.list(classification, include_deleted)

    @r.get("/memory/search")
    async def search_memory(q: str = Query(min_length=1, max_length=500), limit: int = Query(10, ge=1, le=50)):
        return memory.search(q, limit)

    @r.get("/memory/export")
    async def export_memory(include_sensitive: bool = False):
        return memory.export_memories(include_sensitive)

    @r.post("/memory/import")
    async def import_memory(payload: dict):
        try:
            return {"imported": memory.import_memories(payload)}
        except ImportError_ as exc:
            raise HTTPException(422, str(exc)) from exc

    @r.post("/memory/purge-expired")
    async def purge_expired():
        return {"purged": memory.purge_expired()}

    @r.get("/memory/{memory_id}")
    async def get_memory(memory_id: str):
        try:
            return memory.get(memory_id)
        except MemoryNotFound:
            raise HTTPException(404, "Memory not found")

    @r.patch("/memory/{memory_id}")
    async def update_memory(memory_id: str, body: MemoryUpdate):
        try:
            return memory.update(memory_id, body)
        except MemoryNotFound:
            raise HTTPException(404, "Memory not found")

    @r.post("/memory/{memory_id}/restore")
    async def restore_memory(memory_id: str):
        try:
            return memory.restore(memory_id)
        except MemoryNotFound:
            raise HTTPException(404, "Memory not found")

    @r.delete("/memory/{memory_id}", status_code=204)
    async def delete_memory(memory_id: str, hard: bool = False):
        try:
            memory.hard_delete(memory_id) if hard else memory.soft_delete(memory_id)
        except MemoryNotFound:
            raise HTTPException(404, "Memory not found")

    # ---- chat ----
    def _prepare(req: ChatRequest):
        msgs = [m.model_dump() for m in req.history] + [{"role": "user", "content": req.message}]
        ctx = context.build(req.message) if req.use_memory else ""
        return msgs, ctx or None

    @r.post("/chat")
    async def chat(req: ChatRequest):
        msgs, ctx = _prepare(req)
        try:
            return {"reply": await claude.complete(msgs, req.model, ctx)}
        except ClaudeNotConfiguredError:
            raise HTTPException(503, "Boss, no Anthropic API key is configured yet.")
        except ClaudeUnavailableError as exc:
            raise HTTPException(502, str(exc))

    @r.post("/chat/stream")
    async def chat_stream(req: ChatRequest):
        msgs, ctx = _prepare(req)
        if not claude.is_configured():
            raise HTTPException(503, "Boss, no Anthropic API key is configured yet.")

        async def events():
            try:
                async for chunk in claude.stream(msgs, req.model, ctx):
                    yield f"data: {json.dumps({'delta': chunk})}\n\n"
                yield f"data: {json.dumps({'done': True})}\n\n"
            except (ClaudeUnavailableError, ClaudeNotConfiguredError) as exc:
                yield f"data: {json.dumps({'error': str(exc)})}\n\n"

        return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-store"})

    app.include_router(r)
    return app
