# C.le. StoryOS

C.le. StoryOS is a deterministic long-form fiction operating system for managing manuscript text, story state, Canon, character knowledge, continuity, candidate claims, context compilation, and author approval workflows.

This repository is the standalone home of StoryOS. It is intentionally separated from `C.le.console`.

## Core principles

- Human-readable project files are the canonical source of truth.
- Stable IDs are independent from display names and file paths.
- Story State is projected from append-oriented Story Events.
- SQLite indexes, caches, embeddings and workflow databases are rebuildable runtime data, never Canon.
- AI may propose Candidate Claims but cannot directly mutate Canon.
- Deterministic timeline, authority, knowledge and spoiler gates run before semantic retrieval.
- Author approval is the final authority.

## Migration status

The StoryOS core is being migrated from the earlier development branches in `Cle0726/C.le.console` into this standalone repository. The old repository is treated only as a migration source; new StoryOS development belongs here.

## License

No open-source license has been selected yet. Until a license is added, the source is publicly visible but reuse rights are not granted beyond what applicable law permits.
