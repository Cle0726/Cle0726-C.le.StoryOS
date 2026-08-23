import { useEffect, useMemo, useRef, useState } from 'react';
import {
  clearManuscriptRecovery,
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

type Selection =
  | { kind: 'manuscript'; value: ManuscriptSummary }
  | { kind: 'entity'; value: EntitySummary };

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

function JsonRows({ value }: { value: Record<string, unknown> }) {
  const rows = Object.entries(value);
  if (!rows.length) return <div className="empty-inline">暂无</div>;
  return (
    <dl className="kv-list">
      {rows.map(([key, item]) => (
        <div className="kv-row" key={key}>
          <dt>{key}</dt>
          <dd>{typeof item === 'string' || typeof item === 'number' || typeof item === 'boolean' ? String(item) : JSON.stringify(item)}</dd>
        </div>
      ))}
    </dl>
  );
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
  const [navMode, setNavMode] = useState<'manuscripts' | 'entities'>('manuscripts');

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

  function cancelPendingRecoveryTimer() {
    recoveryGenerationRef.current += 1;
    if (recoveryTimerRef.current != null) {
      window.clearTimeout(recoveryTimerRef.current);
      recoveryTimerRef.current = null;
    }
  }

  function canDiscardDraft(): boolean {
    return !dirty || window.confirm('当前正文有未保存修改。StoryOS 会尽量保留 recovery 草稿，但离开不会正式保存正文。确定离开吗？');
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
            ? '发现未正式保存的 recovery 草稿，请先决定恢复或清除'
            : '发现基于旧磁盘版本的 recovery 草稿，请先比较后决定',
        );
      } else if (recovery.recovery) {
        setRecoveryMessage('Recovery 区内容与当前磁盘正文一致');
      } else {
        setRecoveryMessage(null);
      }
    } finally {
      setRecoveryLoading(false);
    }
  }

  async function openManuscriptFor(project: string, item: ManuscriptSummary) {
    setSelection({ kind: 'manuscript', value: item });
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
      setError(`正文已打开，但 recovery 读取失败：${recoveryResult.reason instanceof Error ? recoveryResult.reason.message : String(recoveryResult.reason)}`);
    }
  }

  async function openProjectPath(path: string, preferredRecoveryPath?: string) {
    if (!canDiscardDraft()) return;
    setLoading(true);
    setError(null);
    try {
      if (Number.isNaN(through)) throw new Error('时间线边界必须是非负整数');
      const normalizedPath = path.trim();
      if (!normalizedPath) throw new Error('项目路径不能为空');
      const next = await loadWorkspace(normalizedPath, through);
      setProjectPath(normalizedPath);
      setManualProjectPath(normalizedPath);
      setSnapshot(next);
      setSelection(null);
      setEntityView(null);
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

  async function chooseProjectFolder() {
    setError(null);
    try {
      const path = await pickProjectDirectory();
      if (path) await openProjectPath(path);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    }
  }

  function showLauncher() {
    if (!canDiscardDraft()) return;
    clearDocumentEditor();
    setSnapshot(null);
    setSelection(null);
    setEntityView(null);
    setProjectPath('');
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
      setRecoveryMessage('正在更新 recovery 草稿…');
      recoveryQueueRef.current = recoveryQueueRef.current
        .catch(() => undefined)
        .then(async () => {
          const result = await saveManuscriptRecovery(projectPath, manuscriptPath, baseSha256, content);
          if (generation === recoveryGenerationRef.current) {
            setLastRecoveryDraftSha(result.recovery.draft_sha256);
            setRecoveryMessage(`Recovery 已更新 · ${result.recovery.draft_sha256.slice(0, 12)}`);
          }
        })
        .catch((cause) => {
          if (generation === recoveryGenerationRef.current) {
            setRecoveryMessage(`Recovery 自动保存失败：${cause instanceof Error ? cause.message : String(cause)}`);
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
    if (!manuscriptView || !dirty || saving || recoverableRecovery || !projectPath) return;
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
        setRecoveryMessage(`保存前 recovery 已更新 · ${backupSha.slice(0, 12)}`);
      } catch (recoveryCause) {
        setRecoveryMessage(`保存前 recovery 更新失败：${recoveryCause instanceof Error ? recoveryCause.message : String(recoveryCause)}`);
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
        setSaveMessage('检测到磁盘版本变化，未覆盖任何内容');
        setRecoveryMessage(
          backupSha
            ? `当前草稿已留在 recovery · ${backupSha.slice(0, 12)}`
            : '磁盘冲突发生前 recovery 未能更新，请不要关闭当前编辑器',
        );
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
      setSaveMessage(`已保存并归档上一版本 · ${outcome.sha256.slice(0, 12)}`);
      setRecoveryMessage(backupSha ? 'Recovery 内容已与正式正文一致' : null);
      updateManuscriptSummary(outcome.path, outcome);
      await refreshHistoryFor(projectPath, outcome.path);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setSaving(false);
    }
  }

  function restoreRecoveryDraft() {
    if (!recoverableRecovery || !manuscriptView) return;
    setDraftContent(recoverableRecovery.content);
    setDirty(recoverableRecovery.content !== manuscriptView.content);
    setLastRecoveryDraftSha(recoverableRecovery.draft_sha256);
    setRecoveryView(null);
    setConflict(null);
    setSaveMessage('已把 recovery 草稿载入为未保存草稿');
    setRecoveryMessage('Recovery 已恢复；仍需明确点击“保存正文”才会写入正式工作副本');
  }

  async function discardRecoveryDraft() {
    if (!recoverableRecovery || !manuscriptView || recoveryLoading || !projectPath) return;
    setRecoveryLoading(true);
    setError(null);
    try {
      await clearManuscriptRecovery(projectPath, manuscriptView.path, recoverableRecovery.draft_sha256);
      setRecoveryView(null);
      setLastRecoveryDraftSha(null);
      setRecoveryMessage('已清除 recovery 草稿；磁盘正文没有变化');
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
    setSaveMessage(`已重新加载磁盘版本 · ${current.sha256.slice(0, 12)}`);
    setLastRecoveryDraftSha(null);
    updateManuscriptSummary(conflict.path, current);

    if (recoveryToClear) {
      try {
        await clearManuscriptRecovery(projectPath, conflict.path, recoveryToClear);
        setRecoveryMessage('已清除被放弃的冲突草稿 recovery');
      } catch (cause) {
        setRecoveryMessage(`磁盘版本已加载，但旧 recovery 未清除：${cause instanceof Error ? cause.message : String(cause)}`);
      }
    }
    void refreshHistoryFor(projectPath, conflict.path);
  }

  function keepDraftAndAdoptDiskBase() {
    if (!conflict || !manuscriptView) return;
    if (!window.confirm('保留当前草稿，并把最新磁盘 SHA 作为新的保存基线？下一次“保存正文”会明确替换该磁盘版本。')) return;
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
    setRecoveryMessage('Recovery 会按新的磁盘基线重新自动保存');
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
    setSaveMessage(
      selectedRevision.current
        ? '已把当前磁盘版本载入编辑器'
        : `已把历史版本 ${selectedRevision.sha256.slice(0, 12)} 载入为未保存草稿`,
    );
  }

  if (!snapshot) {
    return (
      <main className="app-shell">
        <header className="topbar">
          <div className="brand">
            <div className="mark">S</div>
            <div><strong>C.le. StoryOS</strong><span>Project Session · Recovery Launcher</span></div>
          </div>
          <div className="readonly-badge writer-badge">启动扫描只读 · Canon / Staging 不可写</div>
        </header>
        {error && <div className="error-banner">{error}</div>}
        <section className="panel" style={{ margin: 20, padding: 24 }}>
          <div className="panel-heading">
            <div><span className="eyebrow">PROJECT SESSION</span><h2>继续创作</h2></div>
            <button onClick={chooseProjectFolder} disabled={loading}>{loading ? '读取中…' : '选择项目文件夹'}</button>
          </div>
          <p className="muted">最近项目只保存在本机桌面 WebView。启动扫描只读取项目身份和 recovery 元数据，不读取 recovery 正文内容。</p>
          <div style={{ display: 'flex', gap: 8, margin: '18px 0', flexWrap: 'wrap' }}>
            <input
              value={manualProjectPath}
              onChange={(event) => setManualProjectPath(event.target.value)}
              placeholder="可选：粘贴 StoryOS 项目目录"
              aria-label="StoryOS 项目目录"
              style={{ flex: '1 1 360px' }}
            />
            <button onClick={() => openProjectPath(manualProjectPath)} disabled={loading || !manualProjectPath.trim()}>打开路径</button>
            <button onClick={() => hydrateRecentProjects()} disabled={recentLoading}>{recentLoading ? '刷新中…' : '刷新最近项目'}</button>
          </div>

          {!recentProjects.length && !recentLoading && (
            <div className="empty-state">还没有最近项目。选择一个包含 <code>storyos.yaml</code> 的项目文件夹即可开始。</div>
          )}

          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(300px, 1fr))', gap: 14 }}>
            {recentProjects.map((item) => (
              <article key={item.path} className="panel" style={{ padding: 16 }}>
                <div className="panel-heading">
                  <div>
                    <span className="eyebrow">RECENT PROJECT</span>
                    <h3>{item.session?.project.name ?? item.path.split(/[\\/]/).filter(Boolean).at(-1) ?? 'StoryOS Project'}</h3>
                  </div>
                  {item.session && item.session.summary.recoverable_drafts > 0 && (
                    <span className="draft-state dirty">{item.session.summary.recoverable_drafts} recovery</span>
                  )}
                </div>
                <div className="muted" style={{ wordBreak: 'break-all' }}>{item.path}</div>
                <div className="muted">最近打开：{formatRecentTime(item.lastOpenedAt)}</div>
                {item.loading && <p>正在验证项目与 recovery 状态…</p>}
                {item.error && <p className="error-banner">当前不可用：{item.error}</p>}
                {item.session && (
                  <>
                    <div className="stats-row" style={{ marginTop: 12 }}>
                      <Stat label="正文" value={item.session.summary.manuscripts} />
                      <Stat label="Recovery" value={item.session.summary.recoverable_drafts} />
                      <Stat label="旧基线" value={item.session.summary.stale_base_drafts} />
                    </div>
                    {item.session.recoveries.length > 0 && (
                      <div style={{ display: 'grid', gap: 8, marginTop: 12 }}>
                        {item.session.recoveries.slice(0, 3).map((recovery) => (
                          <button
                            key={recovery.path}
                            onClick={() => openProjectPath(item.path, recovery.path)}
                            disabled={loading}
                          >
                            {recovery.base_matches_current ? '打开恢复稿 · ' : '旧基线恢复稿 · '}{recoveryLabel(recovery)}
                          </button>
                        ))}
                      </div>
                    )}
                  </>
                )}
                <button
                  style={{ marginTop: 12 }}
                  onClick={() => openProjectPath(item.path)}
                  disabled={loading || item.loading || !!item.error}
                >
                  打开项目
                </button>
              </article>
            ))}
          </div>
        </section>
      </main>
    );
  }

  const attention = (snapshot.workflow.attention ?? {}) as Record<string, number>;

  return (
    <main className="app-shell">
      <header className="topbar">
        <div className="brand">
          <div className="mark">S</div>
          <div><strong>C.le. StoryOS</strong><span>Authoring Workspace · Session + History + Recovery</span></div>
        </div>
        <div className="project-controls">
          <span className="muted" title={projectPath}>{snapshot.project.name}</span>
          <input className="through-input" value={throughText} onChange={(event) => setThroughText(event.target.value)} placeholder="sequence" aria-label="时间线 sequence" />
          <button onClick={() => openProjectPath(projectPath)} disabled={loading || saving}>{loading ? '读取中…' : '刷新'}</button>
          <button onClick={showLauncher} disabled={saving}>切换项目</button>
        </div>
        <div className="readonly-badge writer-badge">正文 + Recovery 可写 · Canon 只读</div>
      </header>

      {error && <div className="error-banner">{error}</div>}

      <section className="workspace-grid">
        <aside className="panel navigator">
          <div className="panel-heading">
            <div><span className="eyebrow">PROJECT</span><h2>{snapshot.project.name}</h2></div>
            <span className="muted">{snapshot.project.language || '—'}</span>
          </div>
          <div className="segmented">
            <button className={navMode === 'manuscripts' ? 'active' : ''} onClick={() => setNavMode('manuscripts')}>正文 {snapshot.manuscripts.length}</button>
            <button className={navMode === 'entities' ? 'active' : ''} onClick={() => setNavMode('entities')}>实体 {snapshot.entities.length}</button>
          </div>
          <div className="nav-list">
            {navMode === 'manuscripts' && snapshot.manuscripts.map((item) => (
              <button key={item.path} className={selection?.kind === 'manuscript' && selection.value.path === item.path ? 'nav-item selected' : 'nav-item'} onClick={() => chooseManuscript(item)}>
                <span className="nav-index">EP{String(item.episode ?? '—').padStart(2, '0')}</span>
                <span><strong>{item.title}</strong><small>{item.characters.toLocaleString()} 字符 · {item.lines} 行</small></span>
              </button>
            ))}
            {navMode === 'entities' && snapshot.entities.map((item) => (
              <button key={item.id} className={selection?.kind === 'entity' && selection.value.id === item.id ? 'nav-item selected' : 'nav-item'} onClick={() => chooseEntity(item)}>
                <span className="entity-dot" />
                <span><strong>{item.name}</strong><small>{item.kind} · {item.counts.events_total} events · {item.counts.claims} claims</small></span>
              </button>
            ))}
          </div>
        </aside>

        <section className="panel canvas">
          {!selection && (
            <div className="landing">
              <span className="eyebrow">CURRENT STORY STATE</span>
              <h1>选择正文或实体</h1>
              <p>当前有效时间线：{snapshot.timeline.effective_through_sequence ?? '无 Event'}。正文正式保存仍使用 SHA-256 CAS，未保存编辑只进入隔离 recovery。</p>
              <div className="stats-row"><Stat label="Events" value={snapshot.timeline.events} /><Stat label="Canon" value={snapshot.summary.canon_facts} /><Stat label="Claims" value={snapshot.summary.claims} /></div>
            </div>
          )}

          {manuscriptView && (
            <article className="document-view editor-document">
              <div className="document-meta"><span>S{String(manuscriptView.season ?? 1).padStart(2, '0')} · EP{String(manuscriptView.episode ?? '—').padStart(2, '0')}</span><span>{draftContent.length.toLocaleString()} 字符</span><span className="hash">{manuscriptView.sha256.slice(0, 12)}</span></div>
              <div className="editor-heading">
                <div><span className="eyebrow">MANUSCRIPT WORKING COPY</span><h1>{manuscriptView.title}</h1></div>
                <div className="editor-actions">
                  <span className={dirty ? 'draft-state dirty' : 'draft-state'}>{dirty ? saveMessage ?? '未保存' : saveMessage ?? '已同步'}</span>
                  <button className="save-button" onClick={saveCurrentManuscript} disabled={!dirty || saving || !!conflict || !!recoverableRecovery}>{saving ? '保存中…' : recoverableRecovery ? '先处理恢复稿' : conflict ? '先处理冲突' : '保存正文'}</button>
                </div>
              </div>

              {recoverableRecovery && (
                <section className="conflict-card recovery-card" aria-live="polite">
                  <div className="conflict-heading">
                    <div><span className="eyebrow">CRASH RECOVERY</span><h3>发现未正式保存的 recovery 草稿</h3></div>
                    <div className="conflict-stat">约 {recoveryChangedLines.toLocaleString()} 行不同</div>
                  </div>
                  <p>草稿基线 <code>{recoverableRecovery.base_sha256.slice(0, 12)}</code>，当前磁盘 <code>{manuscriptView.sha256.slice(0, 12)}</code>。{recoverableRecovery.base_matches_current ? '基线仍匹配。' : '基线已过期，请先比较。'}</p>
                  <div className="conflict-actions">
                    <button onClick={restoreRecoveryDraft}>恢复为未保存草稿</button>
                    <button onClick={discardRecoveryDraft} disabled={recoveryLoading}>清除 recovery</button>
                  </div>
                  <div className="compare-grid">
                    <div><strong>Recovery</strong><pre>{recoverableRecovery.content}</pre></div>
                    <div><strong>当前磁盘</strong><pre>{manuscriptView.content}</pre></div>
                  </div>
                </section>
              )}

              {conflict && (
                <section className="conflict-card" aria-live="polite">
                  <div className="conflict-heading">
                    <div><span className="eyebrow">SAVE CONFLICT</span><h3>磁盘版本已变化</h3></div>
                    <div className="conflict-stat">约 {conflictChangedLines.toLocaleString()} 行不同</div>
                  </div>
                  <p>StoryOS 没有覆盖磁盘。当前草稿仍在编辑器/recovery 中。</p>
                  <div className="conflict-actions">
                    <button onClick={reloadConflictDiskVersion}>放弃草稿并重载磁盘</button>
                    <button onClick={keepDraftAndAdoptDiskBase}>保留草稿，采用新基线</button>
                  </div>
                  <div className="compare-grid">
                    <div><strong>当前草稿</strong><pre>{draftContent}</pre></div>
                    <div><strong>磁盘版本</strong><pre>{conflict.current.content}</pre></div>
                  </div>
                </section>
              )}

              <textarea
                className="manuscript-editor"
                value={draftContent}
                onChange={(event) => updateDraft(event.target.value)}
                disabled={!!recoverableRecovery || !!conflict}
                spellCheck={false}
                aria-label="正文编辑器"
              />
              <div className="editor-footer">
                <span>{recoverySaving ? 'Recovery 写入中…' : recoveryMessage ?? '未保存修改会在约 1.2 秒后写入 recovery'}</span>
                <span>正式正文仅在点击“保存正文”后写入</span>
              </div>
            </article>
          )}

          {entityView && (
            <article className="document-view">
              <div className="document-meta"><span>{entityView.entity.kind}</span><span className="hash">{entityView.entity.id}</span></div>
              <span className="eyebrow">STORY ENTITY</span>
              <h1>{entityView.entity.name}</h1>
              <h3>Point-in-time State</h3>
              <JsonRows value={entityView.state.values} />
              <h3>Knowledge</h3>
              <div className="empty-inline">{entityView.state.knowledge.length ? entityView.state.knowledge.join(' · ') : '暂无'}</div>
              <h3>Canon Facts</h3>
              <pre>{JSON.stringify(entityView.canon_facts, null, 2)}</pre>
              <h3>Claims</h3>
              <pre>{JSON.stringify(entityView.claims, null, 2)}</pre>
            </article>
          )}
        </section>

        <aside className="panel inspector">
          <div className="panel-heading"><div><span className="eyebrow">SAFETY / HISTORY</span><h2>检查器</h2></div></div>
          {manuscriptView ? (
            <>
              <div className="stats-row">
                <Stat label="历史" value={manuscriptHistory?.revisions.length ?? 0} />
                <Stat label="Recovery" value={recoveryView?.present ? 1 : 0} />
              </div>
              <h3>版本历史</h3>
              {historyLoading && <div className="empty-inline">读取中…</div>}
              {!historyLoading && !manuscriptHistory?.revisions.length && <div className="empty-inline">暂无历史版本</div>}
              <div className="nav-list">
                {manuscriptHistory?.revisions.map((revision) => (
                  <button key={revision.sha256} className="nav-item" onClick={() => inspectRevision(revision.sha256)}>
                    <span className="hash">{revision.sha256.slice(0, 12)}</span>
                    <span><strong>{revision.current ? '当前磁盘' : '历史版本'}</strong><small>{revision.characters.toLocaleString()} 字符</small></span>
                  </button>
                ))}
              </div>
              {selectedRevision && (
                <div className="revision-preview">
                  <div className="document-meta"><span>{selectedRevision.current ? 'CURRENT' : 'ARCHIVED'}</span><span className="hash">{selectedRevision.sha256.slice(0, 12)}</span></div>
                  <pre>{selectedRevision.content}</pre>
                  <button onClick={loadRevisionIntoDraft} disabled={!!recoverableRecovery}>载入为未保存草稿</button>
                </div>
              )}
            </>
          ) : (
            <>
              <h3>Workflow Attention</h3>
              <JsonRows value={attention} />
              <h3>只读边界</h3>
              <p className="muted">Session / Entity / Canon / Workflow 视图只读；正文与 recovery 使用独立、显式协议。</p>
            </>
          )}
        </aside>
      </section>
    </main>
  );
}
