import { useEffect, useMemo, useRef, useState } from 'react';
import { loadSceneWorkspace } from './storyos';
import type { SceneCharacterView, SceneWorkspaceView } from './types';

interface Props {
  project: string;
  manuscriptPath: string;
  through: number | null;
}

function text(value: unknown): string {
  if (value == null) return '—';
  if (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') {
    return String(value);
  }
  return JSON.stringify(value);
}

function CharacterCard({ row }: { row: SceneCharacterView }) {
  const stateRows = Object.entries(row.state);
  return (
    <article className="scene-context-card">
      <div className="scene-context-card-heading">
        <strong>{row.name}</strong>
        {row.scene_relevant && <span className="scene-context-tag">本集事件</span>}
      </div>
      <div className="scene-context-line"><span>位置</span><b>{text(row.location)}</b></div>
      {stateRows.length > 0 && (
        <div className="scene-context-stack">
          {stateRows.map(([key, value]) => (
            <div className="scene-context-line" key={key}><span>{key}</span><b>{text(value)}</b></div>
          ))}
        </div>
      )}
      <div className="scene-context-subtitle">当前知识</div>
      {row.knowledge.visible.length ? (
        <div className="scene-context-stack">
          {row.knowledge.visible.map((fact) => (
            <div className="scene-context-fact" key={fact.id}>
              <strong>{fact.predicate ?? fact.id}</strong>
              {fact.kind === 'canon_fact' && <span>{text(fact.value)}</span>}
            </div>
          ))}
        </div>
      ) : <div className="empty-inline">暂无可见知识</div>}
      {(row.knowledge.hidden_by_reveal > 0 || row.knowledge.hidden_ungoverned > 0) && (
        <div className="scene-context-warning">
          已隐藏：Reveal {row.knowledge.hidden_by_reveal} · 未治理知识 {row.knowledge.hidden_ungoverned}
        </div>
      )}
    </article>
  );
}

export default function SceneInspector({ project, manuscriptPath, through }: Props) {
  const [authorView, setAuthorView] = useState<SceneWorkspaceView | null>(null);
  const [view, setView] = useState<SceneWorkspaceView | null>(null);
  const [povId, setPovId] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const generation = useRef(0);

  async function fetchView(nextPov: string, preserveAuthor = true) {
    const currentGeneration = ++generation.current;
    setLoading(true);
    setError(null);
    try {
      const next = await loadSceneWorkspace(
        project,
        manuscriptPath,
        through,
        nextPov || null,
      );
      if (currentGeneration !== generation.current) return;
      if (!nextPov || !preserveAuthor) setAuthorView(nextPov ? authorView : next);
      setView(next);
    } catch (cause) {
      if (currentGeneration !== generation.current) return;
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      if (currentGeneration === generation.current) setLoading(false);
    }
  }

  useEffect(() => {
    const currentGeneration = ++generation.current;
    setPovId('');
    setAuthorView(null);
    setView(null);
    setError(null);
    setLoading(true);
    void loadSceneWorkspace(project, manuscriptPath, through, null)
      .then((next) => {
        if (currentGeneration !== generation.current) return;
        setAuthorView(next);
        setView(next);
      })
      .catch((cause) => {
        if (currentGeneration !== generation.current) return;
        setError(cause instanceof Error ? cause.message : String(cause));
      })
      .finally(() => {
        if (currentGeneration === generation.current) setLoading(false);
      });
    return () => {
      generation.current += 1;
    };
  }, [project, manuscriptPath, through]);

  const povOptions = useMemo(
    () => (authorView?.characters ?? []).slice().sort((a, b) => a.name.localeCompare(b.name)),
    [authorView],
  );
  const characters = useMemo(
    () => (view?.characters ?? []).slice().sort((a, b) => {
      if (a.scene_relevant !== b.scene_relevant) return a.scene_relevant ? -1 : 1;
      return a.name.localeCompare(b.name);
    }),
    [view],
  );

  async function changePov(nextPov: string) {
    setPovId(nextPov);
    if (!nextPov && authorView) {
      generation.current += 1;
      setView(authorView);
      setError(null);
      setLoading(false);
      return;
    }
    await fetchView(nextPov);
  }

  return (
    <section className="scene-context">
      <div className="panel-heading scene-context-heading">
        <div>
          <span className="eyebrow">SCENE WORKSPACE</span>
          <h2>创作上下文</h2>
        </div>
        <button onClick={() => fetchView(povId)} disabled={loading}>{loading ? '读取中…' : '刷新'}</button>
      </div>

      <label className="scene-context-mode">
        <span>上下文模式</span>
        <select value={povId} onChange={(event) => void changePov(event.target.value)} disabled={loading || !authorView}>
          <option value="">Author · 全局</option>
          {povOptions.map((character) => (
            <option value={character.id} key={character.id}>POV · {character.name}</option>
          ))}
        </select>
      </label>

      {error && <div className="error-banner">上下文读取失败：{error}</div>}
      {!view && !error && <div className="empty-inline">正在构建章节时间点…</div>}

      {view && (
        <>
          <div className="scene-context-boundary">
            <span>{view.mode === 'author' ? 'AUTHOR' : `POV · ${view.pov?.name ?? '—'}`}</span>
            <strong>sequence {view.timeline.effective_through_sequence ?? '—'}</strong>
            <small>{view.timeline.boundary_source}</small>
          </div>

          {view.mode === 'pov' && (
            <div className="scene-context-safe-note">
              POV 安全模式：其他角色状态、全局 Canon 冲突、plot 与 workflow 已从响应中移除。
            </div>
          )}

          <h3>人物状态 / 知识</h3>
          {characters.length ? characters.map((row) => <CharacterCard row={row} key={row.id} />) : (
            <div className="empty-inline">当前边界没有可见人物状态</div>
          )}

          <h3>本集 Canon Events</h3>
          {view.episode_events.length ? (
            <div className="scene-context-stack">
              {view.episode_events.map((event) => (
                <div className="scene-context-event" key={event.id}>
                  <span>{event.sequence}</span>
                  <strong>{event.subject_name}</strong>
                  <code>{event.type}</code>
                </div>
              ))}
            </div>
          ) : <div className="empty-inline">当前边界没有本集可见 Event</div>}

          {view.mode === 'author' && (
            <>
              <h3>未回收伏笔 / Plot</h3>
              {view.open_plots.length ? (
                <div className="scene-context-stack">
                  {view.open_plots.map((plot) => (
                    <div className="scene-context-fact" key={plot.id}>
                      <strong>{plot.name}</strong>
                      <span>{plot.scene_relevant ? '本集相关 · ' : ''}open</span>
                    </div>
                  ))}
                </div>
              ) : <div className="empty-inline">当前边界没有未回收 plot</div>}

              <h3>Canon 冲突</h3>
              {view.canon_conflicts.length ? (
                <div className="scene-context-stack">
                  {view.canon_conflicts.map((conflict) => (
                    <div className="scene-context-conflict" key={`${conflict.subject}:${conflict.predicate}`}>
                      <strong>{conflict.subject_name} · {conflict.predicate}</strong>
                      <span>{conflict.facts.map((fact) => `${fact.authority}:${text(fact.value)}`).join(' ↔ ')}</span>
                    </div>
                  ))}
                </div>
              ) : <div className="empty-inline">无同权威 Canon 歧义</div>}

              <h3>Workflow Attention</h3>
              <div className="scene-context-stack">
                {Object.entries(view.workflow_attention).map(([key, value]) => (
                  <div className="scene-context-line" key={key}><span>{key}</span><b>{value}</b></div>
                ))}
              </div>
            </>
          )}
        </>
      )}
    </section>
  );
}
