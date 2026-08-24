from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

import yaml

from storyos.authority import CanonFact
from storyos.events import StoryEvent
from storyos.file_lock import ProjectFileLockError, project_file_lock
from storyos.ids import stable_id, validate_id
from storyos.materialization import (
    MaterializationError,
    MaterializationWorkbench,
    quarantine_mapping_from_plan_item,
)
from storyos.project import StoryProject


class CanonCommitError(RuntimeError):
    """Raised when a quarantine candidate cannot be safely committed to Canon."""


class CanonCommitWorkbench:
    """Explicit create-only gate from quarantine into canonical files.

    The exact quarantine SHA-256 must be confirmed by the caller. An immutable
    authorization audit is created before Canon, then the current materialization
    plan is rebuilt before the canonical file is created. The complete mutation
    sequence is serialized by a project-wide OS-managed lock so direct Python API
    callers receive the same transaction boundary as the CLI/desktop service.
    """

    audit_namespace = "canon-commit-v1"

    def __init__(self) -> None:
        self._materialization = MaterializationWorkbench()

    def build_plan(
        self,
        project: StoryProject,
        *,
        claim_id: str | None = None,
    ) -> dict[str, Any]:
        materialization = self._materialization.build_plan(project, claim_id=claim_id)
        audits = self._load_audits(project)
        committed_claims = self._committed_claims(project, audits)
        items = [
            self._build_item(project, item, audits=audits, committed_claims=committed_claims)
            for item in materialization["items"]
        ]
        ready = sum(1 for item in items if item["ready"])
        reasons: dict[str, int] = {}
        for item in items:
            for reason in item["reasons"]:
                reasons[reason] = reasons.get(reason, 0) + 1
        return {
            "schema": "story.canon-commit-plan.v1",
            "filters": {"claim_id": claim_id},
            "summary": {
                "claims": len(items),
                "ready": ready,
                "blocked": len(items) - ready,
                "reasons": dict(sorted(reasons.items())),
            },
            "items": items,
            "policy": {
                "explicit_candidate_sha256_confirmation": True,
                "canonical_create_only": True,
                "canonical_overwrite": False,
                "audit_precedes_canonical_mutation": True,
                "audit_immutable": True,
                "current_materialization_revalidated": True,
                "one_committed_target_per_claim": True,
                "project_wide_mutation_lock": True,
            },
        }

    def commit(
        self,
        project: StoryProject,
        *,
        claim_id: str,
        confirm_sha256: str,
        actor: str,
        note: str = "",
    ) -> tuple[dict[str, Any], str]:
        actor = str(actor).strip()
        if not actor:
            raise CanonCommitError("actor cannot be empty")
        confirm_sha256 = str(confirm_sha256).strip().lower()
        if not _is_sha256(confirm_sha256):
            raise CanonCommitError("confirm_sha256 must be a 64-character SHA-256")

        try:
            with project_file_lock(project.root, "canon-commit"):
                return self._commit_locked(
                    project,
                    claim_id=claim_id,
                    confirm_sha256=confirm_sha256,
                    actor=actor,
                    note=note,
                )
        except ProjectFileLockError as exc:
            raise CanonCommitError(str(exc)) from exc

    def _commit_locked(
        self,
        project: StoryProject,
        *,
        claim_id: str,
        confirm_sha256: str,
        actor: str,
        note: str,
    ) -> tuple[dict[str, Any], str]:
        item = self._single_plan_item(project, claim_id)
        if item.get("state") == "committed":
            if item.get("candidate_sha256") != confirm_sha256:
                raise CanonCommitError(
                    "confirmation SHA-256 does not match the committed quarantine file"
                )
            audit = item.get("audit")
            if not isinstance(audit, dict):
                raise CanonCommitError("committed target is missing its audit record")
            return self._result_payload(item, audit), "unchanged"

        if not item["ready"]:
            reasons = ", ".join(item["reasons"]) or "not_ready"
            raise CanonCommitError(f"claim is not ready for canonical commit: {reasons}")
        if item.get("candidate_sha256") != confirm_sha256:
            raise CanonCommitError(
                "confirmation SHA-256 does not match the current quarantine file"
            )

        audit = self._authorization_mapping(item, actor=actor, note=note)
        audit_path = project.root / str(item["audit_path"])
        if audit_path.exists():
            if _load_data(audit_path) != audit:
                raise CanonCommitError(
                    "an authorization audit already exists with different actor/note or payload"
                )
        else:
            _exclusive_write_yaml(audit_path, audit)

        # The authorization is durable before Canon. Rebuild the plan immediately
        # afterward so world-state, Canon and quarantine drift are checked again.
        rechecked = self._single_plan_item(project, claim_id)
        if rechecked.get("candidate_sha256") != confirm_sha256:
            raise CanonCommitError("quarantine file changed after authorization")
        if rechecked.get("state") == "committed":
            existing_audit = rechecked.get("audit")
            if not isinstance(existing_audit, dict) or existing_audit != audit:
                raise CanonCommitError("canonical target appeared with a different audit")
            return self._result_payload(rechecked, existing_audit), "unchanged"
        if not rechecked["ready"]:
            reasons = ", ".join(rechecked["reasons"]) or "not_ready"
            raise CanonCommitError(
                f"claim stopped being ready after authorization: {reasons}"
            )
        if rechecked.get("audit") != audit:
            raise CanonCommitError("authorization audit changed before canonical create")

        canonical_path = project.root / str(rechecked["canonical_path"])
        payload = dict(rechecked["canonical_payload"])
        if canonical_path.exists():
            if _load_data(canonical_path) != payload:
                raise CanonCommitError("canonical target already exists with different content")
            return self._result_payload(rechecked, audit), "unchanged"

        try:
            _exclusive_write_yaml(canonical_path, payload)
        except FileExistsError:
            if _load_data(canonical_path) != payload:
                raise CanonCommitError(
                    "canonical target was concurrently created with different content"
                )

        try:
            written = _load_data(canonical_path)
        except (OSError, ValueError, yaml.YAMLError) as exc:
            raise CanonCommitError(f"canonical file verification failed: {exc}") from exc
        if written != payload:
            raise CanonCommitError("canonical file verification failed after create")
        return self._result_payload(rechecked, audit), "created"

    def _single_plan_item(self, project: StoryProject, claim_id: str) -> dict[str, Any]:
        plan = self.build_plan(project, claim_id=claim_id)
        if not plan["items"]:
            raise CanonCommitError(f"unknown staged claim: {claim_id}")
        return plan["items"][0]

    def _build_item(
        self,
        project: StoryProject,
        materialization_item: dict[str, Any],
        *,
        audits: list[dict[str, Any]],
        committed_claims: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        claim_id = str(materialization_item["claim_id"])
        candidate_raw = materialization_item.get("candidate")
        if not isinstance(candidate_raw, dict):
            reasons = [
                f"materialization_{reason}"
                for reason in materialization_item.get("reasons", [])
            ] or ["materialization_not_ready"]
            return self._item(
                claim_id=claim_id,
                ready=False,
                state="blocked",
                reasons=reasons,
                materialization=materialization_item,
            )

        kind = str(materialization_item.get("kind") or "")
        target_id = str(materialization_item.get("target_id") or "")
        if kind not in {"event", "fact"} or not target_id:
            return self._item(
                claim_id=claim_id,
                ready=False,
                state="blocked",
                reasons=["materialization_candidate_invalid"],
                materialization=materialization_item,
            )

        directory = "events" if kind == "event" else "facts"
        candidate_path = (
            project.root / "staging" / "materialization" / directory / f"{target_id}.yaml"
        )
        candidate_rel = candidate_path.relative_to(project.root).as_posix()
        canonical_rel = (
            Path("events" if kind == "event" else "canon")
            / "committed"
            / f"{target_id}.yaml"
        ).as_posix()
        canonical_path = project.root / canonical_rel

        prior_commit = committed_claims.get(claim_id)
        if prior_commit is not None and str(prior_commit.get("target_id")) != target_id:
            prior_id = str(prior_commit.get("id") or "")
            return self._item(
                claim_id=claim_id,
                ready=False,
                state="blocked",
                reasons=["claim_already_committed"],
                kind=kind,
                target_id=target_id,
                candidate_path=candidate_rel,
                canonical_path=canonical_rel,
                audit_id=prior_id or None,
                audit_path=self._audit_rel(prior_id),
                audit=prior_commit,
                materialization=materialization_item,
            )

        if not candidate_path.is_file():
            reasons = ["candidate_not_staged"]
            reasons.extend(
                f"materialization_{reason}"
                for reason in materialization_item.get("reasons", [])
            )
            return self._item(
                claim_id=claim_id,
                ready=False,
                state="blocked",
                reasons=reasons,
                kind=kind,
                target_id=target_id,
                candidate_path=candidate_rel,
                canonical_path=canonical_rel,
                canonical_payload=candidate_raw.get("canonical_payload"),
                materialization=materialization_item,
            )

        try:
            staged = _load_data(candidate_path)
            self._validate_staged_candidate(
                staged, kind=kind, target_id=target_id, claim_id=claim_id
            )
            candidate_sha = _sha256_file(candidate_path)
        except (OSError, ValueError, KeyError, TypeError, yaml.YAMLError) as exc:
            return self._item(
                claim_id=claim_id,
                ready=False,
                state="blocked",
                reasons=["candidate_invalid"],
                kind=kind,
                target_id=target_id,
                candidate_path=candidate_rel,
                candidate_sha256=(
                    _sha256_file(candidate_path) if candidate_path.is_file() else None
                ),
                canonical_path=canonical_rel,
                materialization=materialization_item,
                detail=str(exc),
            )

        payload = dict(staged["canonical_payload"])
        payload_sha = _stable_sha256(payload)
        audit_id = stable_id(
            "audit",
            self.audit_namespace,
            json.dumps(
                {
                    "target_id": target_id,
                    "candidate_sha256": candidate_sha,
                    "canonical_payload_sha256": payload_sha,
                },
                sort_keys=True,
                separators=(",", ":"),
            ),
        )
        audit_rel = self._audit_rel(audit_id)
        matching_audit = next((a for a in audits if a.get("id") == audit_id), None)
        if matching_audit is not None and not self._audit_matches_candidate(
            matching_audit,
            claim_id=claim_id,
            claim_fingerprint=str(staged["claim_fingerprint"]),
            kind=kind,
            target_id=target_id,
            candidate_path=candidate_rel,
            candidate_sha256=candidate_sha,
            canonical_path=canonical_rel,
            canonical_payload_sha256=payload_sha,
            canonical_payload=payload,
        ):
            return self._item(
                claim_id=claim_id,
                ready=False,
                state="blocked",
                reasons=["audit_conflict"],
                kind=kind,
                target_id=target_id,
                candidate_path=candidate_rel,
                candidate_sha256=candidate_sha,
                canonical_path=canonical_rel,
                canonical_payload=payload,
                canonical_payload_sha256=payload_sha,
                audit_id=audit_id,
                audit_path=audit_rel,
                audit=matching_audit,
                materialization=materialization_item,
            )

        if canonical_path.is_file():
            try:
                existing = _load_data(canonical_path)
            except (OSError, ValueError, yaml.YAMLError) as exc:
                return self._item(
                    claim_id=claim_id,
                    ready=False,
                    state="blocked",
                    reasons=["canonical_target_invalid"],
                    kind=kind,
                    target_id=target_id,
                    candidate_path=candidate_rel,
                    candidate_sha256=candidate_sha,
                    canonical_path=canonical_rel,
                    canonical_payload=payload,
                    canonical_payload_sha256=payload_sha,
                    audit_id=audit_id,
                    audit_path=audit_rel,
                    audit=matching_audit,
                    materialization=materialization_item,
                    detail=str(exc),
                )
            if existing != payload:
                return self._item(
                    claim_id=claim_id,
                    ready=False,
                    state="blocked",
                    reasons=["canonical_target_conflict"],
                    kind=kind,
                    target_id=target_id,
                    candidate_path=candidate_rel,
                    candidate_sha256=candidate_sha,
                    canonical_path=canonical_rel,
                    canonical_payload=payload,
                    canonical_payload_sha256=payload_sha,
                    audit_id=audit_id,
                    audit_path=audit_rel,
                    audit=matching_audit,
                    materialization=materialization_item,
                )
            if matching_audit is None:
                return self._item(
                    claim_id=claim_id,
                    ready=False,
                    state="blocked",
                    reasons=["canonical_target_untracked"],
                    kind=kind,
                    target_id=target_id,
                    candidate_path=candidate_rel,
                    candidate_sha256=candidate_sha,
                    canonical_path=canonical_rel,
                    canonical_payload=payload,
                    canonical_payload_sha256=payload_sha,
                    audit_id=audit_id,
                    audit_path=audit_rel,
                    materialization=materialization_item,
                )
            return self._item(
                claim_id=claim_id,
                ready=False,
                state="committed",
                reasons=["already_committed"],
                kind=kind,
                target_id=target_id,
                candidate_path=candidate_rel,
                candidate_sha256=candidate_sha,
                canonical_path=canonical_rel,
                canonical_payload=payload,
                canonical_payload_sha256=payload_sha,
                audit_id=audit_id,
                audit_path=audit_rel,
                audit=matching_audit,
                materialization=materialization_item,
            )

        if self._target_id_exists_elsewhere(project, kind=kind, target_id=target_id):
            return self._item(
                claim_id=claim_id,
                ready=False,
                state="blocked",
                reasons=["canonical_id_exists_elsewhere"],
                kind=kind,
                target_id=target_id,
                candidate_path=candidate_rel,
                candidate_sha256=candidate_sha,
                canonical_path=canonical_rel,
                canonical_payload=payload,
                canonical_payload_sha256=payload_sha,
                audit_id=audit_id,
                audit_path=audit_rel,
                audit=matching_audit,
                materialization=materialization_item,
            )

        reasons: list[str] = []
        if not materialization_item.get("ready"):
            reasons.extend(
                f"materialization_{reason}"
                for reason in materialization_item.get("reasons", [])
            )
        else:
            try:
                expected = quarantine_mapping_from_plan_item(materialization_item)
            except MaterializationError:
                reasons.append("materialization_candidate_invalid")
            else:
                if staged != expected:
                    reasons.append("candidate_drift")

        return self._item(
            claim_id=claim_id,
            ready=not reasons,
            state=(
                "authorized"
                if matching_audit is not None and not reasons
                else ("ready" if not reasons else "blocked")
            ),
            reasons=reasons,
            kind=kind,
            target_id=target_id,
            candidate_path=candidate_rel,
            candidate_sha256=candidate_sha,
            canonical_path=canonical_rel,
            canonical_payload=payload,
            canonical_payload_sha256=payload_sha,
            audit_id=audit_id,
            audit_path=audit_rel,
            audit=matching_audit,
            materialization=materialization_item,
        )

    @staticmethod
    def _item(
        *,
        claim_id: str,
        ready: bool,
        state: str,
        reasons: list[str],
        materialization: dict[str, Any],
        kind: str | None = None,
        target_id: str | None = None,
        candidate_path: str | None = None,
        candidate_sha256: str | None = None,
        canonical_path: str | None = None,
        canonical_payload: Any = None,
        canonical_payload_sha256: str | None = None,
        audit_id: str | None = None,
        audit_path: str | None = None,
        audit: dict[str, Any] | None = None,
        detail: str | None = None,
    ) -> dict[str, Any]:
        item = {
            "claim_id": claim_id,
            "ready": ready,
            "state": state,
            "reasons": _unique(reasons),
            "kind": kind,
            "target_id": target_id,
            "candidate_path": candidate_path,
            "candidate_sha256": candidate_sha256,
            "canonical_path": canonical_path,
            "canonical_payload": canonical_payload,
            "canonical_payload_sha256": canonical_payload_sha256,
            "audit_id": audit_id,
            "audit_path": audit_path,
            "audit": audit,
            "materialization": materialization,
        }
        if detail is not None:
            item["detail"] = detail
        return item

    def _authorization_mapping(
        self,
        item: dict[str, Any],
        *,
        actor: str,
        note: str,
    ) -> dict[str, Any]:
        audit_id = str(item["audit_id"])
        if not validate_id(audit_id, "audit"):
            raise CanonCommitError(f"invalid audit id: {audit_id}")
        return {
            "schema": "story.canon-commit-audit.v1",
            "id": audit_id,
            "action": "authorize_canonical_create",
            "actor": actor,
            "note": note,
            "claim_id": item["claim_id"],
            "claim_fingerprint": item["materialization"]["candidate"]["claim_fingerprint"],
            "kind": item["kind"],
            "target_id": item["target_id"],
            "candidate_path": item["candidate_path"],
            "candidate_sha256": item["candidate_sha256"],
            "canonical_path": item["canonical_path"],
            "canonical_payload_sha256": item["canonical_payload_sha256"],
            "canonical_payload": item["canonical_payload"],
            "checks": {
                "materialization_ready_at_authorization": True,
                "quarantine_exact_match": True,
                "candidate_sha256_confirmed": True,
            },
            "policy": {
                "immutable": True,
                "audit_precedes_canonical_mutation": True,
                "canonical_create_only": True,
                "canonical_overwrite": False,
            },
        }

    @staticmethod
    def _result_payload(item: dict[str, Any], audit: dict[str, Any]) -> dict[str, Any]:
        return {
            "schema": "story.canon-commit-result.v1",
            "claim_id": item["claim_id"],
            "kind": item["kind"],
            "target_id": item["target_id"],
            "canonical_path": item["canonical_path"],
            "canonical_payload_sha256": item["canonical_payload_sha256"],
            "audit_id": audit["id"],
            "audit_path": item["audit_path"],
            "candidate_sha256": item["candidate_sha256"],
            "policy": {
                "canonical_create_only": True,
                "canonical_overwrite": False,
                "audit_precedes_canonical_mutation": True,
                "project_wide_mutation_lock": True,
            },
        }

    def _load_audits(self, project: StoryProject) -> list[dict[str, Any]]:
        directory = project.root / "audit" / "canon_commits"
        if not directory.exists():
            return []
        audits: list[dict[str, Any]] = []
        seen: set[str] = set()
        for path in sorted(directory.rglob("*")):
            if not path.is_file() or path.suffix.lower() not in {".yaml", ".yml", ".json"}:
                continue
            raw = _load_data(path)
            if not isinstance(raw, dict) or raw.get("schema") != "story.canon-commit-audit.v1":
                raise CanonCommitError(f"unsupported canon commit audit record: {path}")
            audit_id = str(raw.get("id") or "")
            if not validate_id(audit_id, "audit"):
                raise CanonCommitError(f"invalid canon commit audit id: {audit_id}")
            if audit_id in seen:
                raise CanonCommitError(f"duplicate canon commit audit id: {audit_id}")
            seen.add(audit_id)
            audits.append(dict(raw))
        return audits

    def _committed_claims(
        self,
        project: StoryProject,
        audits: list[dict[str, Any]],
    ) -> dict[str, dict[str, Any]]:
        committed: dict[str, dict[str, Any]] = {}
        for audit in audits:
            canonical_rel = str(audit.get("canonical_path") or "")
            if not canonical_rel:
                continue
            path = project.root / canonical_rel
            if not path.is_file():
                continue
            try:
                payload = _load_data(path)
            except (OSError, ValueError, yaml.YAMLError):
                continue
            if _stable_sha256(payload) != audit.get("canonical_payload_sha256"):
                continue
            if payload != audit.get("canonical_payload"):
                continue
            claim_id = str(audit.get("claim_id") or "")
            if not claim_id:
                continue
            existing = committed.get(claim_id)
            if existing is not None and existing.get("target_id") != audit.get("target_id"):
                raise CanonCommitError(f"claim has multiple audited canonical targets: {claim_id}")
            committed[claim_id] = audit
        return committed

    @staticmethod
    def _audit_matches_candidate(
        audit: dict[str, Any],
        *,
        claim_id: str,
        claim_fingerprint: str,
        kind: str,
        target_id: str,
        candidate_path: str,
        candidate_sha256: str,
        canonical_path: str,
        canonical_payload_sha256: str,
        canonical_payload: dict[str, Any],
    ) -> bool:
        policy = audit.get("policy") or {}
        return (
            audit.get("schema") == "story.canon-commit-audit.v1"
            and audit.get("action") == "authorize_canonical_create"
            and audit.get("claim_id") == claim_id
            and audit.get("claim_fingerprint") == claim_fingerprint
            and audit.get("kind") == kind
            and audit.get("target_id") == target_id
            and audit.get("candidate_path") == candidate_path
            and audit.get("candidate_sha256") == candidate_sha256
            and audit.get("canonical_path") == canonical_path
            and audit.get("canonical_payload_sha256") == canonical_payload_sha256
            and audit.get("canonical_payload") == canonical_payload
            and policy.get("immutable") is True
            and policy.get("audit_precedes_canonical_mutation") is True
            and policy.get("canonical_create_only") is True
            and policy.get("canonical_overwrite") is False
        )

    @staticmethod
    def _validate_staged_candidate(
        raw: Any,
        *,
        kind: str,
        target_id: str,
        claim_id: str,
    ) -> None:
        if not isinstance(raw, dict):
            raise ValueError("quarantine candidate must be an object")
        if raw.get("schema") != "story.materialization-candidate.v1":
            raise ValueError("unsupported quarantine candidate schema")
        if raw.get("kind") != kind or raw.get("target_id") != target_id or raw.get("claim_id") != claim_id:
            raise ValueError("quarantine candidate identity mismatch")
        if not _is_sha256(str(raw.get("claim_fingerprint") or "")):
            raise ValueError("quarantine claim fingerprint must be SHA-256")
        policy = raw.get("policy") or {}
        if policy.get("quarantine_only") is not True:
            raise ValueError("quarantine_only policy must be true")
        if policy.get("canonical_mutation") is not False:
            raise ValueError("quarantine candidate must not claim canonical mutation")
        if policy.get("commit_required") is not True:
            raise ValueError("quarantine candidate must require commit")
        payload = raw.get("canonical_payload")
        if not isinstance(payload, dict) or payload.get("id") != target_id:
            raise ValueError("canonical payload identity mismatch")
        data = {key: value for key, value in payload.items() if key != "schema"}
        if kind == "event":
            if payload.get("schema") != "story.event.v1":
                raise ValueError("event candidate must carry story.event.v1")
            StoryEvent.from_mapping(data)
        else:
            if payload.get("schema") != "story.canon.v1":
                raise ValueError("fact candidate must carry story.canon.v1")
            CanonFact.from_mapping(data)

    @staticmethod
    def _target_id_exists_elsewhere(
        project: StoryProject,
        *,
        kind: str,
        target_id: str,
    ) -> bool:
        if kind == "event":
            return any(event.id == target_id for event in project.load_events())
        return any(fact.id == target_id for fact in project.load_canon_facts())

    @staticmethod
    def _audit_rel(audit_id: str) -> str | None:
        if not validate_id(audit_id, "audit"):
            return None
        return (Path("audit") / "canon_commits" / f"{audit_id}.yaml").as_posix()


def _stable_sha256(value: Any) -> str:
    raw = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _is_sha256(value: str) -> bool:
    return len(value) == 64 and all(char in "0123456789abcdef" for char in value)


def _load_data(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as fh:
        if path.suffix.lower() == ".json":
            return json.load(fh)
        return yaml.safe_load(fh)


def _exclusive_write_yaml(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = yaml.safe_dump(data, allow_unicode=True, sort_keys=False, width=120)
    with path.open("x", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
        fh.flush()
        os.fsync(fh.fileno())


def _unique(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        if value not in result:
            result.append(value)
    return result
