import { useMemo, useState } from 'react';
import {
  loadEntity,
  loadManuscript,
  loadManuscriptHistory,
  loadManuscriptRevision,
  loadWorkspace,
  saveManuscript,
} from './storyos';
import type {
  EntitySummary,
  EntityView,
  ManuscriptConflict,
  ManuscriptHistoryView,
  ManuscriptRevisionView,
  ManuscriptSummary,
  ManuscriptView,
  WorkspaceSnapshot,
} from './types';

type Selection =
  | { kind: 'manuscript'; value: ManuscriptSummary }
  | { kind: 'entity'; value: EntitySummary };

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

function previewText(value: string, limit = 8000): string {
  if (value.length <= limit) return value;
  return `${value.slice(0, limit)}\n\n… 预览已截断，共 ${value.length.toLocaleString()} 字符`;
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

export default function App() {
  const [projectPath, setProjectPath] = useState('');
  const [throughText, setThroughText] = useState('');
  const [snapshot, setSnapshot] = useState<WorkspaceSnapshot | null>(null);
  const [selection, setSelection] = useState<Selection | null>(null);
  const [entityView, setEntityView] = useState<EntityView | null>(null);
  const [manuscriptView, setManuscriptView] = useState<ManuscriptView | null>(null);
  const [manuscriptHistory, setManuscriptHistory] = useState<ManuscriptHistoryView | null>(null);
  const [selectedRevision, setSelectedRevision] = useState<ManuscriptRevisionView | null>(null);
  const [conflict, setConflict] = useState<ManuscriptConflict | null>(null);
  const [draftContent, setDraftContent] = useState('');
  const [dirty, setDirty] = useState(false);
  const [saveMessage, setSaveMessage] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [navMode, setNavMode] = useState<'manuscripts' | 'entities'>('manuscripts');

  const through = useMemo(() => {
    if (!throughText.trim()) return null;
    const parsed = Number(throughText);
    return Number.isSafeInteger(parsed) && parsed >= 0 ? parsed : Number.NaN;
  }, [throughText]);

  const conflictChangedLines = useMemo(
    () => conflict ? changedLineCount(draftContent, conflict.current.content) : 0,
    [conflict, draftContent],
  );

  function canDiscardDraft(): boolean {
    return !dirty || window.confirm('当前正文有未保存修改。确定放弃这些修改吗？');
  }

  function clearDocumentEditor() {
    setManuscriptView(null);
    setManuscriptHistory(null);
    setSelectedRevision(null);
    setConflict(null);
    setDraftContent('');
    setDirty(false);
    setSaveMessage(null);
  }

  function updateManuscriptSummary(
    path: string,
    values: Pick<ManuscriptView, 'sha256' | 'bytes' | 'characters' | 'lines'>,
  ) {
    setSnapshot((current) => current ? {
      ...current,
      manuscripts: current.manuscripts.map((item) => item.path === path ? {
        ...item,
        ...values,
      } : item),
    } : current);
    setSelection((current) => {
      if (!current || current.kind !== 'manuscript' || current.value.path !== path) return current;
      return {
        kind: 'manuscript',
        value: {
          ...current.value,
          ...values,
        },
      };
    });
  }

  async function refreshHistory(path: string) {
    setHistoryLoading(true);
    try {
      setManuscriptHistory(await loadManuscriptHistory(projectPath, path));
    } finally {
      setHistoryLoading(false);
    }
  }

  async function openProject() {
    if (!canDiscardDraft()) return;
    setLoading(true);
    setError(null);
    try {
      if (Number.isNaN(through)) throw new Error('时间线边界必须是非负整数');
      const next = await loadWorkspace(projectPath, through);
      setSnapshot(next);
      setSelection(null);
      setEntityView(null);
      clearDocumentEditor();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setLoading(false);
    }
  }

  async function chooseManuscript(item: ManuscriptSummary) {
    if (!snapshot) return;
    if (selection?.kind === 'manuscript' && selection.value.path === item.path && manuscriptView) return;
    if (!canDiscardDraft()) return;
    setSelection({ kind: 'manuscript', value: item });
    setEntityView(null);
    clearDocumentEditor();
    setLoading(true);
    setError(null);
    try {
      const document = await loadManuscript(projectPath, item.path);
      setManuscriptView(document);
      setDraftContent(document.content);
      setDirty(false);
      try {
        await refreshHistory(item.path);
      } catch (historyCause) {
        setError(`正文已打开，但版本历史读取失败：${historyCause instanceof Error ? historyCause.message : String(historyCause)}`);
      }
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setLoading(false);
    }
  }

  async function chooseEntity(item: EntitySummary) {
    if (!snapshot) return;
    if (!canDiscardDraft()) return;
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

  async function saveCurrentManuscript() {
    if (!manuscriptView || !dirty || saving) return;
    setSaving(true);
    setError(null);
    setSaveMessage(null);
    try {
      const outcome = await saveManuscript(
        projectPath,
        manuscriptView.path,
        manuscriptView.sha256,
        draftContent,
      );
      if (outcome.schema === 'story.authoring-manuscript-conflict.v1') {
        setConflict(outcome);
        setSaveMessage('检测到磁盘版本变化，未覆盖任何内容');
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
      setSelectedRevision(null);
      setSaveMessage(`已保存并归档上一版本 · ${outcome.sha256.slice(0, 12)}`);
      updateManuscriptSummary(outcome.path, outcome);
      try {
        await refreshHistory(outcome.path);
      } catch (historyCause) {
        setError(`正文已保存，但版本历史刷新失败：${historyCause instanceof Error ? historyCause.message : String(historyCause)}`);
      }
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setSaving(false);
    }
  }

  function reloadConflictDiskVersion() {
    if (!conflict || !manuscriptView) return;
    const current = conflict.current;
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
    setSelectedRevision(null);
    setSaveMessage(`已重新加载磁盘版本 · ${current.sha256.slice(0, 12)}`);
    updateManuscriptSummary(conflict.path, current);
    void refreshHistory(conflict.path).catch((cause) => {
      setError(`磁盘版本已重新加载，但历史刷新失败：${cause instanceof Error ? cause.message : String(cause)}`);
    });
  }

  function keepDraftAndAdoptDiskBase() {
    if (!conflict || !manuscriptView) return;
    const confirmed = window.confirm(
      '这会保留当前草稿，但把最新磁盘 SHA 作为新的保存基线。下一次点击“保存正文”时，当前草稿将替换磁盘版本。确定继续吗？',
    );
    if (!confirmed) return;
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
    setSelectedRevision(null);
    setSaveMessage('已采用磁盘版本为新基线；当前草稿仍未保存');
    updateManuscriptSummary(conflict.path, current);
  }

  async function inspectRevision(sha256: string) {
    if (!manuscriptView) return;
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
    if (!selectedRevision || !manuscriptView) return;
    setDraftContent(selectedRevision.content);
    setDirty(selectedRevision.content !== manuscriptView.content);
    setConflict(null);
    setSaveMessage(
      selectedRevision.current
        ? '已把当前磁盘版本载入编辑器'
        : `已把历史版本 ${selectedRevision.sha256.slice(0, 12)} 载入为未保存草稿`,
    );
  }

  const attention = (snapshot?.workflow.attention ?? {}) as Record<string, number>;

  return (
    <main className="app-shell">
      <header className="topbar">
        <div className="brand">
          <div className="mark">S</div>
          <div><strong>C.le. StoryOS</strong><span>Authoring Workspace · History + Conflict Safety</span></div>
        </div>
        <div className="project-controls">
          <input value={projectPath} onChange={(event) => setProjectPath(event.target.value)} placeholder="StoryOS 项目目录" aria-label="StoryOS 项目目录" />
          <input className="through-input" value={throughText} onChange={(event) => setThroughText(event.target.value)} placeholder="sequence" aria-label="时间线 sequence" />
          <button onClick={openProject} disabled={loading || saving || !projectPath.trim()}>{loading ? '读取中…' : '打开项目'}</button>
        </div>
        <div className="readonly-badge writer-badge">正文 + 历史可写 · Canon 只读</div>
      </header>

      {error && <div className="error-banner">{error}</div>}

      <section className="workspace-grid">
        <aside className="panel navigator">
          <div className="panel-heading">
            <div><span className="eyebrow">PROJECT</span><h2>{snapshot?.project.name || '未打开项目'}</h2></div>
            <span className="muted">{snapshot?.project.language || '—'}</span>
          </div>
          <div className="segmented">
            <button className={navMode === 'manuscripts' ? 'active' : ''} onClick={() => setNavMode('manuscripts')}>正文 {snapshot?.manuscripts.length ?? 0}</button>
            <button className={navMode === 'entities' ? 'active' : ''} onClick={() => setNavMode('entities')}>实体 {snapshot?.entities.length ?? 0}</button>
          </div>
          <div className="nav-list">
            {!snapshot && <div className="empty-state">输入 StoryOS 项目目录后打开。正文工作副本可保存并归档旧版本，但桌面端不会获得 Canon 或 Staging 写权限。</div>}
            {snapshot && navMode === 'manuscripts' && snapshot.manuscripts.map((item) => (
              <button key={item.path} className={selection?.kind === 'manuscript' && selection.value.path === item.path ? 'nav-item selected' : 'nav-item'} onClick={() => chooseManuscript(item)}>
                <span className="nav-index">EP{String(item.episode ?? '—').padStart(2, '0')}</span>
                <span><strong>{item.title}</strong><small>{item.characters.toLocaleString()} 字符 · {item.lines} 行</small></span>
              </button>
            ))}
            {snapshot && navMode === 'entities' && snapshot.entities.map((item) => (
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
              <h1>{snapshot ? '选择正文或实体' : 'StoryOS 创作工作台'}</h1>
              <p>{snapshot ? `当前有效时间线：${snapshot.timeline.effective_through_sequence ?? '无 Event'}` : 'Canonical State、Review、Materialization 与 Commit readiness 仍由后端确定性引擎提供。正文保存会自动归档被替换版本，CAS 冲突不会静默覆盖。'}</p>
              {snapshot && <div className="stats-row"><Stat label="Events" value={snapshot.timeline.events} /><Stat label="Canon" value={snapshot.summary.canon_facts} /><Stat label="Claims" value={snapshot.summary.claims} /></div>}
            </div>
          )}
          {manuscriptView && (
            <article className="document-view editor-document">
              <div className="document-meta"><span>S{String(manuscriptView.season ?? 1).padStart(2, '0')} · EP{String(manuscriptView.episode ?? '—').padStart(2, '0')}</span><span>{draftContent.length.toLocaleString()} 字符</span><span className="hash">{manuscriptView.sha256.slice(0, 12)}</span></div>
              <div className="editor-heading">
                <div><span className="eyebrow">MANUSCRIPT WORKING COPY</span><h1>{manuscriptView.title}</h1></div>
                <div className="editor-actions">
                  <span className={dirty ? 'draft-state dirty' : 'draft-state'}>{dirty ? saveMessage ?? '未保存' : saveMessage ?? '已同步'}</span>
                  <button className="save-button" onClick={saveCurrentManuscript} disabled={!dirty || saving || !!conflict}>{saving ? '保存中…' : conflict ? '先处理冲突' : '保存正文'}</button>
                </div>
              </div>

              {conflict && (
                <section className="conflict-card" aria-live="polite">
                  <div className="conflict-heading">
                    <div><span className="eyebrow">CAS CONFLICT</span><h3>磁盘版本已变化，StoryOS 没有覆盖它</h3></div>
                    <div className="conflict-stat">约 {conflictChangedLines.toLocaleString()} 行不同</div>
                  </div>
                  <p>加载时基线 <code>{conflict.expected_sha256.slice(0, 12)}</code>，当前磁盘 <code>{conflict.current_sha256.slice(0, 12)}</code>。请选择明确的处理方式。</p>
                  <div className="conflict-grid">
                    <div className="conflict-side"><h4>当前未保存草稿</h4><pre>{previewText(draftContent)}</pre></div>
                    <div className="conflict-side"><h4>当前磁盘版本</h4><pre>{previewText(conflict.current.content)}</pre></div>
                  </div>
                  <div className="conflict-actions">
                    <button onClick={reloadConflictDiskVersion}>重新加载磁盘版本</button>
                    <button className="danger-outline" onClick={keepDraftAndAdoptDiskBase}>保留草稿，并允许下次覆盖磁盘版</button>
                  </div>
                </section>
              )}

              <textarea
                className="manuscript-editor"
                value={draftContent}
                onChange={(event) => updateDraft(event.target.value)}
                onKeyDown={(event) => {
                  if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 's') {
                    event.preventDefault();
                    void saveCurrentManuscript();
                  }
                }}
                aria-label={`${manuscriptView.title} 正文编辑器`}
                spellCheck={false}
              />
              <div className="editor-footer"><span>保存使用 SHA-256 CAS；被替换的旧版本先进入不可变历史。冲突时不会自动合并或覆盖。</span><span>Ctrl/Cmd + S</span></div>
            </article>
          )}
          {entityView && (
            <article className="entity-view">
              <span className="eyebrow">{entityView.entity.kind.toUpperCase()}</span>
              <h1>{entityView.entity.name}</h1>
              <p className="mono-id">{entityView.entity.id}</p>
              <section className="detail-block"><h3>Story State</h3><JsonRows value={entityView.state.values} /></section>
              <section className="detail-block"><h3>Canon Facts</h3><div className="card-list">{entityView.canon_facts.length ? entityView.canon_facts.map((fact, index) => <pre className="data-card" key={index}>{JSON.stringify(fact, null, 2)}</pre>) : <div className="empty-inline">暂无 Canon Fact</div>}</div></section>
              <section className="detail-block"><h3>Candidate Claims</h3><div className="card-list">{entityView.claims.length ? entityView.claims.map((claim, index) => <pre className="data-card" key={index}>{JSON.stringify(claim, null, 2)}</pre>) : <div className="empty-inline">暂无 Claim</div>}</div></section>
            </article>
          )}
        </section>

        <aside className="panel inspector">
          <div className="panel-heading"><div><span className="eyebrow">SAFETY / WORKFLOW</span><h2>检查器</h2></div><span className="readonly-badge compact writer-badge">HISTORY SAFE</span></div>
          <section className="inspector-section">
            <h3>时间线</h3>
            <div className="stats-grid"><Stat label="当前 sequence" value={snapshot?.timeline.effective_through_sequence} /><Stat label="总 Events" value={snapshot?.timeline.events_total} /></div>
          </section>
          <section className="inspector-section">
            <h3>需要关注</h3>
            <div className="attention-list">
              <Stat label="未审核 Claims" value={attention.unreviewed_claims ?? 0} />
              <Stat label="过期 Reviews" value={attention.stale_reviews ?? 0} />
              <Stat label="可 Materialize" value={attention.materialization_ready ?? 0} />
              <Stat label="可 Commit" value={attention.canon_commit_ready ?? 0} />
              <Stat label="引用错误" value={attention.reference_errors ?? 0} />
            </div>
          </section>

          {manuscriptHistory && (
            <section className="inspector-section history-section">
              <div className="section-inline-heading"><h3>正文版本历史</h3><span>{manuscriptHistory.revisions.length} 个版本</span></div>
              <div className="history-list">
                {manuscriptHistory.revisions.map((revision, index) => (
                  <button
                    key={revision.sha256}
                    className={selectedRevision?.sha256 === revision.sha256 ? 'history-item selected' : 'history-item'}
                    onClick={() => void inspectRevision(revision.sha256)}
                    disabled={historyLoading}
                  >
                    <span><strong>{revision.current ? '当前磁盘版本' : `历史 ${index}`}</strong><small>{revision.characters.toLocaleString()} 字符 · {revision.lines} 行</small></span>
                    <code>{revision.sha256.slice(0, 10)}</code>
                  </button>
                ))}
              </div>
              {historyLoading && <div className="empty-inline">读取版本中…</div>}
              {selectedRevision && (
                <div className="revision-preview">
                  <div className="revision-preview-head"><strong>{selectedRevision.current ? '当前版本预览' : '历史版本预览'}</strong><code>{selectedRevision.sha256.slice(0, 12)}</code></div>
                  <pre>{previewText(selectedRevision.content, 3000)}</pre>
                  <button onClick={loadRevisionIntoDraft}>{selectedRevision.current ? '载入当前版本到编辑器' : '载入为未保存草稿'}</button>
                </div>
              )}
            </section>
          )}

          <section className="inspector-section"><h3>Canon Authority</h3>{snapshot ? <JsonRows value={snapshot.canon.authorities} /> : <div className="empty-inline">—</div>}</section>
          <section className="inspector-section"><h3>安全边界</h3><ul className="policy-list"><li><span className="ok" />Snapshot / Entity / Manuscript / History 读取只读</li><li><span className="ok" />正文保存必须匹配加载时 SHA-256</li><li><span className="ok" />旧版本只写入内容寻址历史目录</li><li><span className="ok" />冲突只返回磁盘快照，不做写入</li><li><span className="ok" />Canonical mutation 禁止</li><li><span className="ok" />Staging mutation 禁止</li><li><span className="ok" />无通用 Shell IPC</li></ul></section>
          {!!snapshot?.diagnostics.reference_errors.length && <section className="inspector-section danger"><h3>Reference Errors</h3>{snapshot.diagnostics.reference_errors.map((item) => <div key={item} className="diagnostic">{item}</div>)}</section>}
        </aside>
      </section>
    </main>
  );
}
