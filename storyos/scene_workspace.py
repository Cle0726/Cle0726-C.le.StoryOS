from __future__ import annotations

from collections import Counter
from typing import Any

from storyos.authority import CanonFact, CanonResolver
from storyos.canon_commit import CanonCommitWorkbench
from storyos.claim_review import ClaimReviewWorkbench
from storyos.knowledge import KnowledgeTimeline
from storyos.materialization import MaterializationWorkbench
from storyos.project import StoryProject
from storyos.state import StoryStateProjector
from storyos.workspace import AuthoringWorkspace, AuthoringWorkspaceError


class SceneWorkspaceError(RuntimeError):
    """Raised when a scene-oriented authoring view cannot be built safely."""


class SceneWorkspace:
    """Deterministic, read-only authoring view anchored to one manuscript.

    The service intentionally does not expose manuscript content. The editor loads the
    working copy through the existing manuscript read protocol, while this view supplies
    point-in-time story context and navigation metadata.

    When ``pov_entity_id`` is supplied the response becomes conservative POV mode:
    only the POV character's projected state is exposed, knowledge is limited to known
    Canon facts that have passed the reveal boundary, and global Canon conflicts / plot
    threads are withheld.
    """

    def __init__(self) -> None:
        self._workspace = AuthoringWorkspace()
        self._state = StoryStateProjector()
        self._resolver = CanonResolver()
        self._reviews = ClaimReviewWorkbench()
        self._materialization = MaterializationWorkbench()
        self._commits = CanonCommitWorkbench()

    def build(
        self,
        project: StoryProject,
        manuscript_path: str,
        *,
        through_sequence: int | None = None,
        pov_entity_id: str | None = None,
    ) -> dict[str, Any]:
        manuscripts = self._workspace.list_manuscripts(project)
        document = self._workspace.load_manuscript(project, manuscript_path)
        current = next(
            (item for item in manuscripts if item["path"] == document["path"]),
            None,
        )
        if current is None:
            raise SceneWorkspaceError(
                f"manuscript is not present in deterministic listing: {manuscript_path}"
            )

        entities = sorted(
            project.load_entities(), key=lambda item: (item.kind, item.name, item.id)
        )
        entity_by_id = {entity.id: entity for entity in entities}
        characters = [entity for entity in entities if entity.kind == "character"]
        plots = [entity for entity in entities if entity.kind == "plot"]

        pov = None
        if pov_entity_id is not None:
            pov = entity_by_id.get(pov_entity_id)
            if pov is None:
                raise SceneWorkspaceError(f"unknown POV entity id: {pov_entity_id}")
            if pov.kind != "character":
                raise SceneWorkspaceError("POV entity must be a character")

        events = sorted(project.load_events(), key=lambda item: (item.at.sequence, item.id))
        facts = sorted(
            project.load_canon_facts(),
            key=lambda item: (item.subject, item.predicate, -int(item.authority), item.id),
        )
        effective, boundary_source = _resolve_boundary(
            events,
            season=current["season"],
            episode=current["episode"],
            requested=through_sequence,
        )
        visible_events = (
            []
            if effective is None
            else [event for event in events if event.at.sequence <= effective]
        )
        states = (
            {}
            if effective is None
            else self._state.project(events, through_sequence=effective)
        )

        episode_events = [
            event
            for event in visible_events
            if _same_episode(event, current["season"], current["episode"])
        ]
        episode_subjects = {event.subject for event in episode_events}
        latest_event_by_subject: dict[str, int] = {}
        event_counts = Counter()
        for event in visible_events:
            latest_event_by_subject[event.subject] = event.at.sequence
            event_counts[event.subject] += 1

        knowledge = KnowledgeTimeline(events)
        facts_by_id = {fact.id: fact for fact in facts}

        if pov is None:
            character_rows = [
                _character_row(
                    entity,
                    state=states.get(entity.id),
                    facts_by_id=facts_by_id,
                    through_sequence=effective,
                    scene_relevant=entity.id in episode_subjects,
                    latest_event_sequence=latest_event_by_subject.get(entity.id),
                    event_count=event_counts.get(entity.id, 0),
                    pov_mode=False,
                )
                for entity in characters
            ]
        else:
            character_rows = [
                _character_row(
                    pov,
                    state=states.get(pov.id),
                    facts_by_id=facts_by_id,
                    through_sequence=effective,
                    scene_relevant=pov.id in episode_subjects,
                    latest_event_sequence=latest_event_by_subject.get(pov.id),
                    event_count=event_counts.get(pov.id, 0),
                    pov_mode=True,
                )
            ]

        if pov is None:
            canon_conflicts = _canon_conflicts(
                self._resolver,
                facts,
                through_sequence=effective,
                entity_by_id=entity_by_id,
            )
            plot_threads = _plot_threads(
                plots,
                visible_events,
                episode_subjects=episode_subjects,
            )
            workflow = self._workflow_attention(project)
        else:
            canon_conflicts = []
            plot_threads = []
            workflow = {}

        navigation = _navigation(manuscripts, current["path"])
        episode_event_rows = [
            {
                "id": event.id,
                "subject": event.subject,
                "subject_name": (
                    entity_by_id[event.subject].name
                    if event.subject in entity_by_id
                    else event.subject
                ),
                "type": event.type,
                "sequence": event.at.sequence,
                "scene": event.at.scene,
            }
            for event in episode_events
            if pov is None or event.subject == pov.id
        ]

        payload: dict[str, Any] = {
            "schema": "story.authoring-scene-workspace.v1",
            "project_id": str(project.manifest.get("id") or ""),
            "mode": "author" if pov is None else "pov",
            "pov": (
                None
                if pov is None
                else {
                    "id": pov.id,
                    "name": pov.name,
                    "aliases": list(pov.aliases),
                }
            ),
            "manuscript": {
                key: current[key]
                for key in (
                    "path",
                    "name",
                    "title",
                    "season",
                    "episode",
                    "bytes",
                    "characters",
                    "lines",
                    "sha256",
                )
            },
            "timeline": {
                "requested_through_sequence": through_sequence,
                "effective_through_sequence": effective,
                "boundary_source": boundary_source,
                "episode_events": len(episode_event_rows),
                "visible_events": len(visible_events) if pov is None else sum(
                    1 for event in visible_events if event.subject == pov.id
                ),
            },
            "navigation": navigation,
            "episode_events": episode_event_rows,
            "characters": character_rows,
            "canon_conflicts": canon_conflicts,
            "open_plots": plot_threads,
            "workflow_attention": workflow,
            "actions_available": _actions_available(
                navigation,
                has_characters=bool(character_rows),
                has_workflow=bool(workflow),
                pov_mode=pov is not None,
            ),
            "policy": {
                "read_only": True,
                "manuscript_mutation": False,
                "history_mutation": False,
                "recovery_mutation": False,
                "canonical_mutation": False,
                "staging_mutation": False,
                "pov_safe": pov is not None,
                "other_character_state_exposed": pov is None,
                "global_canon_conflicts_exposed": pov is None,
                "global_plot_threads_exposed": pov is None,
                "manuscript_content_included": False,
            },
        }

        # Defense in depth: a scene response must never accidentally inherit the
        # manuscript working-copy content loaded only for path validation/metadata.
        if "content" in payload["manuscript"]:
            raise SceneWorkspaceError("scene workspace must not expose manuscript content")
        return payload

    def _workflow_attention(self, project: StoryProject) -> dict[str, int]:
        review = self._reviews.build_queue(project)
        materialization = self._materialization.build_plan(project)
        commits = self._commits.build_plan(project)
        references = project.validate_references()
        return {
            "unreviewed_claims": int(review["summary"].get("unreviewed", 0)),
            "stale_reviews": int(review["summary"].get("stale_reviews", 0)),
            "materialization_ready": int(materialization["summary"].get("ready", 0)),
            "canon_commit_ready": int(commits["summary"].get("ready", 0)),
            "reference_errors": len(references),
        }


def _resolve_boundary(
    events,
    *,
    season: int | None,
    episode: int | None,
    requested: int | None,
) -> tuple[int | None, str]:
    if requested is not None:
        if requested < 0:
            raise SceneWorkspaceError("through_sequence must be >= 0")
        return requested, "explicit_sequence"

    if season is not None and episode is not None:
        eligible = [
            event.at.sequence
            for event in events
            if event.at.season is not None
            and event.at.episode is not None
            and (event.at.season, event.at.episode) <= (season, episode)
        ]
        if eligible:
            return max(eligible), "episode_end"
        return None, "before_first_positioned_event"

    if not events:
        return None, "no_events"
    return max(event.at.sequence for event in events), "latest_event"


def _same_episode(event, season: int | None, episode: int | None) -> bool:
    if season is None or episode is None:
        return False
    return event.at.season == season and event.at.episode == episode


def _character_row(
    entity,
    *,
    state,
    facts_by_id: dict[str, CanonFact],
    through_sequence: int | None,
    scene_relevant: bool,
    latest_event_sequence: int | None,
    event_count: int,
    pov_mode: bool,
) -> dict[str, Any]:
    values = {} if state is None else dict(state.values)
    knowledge_tokens = [] if state is None else sorted(state.knowledge)
    knowledge_rows: list[dict[str, Any]] = []
    hidden_by_reveal = 0
    hidden_ungoverned = 0

    for token in knowledge_tokens:
        fact = facts_by_id.get(token)
        if fact is None:
            if pov_mode:
                hidden_ungoverned += 1
            else:
                knowledge_rows.append({"id": token, "kind": "knowledge_token"})
            continue
        if pov_mode and not fact.revealed_at(through_sequence):
            hidden_by_reveal += 1
            continue
        knowledge_rows.append(
            {
                "id": fact.id,
                "kind": "canon_fact",
                "subject": fact.subject,
                "predicate": fact.predicate,
                "value": fact.value,
                "authority": fact.authority.name.lower(),
                "active": fact.active_at(through_sequence),
                "revealed": fact.revealed_at(through_sequence),
            }
        )

    row = {
        "id": entity.id,
        "name": entity.name,
        "aliases": list(entity.aliases),
        "scene_relevant": scene_relevant,
        "location": values.get("location"),
        "state": values,
        "knowledge": {
            "visible": knowledge_rows,
            "visible_count": len(knowledge_rows),
            "hidden_by_reveal": hidden_by_reveal,
            "hidden_ungoverned": hidden_ungoverned,
        },
        "latest_event_sequence": latest_event_sequence,
        "event_count": event_count,
    }
    if not pov_mode:
        row["data"] = dict(entity.data)
    return row


def _canon_conflicts(
    resolver: CanonResolver,
    facts: list[CanonFact],
    *,
    through_sequence: int | None,
    entity_by_id: dict[str, Any],
) -> list[dict[str, Any]]:
    keys = sorted({(fact.subject, fact.predicate) for fact in facts})
    rows: list[dict[str, Any]] = []
    for subject, predicate in keys:
        resolution = resolver.resolve(
            facts,
            subject=subject,
            predicate=predicate,
            through_sequence=through_sequence,
        )
        if not resolution.ambiguous:
            continue
        rows.append(
            {
                "subject": subject,
                "subject_name": (
                    entity_by_id[subject].name if subject in entity_by_id else subject
                ),
                "predicate": predicate,
                "facts": [
                    {
                        "id": fact.id,
                        "value": fact.value,
                        "authority": fact.authority.name.lower(),
                    }
                    for fact in resolution.conflicts
                ],
            }
        )
    return rows


def _plot_threads(plots, visible_events, *, episode_subjects: set[str]) -> list[dict[str, Any]]:
    status: dict[str, tuple[bool, int, str]] = {}
    for event in visible_events:
        if event.type not in {"plot.resolved", "plot.reopened"}:
            continue
        raw = event.payload.get("plot_id")
        if raw is None:
            continue
        plot_id = str(raw)
        status[plot_id] = (
            event.type == "plot.resolved",
            event.at.sequence,
            event.id,
        )

    rows: list[dict[str, Any]] = []
    for plot in plots:
        resolved, sequence, event_id = status.get(plot.id, (False, -1, ""))
        if resolved:
            continue
        rows.append(
            {
                "id": plot.id,
                "name": plot.name,
                "aliases": list(plot.aliases),
                "data": dict(plot.data),
                "scene_relevant": plot.id in episode_subjects,
                "last_status_sequence": None if sequence < 0 else sequence,
                "last_status_event_id": None if not event_id else event_id,
                "status": "open",
            }
        )
    return rows


def _navigation(manuscripts: list[dict[str, Any]], current_path: str) -> dict[str, Any]:
    index = next(
        (index for index, item in enumerate(manuscripts) if item["path"] == current_path),
        None,
    )
    if index is None:
        raise SceneWorkspaceError(f"unknown manuscript in navigation: {current_path}")

    def compact(item: dict[str, Any] | None) -> dict[str, Any] | None:
        if item is None:
            return None
        return {
            key: item[key]
            for key in ("path", "title", "season", "episode", "sha256")
        }

    return {
        "index": index,
        "total": len(manuscripts),
        "previous": compact(manuscripts[index - 1] if index > 0 else None),
        "next": compact(manuscripts[index + 1] if index + 1 < len(manuscripts) else None),
    }


def _actions_available(
    navigation: dict[str, Any],
    *,
    has_characters: bool,
    has_workflow: bool,
    pov_mode: bool,
) -> list[dict[str, Any]]:
    rows = [
        {
            "id": "open_previous_manuscript",
            "kind": "read_navigation",
            "enabled": navigation["previous"] is not None,
        },
        {
            "id": "open_next_manuscript",
            "kind": "read_navigation",
            "enabled": navigation["next"] is not None,
        },
        {
            "id": "inspect_character",
            "kind": "read_navigation",
            "enabled": has_characters,
        },
        {
            "id": "switch_context_mode",
            "kind": "read_navigation",
            "enabled": True,
            "target": "author" if pov_mode else "pov",
        },
    ]
    if has_workflow and not pov_mode:
        rows.append(
            {
                "id": "inspect_workflow_queue",
                "kind": "read_navigation",
                "enabled": True,
            }
        )
    return rows
