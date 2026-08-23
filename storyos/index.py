from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path

from storyos.project import StoryProject


class StoryIndex:
    """Disposable SQLite/FTS index; canonical files remain source of truth."""

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)

    def rebuild(self, project: StoryProject) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        if self.db_path.exists():
            self.db_path.unlink()

        conn = sqlite3.connect(self.db_path)
        try:
            conn.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE entities (
                    id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    name TEXT NOT NULL,
                    slug TEXT NOT NULL,
                    aliases_json TEXT NOT NULL,
                    data_json TEXT NOT NULL
                );
                CREATE TABLE events (
                    id TEXT PRIMARY KEY,
                    subject TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    payload_json TEXT NOT NULL,
                    source_json TEXT NOT NULL
                );
                CREATE TABLE canon_facts (
                    id TEXT PRIMARY KEY,
                    subject TEXT NOT NULL,
                    predicate TEXT NOT NULL,
                    value_json TEXT NOT NULL,
                    authority INTEGER NOT NULL,
                    valid_from INTEGER,
                    valid_to INTEGER,
                    reveal_at INTEGER,
                    source_json TEXT NOT NULL,
                    tags_json TEXT NOT NULL
                );
                CREATE TABLE staged_claims (
                    id TEXT PRIMARY KEY,
                    subject TEXT NOT NULL,
                    predicate TEXT NOT NULL,
                    value_json TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    confidence REAL NOT NULL,
                    proposed_authority INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    source_json TEXT NOT NULL
                );
                CREATE INDEX idx_events_subject_sequence ON events(subject, sequence);
                CREATE INDEX idx_canon_subject_predicate ON canon_facts(subject, predicate, authority);
                CREATE INDEX idx_claims_subject_sequence ON staged_claims(subject, sequence);
                """
            )
            conn.execute("INSERT INTO meta(key, value) VALUES('schema', 'story.index.v2')")

            for entity in project.load_entities():
                conn.execute(
                    "INSERT INTO entities VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        entity.id,
                        entity.kind,
                        entity.name,
                        entity.slug,
                        json.dumps(entity.aliases, ensure_ascii=False),
                        json.dumps(entity.data, ensure_ascii=False, sort_keys=True),
                    ),
                )

            for event in project.load_events():
                conn.execute(
                    "INSERT INTO events VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        event.id,
                        event.subject,
                        event.type,
                        event.at.sequence,
                        json.dumps(event.payload, ensure_ascii=False, sort_keys=True),
                        json.dumps(event.source, ensure_ascii=False, sort_keys=True),
                    ),
                )

            for fact in project.load_canon_facts():
                conn.execute(
                    "INSERT INTO canon_facts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        fact.id,
                        fact.subject,
                        fact.predicate,
                        json.dumps(fact.value, ensure_ascii=False, sort_keys=True),
                        int(fact.authority),
                        fact.valid_from,
                        fact.valid_to,
                        fact.reveal_at,
                        json.dumps(fact.source, ensure_ascii=False, sort_keys=True),
                        json.dumps(fact.tags, ensure_ascii=False),
                    ),
                )

            for claim in project.load_claims():
                conn.execute(
                    "INSERT INTO staged_claims VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        claim.id,
                        claim.subject,
                        claim.predicate,
                        json.dumps(claim.value, ensure_ascii=False, sort_keys=True),
                        claim.at.sequence,
                        claim.confidence,
                        int(claim.proposed_authority),
                        claim.status.value,
                        json.dumps(claim.source, ensure_ascii=False, sort_keys=True),
                    ),
                )
            conn.commit()
        finally:
            conn.close()

    def counts(self) -> dict[str, int]:
        with closing(sqlite3.connect(self.db_path)) as conn:
            return {
                name: int(conn.execute(f"SELECT count(*) FROM {name}").fetchone()[0])
                for name in ("entities", "events", "canon_facts", "staged_claims")
            }
