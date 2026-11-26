# JARVIS AI Operating System: Phase 1 (Core backend)

## Architecture
```
 clients (Agent / Tray / Dashboard / Android)        Phase 1 scope
        │  Bearer token, 127.0.0.1 only              ───────────────
        ▼
 FastAPI (api/app.py) ── require_auth on every route
   ├── MemoryService ── MemoryPrivacyPolicy ── MemoryRepository ── SQLAlchemy (SQLite; Postgres-ready)
   │                         ▲                       └── FieldCipher (Fernet) for PRIVATE/SENSITIVE rows
   ├── ClaudeSafeContextBuilder  (PUBLIC only, queried at DB level)
   └── ClaudeService ── Anthropic SDK (streaming, retry+backoff+jitter, timeouts)
 SecureStorage: DPAPI | Keychain | Secret Service | encrypted file  (API key, API token, memory key)
```
Conversation history is supplied by the client per request and is never persisted.

## Folder structure
```
jarvis/
├── jarvis_core/
│   ├── config.py                 settings, model aliases
│   ├── __main__.py               CLI: serve | set-api-key | show-token
│   ├── db/        session.py models.py
│   ├── security/  secure_storage.py
│   ├── memory/    schemas crypto privacy repository service context
│   ├── claude/    service.py
│   └── api/       app.py
└── tests/         51 tests (security, memory, privacy, claude, api)
```

## Setup / run
```
python -m venv .venv && . .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python -m jarvis_core set-api-key      # prompts, stores in secure storage
python -m jarvis_core serve            # http://127.0.0.1:8750
python -m jarvis_core show-token       # token for Authorization: Bearer <token>
python -m pytest
```
Data lives in `~/.jarvis` (override with `JARVIS_DATA_DIR`).
Models: `JARVIS_SONNET_MODEL` / `JARVIS_OPUS_MODEL` (defaults `claude-sonnet-5-5`, `claude-opus-5-5`).
