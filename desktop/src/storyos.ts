import { invoke } from '@tauri-apps/api/core';
import type {
  EntityView,
  ManuscriptSaveResult,
  ManuscriptView,
  WorkspaceSnapshot,
} from './types';

type WorkspaceAction = 'snapshot' | 'entity' | 'manuscript' | 'manuscript-save';

interface WorkspaceRequest {
  action: WorkspaceAction;
  project: string;
  entityId?: string;
  manuscriptPath?: string;
  expectedSha256?: string;
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

function exactSha256(value: string): string {
  const trimmed = value.trim();
  if (!/^[0-9a-f]{64}$/.test(trimmed)) throw new Error('正文版本 SHA-256 无效');
  return trimmed;
}

function verifyPolicy(row: Record<string, unknown>, action: WorkspaceAction): void {
  const policy = row.policy as Record<string, unknown> | undefined;
  if (!policy || policy.canonical_mutation !== false || policy.staging_mutation !== false) {
    throw new Error('StoryOS Workspace 拒绝了可能修改 Canon/Staging 的响应');
  }
  if (action === 'manuscript-save') {
    if (policy.read_only !== false || policy.manuscript_mutation !== true) {
      throw new Error('StoryOS Workspace 拒绝了超出正文工作副本范围的写响应');
    }
    return;
  }
  if (policy.read_only !== true || policy.manuscript_mutation === true) {
    throw new Error('StoryOS Workspace 拒绝了非只读响应');
  }
}

async function callWorkspace<T>(request: WorkspaceRequest, schema: string): Promise<T> {
  const payload = await invoke<unknown>('storyos_workspace', { request });
  if (!payload || typeof payload !== 'object') throw new Error('StoryOS Workspace 返回了无效响应');
  const row = payload as Record<string, unknown>;
  if (row.schema !== schema) throw new Error(`StoryOS Workspace schema 不匹配：${String(row.schema)}`);
  verifyPolicy(row, request.action);
  return payload as T;
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

export function saveManuscript(
  project: string,
  manuscriptPath: string,
  expectedSha256: string,
  content: string,
): Promise<ManuscriptSaveResult> {
  if (content.includes('\0')) throw new Error('正文不能包含 NUL 字符');
  if (new TextEncoder().encode(content).byteLength > MAX_MANUSCRIPT_BYTES) {
    throw new Error('正文超过 16 MiB 桌面安全限制');
  }
  return callWorkspace<ManuscriptSaveResult>({
    action: 'manuscript-save',
    project: nonEmpty(project, '项目路径'),
    manuscriptPath: nonEmpty(manuscriptPath, '正文路径'),
    expectedSha256: exactSha256(expectedSha256),
    content,
  }, 'story.authoring-manuscript-save.v1');
}
