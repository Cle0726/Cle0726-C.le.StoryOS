export interface ProjectSessionRecovery {
  path: string;
  title: string;
  season: number | null;
  episode: number | null;
  current_sha256: string;
  base_sha256: string;
  draft_sha256: string;
  bytes: number;
  characters: number;
  lines: number;
  captured_mtime_ns: number;
  base_matches_current: boolean;
  draft_matches_current: boolean;
  recoverable: true;
}

export interface ProjectSessionView {
  schema: 'story.authoring-project-session.v1';
  project: {
    id: string;
    name: string;
    language: string;
  };
  summary: {
    manuscripts: number;
    recovery_slots: number;
    recoverable_drafts: number;
    stale_base_drafts: number;
  };
  recoveries: ProjectSessionRecovery[];
  policy: ReadOnlyPolicy;
}

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

export interface SceneKnowledgeFact {
  id: string;
  kind: 'canon_fact' | 'knowledge_token';
  subject?: string;
  predicate?: string;
  value?: unknown;
  authority?: string;
  active?: boolean;
  revealed?: boolean;
}

export interface SceneCharacterView {
  id: string;
  name: string;
  aliases: string[];
  scene_relevant: boolean;
  location: unknown;
  state: Record<string, unknown>;
  knowledge: {
    visible: SceneKnowledgeFact[];
    visible_count: number;
    hidden_by_reveal: number;
    hidden_ungoverned: number;
  };
  latest_event_sequence: number | null;
  event_count: number;
  data?: Record<string, unknown>;
}

export interface SceneNavigationTarget {
  path: string;
  title: string;
  season: number | null;
  episode: number | null;
  sha256: string;
}

export interface SceneWorkspaceView {
  schema: 'story.authoring-scene-workspace.v1';
  project_id: string;
  mode: 'author' | 'pov';
  pov: {
    id: string;
    name: string;
    aliases: string[];
  } | null;
  manuscript: ManuscriptSummary;
  timeline: {
    requested_through_sequence: number | null;
    effective_through_sequence: number | null;
    boundary_source: string;
    episode_events: number;
    visible_events: number;
  };
  navigation: {
    index: number;
    total: number;
    previous: SceneNavigationTarget | null;
    next: SceneNavigationTarget | null;
  };
  episode_events: Array<{
    id: string;
    subject: string;
    subject_name: string;
    type: string;
    sequence: number;
    scene: number | null;
  }>;
  characters: SceneCharacterView[];
  canon_conflicts: Array<{
    subject: string;
    subject_name: string;
    predicate: string;
    facts: Array<{ id: string; value: unknown; authority: string }>;
  }>;
  open_plots: Array<{
    id: string;
    name: string;
    aliases: string[];
    data: Record<string, unknown>;
    scene_relevant: boolean;
    last_status_sequence: number | null;
    last_status_event_id: string | null;
    status: 'open';
  }>;
  workflow_attention: Record<string, number>;
  actions_available: Array<Record<string, unknown>>;
  policy: ReadOnlyPolicy & {
    pov_safe: boolean;
    other_character_state_exposed: boolean;
    global_canon_conflicts_exposed: boolean;
    global_plot_threads_exposed: boolean;
    manuscript_content_included: false;
  };
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

export interface ManuscriptRecoveryDraft {
  base_sha256: string;
  draft_sha256: string;
  bytes: number;
  characters: number;
  lines: number;
  captured_mtime_ns: number;
  base_matches_current: boolean;
  draft_matches_current: boolean;
  recoverable: boolean;
  content: string;
}

export interface ManuscriptRecoveryView {
  schema: 'story.authoring-manuscript-recovery.v1';
  project_id: string;
  path: string;
  current_sha256: string;
  present: boolean;
  recovery: ManuscriptRecoveryDraft | null;
  policy: ReadOnlyPolicy;
}

export interface ManuscriptRecoverySaveResult {
  schema: 'story.authoring-manuscript-recovery-save.v1';
  project_id: string;
  path: string;
  recovery: ManuscriptRecoveryDraft;
  policy: RecoveryWritePolicy;
}

export interface ManuscriptRecoveryClearResult {
  schema: 'story.authoring-manuscript-recovery-clear.v1';
  project_id: string;
  path: string;
  expected_draft_sha256: string;
  cleared: boolean;
  reason: 'cleared' | 'absent';
  policy: RecoveryWritePolicy;
}

export type ManuscriptSaveOutcome = ManuscriptSaveResult | ManuscriptConflict;

export interface ReadOnlyPolicy {
  read_only: true;
  manuscript_mutation?: false;
  history_mutation?: false;
  recovery_mutation?: false;
  canonical_mutation: false;
  staging_mutation: false;
}

export interface ManuscriptWritePolicy {
  read_only: false;
  manuscript_mutation: true;
  history_mutation: true;
  recovery_mutation?: false;
  canonical_mutation: false;
  staging_mutation: false;
}

export interface RecoveryWritePolicy {
  read_only: false;
  manuscript_mutation: false;
  history_mutation: false;
  recovery_mutation: true;
  canonical_mutation: false;
  staging_mutation: false;
}
