"""
The library configuration, read-only, over HTTP.

`GET /api/config` answers the two questions a panel needs to ask before it trusts
anything else on screen: **where does this install think the library is**, and **is the
configuration actually valid**. It reads the resolved model rather than the files, so it
reports what the running process is using, which is the only thing worth reporting.

Read-only on purpose. Editing a roots table from a web form means a mis-click can point a
destructive operation at another directory, and the recovery for that is a restore from
the homelab rather than an undo button. The file is edited in a text editor; the panel's
job is to show what it resolved to and to say loudly when it did not resolve at all.

The ``/api`` prefix is load-bearing, exactly as it is for `routes_photos`: `app.py`
mounts an SPA catch-all after every router, and an unprefixed route would be shadowed by
it and answer `text/html`.
"""
from __future__ import annotations

from typing import Any, Dict, List

from fastapi import APIRouter
from pydantic import BaseModel, Field

from photo_flow import config

router = APIRouter(prefix="/api/config", tags=["config"])


class ConfigResponse(BaseModel):
    """
    The resolved configuration and its health.

    Attributes:
        install: Machine-local half — the file, whether it exists, the roots, the cameras.
        library: Library-local half — the three organisation axes of decision 0003.
        valid: False when the library file could not be read. The install half cannot be
            invalid here: `photo_flow.config` refuses to import with a bad one, so a
            process that can answer this request already has a good one.
        errors: Every problem found, ready to display verbatim.
        unimplemented: Axes set away from their default and therefore recorded but not
            acted on — no operation reads them, and a setting that silently does nothing
            is worth saying out loud.
    """

    install: Dict[str, Any]
    library: Dict[str, Any]
    valid: bool
    errors: List[str] = Field(default_factory=list)
    unimplemented: List[str] = Field(default_factory=list)


@router.get("", response_model=ConfigResponse)
def get_config() -> ConfigResponse:
    """
    Return the configuration this process resolved at startup.

    Returns:
        The install half, the library half, and their validation state.
    """
    organisation = config.ORGANISATION
    return ConfigResponse(
        install=config.INSTALL.to_dict(),
        library=organisation.to_dict(),
        valid=organisation.readable,
        errors=list(organisation.errors),
        unimplemented=list(organisation.unimplemented),
    )
