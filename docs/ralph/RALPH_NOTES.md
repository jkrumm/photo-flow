# RALPH Notes — Photo-Flow Control Panel

Per-group learning notes. Each group appends its section here after completion.

## Group 1: Foundation — packaging, deps, FastAPI skeleton, test harness

### What was implemented
Added FastAPI/uvicorn/sse-starlette deps to `setup.py` and `requirements.txt` (also re-added the missing `python-dotenv`), bumped version to 0.4.0, created the `photo_flow/api/` package with a `/health` route and `__main__.py` entry point, and stood up a `tests/` harness with a health-check test and an import smoke test.

### Deviations from prompt
None — implemented exactly as specified.

### Gotchas & surprises
`requirements.txt` was missing `python-dotenv` despite `immich_client.py` importing it (the bug was real). The existing venv used pip 21.2.4 which still works fine — no upgrade needed for this group.

### Security notes
`__main__.py` binds to `127.0.0.1:7720` only — never `0.0.0.0`. The `photoflow serve` console entry is deferred to Group 13 per the prompt.

### Tests added
- `tests/test_health.py` — `test_health()` via FastAPI `TestClient`
- `tests/test_imports.py` — smoke-imports cli, workflow, metadata_extractor, api.app

### Future improvements
`venv/bin/pip install -e .[dev]` pattern could be used instead of separate `pip install pytest httpx` once the dev extras are wired into CI.
