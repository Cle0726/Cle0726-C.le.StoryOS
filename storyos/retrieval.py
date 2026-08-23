from __future__ import annotations

import json
import re
import sqlite3
import unicodedata
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from storyos.authority import CanonAuthority
from storyos.context import RetrievalHit


class RetrievalIndexError(RuntimeError):
    """Raised when the disposable StoryOS index cannot be used for retrieval."""


@dataclass(frozen=True)
class _SearchDocument:
    ref: str
    fields: tuple[tuple[float, str], ...]


class SQLiteCanonicalRetriever:
    """Deterministic lexical retriever over entities and mainline Canon only.

    Events and staged Candidate Claims are intentionally excluded from the corpus.
    Retrieval produces candidate refs/scores; ContextCompiler remains the safety gate.
    """

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)

    def validate(self) -> None:
        if not self.db_path.is_file():
            raise RetrievalIndexError(f"missing StoryOS index: {self.db_path}")
        try:
            with closing(sqlite3.connect(self.db_path)) as conn:
                row = conn.execute("SELECT value FROM meta WHERE key = 'schema'").fetchone()
        except sqlite3.Error as exc:
            raise RetrievalIndexError(f"cannot read StoryOS index: {exc}") from exc
        if row is None or row[0] != "story.index.v2":
            found = None if row is None else row[0]
            raise RetrievalIndexError(f"unsupported StoryOS index schema: {found!r}")

    def search(self, query: str, *, limit: int = 8) -> tuple[RetrievalHit, ...]:
        if limit < 0:
            raise ValueError("retrieval limit must be >= 0")
        if limit == 0:
            return ()
        terms = _query_terms(query)
        if not terms:
            return ()
        normalized_query = _normalize_text(query)

        self.validate()
        try:
            with closing(sqlite3.connect(self.db_path)) as conn:
                documents = tuple(self._load_documents(conn))
        except sqlite3.Error as exc:
            raise RetrievalIndexError(f"cannot read StoryOS index: {exc}") from exc

        hits: list[RetrievalHit] = []
        for document in documents:
            score = _score_document(document, terms, normalized_query)
            if score > 0:
                hits.append(RetrievalHit(ref=document.ref, score=score))
        hits.sort(key=lambda hit: (-hit.score, hit.ref))
        return tuple(hits[:limit])

    def _load_documents(self, conn: sqlite3.Connection) -> Iterable[_SearchDocument]:
        for entity_id, name, aliases_json, data_json in conn.execute(
            "SELECT id, name, aliases_json, data_json FROM entities ORDER BY id"
        ):
            aliases = " ".join(str(value) for value in _json_sequence(aliases_json))
            yield _SearchDocument(
                ref=str(entity_id),
                fields=((1.0, str(name)), (0.85, aliases), (0.45, str(data_json))),
            )

        minimum_authority = int(CanonAuthority.PLANNING)
        for fact_id, subject, predicate, value_json, source_json, tags_json in conn.execute(
            """SELECT id, subject, predicate, value_json, source_json, tags_json
               FROM canon_facts WHERE authority >= ? ORDER BY id""",
            (minimum_authority,),
        ):
            yield _SearchDocument(
                ref=str(fact_id),
                fields=(
                    (1.0, str(value_json)),
                    (0.9, str(predicate)),
                    (0.65, str(tags_json)),
                    (0.45, str(source_json)),
                    (0.3, str(subject)),
                ),
            )


def _json_sequence(raw: str) -> tuple:
    try:
        value = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return ()
    return tuple(value) if isinstance(value, (list, tuple)) else ()


def _normalize_text(value: str) -> str:
    return unicodedata.normalize("NFKC", str(value)).casefold().strip()


def _query_terms(query: str) -> tuple[str, ...]:
    normalized = _normalize_text(query)
    if not normalized:
        return ()
    runs = re.findall(r"[0-9a-z_]+|[\u3400-\u4dbf\u4e00-\u9fff]+", normalized)
    terms: list[str] = []
    for run in runs:
        if _is_cjk_run(run) and len(run) > 2:
            terms.append(run)
            terms.extend(run[index:index + 2] for index in range(len(run) - 1))
        else:
            terms.append(run)
    return tuple(dict.fromkeys(term for term in terms if term))


def _is_cjk_run(value: str) -> bool:
    return bool(value) and all(
        "\u3400" <= char <= "\u4dbf" or "\u4e00" <= char <= "\u9fff"
        for char in value
    )


def _score_document(document: _SearchDocument, terms: tuple[str, ...], normalized_query: str) -> float:
    fields = [(weight, _normalize_text(text)) for weight, text in document.fields if text]
    if not fields:
        return 0.0
    token_weights = [
        max((weight for weight, text in fields if term in text), default=0.0)
        for term in terms
    ]
    matched = [weight for weight in token_weights if weight > 0]
    if not matched:
        return 0.0
    coverage = len(matched) / len(terms)
    best_field = max(matched)
    average_field = sum(matched) / len(matched)
    exact = 1.0 if normalized_query and any(normalized_query in text for _, text in fields) else 0.0
    score = 0.50 * coverage + 0.25 * best_field + 0.15 * average_field + 0.10 * exact
    return round(min(1.0, max(0.0, score)), 6)
