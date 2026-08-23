from __future__ import annotations

import re
import uuid

_KIND_PREFIX = {
    "character": "chr",
    "location": "loc",
    "organization": "org",
    "concept": "con",
    "plot": "plot",
    "canon": "canon",
    "event": "evt",
    "scene": "scn",
    "relationship": "rel",
    "claim": "clm",
    "audit": "aud",
}

_ID_RE = re.compile(r"^(?P<prefix>[a-z]+)_(?P<body>[0-9a-f]{32})$")

# Stable namespace for deterministic IDs derived from imported source identities.
# Do not change this value after release: it is part of the StoryOS import protocol.
_STABLE_IMPORT_NAMESPACE = uuid.UUID("b46202e9-2a3f-4b66-84e4-891031e2b2aa")


def new_id(kind: str) -> str:
    """Create a fresh typed identifier independent of names and paths."""
    try:
        prefix = _KIND_PREFIX[kind]
    except KeyError as exc:
        raise ValueError(f"unsupported id kind: {kind}") from exc
    return f"{prefix}_{uuid.uuid4().hex}"


def stable_id(kind: str, namespace: str, key: str) -> str:
    """Create a deterministic typed ID for a stable external/source identity.

    `namespace` identifies the import domain/project, while `key` should describe
    an identity that survives display-name and path changes (for example an
    external asset code or source scene ID). The same inputs always yield the
    same ID on every machine and import run.
    """
    try:
        prefix = _KIND_PREFIX[kind]
    except KeyError as exc:
        raise ValueError(f"unsupported id kind: {kind}") from exc

    namespace = str(namespace).strip()
    key = str(key).strip()
    if not namespace:
        raise ValueError("stable id namespace cannot be empty")
    if not key:
        raise ValueError("stable id key cannot be empty")

    identity = f"{namespace}\0{kind}\0{key}"
    return f"{prefix}_{uuid.uuid5(_STABLE_IMPORT_NAMESPACE, identity).hex}"


def validate_id(value: str, kind: str | None = None) -> bool:
    match = _ID_RE.fullmatch(value)
    if not match:
        return False
    if kind is None:
        return match.group("prefix") in _KIND_PREFIX.values()
    return match.group("prefix") == _KIND_PREFIX.get(kind)
