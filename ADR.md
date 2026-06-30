# PhotoFlow Desktop App - Architecture Decision Records

> **Purpose**: LLM-optimized reference for AI coding agents working on PhotoFlow GUI migration.
> **Status**: Planning phase - decisions finalized, implementation not started.
> **Last Updated**: 2026-02-02

---

## Project Context

**Current State**: CLI tool (`photo-flow`) for managing Fuji X-T4 photography workflow.
**Target State**: Commercial GUI desktop app for non-technical users.
**Distribution**: Direct download initially, macOS App Store later.

---

## ADR-001: Desktop Framework Selection

### Decision: Tauri 2 + Python Sidecar

### Alternatives Considered

| Option | Pros | Cons | Verdict |
|--------|------|------|---------|
| **Tauri 2 + Python Sidecar** | Small bundle (~10MB), native performance, reuse 85% Python code, user knows TS/React | Multi-language complexity | ✅ SELECTED |
| Full Rust Rewrite | Single language, best performance | 6+ months work, lose Python expertise | ❌ Rejected |
| Electron + Python | Familiar, large ecosystem | 150MB+ bundle, resource heavy | ❌ Rejected |
| PyQt/PySide6 | Pure Python | Dated UI, hard to make modern | ❌ Rejected |
| NiceGUI/Gradio | Quick prototyping | Not suitable for commercial desktop | ❌ Rejected |

### Rationale
- User comfortable with TypeScript/React (existing skill)
- 85% of existing Python logic reusable via sidecar
- Tauri produces small, native bundles ideal for App Store
- Modern web UI capabilities (animations, styling flexibility)

### Implementation Notes
```
Tauri spawns Python sidecar as subprocess
Communication: JSON via stdout/stdin
Python sidecar receives --config flag pointing to settings JSON
```

---

## ADR-002: Monorepo Structure

### Decision: Bun Workspaces + Turborepo

### Repository Layout
```
photoflow/
├── apps/
│   ├── desktop/              # Tauri 2 app
│   │   ├── src/              # React frontend (TypeScript)
│   │   └── src-tauri/        # Rust backend
│   ├── web/                  # Astro marketing site
│   └── python/               # Python sidecar (bundled with PyInstaller)
│       ├── photo_flow/       # Existing CLI code (refactored)
│       └── tests/            # pytest unit tests
├── packages/
│   ├── config/               # Shared configs + JSON Schema
│   │   ├── schema/           # settings.schema.json
│   │   └── src/              # Generated TypeScript types
│   └── ui/                   # basalt-ui integration (if needed)
├── e2e/
│   └── desktop/              # WebdriverIO or Playwright tests
├── package.json              # Bun workspace root
├── bun.lockb
├── turbo.json                # Turborepo config
├── biome.json                # TS/JS linting
└── rustfmt.toml              # Rust formatting
```

### Package Manager: Bun
- Officially supported and recommended by Tauri 2
- Faster than pnpm/npm
- Native workspace support

### Build Orchestration: Turborepo
- Manages JS/TS build dependencies
- Python and Rust use native tooling (not Turborepo)

---

## ADR-003: External Dependency Elimination

### Decision: Replace exiftool and rsync with Pure Python

### exiftool Replacement

| Library | Approach | Decision |
|---------|----------|----------|
| **pyexiv2** | Embeds libexiv2, full EXIF/IPTC/XMP support | ✅ SELECTED |
| piexif | Pure Python, EXIF only | ❌ No XMP/IPTC |
| Pillow | Basic EXIF only | ❌ Loses ratings on save |

### rsync Replacement

| Option | Approach | Decision |
|--------|----------|----------|
| **Deferred** | Backup feature post-MVP | ✅ SELECTED |
| paramiko | Pure Python SSH/SFTP | Alternative if needed |
| Native Rust | Tauri handles file sync | Complex, later phase |

### Rationale
- External binaries complicate bundling and App Store submission
- pyexiv2 embeds its C library, bundles cleanly with PyInstaller
- Backup to homelab is power-user feature, defer for initial release

---

## ADR-004: Linting and Formatting

### Decision: Biome (TS/JS) + Ruff (Python) + rustfmt (Rust)

### Tool Matrix

| Language | Linter | Formatter | Config File |
|----------|--------|-----------|-------------|
| TypeScript/JS | Biome | Biome | `biome.json` |
| Python | Ruff | Ruff | `pyproject.toml` |
| Rust | Clippy | rustfmt | `rustfmt.toml`, `Cargo.toml` |
| JSON | Biome | Biome | `biome.json` |

### Why Biome over ESLint+Prettier
- 10-25x faster (Rust-based)
- Single tool for lint + format
- Zero config for most cases
- Active development, modern defaults

### Configuration References

**biome.json** (root):
```json
{
  "$schema": "https://biomejs.dev/schemas/1.9.4/schema.json",
  "organizeImports": { "enabled": true },
  "linter": {
    "enabled": true,
    "rules": {
      "recommended": true,
      "correctness": {
        "noUnusedImports": "error",
        "noUnusedVariables": "error"
      }
    }
  },
  "formatter": {
    "indentStyle": "space",
    "indentWidth": 2,
    "lineWidth": 100
  }
}
```

**pyproject.toml** (apps/python):
```toml
[tool.ruff]
target-version = "py311"
line-length = 100

[tool.ruff.lint]
select = ["E", "W", "F", "I", "B", "C4", "UP", "ARG", "SIM", "TCH", "PTH", "RUF"]
```

**rustfmt.toml** (apps/desktop/src-tauri):
```toml
edition = "2021"
max_width = 100
tab_spaces = 4
use_small_heuristics = "Default"
```

---

## ADR-005: Cross-Language Configuration

### Decision: JSON Schema as Single Source of Truth

### Architecture
```
┌─────────────────────────────────────────────────────────────┐
│  packages/config/schema/settings.schema.json                │
│  (Single Source of Truth)                                   │
└─────────────────────┬───────────────────────────────────────┘
                      │
        ┌─────────────┼─────────────┐
        │             │             │
        ▼             ▼             ▼
┌───────────┐  ┌───────────┐  ┌───────────┐
│ TypeScript│  │  Python   │  │   Rust    │
│   types   │  │  Pydantic │  │   serde   │
│ (zod/gen) │  │  models   │  │  structs  │
└───────────┘  └───────────┘  └───────────┘
```

### Settings Flow
1. User edits settings in Tauri React UI
2. React validates with TypeScript types
3. Tauri Rust validates paths exist/writable
4. Settings saved to `~/.config/photoflow/settings.json`
5. Python sidecar loads settings via Pydantic on each command

### Key Settings Structure
```json
{
  "version": 1,
  "paths": {
    "camera": "/Volumes/Fuji X-T4/DCIM",
    "staging": "/Users/user/Pictures/Staging",
    "final": "/Users/user/Pictures/Final",
    "raws": "/Volumes/EXT/Bilder/RAWs",
    "videos": "/Volumes/EXT/Videos"
  },
  "compression": {
    "maxWidth": 5200,
    "maxHeight": 3467,
    "quality": 92,
    "chromaSubsampling": "4:4:4"
  },
  "gallery": {
    "enabled": true,
    "minRating": 4,
    "outputPath": "~/SourceRoot/photo-flow/photo_gallery/src"
  },
  "backup": {
    "enabled": false,
    "remote": {
      "host": "homelab.example.com",
      "user": "user",
      "path": "/backup/photos"
    }
  }
}
```

### Pydantic Model Pattern
```python
from pydantic import BaseModel, Field, field_validator
from pathlib import Path

class PathSettings(BaseModel):
    camera: Path
    staging: Path
    final: Path
    raws: Path

    @field_validator("staging", "final", "raws")
    @classmethod
    def must_exist_and_be_writable(cls, v: Path) -> Path:
        if not v.exists():
            raise ValueError(f"Path does not exist: {v}")
        # Test write access
        test_file = v / ".photoflow_write_test"
        try:
            test_file.touch()
            test_file.unlink()
        except PermissionError:
            raise ValueError(f"Path is not writable: {v}")
        return v
```

---

## ADR-006: UI Component Library

### Decision: basalt-ui (User's Own Package)

### Details
- User maintains `basalt-ui` package (Tailwind + shadcn extension)
- Ships Tailwind config, shadcn components, design tokens
- Import as `@basalt-ui/react` in desktop app

### Integration
```typescript
// apps/desktop/src/components/Button.tsx
import { Button } from "@basalt-ui/react";
```

---

## ADR-007: Testing Strategy

### Decision: pytest (Python) + WebdriverIO (Tauri E2E)

### Test Matrix

| Layer | Tool | Location | Focus |
|-------|------|----------|-------|
| Python Unit | pytest | `apps/python/tests/` | Settings validation, workflow logic |
| Python Integration | pytest | `apps/python/tests/` | File operations with temp dirs |
| Tauri E2E | WebdriverIO + tauri-driver | `e2e/desktop/` | Full app workflows |
| Web E2E | Playwright | `e2e/web/` | Marketing site (if needed) |

### Why WebdriverIO for Tauri
- Official Tauri recommendation
- `tauri-driver` provides WebDriver interface
- Works with actual built app bundle

### Alternative: Playwright via CDP
- Chromium DevTools Protocol access
- More familiar API
- Requires `--remote-debugging-port` flag in dev

### Python Test Example
```python
# apps/python/tests/test_settings.py
import pytest
from pydantic import ValidationError
from photo_flow.settings import PathSettings

class TestPathSettings:
    def test_rejects_nonexistent_path(self, tmp_path):
        with pytest.raises(ValidationError) as exc:
            PathSettings(
                camera="/Volumes/Camera",
                staging="/nonexistent/path",
                final=str(tmp_path),
                raws=str(tmp_path),
            )
        assert "does not exist" in str(exc.value)
```

---

## ADR-008: Python Sidecar Communication

### Decision: JSON via stdout/stdin

### Protocol
```
Tauri (Rust) → spawns → Python sidecar
                        ↓
                        args: ["status", "--config", "/path/to/settings.json"]
                        ↓
                        stdout: {"event": "status", "data": {...}}
```

### Message Format
```python
# Python emits structured JSON to stdout
def emit(event: str, **data):
    print(json.dumps({"event": event, **data}), flush=True)

# Events:
# {"event": "progress", "current": 5, "total": 100, "message": "Importing..."}
# {"event": "complete", "result": {...}}
# {"event": "error", "message": "Camera not connected"}
```

### Rust Side
```rust
use tauri::api::process::{Command, CommandEvent};

#[tauri::command]
async fn run_workflow(command: String, config_path: String) -> Result<Value, String> {
    let (mut rx, _child) = Command::new_sidecar("photoflow-core")?
        .args([&command, "--config", &config_path])
        .spawn()?;

    while let Some(event) = rx.recv().await {
        match event {
            CommandEvent::Stdout(line) => {
                let msg: Value = serde_json::from_str(&line)?;
                // Forward to frontend via events
            }
            CommandEvent::Error(err) => return Err(err),
            _ => {}
        }
    }
    Ok(json!({"success": true}))
}
```

---

## ADR-009: Bundling Strategy

### Decision: PyInstaller (Python) + Tauri Bundle (App)

### Build Pipeline
```
1. PyInstaller bundles Python sidecar → single executable
   - Output: apps/python/dist/photoflow-core
   - Includes: pyexiv2 + libexiv2, Pillow, Pydantic

2. Tauri bundles desktop app
   - Copies Python executable to resources
   - Output: .app bundle (macOS), .msi (Windows), .deb (Linux)

3. Code signing + notarization (macOS)
   - Apple Developer certificate
   - `tauri build` handles notarization with proper config
```

### PyInstaller Spec
```python
# apps/python/photoflow.spec
a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],  # pyexiv2 includes libexiv2 automatically
    datas=[],
    hiddenimports=['pydantic', 'pydantic_settings'],
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    name='photoflow-core',
    console=False,
)
```

---

## ADR-010: Migration Path from CLI

### Decision: Incremental Refactoring

### Phase 1: Preparation (Current CLI Still Works)
- [ ] Add Pydantic settings models
- [ ] Replace exiftool with pyexiv2
- [ ] Add JSON stdout mode to CLI
- [ ] Write unit tests for core logic

### Phase 2: Sidecar Creation
- [ ] Create `main.py` entry point for sidecar
- [ ] Refactor `PhotoWorkflow` to accept settings object
- [ ] Remove CLI-specific code from core logic
- [ ] PyInstaller bundling works

### Phase 3: Tauri Shell
- [ ] Initialize Tauri project
- [ ] Basic React UI with basalt-ui
- [ ] Sidecar spawning works
- [ ] Settings UI implemented

### Phase 4: Feature Parity
- [ ] All CLI commands available in GUI
- [ ] Progress reporting to UI
- [ ] Error handling and display
- [ ] E2E tests passing

### Phase 5: Polish
- [ ] Code signing and notarization
- [ ] Auto-updates
- [ ] Marketing site (Astro)
- [ ] App Store submission prep

---

## Quick Reference: Commands

### Development
```bash
# Install dependencies
bun install

# Run desktop app (dev mode)
cd apps/desktop && bun run tauri dev

# Run marketing site
cd apps/web && bun run dev

# Run Python tests
cd apps/python && pytest

# Lint all
bun run lint        # Biome for TS/JS
cd apps/python && ruff check .
cd apps/desktop/src-tauri && cargo clippy

# Format all
bun run format      # Biome for TS/JS
cd apps/python && ruff format .
cd apps/desktop/src-tauri && cargo fmt
```

### Build
```bash
# Build Python sidecar
cd apps/python && pyinstaller photoflow.spec

# Build desktop app (includes sidecar)
cd apps/desktop && bun run tauri build

# Build marketing site
cd apps/web && bun run build
```

### Test
```bash
# Python unit tests
cd apps/python && pytest -v

# Tauri E2E
cd e2e/desktop && bun run test
```

---

## Dependencies Summary

### Python (apps/python/pyproject.toml)
```toml
dependencies = [
    "pillow>=11.0.0",
    "pyexiv2>=2.14.0",
    "pydantic>=2.10.0",
    "pydantic-settings>=2.6.0",
]

[project.optional-dependencies]
dev = ["pytest>=8.0.0", "ruff>=0.8.0"]
```

### Rust (apps/desktop/src-tauri/Cargo.toml)
```toml
[dependencies]
tauri = { version = "2", features = ["shell-sidecar"] }
serde = { version = "1", features = ["derive"] }
serde_json = "1"
```

### TypeScript (apps/desktop/package.json)
```json
{
  "dependencies": {
    "@tauri-apps/api": "^2",
    "react": "^18",
    "@basalt-ui/react": "workspace:*"
  },
  "devDependencies": {
    "@biomejs/biome": "^1.9",
    "@tauri-apps/cli": "^2",
    "typescript": "^5.7"
  }
}
```

---

## Open Questions (For Future ADRs)

1. **Auto-updates**: Tauri's built-in updater vs custom solution?
2. **Licensing**: Which license for commercial distribution?
3. **Telemetry**: Opt-in analytics for crash reporting?
4. **Localization**: i18n from start or defer?
5. **Windows/Linux**: Priority of cross-platform support?

---

## Document Maintenance

**When to Update This ADR**:
- New major architectural decision made
- Decision reversed or significantly modified
- Implementation reveals decision was wrong

**Update Protocol**:
1. Add new ADR-XXX section
2. Update "Last Updated" date
3. Cross-reference related ADRs if needed
