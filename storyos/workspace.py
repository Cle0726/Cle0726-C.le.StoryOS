from __future__ import annotations

import hashlib
import re
from collections import Counter
from pathlib import Path
from typing import Any

from storyos.canon_commit import CanonCommitWorkbench
from storyos.claim_review import ClaimReviewWorkbench
from storyos.materialization import MaterializationWorkbench
from storyos.project import StoryProject
from storyos.state import StoryStateProjector


class AuthoringWorkspaceError(RuntimeError):
    """Raised when a read-only authoring workspace view cannot be built safely."""


class AuthoringWorkspace:
    """Read-only aggregation layer for authoring clients.

    The workspace never calls review decision, materialization staging or Canon commit
    mutation methods. It only aggregates their current plans and reads manuscript data.
    """

    def __init__(self) -> None:
        self._state = StoryStateProjector()
        self._reviews = ClaimReviewWorkbench()
        self._materialization = MaterializationWorkbench()
        self._commits = CanonCommitWorkbench()

    def build_snapshot(
        self,
        project: StoryProject,
        *,
        through_sequence: int | None = None,
    ) -> dict[str, Any]:
        entities = sorted(
            project.load_entities(), key=lambda item: (item.kind, item.name, item.id)
        )
        events = sorted(project.load_events(), key=lambda item: (item.at.sequence, item.id))
        facts = sorted(
            project.load_canon_facts(),
            key=lambda item: (item.subject, item.predicate, -int(item.authority), item.id),
        )
        claims = sorted(project.load_claims(), key=lambda item: (item.at.sequence, item.id))

        effective = _effective_sequence(events, through_sequence)
        visible_events = [
            event for event in events if effective is None or event.at.sequence <= effective
        ]
        states = self._state.project(events, through_sequence=effective)
        review_queue = self._reviews.build_queue(project)
        materialization_plan = self._materialization.build_plan(project)
        commit_plan = self._commits.build_plan(project)
        reference_errors = project.validate_references()

        event_counts = Counter(event.subject for event in visible_events)
        total_event_counts = Counter(event.subject for event in events)
        canon_counts = Counter(fact.subject for fact in facts)
        active_canon_counts = Counter(fact.subject for fact in facts if fact.active_at(effective))
        claim_counts = Counter(claim.subject for claim in claims)
        latest_by_subject: dict[str, int] = {}
        for event in visible_events:
            latest_by_subject[event.subject] = event.at.sequence

        entity_rows: list[dict[str, Any]] = []
        for entity in entities:
            state = states.get(entity.id)
            entity_rows.append(
                {
                    "id": entity.id,
                    "kind": entity.kind,
                    "name": entity.name,
                    "slug": entity.slug,
                    "aliases": list(entity.aliases),
                    "data": entity.data,
                    "state": {
                        "values": {} if state is None else state.values,
                        "knowledge_count": 0 if state is None else len(state.knowledge),
                        "resolved_plot_count": 0 if state is None else len(state.resolved_plots),
                    },
                    "counts": {
                        "events": event_counts.get(entity.id, 0),
                        "events_total": total_event_counts.get(entity.id, 0),
                        "canon_facts": canon_counts.get(entity.id, 0),
                        "active_canon_facts": active_canon_counts.get(entity.id, 0),
                        "claims": claim_counts.get(entity.id, 0),
                    },
                    "latest_event_sequence": latest_by_subject.get(entity.id),
                }
            )

        authorities = Counter(fact.authority.name.lower() for fact in facts)
        manuscripts = self.list_manuscripts(project)

        return {
            "schema": "story.authoring-workspace.v1",
            "project": {
                "id": str(project.manifest.get("id") or ""),
                "name": str(project.manifest.get("name") or ""),
                "language": str(project.manifest.get("language") or ""),
                "paths": dict(project.manifest.get("paths") or {}),
                "import": dict(project.manifest.get("import") or {}),
            },
            "timeline": {
                "requested_through_sequence": through_sequence,
                "effective_through_sequence": effective,
                "latest_event_sequence": None if not events else events[-1].at.sequence,
                "events": len(visible_events),
                "events_total": len(events),
            },
            "summary": {
                "manuscripts": len(manuscripts),
                "entities": len(entities),
                "characters": sum(1 for item in entities if item.kind == "character"),
                "canon_facts": len(facts),
                "active_canon_facts": sum(1 for fact in facts if fact.active_at(effective)),
                "claims": len(claims),
                "reference_errors": len(reference_errors),
            },
            "manuscripts": manuscripts,
            "entities": entity_rows,
            "canon": {"authorities": dict(sorted(authorities.items()))},
            "workflow": {
                "review": review_queue["summary"],
                "materialization": materialization_plan["summary"],
                "canon_commit": commit_plan["summary"],
                "attention": {
                    "unreviewed_claims": int(review_queue["summary"].get("unreviewed", 0)),
                    "stale_reviews": int(review_queue["summary"].get("stale_reviews", 0)),
                    "materialization_ready": int(materialization_plan["summary"].get("ready", 0)),
                    "canon_commit_ready": int(commit_plan["summary"].get("ready", 0)),
                    "reference_errors": len(reference_errors),
                },
            },
            "diagnostics": {"reference_errors": reference_errors},
            "policy": {
                "read_only": True,
                "canonical_mutation": False,
                "staging_mutation": False,
                "mutation_commands_are_separate": True,
            },
        }

    def build_entity_view(
        self,
        project: StoryProject,
        entity_id: str,
        *,
        through_sequence: int | None = None,
    ) -> dict[str, Any]:
        entities = {entity.id: entity for entity in project.load_entities()}
        entity = entities.get(entity_id)
        if entity is None:
            raise AuthoringWorkspaceError(f"unknown entity id: {entity_id}")

        events = sorted(project.load_events(), key=lambda item: (item.at.sequence, item.id))
        facts = sorted(
            (fact for fact in project.load_canon_facts() if fact.subject == entity_id),
            key=lambda item: (item.predicate, -int(item.authority), item.id),
        )
        effective = _effective_sequence(events, through_sequence)
        visible_events = [
            event
            for event in events
            if event.subject == entity_id
            and (effective is None or event.at.sequence <= effective)
        ]
        state = self._state.project(events, through_sequence=effective).get(entity_id)

        review_queue = self._reviews.build_queue(project, subject=entity_id)
        materialization = self._materialization.build_plan(project)
        commit_plan = self._commits.build_plan(project)
        materialization_by_claim = {
            str(item["claim_id"]): item for item in materialization["items"]
        }
        commit_by_claim = {str(item["claim_id"]): item for item in commit_plan["items"]}

        claim_rows: list[dict[str, Any]] = []
        for row in review_queue["items"]:
            claim_id = str(row["claim"]["id"])
            claim_rows.append(
                {
                    **row,
                    "materialization": _materialization_ui_item(
                        materialization_by_claim.get(claim_id)
                    ),
                    "canon_commit": _commit_ui_item(commit_by_claim.get(claim_id)),
                }
            )

        return {
            "schema": "story.authoring-entity.v1",
            "project_id": str(project.manifest.get("id") or ""),
            "through_sequence": effective,
            "entity": {
                "id": entity.id,
                "kind": entity.kind,
                "name": entity.name,
                "slug": entity.slug,
                "aliases": list(entity.aliases),
                "data": entity.data,
            },
            "state": {
                "values": {} if state is None else state.values,
                "knowledge": [] if state is None else sorted(state.knowledge),
                "resolved_plots": [] if state is None else sorted(state.resolved_plots),
            },
            "events": [_event_payload(event) for event in visible_events],
            "canon_facts": [
                {
                    **_canon_payload(fact),
                    "active": fact.active_at(effective, include_nonmainline=True),
                    "mainline_active": fact.active_at(effective),
                    "revealed": fact.revealed_at(effective),
                }
                for fact in facts
            ],
            "claims": claim_rows,
            "workflow": {
                "review": review_queue["summary"],
                "materialization_ready": sum(
                    1
                    for row in claim_rows
                    if row["materialization"] is not None
                    and row["materialization"]["ready"]
                ),
                "canon_commit_ready": sum(
                    1
                    for row in claim_rows
                    if row["canon_commit"] is not None and row["canon_commit"]["ready"]
                ),
            },
            "policy": {
                "read_only": True,
                "canonical_mutation": False,
                "staging_mutation": False,
            },
        }

    def list_manuscripts(self, project: StoryProject) -> list[dict[str, Any]]:
        root = _manuscript_root(project)
        if not root.exists():
            return []

        rows: list[dict[str, Any]] = []
        for path in sorted(root.rglob("*")):
            if path.is_symlink():
                raise AuthoringWorkspaceError(
                    f"manuscript symlinks are not supported: {path}"
                )
            if not path.is_file() or path.suffix.lower() not in {".txt", ".md", ".markdown"}:
                continue
            resolved = path.resolve()
            if not resolved.is_relative_to(root):
                raise AuthoringWorkspaceError(
                    f"manuscript path escapes the configured root: {path}"
                )
            raw = resolved.read_bytes()
            try:
                text = raw.decode("utf-8-sig")
            except UnicodeDecodeError as exc:
                raise AuthoringWorkspaceError(
                    f"manuscript is not UTF-8 text: {path}"
                ) from exc
            relative = path.relative_to(project.root).as_posix()
            season, episode = _story_numbers(path.relative_to(root))
            rows.append(
                {
                    "path": relative,
                    "name": path.name,
                    "title": _manuscript_title(path),
                    "season": season,
                    "episode": episode,
                    "bytes": len(raw),
                    "characters": len(text),
                    "lines": 0 if not text else text.count("\n") + 1,
                    "sha256": hashlib.sha256(raw).hexdigest(),
                }
            )
        rows.sort(
            key=lambda item: (
                10**9 if item["season"] is None else item["season"],
                10**9 if item["episode"] is None else item["episode"],
                item["path"],
            )
        )
        return rows

    def load_manuscript(self, project: StoryProject, relative_path: str) -> dict[str, Any]:
        root = _manuscript_root(project)
        requested = Path(relative_path)
        if requested.is_absolute():
            raise AuthoringWorkspaceError("manuscript path must be project-relative")
        candidate = project.root / requested
        if candidate.is_symlink():
            raise AuthoringWorkspaceError("manuscript symlinks are not supported")
        path = candidate.resolve()
        if not path.is_relative_to(root):
            raise AuthoringWorkspaceError(
                "manuscript path escapes the configured manuscript root"
            )
        if not path.is_file():
            raise AuthoringWorkspaceError(f"unknown manuscript file: {relative_path}")
        if path.suffix.lower() not in {".txt", ".md", ".markdown"}:
            raise AuthoringWorkspaceError("unsupported manuscript file type")

        raw = path.read_bytes()
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise AuthoringWorkspaceError(
                f"manuscript is not UTF-8 text: {relative_path}"
            ) from exc
        season, episode = _story_numbers(path.relative_to(root))
        return {
            "schema": "story.authoring-manuscript.v1",
            "project_id": str(project.manifest.get("id") or ""),
            "path": path.relative_to(project.root).as_posix(),
            "title": _manuscript_title(path),
            "season": season,
            "episode": episode,
            "bytes": len(raw),
            "characters": len(text),
            "lines": 0 if not text else text.count("\n") + 1,
            "sha256": hashlib.sha256(raw).hexdigest(),
            "content": text,
            "policy": {
                "read_only": True,
                "canonical_mutation": False,
                "staging_mutation": False,
            },
        }


def _effective_sequence(events, requested: int | None) -> int | None:
    if requested is not None:
        if requested < 0:
            raise AuthoringWorkspaceError("through_sequence must be >= 0")
        return requested
    if not events:
        return None
    return max(event.at.sequence for event in events)


def _manuscript_root(project: StoryProject) -> Path:
    paths = dict(project.manifest.get("paths") or {})
    configured = Path(str(paths.get("manuscript") or "manuscript"))
    if configured.is_absolute():
        raise AuthoringWorkspaceError("project manuscript path must be relative")
    root = (project.root / configured).resolve()
    if not root.is_relative_to(project.root):
        raise AuthoringWorkspaceError("project manuscript path escapes the project root")
    return root


def _story_numbers(relative: Path) -> tuple[int | None, int | None]:
    season = None
    for part in relative.parts[:-1]:
        match = re.fullmatch(r"S(\d+)", part, flags=re.IGNORECASE)
        if match:
            season = int(match.group(1))
            break
    stem = relative.stem
    match = re.match(r"EP(\d+)(?:[_\-\s]|$)", stem, flags=re.IGNORECASE)
    if match:
        episode = int(match.group(1))
    else:
        chinese = re.match(r"第(\d+)集", stem)
        episode = None if chinese is None else int(chinese.group(1))
    return season, episode


def _manuscript_title(path: Path) -> str:
    stem = path.stem
    stem = re.sub(r"^EP\d+[_\-\s]*", "", stem, flags=re.IGNORECASE)
    stem = re.sub(r"^第\d+集[_\-\s]*", "", stem)
    return stem or path.stem


def _event_payload(event) -> dict[str, Any]:
    return {
        "id": event.id,
        "subject": event.subject,
        "type": event.type,
        "at": {
            "sequence": event.at.sequence,
            "season": event.at.season,
            "episode": event.at.episode,
            "scene": event.at.scene,
        },
        "payload": event.payload,
        "source": event.source,
    }


def _canon_payload(fact) -> dict[str, Any]:
    return {
        "id": fact.id,
        "subject": fact.subject,
        "predicate": fact.predicate,
        "value": fact.value,
        "authority": fact.authority.name.lower(),
        "valid_from": fact.valid_from,
        "valid_to": fact.valid_to,
        "reveal_at": fact.reveal_at,
        "source": fact.source,
        "tags": list(fact.tags),
    }


def _materialization_ui_item(item: dict[str, Any] | None) -> dict[str, Any] | None:
    if item is None:
        return None
    return {
        "ready": bool(item.get("ready")),
        "reasons": list(item.get("reasons") or []),
        "kind": item.get("kind"),
        "target_id": item.get("target_id"),
        "check": item.get("check"),
    }


def _commit_ui_item(item: dict[str, Any] | None) -> dict[str, Any] | None:
    if item is None:
        return None
    return {
        "ready": bool(item.get("ready")),
        "state": item.get("state"),
        "reasons": list(item.get("reasons") or []),
        "kind": item.get("kind"),
        "target_id": item.get("target_id"),
        "candidate_path": item.get("candidate_path"),
        "candidate_sha256": item.get("candidate_sha256"),
        "canonical_path": item.get("canonical_path"),
        "audit_id": item.get("audit_id"),
    }
