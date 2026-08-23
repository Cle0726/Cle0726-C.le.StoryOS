import { invoke } from '@tauri-apps/api/core';
import type {
  EntityView,
  ManuscriptHistoryView,
  ManuscriptRecoveryClearResult,
  ManuscriptRecoverySaveResult,
  ManuscriptRecoveryView,
  ManuscriptRevisionView,
  ManuscriptSaveOutcome,
  ManuscriptView,
  ProjectSessionView,
  WorkspaceSnapshot,
} from './types';

type WorkspaceAction =
  | 'snapshot'
  | 'entity'
  | 'manuscript'
  | 'manuscript-history'
  | 'manuscript-revision'
  | 'manuscript-recovery'
  | 'manuscript-recovery-save'
  | 'manuscript-recovery-clear'
  | 'manuscript-save';

interface WorkspaceRequest {
  action: WorkspaceAction;
  project: string;
  entityId?: string;
  manuscriptPath?: string;
  expectedSha256?: string;
  revisionSha256?: string;
  baseSha256?: string;
  expectedDraftSha256?: string;
  content?: string;
  through?: number;
}

const MAX_MANUSCRIPT_BYTES = 16 * 1024 * 1024;

function nonEmpty(value: string, label: string): string {
  const trimmed = value.trim();
  if (!trimmed) throw new Error(`${label}不能为空`);
  if (/\r|\n|\0/.test(trimmed)) throw new Error(`${label}包含非法控制字符`);
  return trimmed;
}

function timelineBoundary(value: number | null | undefined): number | undefined {
  if (value == null) return undefined;
  if (!Number.isSafeInteger(value) || value < 0) throw new Error('时间线边界必须是非负安全整数');
  return value;
}

function exactSha256(value: string, label = '正文版本 SHA-256'): string {
  const trimmed = value.trim();
  if (!/^[0-9a-f]{64}$/.test(trimmed)) throw new Error(`${label} 无效`);
  return trimmed;
}

function verifyPolicy(row: Record<string, unknown>, action: WorkspaceAction, schema: string): void {
  const policy = row.policy as Record<string, unknown> | undefined;
  if (!policy || policy.canonical_mutation !== false || policy.staging_mutation !== false) {
    throw new Error('StoryOS Workspace 拒绝了可能修改 Canon/Staging 的响应');
  }

  if (action === 'manuscript-save') {
    if (schema === 'story.authoring-manuscript-conflict.v1') {
      if (
        policy.read_only !== true
        || policy.manuscript_mutation !== false
        || policy.history_mutation !== false
        || policy.recovery_mutation === true
      ) {
        throw new Error('StoryOS Workspace 拒绝了非只读的正文冲突响应');
      }
      return;
    }
    if (
      policy.read_only !== false
      || policy.manuscript_mutation !== true
      || policy.history_mutation !== true
      || policy.recovery_mutation === true
    ) {
      throw new Error('StoryOS Workspace 拒绝了超出正文/历史范围的写响应');
    }
    return;
  }

  if (action === 'manuscript-recovery-save' || action === 'manuscript-recovery-clear') {
    if (
      policy.read_only !== false
      || policy.manuscript_mutation !== false
      || policy.history_mutation !== false
      || policy.recovery_mutation !== true
    ) {
      throw new Error('StoryOS Workspace 拒绝了超出 recovery 范围的写响应');
    }
    return;
  }

  if (
    policy.read_only !== true
    || policy.manuscript_mutation === true
    || policy.history_mutation === true
    || policy.recovery_mutation === true
  ) {
    throw new Error('StoryOS Workspace 拒绝了非只读响应');
  }
}

async function callWorkspace<T>(
  request: WorkspaceRequest,
  expectedSchemas: string | string[],
): Promise<T> {
  const payload = await invoke<unknown>('storyos_workspace', { request });
  if (!payload || typeof payload !== 'object') throw new Error('StoryOS Workspace 返回了无效响应');
  const row = payload as Record<string, unknown>;
  const schema = String(row.schema ?? '');
  const allowed = Array.isArray(expectedSchemas) ? expectedSchemas : [expectedSchemas];
  if (!allowed.includes(schema)) throw new Error(`StoryOS Workspace schema 不匹配：${schema}`);
  verifyPolicy(row, request.action, schema);
  return payload as T;
}

export async function loadProjectSession(project: string): Promise<ProjectSessionView> {
  const payload = await invoke<unknown>('storyos_project_session', {
    project: nonEmpty(project, '项目路径'),
  });
  if (!payload || typeof payload !== 'object') throw new Error('StoryOS Session 返回了无效响应');
  const row = payload as Record<string, unknown>;
  if (row.schema !== 'story.authoring-project-session.v1') {
    throw new Error(`StoryOS Session schema 不匹配：${String(row.schema ?? '')}`);
  }
  const policy = row.policy as Record<string, unknown> | undefined;
  if (
    !policy
    || policy.read_only !== true
    || policy.manuscript_mutation !== false
    || policy.history_mutation !== false
    || policy.recovery_mutation !== false
    || policy.canonical_mutation !== false
    || policy.staging_mutation !== false
  ) {
    throw new Error('StoryOS Session 拒绝了非只读响应');
  }
  const recoveries = Array.isArray(row.recoveries) ? row.recoveries : [];
  if (recoveries.some((item) => item && typeof item === 'object' && 'content' in (item as object))) {
    throw new Error('StoryOS Session 拒绝了包含 recovery 正文的启动数据');
  }
  return payload as ProjectSessionView;
}

export async function pickProjectDirectory(): Promise<string | null> {
  const path = await invoke<unknown>('pick_project_directory');
  if (path == null) return null;
  if (typeof path !== 'string') throw new Error('系统文件夹选择器返回了无效路径');
  return nonEmpty(path, '项目路径');
}

export function loadWorkspace(project: string, through?: number | null): Promise<WorkspaceSnapshot> {
  return callWorkspace<WorkspaceSnapshot>({
    action: 'snapshot',
    project: nonEmpty(project, '项目路径'),
    through: timelineBoundary(through),
  }, 'story.authoring-workspace.v1');
}

export function loadEntity(project: string, entityId: string, through?: number | null): Promise<EntityView> {
  return callWorkspace<EntityView>({
    action: 'entity',
    project: nonEmpty(project, '项目路径'),
    entityId: nonEmpty(entityId, '实体 ID'),
    through: timelineBoundary(through),
  }, 'story.authoring-entity.v1');
}

export function loadManuscript(project: string, manuscriptPath: string): Promise<ManuscriptView> {
  return callWorkspace<ManuscriptView>({
    action: 'manuscript',
    project: nonEmpty(project, '项目路径'),
    manuscriptPath: nonEmpty(manuscriptPath, '正文路径'),
  }, 'story.authoring-manuscript.v1');
}

export function loadManuscriptHistory(
  project: string,
  manuscriptPath: string,
): Promise<ManuscriptHistoryView> {
  return callWorkspace<ManuscriptHistoryView>({
    action: 'manuscript-history',
    project: nonEmpty(project, '项目路径'),
    manuscriptPath: nonEmpty(manuscriptPath, '正文路径'),
  }, 'story.authoring-manuscript-history.v1');
}

export function loadManuscriptRevision(
  project: string,
  manuscriptPath: string,
  revisionSha256: string,
): Promise<ManuscriptRevisionView> {
  return callWorkspace<ManuscriptRevisionView>({
    action: 'manuscript-revision',
    project: nonEmpty(project, '项目路径'),
    manuscriptPath: nonEmpty(manuscriptPath, '正文路径'),
    revisionSha256: exactSha256(revisionSha256, '历史版本 SHA-256'),
  }, 'story.authoring-manuscript-revision.v1');
}

export function loadManuscriptRecovery(
  project: string,
  manuscriptPath: string,
): Promise<ManuscriptRecoveryView> {
  return callWorkspace<ManuscriptRecoveryView>({
    action: 'manuscript-recovery',
    project: nonEmpty(project, '项目路径'),
    manuscriptPath: nonEmpty(manuscriptPath, '正文路径'),
  }, 'story.authoring-manuscript-recovery.v1');
}

export function saveManuscriptRecovery(
  project: string,
  manuscriptPath: string,
  baseSha256: string,
  content: string,
): Promise<ManuscriptRecoverySaveResult> {
  if (content.includes('\0')) throw new Error('恢复草稿不能包含 NUL 字符');
  if (new TextEncoder().encode(content).byteLength > MAX_MANUSCRIPT_BYTES) {
    throw new Error('恢复草稿超过 16 MiB 桌面安全限制');
  }
  return callWorkspace<ManuscriptRecoverySaveResult>({
    action: 'manuscript-recovery-save',
    project: nonEmpty(project, '项目路径'),
    manuscriptPath: nonEmpty(manuscriptPath, '正文路径'),
    baseSha256: exactSha256(baseSha256, '恢复草稿基线 SHA-256'),
    content,
  }, 'story.authoring-manuscript-recovery-save.v1');
}

export function clearManuscriptRecovery(
  project: string,
  manuscriptPath: string,
  expectedDraftSha256: string,
): Promise<ManuscriptRecoveryClearResult> {
  return callWorkspace<ManuscriptRecoveryClearResult>({
    action: 'manuscript-recovery-clear',
    project: nonEmpty(project, '项目路径'),
    manuscriptPath: nonEmpty(manuscriptPath, '正文路径'),
    expectedDraftSha256: exactSha256(expectedDraftSha256, '恢复草稿 SHA-256'),
  }, 'story.authoring-manuscript-recovery-clear.v1');
}

export function saveManuscript(
  project: string,
  manuscriptPath: string,
  expectedSha256: string,
  content: string,
): Promise<ManuscriptSaveOutcome> {
  if (content.includes('\0')) throw new Error('正文不能包含 NUL 字符');
  if (new TextEncoder().encode(content).byteLength > MAX_MANUSCRIPT_BYTES) {
    throw new Error('正文超过 16 MiB 桌面安全限制');
  }
  return callWorkspace<ManuscriptSaveOutcome>({
    action: 'manuscript-save',
    project: nonEmpty(project, '项目路径'),
    manuscriptPath: nonEmpty(manuscriptPath, '正文路径'),
    expectedSha256: exactSha256(expectedSha256),
    content,
  }, [
    'story.authoring-manuscript-save.v1',
    'story.authoring-manuscript-conflict.v1',
  ]);
}
