export interface ManuscriptSummary {
  path: string;
  name: string;
  title: string;
  season: number | null;
  episode: number | null;
  bytes: number;
  characters: number;
  lines: number;
  sha256: string;
}

export interface EntitySummary {
  id: string;
  kind: string;
  name: string;
  slug: string;
  aliases: string[];
  data: Record<string, unknown>;
  state: {
    values: Record<string, unknown>;
    knowledge_count: number;
    resolved_plot_count: number;
  };
  counts: {
    events: number;
    events_total: number;
    canon_facts: number;
    active_canon_facts: number;
    claims: number;
  };
  latest_event_sequence: number | null;
}

export interface WorkspaceSnapshot {
  schema: 'story.authoring-workspace.v1';
  project: {
    id: string;
    name: string;
    language: string;
    paths: Record<string, unknown>;
    import: Record<string, unknown>;
  };
  timeline: {
    requested_through_sequence: number | null;
    effective_through_sequence: number | null;
    latest_event_sequence: number | null;
    events: number;
    events_total: number;
  };
  summary: Record<string, number>;
  manuscripts: ManuscriptSummary[];
  entities: EntitySummary[];
  canon: { authorities: Record<string, number> };
  workflow: {
    review?: Record<string, number>;
    materialization?: Record<string, number>;
    canon_commit?: Record<string, number>;
    attention?: Record<string, number>;
    [key: string]: unknown;
  };
  diagnostics: { reference_errors: string[] };
  context: Record<string, unknown>;
  policy: ReadOnlyPolicy & { mutation_commands_are_separate: true };
}

export interface EntityView {
  schema: 'story.authoring-entity.v1';
  project_id: string;
  through_sequence: number | null;
  entity: {
    id: string;
    kind: string;
    name: string;
    slug: string;
    aliases: string[];
    data: Record<string, unknown>;
  };
  state: {
    values: Record<string, unknown>;
    knowledge: string[];
    resolved_plots: string[];
  };
  events: Array<Record<string, unknown>>;
  canon_facts: Array<Record<string, unknown>>;
  claims: Array<Record<string, unknown>>;
  workflow: Record<string, unknown>;
  policy: ReadOnlyPolicy;
}

export interface ManuscriptView {
  schema: 'story.authoring-manuscript.v1';
  project_id: string;
  path: string;
  title: string;
  season: number | null;
  episode: number | null;
  bytes: number;
  characters: number;
  lines: number;
  sha256: string;
  content: string;
  policy: ReadOnlyPolicy;
}

export interface ManuscriptRevisionSummary {
  sha256: string;
  bytes: number;
  characters: number;
  lines: number;
  current: boolean;
  captured_mtime_ns: number | null;
}

export interface ManuscriptHistoryView {
  schema: 'story.authoring-manuscript-history.v1';
  project_id: string;
  path: string;
  current_sha256: string;
  revisions: ManuscriptRevisionSummary[];
  policy: ReadOnlyPolicy;
}

export interface ManuscriptRevisionView {
  schema: 'story.authoring-manuscript-revision.v1';
  project_id: string;
  path: string;
  sha256: string;
  bytes: number;
  characters: number;
  lines: number;
  current: boolean;
  captured_mtime_ns?: number | null;
  content: string;
  policy: ReadOnlyPolicy;
}

export interface ManuscriptSaveResult {
  schema: 'story.authoring-manuscript-save.v1';
  project_id: string;
  path: string;
  previous_sha256: string;
  sha256: string;
  bytes: number;
  characters: number;
  lines: number;
  written: true;
  history: {
    archived_previous_sha256: string;
    created: boolean;
  };
  policy: ManuscriptWritePolicy;
}

export interface ManuscriptConflictCurrent {
  title: string;
  season: number | null;
  episode: number | null;
  bytes: number;
  characters: number;
  lines: number;
  sha256: string;
  content: string;
}

export interface ManuscriptConflict {
  schema: 'story.authoring-manuscript-conflict.v1';
  project_id: string;
  path: string;
  reason: 'stale_working_copy';
  expected_sha256: string;
  current_sha256: string;
  current: ManuscriptConflictCurrent;
  policy: ReadOnlyPolicy;
}

export type ManuscriptSaveOutcome = ManuscriptSaveResult | ManuscriptConflict;

export interface ReadOnlyPolicy {
  read_only: true;
  manuscript_mutation?: false;
  history_mutation?: false;
  canonical_mutation: false;
  staging_mutation: false;
}

export interface ManuscriptWritePolicy {
  read_only: false;
  manuscript_mutation: true;
  history_mutation: true;
  canonical_mutation: false;
  staging_mutation: false;
}
