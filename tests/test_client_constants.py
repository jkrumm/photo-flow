"""
Mechanical pins between Python constants and the SPA's hand-copied mirrors of them.

The control panel is a separate build with its own source of truth for a handful of
numbers the server also owns. Where the client merely *displays* a server value that is
fine; where the client uses it to make a **request decision**, a silent divergence is a
behaviour change nobody sees in either diff. These tests are the missing mechanism.

They parse TypeScript with a regex on purpose. The alternative — an endpoint that serves
the constants — is real API surface added to make a test possible, and the values change
roughly never; what was missing was not a channel but a tripwire.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from photo_flow.config import THUMB_SIZES

WEB_SRC = Path(__file__).resolve().parent.parent / "control_panel" / "web" / "src"


def _read(relative: str) -> str:
    """
    Read a file from the SPA source tree.

    Args:
        relative: Path relative to `control_panel/web/src`.

    Returns:
        File contents.
    """
    path = WEB_SRC / relative
    if not path.exists():  # pragma: no cover - only when the SPA tree is absent
        pytest.skip(f"SPA source not present: {path}")
    return path.read_text(encoding="utf-8")


def test_thumb_long_edge_matches_thumb_sizes() -> None:
    """
    `photos.ts`'s `THUMB_LONG_EDGE` must equal `config.THUMB_SIZES`.

    It is not decoration: `needsMaster` compares a frame's required long edge against it
    to decide whether to pull a 0.43–21.7 MB master. Raising `THUMB_SIZES['view']` on the
    server without touching the client would leave the compare stage fetching masters it
    no longer needs; lowering it would leave the stage upscaling a proxy and calling it
    1:1 — which is the exact failure the `original` endpoint exists to prevent.
    """
    source = _read("lib/photos.ts")
    match = re.search(
        r"export const THUMB_LONG_EDGE: Record<ThumbTier, number> = (\{[^}]*\})",
        source,
    )
    assert match is not None, "THUMB_LONG_EDGE not found in lib/photos.ts"
    # `{ grid: 320, view: 2048 }` -> JSON.
    literal = re.sub(r"(\w+):", r'"\1":', match.group(1))
    assert json.loads(literal) == THUMB_SIZES


def test_default_photo_limit_within_server_bound() -> None:
    """
    `photos.ts`'s `DEFAULT_PHOTO_LIMIT` must be a limit the list endpoint accepts.

    The contact sheet asks for the whole result set in one page, so this sits at the
    endpoint's ceiling rather than at its default. Lowering `le=` on the server without
    touching the client would 422 every list request the culling screen makes.
    """
    import inspect

    from photo_flow.api.routes_photos import photos_list

    source = _read("lib/photos.ts")
    match = re.search(r"export const DEFAULT_PHOTO_LIMIT = (\d+)", source)
    assert match is not None, "DEFAULT_PHOTO_LIMIT not found in lib/photos.ts"
    # The bound lives on the FastAPI `Query`, not in a module constant, so it is read off
    # the signature rather than restated here.
    limit_param = inspect.signature(photos_list).parameters["limit"]
    # Pydantic v2 stores `le=` as an `annotated_types.Le` in the field's metadata list.
    ceilings = [getattr(item, "le", None) for item in limit_param.default.metadata]
    ceiling = next((value for value in ceilings if value is not None), None)
    assert ceiling is not None, "photos_list has no upper bound on `limit`"
    assert int(match.group(1)) <= ceiling


def test_max_write_paths_matches_server_cap() -> None:
    """`photos.ts`'s `MAX_WRITE_PATHS` must equal the rating / label / trash batch cap."""
    from photo_flow.api.routes_photos import MAX_WRITE_PATHS as server_cap

    source = _read("lib/photos.ts")
    match = re.search(r"export const MAX_WRITE_PATHS = (\d+)", source)
    assert match is not None, "MAX_WRITE_PATHS not found in lib/photos.ts"
    assert int(match.group(1)) == server_cap


def test_max_warm_paths_matches_server_cap() -> None:
    """
    `photos.ts`'s `MAX_WARM_PATHS` must not exceed the server's own cap.

    A client batch larger than the server's ceiling is silently truncated, so the tail of
    a prewarm ring is dropped and the frames the user is about to step onto are the ones
    that miss.
    """
    from photo_flow.api.routes_photos import MAX_WARM_PATHS as server_cap

    source = _read("lib/photos.ts")
    match = re.search(r"export const MAX_WARM_PATHS = (\d+)", source)
    assert match is not None, "MAX_WARM_PATHS not found in lib/photos.ts"
    assert int(match.group(1)) <= server_cap


def _load_grid_survey():  # type: ignore[no-untyped-def]
    """
    Import `scripts/grid_survey.py` by path.

    Not a package (no `__init__.py`, deliberately — it is a one-off survey script, not
    library code), so it needs `importlib` rather than a normal import statement. Safe to
    import: every top-level statement in the module is a def/const, and the CLI itself
    lives behind `if __name__ == "__main__":`.
    """
    import importlib.util

    path = Path(__file__).resolve().parent.parent / "scripts" / "grid_survey.py"
    spec = importlib.util.spec_from_file_location("grid_survey", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_grid_survey_constants_match_photo_grid() -> None:
    """
    `scripts/grid_survey.py`'s `GAP` / `PAD` / `CELL_ASPECT` / `DENSITY_STEPS` must equal
    `photo-grid.tsx`'s.

    The script's own header claims its arithmetic "is the component's arithmetic and not a
    second model of it" — a claim nothing enforced until this test. `photo-grid.tsx` is the
    source of truth (the script mirrors it, not the other way round); a silent divergence
    here would make the F6 density finding a survey of a grid that no longer exists.
    """
    survey = _load_grid_survey()
    source = _read("components/photos/photo-grid.tsx")

    def _const(name: str, pattern: str) -> str:
        match = re.search(pattern, source)
        assert match is not None, f"{name} not found in photo-grid.tsx"
        return match.group(1)

    assert survey.GAP == int(_const("GAP", r"const GAP = (\d+)"))
    assert survey.PAD == int(_const("PAD", r"const PAD = (\d+)"))
    assert survey.CELL_ASPECT == float(_const("CELL_ASPECT", r"const CELL_ASPECT = ([\d.]+)"))

    steps = _const(
        "DENSITY_STEPS", r"export const DENSITY_STEPS: readonly number\[\] = \[([^\]]*)\]"
    )
    assert list(survey.DENSITY_STEPS) == [int(step.strip()) for step in steps.split(",")]
