from __future__ import annotations

"""Compatibility import for the hardened Canon commit API.

The project-wide mutation lock now lives inside ``storyos.canon_commit`` itself, so
all direct Python, CLI, and desktop callers share the same transaction boundary.
This module remains as a stable import path for code introduced during hardening.
"""

from storyos.canon_commit import CanonCommitError, CanonCommitWorkbench

__all__ = ["CanonCommitError", "CanonCommitWorkbench"]
