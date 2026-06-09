# Group 1: Foundation — packaging, deps, FastAPI skeleton, test harness

## What You're Doing

Lay the foundation: add the new Python dependencies, create an empty-but-importable
`photo_flow/api/` FastAPI package with a `/health` route, and stand up a `pytest` test harness.
No business logic yet. This group has **no validation gate** — it only needs to leave the repo
importable and `venv/bin/photoflow --help` working.

---

## Research & Exploration First

1. Read `control_panel/PRD.md` (architecture + decisions).
2. Read `setup.py` and `requirements.txt` — note they are out of sync (`requirements.txt` omits
   `python-dotenv`, which `immich_client.py` imports). Fix that while you're here.
3. Read `photo_flow/cli.py` to see how `PhotoWorkflow` is instantiated and called.

---

## What to Implement

### 1. Dependencies

Add to `setup.py` `install_requires` and `requirements.txt` (keep them in sync, re-add the missing
`python-dotenv`):
```
fastapi>=0.115
uvicorn[standard]>=0.32
sse-starlette>=2.1
python-dotenv>=1.0.0
```
Add a dev/test extra (or a `requirements-dev.txt`) with `pytest>=8` and `httpx>=0.27` (FastAPI
TestClient needs httpx). Then install into the existing venv:
```bash
venv/bin/pip install -e . && venv/bin/pip install pytest httpx
```

### 2. `photo_flow/api/` package

Minimal FastAPI app — `photo_flow/api/__init__.py` and `photo_flow/api/app.py`:
```python
# app.py
from fastapi import FastAPI

def create_app() -> FastAPI:
    app = FastAPI(title="Photo-Flow Control Panel", version="0.4.0")

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok"}

    return app

app = create_app()
```
Add `photo_flow/api/__main__.py` so `python -m photo_flow.api` runs uvicorn bound to
`127.0.0.1:7720` (import the app, `uvicorn.run(app, host="127.0.0.1", port=7720)`). Do NOT add the
`photoflow serve` console entry yet — that lands in Group 13.

### 3. Test harness

Create `tests/__init__.py` and `tests/test_health.py` using FastAPI `TestClient`:
```python
from fastapi.testclient import TestClient
from photo_flow.api.app import create_app

def test_health():
    client = TestClient(create_app())
    assert client.get("/health").json() == {"status": "ok"}
```
Add a `tests/test_imports.py` that imports the core modules (smoke).

### 4. Gitignore

Ensure these are ignored (the runner manages most; add web build dirs):
```
control_panel/web/node_modules/
control_panel/web/dist/
```
(`.ralph-*`, `*.env`, `venv/` are already covered.)

---

## Validation

No gate for Group 1. Sanity-check yourself:
```bash
venv/bin/python -c "import photo_flow.api.app"
venv/bin/python -m pytest -q
venv/bin/photoflow --help
```

---

## Commit

```
feat(api): scaffold FastAPI package, deps, and pytest harness
```

---

## Done

Append learning notes to `docs/ralph/RALPH_NOTES.md`, then:
```
RALPH_TASK_COMPLETE: Group 1
```
