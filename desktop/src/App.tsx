import { useEffect, useMemo, useRef, useState } from 'react';
import {
  clearManuscriptRecovery,
  createStoryManuscript,
  createStoryProject,
  loadEntity,
  loadManuscript,
  loadManuscriptHistory,
  loadManuscriptRecovery,
  loadManuscriptRevision,
  loadProjectSession,
  loadWorkspace,
  pickProjectDirectory,
  saveManuscript,
  saveManuscriptRecovery,
} from './storyos';
import type {
  EntitySummary,
  EntityView,
  ManuscriptConflict,
  ManuscriptHistoryView,
  ManuscriptRecoveryView,
  ManuscriptRevisionView,
  ManuscriptSummary,
  ManuscriptView,
  ProjectSessionRecovery,
  ProjectSessionView,
  WorkspaceSnapshot,
} from './types';
import EntityDetail from './EntityDetail';
import GovernanceWorkbench from './GovernanceWorkbench';
import SceneInspector from './SceneInspector';

type Selection =
  | { kind: 'manuscript'; value: ManuscriptSummary }
  | { kind: 'entity'; value: EntitySummary };

type NavMode = 'manuscripts' | 'entities' | 'governance';

interface StoredRecentProject {
  path: string;
  lastOpenedAt: number;
}

interface RecentProject extends StoredRecentProject {
  session: ProjectSessionView | null;
  error: string | null;
  loading: boolean;
}

const RECENT_PROJECTS_KEY = 'cle.storyos.recent-projects.v1';
const MAX_RECENT_PROJECTS = 8;

function readStoredRecentProjects(): StoredRecentProject[] {
  try {
    const raw = window.localStorage.getItem(RECENT_PROJECTS_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw) as unknown;
    if (!Array.isArray(parsed)) return [];
    const rows: StoredRecentProject[] = [];
    for (const item of parsed) {
      if (!item || typeof item !== 'object') continue;
      const row = item as Record<string, unknown>;
      if (typeof row.path !== 'string' || !row.path.trim()) continue;
      const lastOpenedAt = typeof row.lastOpenedAt === 'number' && Number.isFinite(row.lastOpenedAt)
        ? row.lastOpenedAt
        : 0;
      if (!rows.some((current) => current.path === row.path)) {
        rows.push({ path: row.path, lastOpenedAt });
      }
    }
    return rows.sort((a, b) => b.lastOpenedAt - a.lastOpenedAt).slice(0, MAX_RECENT_PROJECTS);
  } catch {
    return [];
  }
}

function storeRecentProject(path: string): StoredRecentProject[] {
  const next = [
    { path, lastOpenedAt: Date.now() },
    ...readStoredRecentProjects().filter((item) => item.path !== path),
  ].slice(0, MAX_RECENT_PROJECTS);
  window.localStorage.setItem(RECENT_PROJECTS_KEY, JSON.stringify(next));
  return next;
}

function removeRecentProject(path: string): StoredRecentProject[] {
  const next = readStoredRecentProjects().filter((item) => item.path !== path);
  window.localStorage.setItem(RECENT_PROJECTS_KEY, JSON.stringify(next));
  return next;
}

function Stat({ label, value }: { label: string; value: number | string | null | undefined }) {
  return <div className="stat"><span>{label}</span><strong>{value ?? '—'}</strong></div>;
}

function changedLineCount(left: string, right: string): number {
  const a = left.split('\n');
  const b = right.split('\n');
  const count = Math.max(a.length, b.length);
  let changed = 0;
  for (let index = 0; index < count; index += 1) {
    if (a[index] !== b[index]) changed += 1;
  }
  return changed;
}

function formatRecentTime(value: number): string {
  if (!value) return '未知时间';
  return new Date(value).toLocaleString();
}

function recoveryLabel(item: ProjectSessionRecovery): string {
  const episode = item.episode == null ? 'EP—' : `EP${String(item.episode).padStart(2, '0')}`;
  return `${episode} · ${item.title}`;
}

function countWritingCharacters(value: string): number {
  return value.replace(/\s/g, '').length;
}

function attentionLabel(key: string): string {
  const labels: Record<string, string> = {
    unreviewed_claims: '待审核 Claim',
    stale_reviews: '过期审核',
    materialization_ready: '可生成候选',
    canon_commit_ready: '可写入 Canon',
    reference_errors: '引用问题',
  };
  return labels[key] ?? key.replaceAll('_', ' ');
}

export default function App() {
  const [projectPath, setProjectPath] = useState('');
  const [manualProjectPath, setManualProjectPath] = useState('');
  const [throughText, setThroughText] = useState('');
  const [snapshot, setSnapshot] = useState<WorkspaceSnapshot | null>(null);
  const [recentProjects, setRecentProjects] = useState<RecentProject[]>([]);
  const [recentLoading, setRecentLoading] = useState(false);
  const [selection, setSelection] = useState<Selection | null>(null);
  const [entityView, setEntityView] = useState<EntityView | null>(null);
  const [manuscriptView, setManuscriptView] = useState<ManuscriptView | null>(null);
  const [manuscriptHistory, setManuscriptHistory] = useState<ManuscriptHistoryView | null>(null);
  const [selectedRevision, setSelectedRevision] = useState<ManuscriptRevisionView | null>(null);
  const [recoveryView, setRecoveryView] = useState<ManuscriptRecoveryView | null>(null);
  const [conflict, setConflict] = useState<ManuscriptConflict | null>(null);
  const [draftContent, setDraftContent] = useState('');
  const [dirty, setDirty] = useState(false);
  const [saveMessage, setSaveMessage] = useState<string | null>(null);
  const [recoveryMessage, setRecoveryMessage] = useState<string | null>(null);
  const [lastRecoveryDraftSha, setLastRecoveryDraftSha] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [recoveryLoading, setRecoveryLoading] = useState(false);
  const [recoverySaving, setRecoverySaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [navMode, setNavMode] = useState<NavMode>('manuscripts');
  const [navQuery, setNavQuery] = useState('');
  const [focusMode, setFocusMode] = useState(false);

  const [newProjectOpen, setNewProjectOpen] = useState(false);
  const [newProjectFolder, setNewProjectFolder] = useState('');
  const [newProjectName, setNewProjectName] = useState('');
  const [newProjectLanguage, setNewProjectLanguage] = useState('zh-CN');
  const [creatingProject, setCreatingProject] = useState(false);

  const [newChapterOpen, setNewChapterOpen] = useState(false);
  const [newChapterTitle, setNewChapterTitle] = useState('');
  const [newChapterSeason, setNewChapterSeason] = useState('1');
  const [newChapterEpisode, setNewChapterEpisode] = useState('1');
  const [creatingChapter, setCreatingChapter] = useState(false);

  const recoveryGenerationRef = useRef(0);
  const recoveryTimerRef = useRef<number | null>(null);
  const recoveryQueueRef = useRef<Promise<void>>(Promise.resolve());

  const through = useMemo(() => {
    if (!throughText.trim()) return null;
    const parsed = Number(throughText);
    return Number.isSafeInteger(parsed) && parsed >= 0 ? parsed : Number.NaN;
  }, [throughText]);

  const recoverableRecovery = recoveryView?.recovery?.recoverable ? recoveryView.recovery : null;
  const recoveryChangedLines = useMemo(
    () => recoverableRecovery && manuscriptView
      ? changedLineCount(recoverableRecovery.content, manuscriptView.content)
      : 0,
    [recoverableRecovery, manuscriptView],
  );
  const conflictChangedLines = useMemo(
    () => conflict ? changedLineCount(draftContent, conflict.current.content) : 0,
    [conflict, draftContent],
  );

  const filteredManuscripts = useMemo(() => {
    const query = navQuery.trim().toLocaleLowerCase();
    if (!snapshot) return [];
    if (!query) return snapshot.manuscripts;
    return snapshot.manuscripts.filter((item) => (
      `${item.title} ${item.name} ${item.path} S${item.season ?? ''} EP${item.episode ?? ''}`
        .toLocaleLowerCase()
        .includes(query)
    ));
  }, [navQuery, snapshot]);

  const filteredEntities = useMemo(() => {
    const query = navQuery.trim().toLocaleLowerCase();
    if (!snapshot) return [];
    if (!query) return snapshot.entities;
    return snapshot.entities.filter((item) => (
      `${item.name} ${item.kind} ${item.slug} ${item.aliases.join(' ')}`
        .toLocaleLowerCase()
        .includes(query)
    ));
  }, [navQuery, snapshot]);

  const attention = useMemo(
    () => (snapshot?.workflow.attention ?? {}) as Record<string, number>,
    [snapshot],
  );

  function cancelPendingRecoveryTimer() {
    recoveryGenerationRef.current += 1;
    if (recoveryTimerRef.current != null) {
      window.clearTimeout(recoveryTimerRef.current);
      recoveryTimerRef.current = null;
    }
  }

  function canDiscardDraft(): boolean {
    return !dirty || window.confirm('当前正文有未正式保存的修改。Recovery 会尽量保留草稿，但离开不会写入正式正文。确定离开吗？');
  }

  function clearDocumentEditor() {
    cancelPendingRecoveryTimer();
    setManuscriptView(null);
    setManuscriptHistory(null);
    setSelectedRevision(null);
    setRecoveryView(null);
    setConflict(null);
    setDraftContent('');
    setDirty(false);
    setSaveMessage(null);
    setRecoveryMessage(null);
    setLastRecoveryDraftSha(null);
    setRecoverySaving(false);
  }

  function updateManuscriptSummary(
    path: string,
    values: Pick<ManuscriptView, 'sha256' | 'bytes' | 'characters' | 'lines'>,
  ) {
    setSnapshot((current) => current ? {
      ...current,
      manuscripts: current.manuscripts.map((item) => item.path === path ? { ...item, ...values } : item),
    } : current);
    setSelection((current) => {
      if (!current || current.kind !== 'manuscript' || current.value.path !== path) return current;
      return { kind: 'manuscript', value: { ...current.value, ...values } };
    });
  }

  async function hydrateRecentProjects(source = readStoredRecentProjects()) {
    setRecentLoading(true);
    const loadingRows: RecentProject[] = source.map((item) => ({
      ...item,
      session: null,
      error: null,
      loading: true,
    }));
    setRecentProjects(loadingRows);
    const rows = await Promise.all(source.map(async (item): Promise<RecentProject> => {
      try {
        return {
          ...item,
          session: await loadProjectSession(item.path),
          error: null,
          loading: false,
        };
      } catch (cause) {
        return {
          ...item,
          session: null,
          error: cause instanceof Error ? cause.message : String(cause),
          loading: false,
        };
      }
    }));
    setRecentProjects(rows);
    setRecentLoading(false);
  }

  useEffect(() => {
    void hydrateRecentProjects();
  }, []);

  useEffect(() => {
    const beforeUnload = (event: BeforeUnloadEvent) => {
      if (!dirty) return;
      event.preventDefault();
      event.returnValue = '';
    };
    window.addEventListener('beforeunload', beforeUnload);
    return () => window.removeEventListener('beforeunload', beforeUnload);
  }, [dirty]);

  async function refreshHistoryFor(project: string, path: string) {
    setHistoryLoading(true);
    try {
      setManuscriptHistory(await loadManuscriptHistory(project, path));
    } finally {
      setHistoryLoading(false);
    }
  }

  async function refreshRecoveryFor(project: string, path: string) {
    setRecoveryLoading(true);
    try {
      const recovery = await loadManuscriptRecovery(project, path);
      setRecoveryView(recovery);
      setLastRecoveryDraftSha(recovery.recovery?.draft_sha256 ?? null);
      if (recovery.recovery?.recoverable) {
        setRecoveryMessage(
          recovery.recovery.base_matches_current
            ? '发现未正式保存的恢复稿，请先决定恢复或清除'
            : '恢复稿基于较旧版本，请先比较后决定',
        );
      } else if (recovery.recovery) {
        setRecoveryMessage('恢复区内容与当前正式正文一致');
      } else {
        setRecoveryMessage(null);
      }
    } finally {
      setRecoveryLoading(false);
    }
  }

  async function openManuscriptFor(project: string, item: ManuscriptSummary) {
    setSelection({ kind: 'manuscript', value: item });
    setNavMode('manuscripts');
    setEntityView(null);
    clearDocumentEditor();
    const document = await loadManuscript(project, item.path);
    setManuscriptView(document);
    setDraftContent(document.content);
    setDirty(false);

    const [historyResult, recoveryResult] = await Promise.allSettled([
      refreshHistoryFor(project, item.path),
      refreshRecoveryFor(project, item.path),
    ]);
    if (historyResult.status === 'rejected') {
      setError(`正文已打开，但版本历史读取失败：${historyResult.reason instanceof Error ? historyResult.reason.message : String(historyResult.reason)}`);
    }
    if (recoveryResult.status === 'rejected') {
      setError(`正文已打开，但恢复稿读取失败：${recoveryResult.reason instanceof Error ? recoveryResult.reason.message : String(recoveryResult.reason)}`);
    }
  }

  async function openProjectPath(path: string, preferredRecoveryPath?: string) {
    if (!canDiscardDraft()) return;
    setLoading(true);
    setError(null);
    try {
      if (Number.isNaN(through)) throw new Error('高级时间线边界必须是非负整数');
      const normalizedPath = path.trim();
      if (!normalizedPath) throw new Error('项目路径不能为空');
      const next = await loadWorkspace(normalizedPath, through);
      setProjectPath(normalizedPath);
      setManualProjectPath(normalizedPath);
      setSnapshot(next);
      setSelection(null);
      setEntityView(null);
      setNavMode('manuscripts');
      setNavQuery('');
      setFocusMode(false);
      clearDocumentEditor();
      const stored = storeRecentProject(normalizedPath);
      void hydrateRecentProjects(stored);

      if (preferredRecoveryPath) {
        const item = next.manuscripts.find((row) => row.path === preferredRecoveryPath);
        if (item) await openManuscriptFor(normalizedPath, item);
      }
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setLoading(false);
    }
  }

  async function refreshSnapshot() {
    if (!projectPath) return;
    if (Number.isNaN(through)) {
      setError('高级时间线边界必须是非负整数');
      return;
    }
    const next = await loadWorkspace(projectPath, through);
    setSnapshot(next);
  }

  async function chooseProjectFolder() {
    setError(null);
    try {
      const path = await pickProjectDirectory();
      if (path) await openProjectPath(path);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    }
  }

  async function chooseNewProjectFolder() {
    setError(null);
    try {
      const path = await pickProjectDirectory();
      if (path) setNewProjectFolder(path);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    }
  }

  async function createProject() {
    if (!newProjectFolder.trim() || !newProjectName.trim() || creatingProject) return;
    setCreatingProject(true);
    setError(null);
    try {
      const result = await createStoryProject(newProjectFolder, newProjectName, newProjectLanguage);
      setNewProjectOpen(false);
      setNewProjectName('');
      setNewProjectFolder('');
      await openProjectPath(result.project.path);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setCreatingProject(false);
    }
  }

  function showLauncher() {
    if (!canDiscardDraft()) return;
    clearDocumentEditor();
    setSnapshot(null);
    setSelection(null);
    setEntityView(null);
    setProjectPath('');
    setFocusMode(false);
    setError(null);
    void hydrateRecentProjects();
  }

  async function chooseManuscript(item: ManuscriptSummary) {
    if (!snapshot || !projectPath) return;
    if (selection?.kind === 'manuscript' && selection.value.path === item.path && manuscriptView) return;
    if (!canDiscardDraft()) return;
    setLoading(true);
    setError(null);
    try {
      await openManuscriptFor(projectPath, item);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setLoading(false);
    }
  }

  async function chooseEntity(item: EntitySummary) {
    if (!snapshot || !projectPath || !canDiscardDraft()) return;
    setSelection({ kind: 'entity', value: item });
    setNavMode('entities');
    clearDocumentEditor();
    setLoading(true);
    setError(null);
    try {
      setEntityView(await loadEntity(projectPath, item.id, through));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setLoading(false);
    }
  }

  function openGovernance() {
    if (!canDiscardDraft()) return;
    clearDocumentEditor();
    setEntityView(null);
    setSelection(null);
    setNavMode('governance');
    setFocusMode(false);
  }

  function prepareNewChapter() {
    if (!snapshot || dirty) return;
    const positioned = snapshot.manuscripts
      .filter((item) => item.season != null && item.episode != null)
      .sort((a, b) => (a.season! - b.season!) || (a.episode! - b.episode!));
    const last = positioned.at(-1);
    setNewChapterSeason(String(last?.season ?? 1));
    setNewChapterEpisode(String((last?.episode ?? 0) + 1));
    setNewChapterTitle('');
    setNewChapterOpen(true);
  }

  async function createChapter() {
    if (!snapshot || !projectPath || creatingChapter) return;
    const season = Number(newChapterSeason);
    const episode = Number(newChapterEpisode);
    setCreatingChapter(true);
    setError(null);
    try {
      const result = await createStoryManuscript(
        projectPath,
        newChapterTitle,
        season,
        episode,
      );
      const next = await loadWorkspace(projectPath, through);
      setSnapshot(next);
      const item = next.manuscripts.find((row) => row.path === result.path);
      setNewChapterOpen(false);
      if (!item) throw new Error('章节已创建，但刷新后没有出现在正文列表中');
      await openManuscriptFor(projectPath, item);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setCreatingChapter(false);
    }
  }

  function updateDraft(value: string) {
    setDraftContent(value);
    setDirty(value !== (manuscriptView?.content ?? ''));
    setSaveMessage(null);
    if (conflict) setConflict(null);
  }

  useEffect(() => {
    if (
      !manuscriptView
      || !dirty
      || conflict
      || recoverableRecovery
      || saving
      || !projectPath
    ) return undefined;

    const generation = ++recoveryGenerationRef.current;
    const manuscriptPath = manuscriptView.path;
    const baseSha256 = manuscriptView.sha256;
    const content = draftContent;
    const timer = window.setTimeout(() => {
      recoveryTimerRef.current = null;
      setRecoverySaving(true);
      setRecoveryMessage('正在保存恢复稿…');
      recoveryQueueRef.current = recoveryQueueRef.current
        .catch(() => undefined)
        .then(async () => {
          const result = await saveManuscriptRecovery(projectPath, manuscriptPath, baseSha256, content);
          if (generation === recoveryGenerationRef.current) {
            setLastRecoveryDraftSha(result.recovery.draft_sha256);
            setRecoveryMessage('恢复稿已自动保存');
          }
        })
        .catch((cause) => {
          if (generation === recoveryGenerationRef.current) {
            setRecoveryMessage(`恢复稿自动保存失败：${cause instanceof Error ? cause.message : String(cause)}`);
          }
        })
        .finally(() => {
          if (generation === recoveryGenerationRef.current) setRecoverySaving(false);
        });
    }, 1200);
    recoveryTimerRef.current = timer;

    return () => {
      window.clearTimeout(timer);
      if (recoveryTimerRef.current === timer) recoveryTimerRef.current = null;
    };
  }, [conflict, dirty, draftContent, manuscriptView, projectPath, recoverableRecovery, saving]);

  async function saveCurrentManuscript() {
    if (!manuscriptView || !dirty || saving || recoverableRecovery || conflict || !projectPath) return;
    cancelPendingRecoveryTimer();
    setSaving(true);
    setError(null);
    setSaveMessage(null);
    let backupSha: string | null = null;
    try {
      await recoveryQueueRef.current.catch(() => undefined);
      try {
        const backup = await saveManuscriptRecovery(
          projectPath,
          manuscriptView.path,
          manuscriptView.sha256,
          draftContent,
        );
        backupSha = backup.recovery.draft_sha256;
        setLastRecoveryDraftSha(backupSha);
        setRecoveryMessage('保存前恢复稿已更新');
      } catch (recoveryCause) {
        setRecoveryMessage(`保存前恢复稿更新失败：${recoveryCause instanceof Error ? recoveryCause.message : String(recoveryCause)}`);
      }

      const outcome = await saveManuscript(
        projectPath,
        manuscriptView.path,
        manuscriptView.sha256,
        draftContent,
      );
      if (outcome.schema === 'story.authoring-manuscript-conflict.v1') {
        setConflict(outcome);
        setRecoveryView(null);
        setSaveMessage('检测到外部版本变化，没有覆盖磁盘内容');
        setRecoveryMessage(backupSha ? '当前草稿已安全保留在恢复区' : '发生冲突且恢复稿未更新，请不要关闭编辑器');
        return;
      }

      const nextView: ManuscriptView = {
        ...manuscriptView,
        content: draftContent,
        sha256: outcome.sha256,
        bytes: outcome.bytes,
        characters: outcome.characters,
        lines: outcome.lines,
      };
      setManuscriptView(nextView);
      setDirty(false);
      setConflict(null);
      setRecoveryView(null);
      setSelectedRevision(null);
      setSaveMessage('正文已正式保存');
      setRecoveryMessage(backupSha ? '恢复稿已与正式正文同步' : null);
      updateManuscriptSummary(outcome.path, outcome);
      await refreshHistoryFor(projectPath, outcome.path);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setSaving(false);
    }
  }

  useEffect(() => {
    const handleShortcut = (event: KeyboardEvent) => {
      const key = event.key.toLocaleLowerCase();
      if ((event.metaKey || event.ctrlKey) && key === 's') {
        event.preventDefault();
        void saveCurrentManuscript();
        return;
      }
      if ((event.metaKey || event.ctrlKey) && event.shiftKey && key === 'f') {
        event.preventDefault();
        if (manuscriptView) setFocusMode((current) => !current);
        return;
      }
      if (event.key === 'Escape' && focusMode) setFocusMode(false);
    };
    window.addEventListener('keydown', handleShortcut);
    return () => window.removeEventListener('keydown', handleShortcut);
  }, [conflict, dirty, draftContent, focusMode, manuscriptView, projectPath, recoverableRecovery, saving]);

  function restoreRecoveryDraft() {
    if (!recoverableRecovery || !manuscriptView) return;
    setDraftContent(recoverableRecovery.content);
    setDirty(recoverableRecovery.content !== manuscriptView.content);
    setLastRecoveryDraftSha(recoverableRecovery.draft_sha256);
    setRecoveryView(null);
    setConflict(null);
    setSaveMessage('已恢复未正式保存的草稿');
    setRecoveryMessage('草稿已载入；点击“保存正文”后才会写入正式工作副本');
  }

  async function discardRecoveryDraft() {
    if (!recoverableRecovery || !manuscriptView || recoveryLoading || !projectPath) return;
    setRecoveryLoading(true);
    setError(null);
    try {
      await clearManuscriptRecovery(projectPath, manuscriptView.path, recoverableRecovery.draft_sha256);
      setRecoveryView(null);
      setLastRecoveryDraftSha(null);
      setRecoveryMessage('已清除恢复稿；正式正文没有变化');
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setRecoveryLoading(false);
    }
  }

  async function reloadConflictDiskVersion() {
    if (!conflict || !manuscriptView || !projectPath) return;
    const current = conflict.current;
    const recoveryToClear = lastRecoveryDraftSha;
    const nextView: ManuscriptView = {
      ...manuscriptView,
      title: current.title,
      season: current.season,
      episode: current.episode,
      bytes: current.bytes,
      characters: current.characters,
      lines: current.lines,
      sha256: current.sha256,
      content: current.content,
    };
    setManuscriptView(nextView);
    setDraftContent(current.content);
    setDirty(false);
    setConflict(null);
    setRecoveryView(null);
    setSelectedRevision(null);
    setSaveMessage('已重新加载磁盘版本');
    setLastRecoveryDraftSha(null);
    updateManuscriptSummary(conflict.path, current);

    if (recoveryToClear) {
      try {
        await clearManuscriptRecovery(projectPath, conflict.path, recoveryToClear);
        setRecoveryMessage('已清除被放弃的冲突恢复稿');
      } catch (cause) {
        setRecoveryMessage(`磁盘版本已加载，但旧恢复稿未清除：${cause instanceof Error ? cause.message : String(cause)}`);
      }
    }
    void refreshHistoryFor(projectPath, conflict.path);
  }

  function keepDraftAndAdoptDiskBase() {
    if (!conflict || !manuscriptView) return;
    if (!window.confirm('保留当前草稿，并把最新磁盘版本作为新的保存基线？下一次正式保存会替换该磁盘版本。')) return;
    const current = conflict.current;
    setManuscriptView({
      ...manuscriptView,
      title: current.title,
      season: current.season,
      episode: current.episode,
      bytes: current.bytes,
      characters: current.characters,
      lines: current.lines,
      sha256: current.sha256,
      content: current.content,
    });
    setDirty(draftContent !== current.content);
    setConflict(null);
    setRecoveryView(null);
    setSelectedRevision(null);
    setSaveMessage('已采用磁盘版本为新基线；当前草稿仍未保存');
    setRecoveryMessage('恢复稿会按新的磁盘基线重新自动保存');
    updateManuscriptSummary(conflict.path, current);
  }

  async function inspectRevision(sha256: string) {
    if (!manuscriptView || !projectPath) return;
    setHistoryLoading(true);
    setError(null);
    try {
      setSelectedRevision(await loadManuscriptRevision(projectPath, manuscriptView.path, sha256));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setHistoryLoading(false);
    }
  }

  function loadRevisionIntoDraft() {
    if (!selectedRevision || !manuscriptView || recoverableRecovery) return;
    setDraftContent(selectedRevision.content);
    setDirty(selectedRevision.content !== manuscriptView.content);
    setConflict(null);
    setSaveMessage(selectedRevision.current ? '已载入当前磁盘版本' : '已把历史版本载入为未保存草稿');
  }

  if (!snapshot) {
    return (
      <main className="app-shell launcher-shell">
        <header className="topbar launcher-topbar">
          <div className="brand">
            <div className="mark">S</div>
            <div><strong>C.le. StoryOS</strong><span>长篇小说创作与故事状态管理</span></div>
          </div>
          <div className="readonly-badge writer-badge">本地优先 · 人类最终确认</div>
        </header>

        {error && <div className="error-banner global-banner">{error}</div>}

        <section className="launcher-content">
          <div className="launcher-hero">
            <span className="eyebrow">YOUR STORY UNIVERSE</span>
            <h1>进入你的故事宇宙</h1>
            <p>从正文开始写作，让人物状态、时间线、Claim 与 Canon 在同一个项目里保持可追踪。</p>
            <div className="launcher-actions">
              <button className="primary-action launcher-primary" onClick={() => setNewProjectOpen(true)}>新建作品</button>
              <button onClick={() => void chooseProjectFolder()} disabled={loading}>{loading ? '正在打开…' : '打开已有项目'}</button>
            </div>
          </div>

          {newProjectOpen && (
            <section className="launcher-create-card">
              <div className="panel-heading">
                <div><span className="eyebrow">NEW STORY</span><h2>新建 StoryOS 作品</h2></div>
                <button onClick={() => setNewProjectOpen(false)} disabled={creatingProject}>关闭</button>
              </div>
              <div className="form-grid">
                <label><span>作品名称</span><input autoFocus value={newProjectName} onChange={(event) => setNewProjectName(event.target.value)} placeholder="例如：断弦之歌" /></label>
                <label><span>语言</span><select value={newProjectLanguage} onChange={(event) => setNewProjectLanguage(event.target.value)}><option value="zh-CN">简体中文</option><option value="zh-TW">繁體中文</option><option value="en">English</option><option value="ja">日本語</option></select></label>
                <label className="form-wide"><span>保存位置</span><div className="folder-field"><input readOnly value={newProjectFolder} placeholder="选择一个文件夹；StoryOS 不会覆盖其中已有文件" /><button onClick={() => void chooseNewProjectFolder()}>选择文件夹</button></div></label>
              </div>
              <div className="form-actions">
                <span className="muted">只会新增 StoryOS 项目结构，不会删除或覆盖其他文件。</span>
                <button className="primary-action" onClick={() => void createProject()} disabled={creatingProject || !newProjectName.trim() || !newProjectFolder.trim()}>{creatingProject ? '创建中…' : '创建并进入作品'}</button>
              </div>
            </section>
          )}

          <section className="recent-section">
            <div className="section-heading-row">
              <div><span className="eyebrow">RECENT STORIES</span><h2>最近创作</h2></div>
              <button onClick={() => void hydrateRecentProjects()} disabled={recentLoading}>{recentLoading ? '刷新中…' : '刷新'}</button>
            </div>

            {!recentProjects.length && !recentLoading && (
              <div className="empty-state launcher-empty">还没有最近作品。新建一个项目，或打开包含 <code>storyos.yaml</code> 的已有项目。</div>
            )}

            <div className="recent-grid">
              {recentProjects.map((item) => (
                <article key={item.path} className="recent-card">
                  <div className="recent-card-top">
                    <div>
                      <span className="eyebrow">STORY PROJECT</span>
                      <h3>{item.session?.project.name ?? item.path.split(/[\\/]/).filter(Boolean).at(-1) ?? 'StoryOS Project'}</h3>
                    </div>
                    {item.session && item.session.summary.recoverable_drafts > 0 && <span className="draft-state dirty">有恢复稿</span>}
                  </div>
                  <div className="recent-path" title={item.path}>{item.path}</div>
                  <div className="muted">最近打开：{formatRecentTime(item.lastOpenedAt)}</div>
                  {item.loading && <div className="empty-inline">正在检查项目…</div>}
                  {item.error && <div className="recent-error">项目暂时不可用</div>}
                  {item.session && (
                    <div className="stats-row recent-stats">
                      <Stat label="章节" value={item.session.summary.manuscripts} />
                      <Stat label="恢复稿" value={item.session.summary.recoverable_drafts} />
                    </div>
                  )}
                  {item.session && item.session.recoveries.length > 0 && (
                    <div className="recent-recovery-list">
                      {item.session.recoveries.slice(0, 2).map((recovery) => (
                        <button key={recovery.path} onClick={() => void openProjectPath(item.path, recovery.path)} disabled={loading}>
                          {recovery.base_matches_current ? '恢复写作 · ' : '检查旧恢复稿 · '}{recoveryLabel(recovery)}
                        </button>
                      ))}
                    </div>
                  )}
                  <div className="recent-card-actions">
                    <button className="primary-action" onClick={() => void openProjectPath(item.path)} disabled={loading || item.loading || !!item.error}>继续创作</button>
                    <button onClick={() => void hydrateRecentProjects(removeRecentProject(item.path))}>移除记录</button>
                  </div>
                </article>
              ))}
            </div>
          </section>

          <details className="advanced-launcher">
            <summary>高级：直接输入项目路径</summary>
            <div className="advanced-path-row">
              <input value={manualProjectPath} onChange={(event) => setManualProjectPath(event.target.value)} placeholder="StoryOS 项目目录" aria-label="StoryOS 项目目录" />
              <button onClick={() => void openProjectPath(manualProjectPath)} disabled={loading || !manualProjectPath.trim()}>打开路径</button>
            </div>
          </details>
        </section>
      </main>
    );
  }

  return (
    <main className={`app-shell product-shell${focusMode ? ' focus-mode' : ''}`}>
      <header className="topbar product-topbar">
        <div className="brand">
          <div className="mark">S</div>
          <div><strong>C.le. StoryOS</strong><span>{snapshot.project.name}</span></div>
        </div>
        <div className="product-top-actions">
          {manuscriptView && (
            <button className={focusMode ? 'active-soft' : ''} onClick={() => setFocusMode((current) => !current)} title="Cmd/Ctrl + Shift + F">{focusMode ? '退出专注' : '专注模式'}</button>
          )}
          <button onClick={() => void refreshSnapshot()} disabled={loading || saving}>刷新项目</button>
          <button onClick={showLauncher} disabled={saving}>切换作品</button>
        </div>
        <div className="readonly-badge writer-badge">安全写作 · Canon 需明确确认</div>
      </header>

      {error && <div className="error-banner global-banner">{error}</div>}

      <section className="workspace-grid product-workspace-grid">
        <aside className="panel navigator product-navigator">
          <div className="panel-heading navigator-heading">
            <div><span className="eyebrow">STORY</span><h2>{snapshot.project.name}</h2></div>
            <button className="icon-action" onClick={prepareNewChapter} disabled={dirty} title={dirty ? '请先保存当前正文' : '新建章节'}>＋</button>
          </div>

          <div className="segmented product-nav-tabs">
            <button className={navMode === 'manuscripts' ? 'active' : ''} onClick={() => setNavMode('manuscripts')}>正文</button>
            <button className={navMode === 'entities' ? 'active' : ''} onClick={() => setNavMode('entities')}>人物/实体</button>
            <button className={navMode === 'governance' ? 'active' : ''} onClick={openGovernance}>治理</button>
          </div>

          {navMode !== 'governance' && (
            <input className="nav-search" value={navQuery} onChange={(event) => setNavQuery(event.target.value)} placeholder={navMode === 'manuscripts' ? '搜索章节…' : '搜索人物或实体…'} />
          )}

          <div className="nav-list product-nav-list">
            {navMode === 'manuscripts' && filteredManuscripts.map((item) => (
              <button key={item.path} className={selection?.kind === 'manuscript' && selection.value.path === item.path ? 'nav-item selected' : 'nav-item'} onClick={() => void chooseManuscript(item)}>
                <span className="nav-index">EP{String(item.episode ?? '—').padStart(2, '0')}</span>
                <span><strong>{item.title}</strong><small>{item.characters.toLocaleString()} 字符 · {item.lines} 行</small></span>
              </button>
            ))}
            {navMode === 'entities' && filteredEntities.map((item) => (
              <button key={item.id} className={selection?.kind === 'entity' && selection.value.id === item.id ? 'nav-item selected' : 'nav-item'} onClick={() => void chooseEntity(item)}>
                <span className="entity-dot" />
                <span><strong>{item.name}</strong><small>{item.kind} · {item.counts.events_total} events · {item.counts.canon_facts} canon</small></span>
              </button>
            ))}
            {navMode === 'governance' && (
              <div className="governance-nav-summary">
                {Object.entries(attention).map(([key, value]) => (
                  <div key={key}><span>{attentionLabel(key)}</span><strong>{value}</strong></div>
                ))}
              </div>
            )}
          </div>

          {navMode === 'manuscripts' && filteredManuscripts.length === 0 && (
            <div className="navigator-empty">
              <span>{snapshot.manuscripts.length ? '没有匹配的章节' : '还没有正文'}</span>
              {!snapshot.manuscripts.length && <button onClick={prepareNewChapter}>创建第一章</button>}
            </div>
          )}
          {navMode === 'entities' && filteredEntities.length === 0 && <div className="navigator-empty"><span>{snapshot.entities.length ? '没有匹配实体' : '项目中还没有人物/实体数据'}</span></div>}

          <details className="timeline-advanced">
            <summary>高级时间边界</summary>
            <div>
              <input className="through-input" value={throughText} onChange={(event) => setThroughText(event.target.value)} placeholder="sequence" aria-label="时间线 sequence" />
              <button onClick={() => void refreshSnapshot()}>应用</button>
            </div>
          </details>
        </aside>

        <section className="panel canvas product-canvas">
          {navMode === 'governance' && (
            <GovernanceWorkbench project={projectPath} onChanged={refreshSnapshot} />
          )}

          {navMode !== 'governance' && !selection && (
            <div className="landing product-landing">
              <span className="eyebrow">CURRENT STORY</span>
              <h1>{snapshot.manuscripts.length ? '继续写你的故事' : '从第一章开始'}</h1>
              <p>{snapshot.manuscripts.length ? '从左侧选择章节开始写作，或查看人物状态与 Story Governance。' : '这个项目已经准备好。创建第一章后即可开始写作。'}</p>
              <div className="stats-row landing-stats">
                <Stat label="章节" value={snapshot.manuscripts.length} />
                <Stat label="人物/实体" value={snapshot.entities.length} />
                <Stat label="Canon" value={snapshot.summary.canon_facts} />
                <Stat label="待审核" value={attention.unreviewed_claims ?? 0} />
              </div>
              <div className="landing-actions">
                {snapshot.manuscripts.length > 0 ? <button className="primary-action" onClick={() => void chooseManuscript(snapshot.manuscripts.at(-1)!)}>打开最近章节</button> : <button className="primary-action" onClick={prepareNewChapter}>创建第一章</button>}
                <button onClick={openGovernance}>打开事实治理</button>
              </div>
            </div>
          )}

          {manuscriptView && navMode === 'manuscripts' && (
            <article className="document-view editor-document product-editor-document">
              <div className="document-meta">
                <span>S{String(manuscriptView.season ?? 1).padStart(2, '0')} · EP{String(manuscriptView.episode ?? '—').padStart(2, '0')}</span>
                <span>{countWritingCharacters(draftContent).toLocaleString()} 字</span>
                <span>{manuscriptView.lines} 行</span>
              </div>
              <div className="editor-heading product-editor-heading">
                <div><span className="eyebrow">MANUSCRIPT</span><h1>{manuscriptView.title}</h1></div>
                <div className="editor-actions">
                  <span className={dirty ? 'draft-state dirty' : 'draft-state'}>{dirty ? saveMessage ?? '未正式保存' : saveMessage ?? '已保存'}</span>
                  <button className="save-button" onClick={() => void saveCurrentManuscript()} disabled={!dirty || saving || !!conflict || !!recoverableRecovery}>{saving ? '保存中…' : recoverableRecovery ? '先处理恢复稿' : conflict ? '先处理冲突' : '保存正文'}</button>
                </div>
              </div>

              <div className="writing-shortcuts">
                <span>Cmd/Ctrl + S 保存</span><span>Cmd/Ctrl + Shift + F 专注</span><span>Esc 退出专注</span>
              </div>

              {recoverableRecovery && (
                <section className="conflict-card recovery-card" aria-live="polite">
                  <div className="conflict-heading">
                    <div><span className="eyebrow">CRASH RECOVERY</span><h3>发现未正式保存的恢复稿</h3></div>
                    <div className="conflict-stat">约 {recoveryChangedLines.toLocaleString()} 行不同</div>
                  </div>
                  <p>{recoverableRecovery.base_matches_current ? '恢复稿基于当前正式版本，可以直接恢复。' : '正式正文已经发生变化，请先比较两个版本。'}</p>
                  <div className="conflict-actions">
                    <button className="primary-action" onClick={restoreRecoveryDraft}>恢复为草稿</button>
                    <button onClick={() => void discardRecoveryDraft()} disabled={recoveryLoading}>放弃恢复稿</button>
                  </div>
                  <div className="compare-grid">
                    <div><strong>恢复稿</strong><pre>{recoverableRecovery.content}</pre></div>
                    <div><strong>正式正文</strong><pre>{manuscriptView.content}</pre></div>
                  </div>
                </section>
              )}

              {conflict && (
                <section className="conflict-card" aria-live="polite">
                  <div className="conflict-heading">
                    <div><span className="eyebrow">SAVE CONFLICT</span><h3>磁盘正文已被其他程序修改</h3></div>
                    <div className="conflict-stat">约 {conflictChangedLines.toLocaleString()} 行不同</div>
                  </div>
                  <p>StoryOS 没有覆盖任何内容。你的草稿仍保留在编辑器和恢复区。</p>
                  <div className="conflict-actions">
                    <button onClick={() => void reloadConflictDiskVersion()}>使用磁盘版本</button>
                    <button className="primary-action" onClick={keepDraftAndAdoptDiskBase}>保留我的草稿</button>
                  </div>
                  <div className="compare-grid">
                    <div><strong>我的草稿</strong><pre>{draftContent}</pre></div>
                    <div><strong>磁盘版本</strong><pre>{conflict.current.content}</pre></div>
                  </div>
                </section>
              )}

              <textarea className="manuscript-editor" value={draftContent} onChange={(event) => updateDraft(event.target.value)} disabled={!!recoverableRecovery || !!conflict} spellCheck aria-label="正文编辑器" />
              <div className="editor-footer">
                <span>{recoverySaving ? '正在自动保存恢复稿…' : recoveryMessage ?? '未正式保存的修改会自动进入恢复区'}</span>
                <span>{dirty ? '尚未正式保存' : '正文已同步'}</span>
              </div>
            </article>
          )}

          {entityView && navMode === 'entities' && <EntityDetail view={entityView} />}
        </section>

        <aside className="panel inspector product-inspector">
          {manuscriptView && navMode === 'manuscripts' ? (
            <>
              <SceneInspector project={projectPath} manuscriptPath={manuscriptView.path} through={through} />
              <div className="inspector-divider" />
              <div className="inspector-section-heading"><span className="eyebrow">VERSION HISTORY</span><h3>版本历史</h3></div>
              {historyLoading && <div className="empty-inline">读取中…</div>}
              {!historyLoading && !manuscriptHistory?.revisions.length && <div className="empty-inline">保存一次正文后，这里会出现历史版本。</div>}
              <div className="nav-list history-list">
                {manuscriptHistory?.revisions.map((revision) => (
                  <button key={revision.sha256} className="nav-item" onClick={() => void inspectRevision(revision.sha256)}>
                    <span className="history-dot" />
                    <span><strong>{revision.current ? '当前正式版本' : '历史版本'}</strong><small>{revision.characters.toLocaleString()} 字符 · {revision.sha256.slice(0, 8)}</small></span>
                  </button>
                ))}
              </div>
              {selectedRevision && (
                <div className="revision-preview">
                  <div className="document-meta"><span>{selectedRevision.current ? 'CURRENT' : 'ARCHIVED'}</span><span>{selectedRevision.characters.toLocaleString()} 字符</span></div>
                  <pre>{selectedRevision.content}</pre>
                  <button onClick={loadRevisionIntoDraft} disabled={!!recoverableRecovery}>载入到编辑器</button>
                </div>
              )}
            </>
          ) : navMode === 'governance' ? (
            <div className="inspector-placeholder">
              <span className="eyebrow">SAFETY MODEL</span>
              <h2>Canon 永远需要你确认</h2>
              <p>审核 Claim 不会直接改 Canon；隔离候选也不会直接改 Canon。只有最后显示候选 SHA 并由你确认提交时，才会创建 Canon 文件。</p>
              <div className="governance-nav-summary inspector-attention">
                {Object.entries(attention).map(([key, value]) => <div key={key}><span>{attentionLabel(key)}</span><strong>{value}</strong></div>)}
              </div>
            </div>
          ) : entityView ? (
            <div className="inspector-placeholder">
              <span className="eyebrow">ENTITY CONTEXT</span>
              <h2>{entityView.entity.name}</h2>
              <p>当前视图按时间边界聚合人物状态、知识、Canon 与待治理 Claim。需要修改 Claim 时请进入“治理”。</p>
              <button onClick={openGovernance}>进入事实治理</button>
            </div>
          ) : (
            <div className="inspector-placeholder">
              <span className="eyebrow">STORY CONTEXT</span>
              <h2>上下文检查器</h2>
              <p>选择章节后，这里会显示 POV 安全上下文、人物状态、伏笔、Canon 冲突和版本历史。</p>
            </div>
          )}
        </aside>
      </section>

      {newChapterOpen && (
        <div className="modal-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget && !creatingChapter) setNewChapterOpen(false); }}>
          <section className="story-modal" role="dialog" aria-modal="true" aria-labelledby="new-chapter-title">
            <div className="panel-heading"><div><span className="eyebrow">NEW MANUSCRIPT</span><h2 id="new-chapter-title">新建章节</h2></div><button onClick={() => setNewChapterOpen(false)} disabled={creatingChapter}>关闭</button></div>
            <div className="form-grid chapter-form">
              <label className="form-wide"><span>章节标题</span><input autoFocus value={newChapterTitle} onChange={(event) => setNewChapterTitle(event.target.value)} placeholder="例如：雨夜来信" /></label>
              <label><span>季</span><input inputMode="numeric" value={newChapterSeason} onChange={(event) => setNewChapterSeason(event.target.value)} /></label>
              <label><span>集 / 章序号</span><input inputMode="numeric" value={newChapterEpisode} onChange={(event) => setNewChapterEpisode(event.target.value)} /></label>
            </div>
            <div className="form-actions"><span className="muted">新建章节只创建空白正文，不会覆盖相同 S/EP 的已有章节。</span><button className="primary-action" onClick={() => void createChapter()} disabled={creatingChapter || !newChapterTitle.trim()}>{creatingChapter ? '创建中…' : '创建并开始写作'}</button></div>
          </section>
        </div>
      )}

      {focusMode && manuscriptView && (
        <div className="focus-status" aria-hidden="true">
          <span>{manuscriptView.title}</span>
          <strong>{countWritingCharacters(draftContent).toLocaleString()} 字</strong>
          <span>{dirty ? '未保存' : '已保存'}</span>
        </div>
      )}
    </main>
  );
}
