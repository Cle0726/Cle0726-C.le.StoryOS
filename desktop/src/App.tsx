import { useMemo, useState } from 'react';
import { loadEntity, loadManuscript, loadWorkspace } from './storyos';
import type { EntitySummary, EntityView, ManuscriptSummary, ManuscriptView, WorkspaceSnapshot } from './types';

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

export default function App() {
  const [projectPath, setProjectPath] = useState('');
  const [throughText, setThroughText] = useState('');
  const [snapshot, setSnapshot] = useState<WorkspaceSnapshot | null>(null);
  const [selection, setSelection] = useState<Selection | null>(null);
  const [entityView, setEntityView] = useState<EntityView | null>(null);
  const [manuscriptView, setManuscriptView] = useState<ManuscriptView | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [navMode, setNavMode] = useState<'manuscripts' | 'entities'>('manuscripts');

  const through = useMemo(() => {
    if (!throughText.trim()) return null;
    const parsed = Number(throughText);
    return Number.isSafeInteger(parsed) && parsed >= 0 ? parsed : Number.NaN;
  }, [throughText]);

  async function openProject() {
    setLoading(true);
    setError(null);
    try {
      if (Number.isNaN(through)) throw new Error('时间线边界必须是非负整数');
      const next = await loadWorkspace(projectPath, through);
      setSnapshot(next);
      setSelection(null);
      setEntityView(null);
      setManuscriptView(null);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setLoading(false);
    }
  }

  async function chooseManuscript(item: ManuscriptSummary) {
    if (!snapshot) return;
    setSelection({ kind: 'manuscript', value: item });
    setEntityView(null);
    setLoading(true);
    setError(null);
    try {
      setManuscriptView(await loadManuscript(projectPath, item.path));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setLoading(false);
    }
  }

  async function chooseEntity(item: EntitySummary) {
    if (!snapshot) return;
    setSelection({ kind: 'entity', value: item });
    setManuscriptView(null);
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

  const attention = (snapshot?.workflow.attention ?? {}) as Record<string, number>;

  return (
    <main className="app-shell">
      <header className="topbar">
        <div className="brand">
          <div className="mark">S</div>
          <div><strong>C.le. StoryOS</strong><span>Authoring Workspace · Read Only</span></div>
        </div>
        <div className="project-controls">
          <input value={projectPath} onChange={(event) => setProjectPath(event.target.value)} placeholder="StoryOS 项目目录" aria-label="StoryOS 项目目录" />
          <input className="through-input" value={throughText} onChange={(event) => setThroughText(event.target.value)} placeholder="sequence" aria-label="时间线 sequence" />
          <button onClick={openProject} disabled={loading || !projectPath.trim()}>{loading ? '读取中…' : '打开项目'}</button>
        </div>
        <div className="readonly-badge">只读桥接</div>
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
            {!snapshot && <div className="empty-state">输入 StoryOS 项目目录后打开。桌面端不会获得 Canon 写权限。</div>}
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
              <p>{snapshot ? `当前有效时间线：${snapshot.timeline.effective_through_sequence ?? '无 Event'}` : 'Canonical State、Review、Materialization 与 Commit readiness 都由后端确定性引擎提供。桌面层只负责阅读和导航。'}</p>
              {snapshot && <div className="stats-row"><Stat label="Events" value={snapshot.timeline.events} /><Stat label="Canon" value={snapshot.summary.canon_facts} /><Stat label="Claims" value={snapshot.summary.claims} /></div>}
            </div>
          )}
          {manuscriptView && (
            <article className="document-view">
              <div className="document-meta"><span>S{String(manuscriptView.season ?? 1).padStart(2, '0')} · EP{String(manuscriptView.episode ?? '—').padStart(2, '0')}</span><span>{manuscriptView.characters.toLocaleString()} 字符</span><span className="hash">{manuscriptView.sha256.slice(0, 12)}</span></div>
              <h1>{manuscriptView.title}</h1>
              <pre>{manuscriptView.content}</pre>
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
          <div className="panel-heading"><div><span className="eyebrow">SAFETY / WORKFLOW</span><h2>检查器</h2></div><span className="readonly-badge compact">READ ONLY</span></div>
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
          <section className="inspector-section"><h3>Canon Authority</h3>{snapshot ? <JsonRows value={snapshot.canon.authorities} /> : <div className="empty-inline">—</div>}</section>
          <section className="inspector-section"><h3>安全边界</h3><ul className="policy-list"><li><span className="ok" />Workspace 只读</li><li><span className="ok" />Canonical mutation 禁止</li><li><span className="ok" />Staging mutation 禁止</li><li><span className="ok" />无通用 Shell IPC</li></ul></section>
          {!!snapshot?.diagnostics.reference_errors.length && <section className="inspector-section danger"><h3>Reference Errors</h3>{snapshot.diagnostics.reference_errors.map((item) => <div key={item} className="diagnostic">{item}</div>)}</section>}
        </aside>
      </section>
    </main>
  );
}
