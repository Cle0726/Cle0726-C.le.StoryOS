import json
from pathlib import Path

import yaml

from storyos.context import ContextCompiler, ContextMode, ContextRequest, RetrievalHit
from storyos.index import StoryIndex
from storyos.inspector import ContextInspector
from storyos.project import StoryProject
from storyos.retrieval import SQLiteCanonicalRetriever


CADEN = "chr_00000000000000000000000000000001"
FUTURE = "chr_00000000000000000000000000000002"
LOCATION = "loc_00000000000000000000000000000001"
SECRET = "canon_00000000000000000000000000000001"
CLAIM = "clm_00000000000000000000000000000001"


class FakeRetriever:
    def __init__(self, hits):
        self.hits = tuple(hits)

    def search(self, query: str, *, limit: int = 8):
        return self.hits[:limit]


def write_yaml(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")


def make_project(tmp_path: Path) -> StoryProject:
    write_yaml(
        tmp_path / "storyos.yaml",
        {"schema": "story.project.v1", "id": "story_demo", "name": "Demo"},
    )
    write_yaml(
        tmp_path / "entities" / "caden.yaml",
        {"schema": "story.entity.v1", "id": CADEN, "kind": "character", "name": "凯登", "slug": "caden"},
    )
    write_yaml(
        tmp_path / "entities" / "future.yaml",
        {"schema": "story.entity.v1", "id": FUTURE, "kind": "character", "name": "未来人物", "slug": "future"},
    )
    write_yaml(
        tmp_path / "entities" / "location.yaml",
        {"schema": "story.entity.v1", "id": LOCATION, "kind": "location", "name": "修理铺", "slug": "workshop"},
    )
    write_yaml(
        tmp_path / "canon" / "secret.yaml",
        {
            "schema": "story.canon.v1",
            "id": SECRET,
            "subject": CADEN,
            "predicate": "identity.secret",
            "value": "未来秘密",
            "authority": "locked",
            "valid_from": 0,
            "reveal_at": 200,
            "tags": [],
        },
    )
    write_yaml(
        tmp_path / "events" / "100-location.yaml",
        {
            "schema": "story.event.v1",
            "id": "evt_00000000000000000000000000000001",
            "subject": CADEN,
            "type": "location.set",
            "at": {"sequence": 100},
            "payload": {"value": LOCATION},
        },
    )
    write_yaml(
        tmp_path / "events" / "110-secret-state.yaml",
        {
            "schema": "story.event.v1",
            "id": "evt_00000000000000000000000000000002",
            "subject": CADEN,
            "type": "secret_relation.set",
            "at": {"sequence": 110},
            "payload": {"value": FUTURE},
        },
    )
    write_yaml(
        tmp_path / "events" / "200-knowledge.yaml",
        {
            "schema": "story.event.v1",
            "id": "evt_00000000000000000000000000000003",
            "subject": CADEN,
            "type": "knowledge.gained",
            "at": {"sequence": 200},
            "payload": {"fact_id": SECRET},
        },
    )
    write_yaml(
        tmp_path / "staging" / "claims" / "claim.yaml",
        {
            "schema": "story.claim.v1",
            "id": CLAIM,
            "subject": CADEN,
            "predicate": "identity.secret",
            "value": "不要进入检索",
            "at": {"sequence": 100},
            "confidence": 0.99,
            "status": "pending",
        },
    )
    return StoryProject.open(tmp_path)


def excluded_reasons(manifest, ref: str) -> set[str]:
    return {item.reason for item in manifest.excluded if item.ref == ref}


def test_high_score_future_secret_is_blocked(tmp_path: Path):
    project = make_project(tmp_path)
    compiler = ContextCompiler(project, retriever=FakeRetriever([RetrievalHit(SECRET, 0.99)]))
    manifest = compiler.compile(
        ContextRequest(
            through_sequence=100,
            participants=(CADEN,),
            pov=CADEN,
            semantic_query="秘密",
        )
    )
    assert SECRET not in {item.ref for item in manifest.included}
    assert "not_revealed" in excluded_reasons(manifest, SECRET)
    trace = ContextInspector().trace(manifest, SECRET)
    assert trace.retrieval_score == 0.99
    assert trace.included is False


def test_secret_is_visible_after_reveal_and_knowledge(tmp_path: Path):
    project = make_project(tmp_path)
    compiler = ContextCompiler(project, retriever=FakeRetriever([RetrievalHit(SECRET, 0.99)]))
    manifest = compiler.compile(
        ContextRequest(
            through_sequence=200,
            participants=(CADEN,),
            pov=CADEN,
            semantic_query="秘密",
        )
    )
    assert SECRET in {item.ref for item in manifest.included}


def test_unbound_entity_and_hidden_objective_state_do_not_leak(tmp_path: Path):
    project = make_project(tmp_path)
    compiler = ContextCompiler(project, retriever=FakeRetriever([RetrievalHit(FUTURE, 1.0)]))
    manifest = compiler.compile(
        ContextRequest(
            through_sequence=120,
            participants=(CADEN,),
            pov=CADEN,
            semantic_query="未来人物",
        )
    )
    assert FUTURE not in {item.ref for item in manifest.included}
    assert "pov_entity_unbound" in excluded_reasons(manifest, FUTURE)
    state = next(item for item in manifest.included if item.kind == "state" and CADEN in item.ref)
    payload = json.loads(state.content)
    assert payload["values"] == {"location": LOCATION}


def test_author_mode_can_see_full_objective_state(tmp_path: Path):
    project = make_project(tmp_path)
    manifest = ContextCompiler(project).compile(
        ContextRequest(
            through_sequence=120,
            participants=(CADEN,),
            mode=ContextMode.AUTHOR,
        )
    )
    state = next(item for item in manifest.included if item.kind == "state")
    payload = json.loads(state.content)
    assert payload["values"]["location"] == LOCATION
    assert payload["values"]["secret_relation"] == FUTURE


def test_retrieval_order_does_not_change_manifest(tmp_path: Path):
    project = make_project(tmp_path)
    hits_a = [RetrievalHit(SECRET, 0.9), RetrievalHit(FUTURE, 0.9)]
    hits_b = list(reversed(hits_a))
    request = ContextRequest(
        through_sequence=100,
        participants=(CADEN,),
        pov=CADEN,
        semantic_query="anything",
    )
    first = ContextCompiler(project, retriever=FakeRetriever(hits_a)).compile(request).as_dict()
    second = ContextCompiler(project, retriever=FakeRetriever(hits_b)).compile(request).as_dict()
    assert first == second


def test_sqlite_retriever_excludes_staged_claims(tmp_path: Path):
    project = make_project(tmp_path)
    db = tmp_path / ".storyos" / "index.sqlite"
    StoryIndex(db).rebuild(project)
    retriever = SQLiteCanonicalRetriever(db)
    refs = {hit.ref for hit in retriever.search("不要进入检索", limit=20)}
    assert CLAIM not in refs
    canon_refs = {hit.ref for hit in retriever.search("未来秘密", limit=20)}
    assert SECRET in canon_refs
