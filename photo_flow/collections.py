"""
Saved collections — named queries over the index, persisted to a TOML file.

Why a file and not a table
--------------------------
Decision 0002 makes the photographs the source of truth and the SQLite index a
**disposable cache**: nothing may exist only in the database. A collection is unique
state — a name and a query that cannot be reconstructed by rescanning the library — so
storing it in `~/.photoflow/index.db` would make `rm index.db` a data-loss event in a
product whose whole premise is not losing data. It lives in
:data:`photo_flow.config.LIBRARY_CONFIG_PATH` (``~/Pictures/photoflow.toml``) instead:
next to the library, portable with it, and readable in a text editor.

The collection is a QUERY, never a layout
-----------------------------------------
Resolving a collection runs the ordinary list query with its stored filters. No file is
moved, copied, linked or renamed, now or ever. A collection that materialised a second
on-disk arrangement would be a second source of truth for where a photograph lives.

File shape
----------
::

    # photo-flow library configuration
    [[collections]]
    id = "keepers"
    name = "Keepers"
    created_at = "2026-08-20T09:00:00+00:00"
    updated_at = "2026-08-20T09:00:00+00:00"

    [collections.query]
    root = "final"
    rating_min = 4

The `query` table speaks exactly the vocabulary of ``GET /api/photos`` — see
``routes_photos.PhotoFilters``. This module deliberately does **not** know that
vocabulary: it stores and returns plain scalars and lists, and the API layer validates
them against the one dataclass that defines them, so there is only ever one filter
language in the codebase.

Tolerant read, strict write, LOSSLESS write
------------------------------------------
A hand-edited file is expected. Reading skips entries it cannot make sense of (a table
with no name, a duplicate id) rather than raising, because a typo in a config file must
never take the culling screen down. The API rejects the same input with a 400, because
there a bad value is a client bug and should be loud.

Tolerance is only honest if the thing tolerated still EXISTS after the next write. A
writer that re-emits its own model erases whatever it could not model — which turns
"we skipped your typo" into "we deleted your saved query", strictly worse than raising,
because a raise leaves the file intact. So:

* **Everything outside the collections region is preserved verbatim**, comments
  included. :func:`_split_document` removes only ``[[collections]]`` and its sub-tables
  from the file text; the surrounding lines are written back byte for byte. That is what
  lets F4's ``[library]`` roots and ``[cameras]`` profiles share this file, and what makes
  the header's "safe to edit by hand" true rather than aspirational.
* **Entries this module cannot model are quarantined, not dropped** (`Store.foreign`).
  They round-trip through the file and stay invisible to the API.
* **Query values are stored raw and cleaned at the boundary** (:meth:`Collection.to_dict`),
  so a value the filter vocabulary cannot express survives the next rename.

The one thing not preserved is a comment *inside* a ``[[collections]]`` block: that
region is owned by the writer. Multi-line strings there are likewise not supported.

Concurrency
-----------
Every mutation is a read-modify-write of a file measured in kilobytes, and the writer is
one single-user localhost daemon. That is deliberate: file locking would buy nothing here
and the atomic ``os.replace`` below already guarantees a reader never sees a half-written
file. Two processes writing at the same instant can lose one edit; nothing can corrupt.
"""
from __future__ import annotations

import logging
import os
import re
import tempfile
from dataclasses import dataclass, field
from datetime import date, datetime, time, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple, Union

try:  # Python 3.11+
    import tomllib as _toml
except ModuleNotFoundError:  # Python 3.9/3.10 — tomli IS tomllib, same author, same API
    import tomli as _toml  # type: ignore[no-redef]

from photo_flow import config

logger = logging.getLogger(__name__)

# Scalars a query value may hold. Everything the filter vocabulary can express is one of
# these, or a homogeneous list of them.
Scalar = Union[str, int, float, bool]
Query = Dict[str, Any]

MAX_NAME_LENGTH = 60
_SLUG_STRIP = re.compile(r"[^a-z0-9]+")
_HEADER = (
    "# photo-flow library configuration.\n"
    "#\n"
    "# Written by the control panel; safe to edit by hand. Collections are SAVED QUERIES:\n"
    "# selecting one filters the library, it never moves a file. Query keys are the query\n"
    "# parameters of GET /api/photos — an unknown key here is ignored, not an error.\n"
)


@dataclass
class Collection:
    """
    One saved query: a stable id, a display name, and the query itself.

    ``query`` holds the value **as it was read**, not a cleaned copy. Cleaning happens
    in :meth:`to_dict`, at the boundary where the API consumes it, so a hand-written
    value this module cannot represent still round-trips through the next write instead
    of being silently deleted by an unrelated rename.

    ``extra`` carries any further top-level keys the file had on the entry, for the same
    reason. Neither is ever shown to the API.
    """

    id: str
    name: str
    query: Query = field(default_factory=dict)
    created_at: str = ""
    updated_at: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Serialise to the plain dict the API consumes — query cleaned, `extra` dropped."""
        return {
            "id": self.id,
            "name": self.name,
            "query": clean_query(self.query),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    def to_entry(self) -> Dict[str, Any]:
        """Serialise to the dict the TOML writer consumes — everything, raw."""
        entry: Dict[str, Any] = {"id": self.id, "name": self.name}
        if self.created_at:
            entry["created_at"] = self.created_at
        if self.updated_at:
            entry["updated_at"] = self.updated_at
        entry.update(self.extra)
        if self.query:
            entry["query"] = dict(self.query)
        return entry


@dataclass
class Store:
    """
    The whole file: the collections this module understands, plus everything it does not.

    ``foreign`` holds ``[[collections]]`` entries that could not be modelled (no name, a
    duplicate id). ``before`` / ``after`` hold the file text on either side of the
    collections region — other tables, blank lines and comments — preserved byte for byte.

    ``unreadable`` marks a store that could not be parsed at all. Such a store still reads
    as empty — the culling screen must not die because a config file has a typo — but it
    REFUSES to be written back: everything the file held is unrepresented here, so a write
    would replace the user's document with this module's own region and nothing else. A
    failed button is recoverable; a truncated hand-edited config is not.
    """

    collections: List[Collection] = field(default_factory=list)
    foreign: List[Dict[str, Any]] = field(default_factory=list)
    before: str = ""
    after: str = ""
    unreadable: bool = False

    def taken_ids(self) -> List[str]:
        """Every id already in the file, quarantined entries included."""
        ids = [c.id for c in self.collections]
        ids += [e["id"] for e in self.foreign if isinstance(e.get("id"), str)]
        return ids


class CollectionError(ValueError):
    """A collection could not be created or changed as asked (bad name, unknown id)."""


class StoreUnreadable(CollectionError):
    """
    The backing file exists but could not be parsed, so no write is allowed.

    Raised by :func:`save` rather than by :func:`read`: reading degrades to an empty list
    on purpose (a typo in a config file must never take the culling screen down), and it
    is only the WRITE that would destroy what could not be read.
    """


# ---------------------------------------------------------------------------
# TOML serialisation
#
# Reading uses tomllib/tomli. Writing is hand-rolled: the standard library has no TOML
# writer, the value set here is four scalar types plus homogeneous lists of them, and
# `tomli-w` would be a third-party dependency carrying a supply-chain cost for ~40 lines
# of obvious code (see rules/dependency-hygiene.md).
# ---------------------------------------------------------------------------


_ESCAPES = {"\\": "\\\\", '"': '\\"', "\n": "\\n", "\r": "\\r", "\t": "\\t", "\b": "\\b", "\f": "\\f"}


def _dump_scalar(value: Scalar) -> str:
    """
    Render one scalar as TOML.

    Args:
        value: A str, bool, int or float.

    Returns:
        The TOML representation.

    Raises:
        TypeError: The value is not a supported scalar.
    """
    # bool before int: bool is a subclass of int in Python, and `True` must not emit `1`.
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        out = []
        for char in value:
            escaped = _ESCAPES.get(char)
            if escaped is not None:
                out.append(escaped)
            elif ord(char) < 0x20 or ord(char) == 0x7F:
                out.append(f"\\u{ord(char):04X}")
            else:
                out.append(char)
        return '"' + "".join(out) + '"'
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        # TOML has no NaN/Inf that round-trips usefully through a filter; refuse them.
        if value != value or value in (float("inf"), float("-inf")):
            raise TypeError(f"Non-finite float is not serialisable: {value!r}")
        return repr(value)
    raise TypeError(f"Unsupported TOML value: {value!r}")


def _dump_value(value: Any) -> str:
    """
    Render a scalar, a list of scalars, or a date/time as TOML.

    Dates arrive only from a hand-edited file — ``tomllib`` decodes a bare TOML datetime
    into a ``datetime`` object — and are re-emitted unquoted so they stay TOML datetimes
    rather than degrading into strings on the round trip.
    """
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_dump_value(item) for item in value) + "]"
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    return _dump_scalar(value)


def _dump_entry(entry: Mapping[str, Any]) -> List[str]:
    """
    Render one ``[[collections]]`` entry, sub-tables included.

    Scalars and lists come first, then each sub-table as ``[collections.<key>]`` — the
    ordering TOML requires, since a bare key after a table header would belong to that
    table rather than to the entry.

    Args:
        entry: The entry's keys. Values may be scalars, lists, or one level of sub-table.

    Returns:
        The entry's lines, without a trailing blank.
    """
    lines = ["[[collections]]"]
    tables: List[str] = []
    for key, value in entry.items():
        if isinstance(value, dict):
            if not value:
                continue
            tables.append(f"[collections.{key}]")
            for sub_key in sorted(value):
                try:
                    tables.append(f"{sub_key} = {_dump_value(value[sub_key])}")
                except TypeError:
                    logger.warning("Dropping unwritable value %s.%s", key, sub_key)
            tables.append("")
            continue
        try:
            lines.append(f"{key} = {_dump_value(value)}")
        except TypeError:
            logger.warning("Dropping unwritable value %s", key)
    if tables:
        lines.append("")
        lines.extend(tables[:-1])
    return lines


def _dump_region(collections: Sequence[Collection], foreign: Sequence[Mapping[str, Any]]) -> str:
    """
    Render the ``[[collections]]`` region — the only part of the file this module owns.

    Args:
        collections: Modelled collections, in the order they should appear.
        foreign: Quarantined entries, re-emitted after them so nothing is lost.

    Returns:
        The region text, ending in a newline, or "" when there is nothing to write.
    """
    entries = [item.to_entry() for item in collections] + [dict(e) for e in foreign]
    if not entries:
        return ""
    lines: List[str] = []
    for entry in entries:
        lines.extend(_dump_entry(entry))
        lines.append("")
    return "\n".join(lines)


# A TOML table header: `[name]` or `[[name]]`, dotted keys included.
_TABLE_HEADER_RE = re.compile(r"^\s*\[\[?\s*([^\]]+?)\s*\]\]?\s*(?:#.*)?$")


def _split_document(text: str) -> Tuple[str, str]:
    """
    Split the file text around the ``[[collections]]`` region, removing it.

    Everything that is not part of a collections table — other top-level tables, the
    preamble, blank lines and comments — is returned verbatim, so a write puts it back
    exactly as it was. This is what makes the file shareable with F4's ``[library]`` and
    ``[cameras]`` tables instead of being owned outright by this module.

    Args:
        text: The current file contents (may be "").

    Returns:
        Tuple of (text before the region, text after it). A file with no collections
        region yields (whole text, "") — new entries are appended.
    """
    before: List[str] = []
    after: List[str] = []
    seen_region = False
    inside = False
    for line in text.splitlines(keepends=True):
        header = _TABLE_HEADER_RE.match(line)
        if header is not None:
            root = header.group(1).split(".", 1)[0].strip().strip("\"'")
            inside = root == "collections"
            if inside:
                seen_region = True
        if inside:
            continue
        (after if seen_region else before).append(line)
    return "".join(before), "".join(after)


def _join(before: str, region: str, after: str) -> str:
    """Reassemble the document, keeping exactly one blank line between the three parts."""
    parts = [part.strip("\n") for part in (before, region, after) if part.strip()]
    return "\n\n".join(parts) + "\n" if parts else ""


# ---------------------------------------------------------------------------
# Paths, ids, timestamps
# ---------------------------------------------------------------------------


def config_path() -> Path:
    """
    The TOML file backing the store.

    Read from :mod:`photo_flow.config` at call time rather than bound at import, so a
    test (and a future F4 config override) can relocate it.
    """
    return Path(config.LIBRARY_CONFIG_PATH)


def _now() -> str:
    """Current UTC time as an ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def slugify(name: str) -> str:
    """
    Derive a URL- and filename-safe id from a display name.

    Args:
        name: The human-facing collection name.

    Returns:
        A lowercase ``a-z0-9-`` slug, or ``"collection"`` when nothing survives
        (a name of pure emoji or CJK is legal and must still get an id).
    """
    slug = _SLUG_STRIP.sub("-", name.strip().lower()).strip("-")
    return slug[:48] or "collection"


def _unique_id(base: str, taken: Sequence[str]) -> str:
    """Append -2, -3, … until the slug is free — the same shape as the filename collision rule."""
    if base not in taken:
        return base
    counter = 2
    while f"{base}-{counter}" in taken:
        counter += 1
    return f"{base}-{counter}"


def _clean_name(name: str) -> str:
    """
    Validate and normalise a display name.

    Raises:
        CollectionError: The name is blank or longer than :data:`MAX_NAME_LENGTH`.
    """
    cleaned = " ".join(str(name).split())
    if not cleaned:
        raise CollectionError("A collection needs a name")
    if len(cleaned) > MAX_NAME_LENGTH:
        raise CollectionError(f"Name is longer than {MAX_NAME_LENGTH} characters")
    return cleaned


def clean_query(raw: Any) -> Query:
    """
    Keep only the entries a query can hold: scalars and homogeneous lists of scalars.

    Deliberately vocabulary-blind — which *keys* are legal is the API layer's job, decided
    against ``routes_photos.PhotoFilters`` so there is exactly one filter language. This
    only enforces that a value is representable at all.

    Applied at the API boundary, never on the way in from the file: an unrepresentable
    value read from a hand-edited file stays on the :class:`Collection` and is written
    back untouched, so the next rename does not quietly delete it.

    Args:
        raw: Whatever the file or the caller supplied for `query`.

    Returns:
        A new dict holding the representable entries.
    """
    if not isinstance(raw, dict):
        return {}
    out: Query = {}
    for key, value in raw.items():
        if not isinstance(key, str):
            continue
        if isinstance(value, (list, tuple)):
            items = [v for v in value if isinstance(v, (str, int, float, bool))]
            if len(items) == len(value):
                out[key] = list(items)
            continue
        if isinstance(value, (str, int, float, bool)):
            out[key] = value
    return out


# ---------------------------------------------------------------------------
# Load / save
# ---------------------------------------------------------------------------


_RESERVED_KEYS = frozenset({"id", "name", "created_at", "updated_at", "query"})


def read() -> Store:
    """
    Read the whole file: the modelled collections, the quarantined entries, and the
    verbatim text on either side of the collections region.

    A missing file is an empty store, not an error — that is the state of a fresh install.
    A malformed or unreadable file is logged and read as empty, and the store is flagged
    ``unreadable`` so :func:`save` refuses to overwrite it. Degrading the READ keeps the
    screen alive; refusing the WRITE keeps the user's document alive. Both matter, and
    they pull in opposite directions — this is where the line is drawn.

    Returns:
        The parsed store.
    """
    path = config_path()
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return Store()
    except OSError as exc:
        logger.error("Could not read %s: %s", path, exc)
        return Store(unreadable=True)

    try:
        text = raw.decode("utf-8")
        document = _toml.loads(text)
    except (UnicodeDecodeError, _toml.TOMLDecodeError) as exc:
        logger.error("Ignoring malformed %s: %s", path, exc)
        return Store(unreadable=True)

    before, after = _split_document(text)
    store = Store(before=before, after=after)

    entries = document.get("collections")
    if not isinstance(entries, list):
        return store

    seen: List[str] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name")
        raw_id = entry.get("id")
        cid = raw_id if isinstance(raw_id, str) and raw_id else None
        if not isinstance(name, str) or not name.strip():
            # No name means no row in the sidebar — but it is still something the user
            # typed, so it is quarantined and written back, never dropped.
            logger.warning("Quarantining a collection with no name in %s", path)
            store.foreign.append(entry)
            continue
        cid = cid or slugify(name)
        if cid in seen:
            logger.warning("Quarantining duplicate collection id %r in %s", cid, path)
            store.foreign.append(entry)
            continue
        seen.append(cid)
        created = entry.get("created_at")
        updated = entry.get("updated_at")
        query = entry.get("query")
        store.collections.append(
            Collection(
                id=cid,
                name=name.strip(),
                query=dict(query) if isinstance(query, dict) else {},
                created_at=created if isinstance(created, str) else "",
                updated_at=updated if isinstance(updated, str) else "",
                extra={k: v for k, v in entry.items() if k not in _RESERVED_KEYS},
            )
        )
    return store


def load() -> List[Collection]:
    """
    Every collection the file holds, in file order.

    Thin wrapper over :func:`read` for the read-only callers (the API listing, counts,
    resolution) that have no business knowing about quarantine or file text.

    Returns:
        Collections in file order, with duplicate ids quarantined (first wins).
    """
    return read().collections


def save(store: Store) -> None:
    """
    Write the whole document, atomically, preserving everything outside the region.

    A temp file in the same directory plus ``os.replace`` — the house contract for every
    in-place write (CLAUDE.md "Safety Mechanisms §2"). A reader either sees the old file
    or the new one, never a truncated one, even if the process dies mid-write.

    Args:
        store: The complete new state, including the preserved surrounding text.

    Raises:
        StoreUnreadable: The file on disk could not be parsed, so writing would destroy it.
        OSError: The file could not be written.
    """
    path = config_path()
    if store.unreadable:
        raise StoreUnreadable(
            f"{path} could not be parsed, so it will not be overwritten. "
            "Fix the syntax error or move the file aside, then try again."
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    before = store.before if store.before.strip() else _HEADER
    payload = _join(before, _dump_region(store.collections, store.foreign), store.after)
    handle = tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=str(path.parent),
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    )
    tmp_path = Path(handle.name)
    try:
        with handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(str(tmp_path), str(path))
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise


def save_all(collections: Sequence[Collection]) -> None:
    """
    Replace the collection list, keeping the rest of the file.

    Re-reads the document first so the preserved regions and any quarantined entries
    survive. Callers that already hold a :class:`Store` should use :func:`save`.

    Args:
        collections: The complete new collection list.

    Raises:
        OSError: The file could not be written.
    """
    store = read()
    store.collections = list(collections)
    save(store)


# ---------------------------------------------------------------------------
# Mutations
# ---------------------------------------------------------------------------


def get(collection_id: str) -> Optional[Collection]:
    """Return one collection by id, or None."""
    for item in load():
        if item.id == collection_id:
            return item
    return None


def create(name: str, query: Query) -> Collection:
    """
    Add a collection.

    Args:
        name: Display name; whitespace-collapsed, must be non-blank.
        query: Filter mapping — validated for shape here, for vocabulary by the caller.

    Returns:
        The stored collection, with its generated id and timestamps.

    Raises:
        CollectionError: The name is blank or too long.
        OSError: The file could not be written.
    """
    cleaned = _clean_name(name)
    store = read()
    stamp = _now()
    item = Collection(
        id=_unique_id(slugify(cleaned), store.taken_ids()),
        name=cleaned,
        query=clean_query(query),
        created_at=stamp,
        updated_at=stamp,
    )
    store.collections.append(item)
    save(store)
    return item


def update(
    collection_id: str,
    *,
    name: Optional[str] = None,
    query: Optional[Query] = None,
) -> Collection:
    """
    Rename a collection and/or replace its query.

    The **id never changes**, even when the name does: it is what a bookmark and a UI
    selection hold on to, and renaming "Keepers" to "Portfolio" must not orphan them.

    Args:
        collection_id: Id of the collection to change.
        name: New display name, or None to leave it.
        query: Complete replacement query, or None to leave it.

    Returns:
        The updated collection.

    Raises:
        CollectionError: Unknown id, or a bad name.
        OSError: The file could not be written.
    """
    store = read()
    for index, item in enumerate(store.collections):
        if item.id != collection_id:
            continue
        updated = Collection(
            id=item.id,
            name=item.name if name is None else _clean_name(name),
            query=item.query if query is None else clean_query(query),
            created_at=item.created_at,
            updated_at=_now(),
            extra=item.extra,
        )
        store.collections[index] = updated
        save(store)
        return updated
    raise CollectionError(f"No collection with id {collection_id!r}")


def delete(collection_id: str) -> bool:
    """
    Remove a collection.

    Deleting a saved query deletes a query. Not one photograph is touched — which is the
    whole reason collections are worth prototyping before a physical album layout.

    Args:
        collection_id: Id of the collection to remove.

    Returns:
        True when something was removed, False when the id was unknown.

    Raises:
        OSError: The file could not be written.
    """
    store = read()
    remaining = [item for item in store.collections if item.id != collection_id]
    if len(remaining) == len(store.collections):
        return False
    store.collections = remaining
    save(store)
    return True
