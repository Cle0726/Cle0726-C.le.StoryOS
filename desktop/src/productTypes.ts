export interface ProjectCreateResult {
  schema: 'story.project-create.v1';
  project: {
    id: string;
    name: string;
    language: string;
    path: string;
  };
  policy: {
    project_mutation: true;
    manuscript_mutation: false;
    canonical_mutation: false;
    staging_mutation: false;
    create_only: true;
  };
}

export interface ManuscriptCreateResult {
  schema: 'story.manuscript-create.v1';
  path: string;
  title: string;
  season: number;
  episode: number;
  policy: {
    project_mutation: false;
    manuscript_mutation: true;
    history_mutation: false;
    recovery_mutation: false;
    canonical_mutation: false;
    staging_mutation: false;
    create_only: true;
  };
}

export interface ClaimReviewIssue {
  code: string;
  severity: string;
  message: string;
  existing_ref: string | null;
}

export interface ClaimReviewRecord {
  schema: 'story.claim-review.v1';
  claim_id: string;
  claim_fingerprint: string;
  decision: 'accept_event_candidate' | 'accept_fact_candidate' | 'reject' | 'defer';
  normalized: { predicate: string; value: unknown } | null;
  note: string;
  policy: {
    canonical_mutation: false;
    materialization_required: true;
  };
}

export interface ClaimReviewItem {
  claim: {
    id: string;
    subject: string;
    predicate: string;
    value: unknown;
    at: {
      sequence: number;
      season: number | null;
      episode: number | null;
      scene: number | null;
    };
    confidence: number;
    source: Record<string, unknown>;
    proposed_authority: string;
    status: string;
    fingerprint: string;
  };
  subject_name: string | null;
  check: {
    can_approve: boolean;
    duplicate_of: string | null;
    issues: ClaimReviewIssue[];
  };
  review: ClaimReviewRecord | null;
  review_stale: boolean;
}

export interface ClaimReviewQueue {
  schema: 'story.claim-review-queue.v1';
  filters: { subject: string | null; predicate: string | null; claim_id: string | null };
  summary: {
    claims: number;
    blocked_by_current_canon_or_state: number;
    reviewed: number;
    unreviewed: number;
    stale_reviews: number;
    decisions: Record<string, number>;
    extraction_unresolved: number | null;
  };
  items: ClaimReviewItem[];
  policy: Record<string, unknown>;
}

export interface ClaimDecisionResult {
  schema: 'story.claim-review-result.v1';
  result: 'created' | 'replaced' | 'unchanged';
  review: ClaimReviewRecord;
  policy: Record<string, unknown>;
}

export interface MaterializationItem {
  claim_id: string;
  ready: boolean;
  reasons: string[];
  kind: 'event' | 'fact' | null;
  target_id: string | null;
  candidate: Record<string, unknown> | null;
  check: {
    can_approve: boolean;
    duplicate_of: string | null;
    issues: ClaimReviewIssue[];
  };
}

export interface MaterializationPlan {
  schema: 'story.materialization-plan.v1';
  summary: {
    claims: number;
    ready: number;
    blocked: number;
    reasons: Record<string, number>;
  };
  items: MaterializationItem[];
  policy: Record<string, unknown>;
}

export interface MaterializationResult {
  schema: 'story.materialization-result.v1';
  result: 'created' | 'unchanged';
  candidate: Record<string, unknown>;
  policy: Record<string, unknown>;
}

export interface CanonCommitItem {
  claim_id: string;
  ready: boolean;
  state: 'ready' | 'blocked' | 'committed' | string;
  reasons: string[];
  kind?: 'event' | 'fact' | null;
  target_id?: string | null;
  candidate_path?: string | null;
  candidate_sha256?: string | null;
  canonical_path?: string | null;
  detail?: string | null;
  materialization?: Record<string, unknown>;
}

export interface CanonCommitPlan {
  schema: 'story.canon-commit-plan.v1';
  summary: {
    claims: number;
    ready: number;
    blocked: number;
    reasons: Record<string, number>;
  };
  items: CanonCommitItem[];
  policy: Record<string, unknown>;
}

export interface CanonCommitResult {
  schema: 'story.canon-commit-command-result.v1';
  result: 'created' | 'unchanged';
  commit: Record<string, unknown>;
  policy: Record<string, unknown>;
}

export type ClaimDecision = ClaimReviewRecord['decision'];
