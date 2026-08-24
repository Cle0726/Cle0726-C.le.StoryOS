import { useEffect, useMemo, useRef, useState } from 'react';
import { loadSceneWorkspace } from './storyos';
import type { SceneCharacterView, SceneWorkspaceView } from './types';
import './scene.css';

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

function uiKey(value: string): string {
  const labels: Record<string, string> = {
    location: '位置',
    'identity.role': '身份 / 角色',
    'identity.real_name': '真实姓名',
    'goal.destination': '目标地点',
    'plot.resolved': '伏笔已回收',
    'plot.reopened': '伏笔重新开启',
    'location.set': '位置变化',
    'knowledge.gained': '获得信息',
    'knowledge.lost': '失去信息',
  };
  return labels[value] ?? value.replaceAll('_', ' ').replaceAll('.', ' · ');
}

function boundaryLabel(value: string): string {
  const labels: Record<string, string> = {
    explicit_sequence: '手动时间边界',
    episode_end: '当前章节末',
    before_first_positioned_event: '章节前暂无已定位事件',
    no_events: '项目暂无事件',
    latest_event: '最新故事事件',
  };
  return labels[value] ?? uiKey(value);
}

function attentionLabel(value: string): string {
  const labels: Record<string, string> = {
    unreviewed_claims: '待审核 Claim',
    stale_reviews: '已过期审核',
    materialization_ready: '可生成隔离候选',
    canon_commit_ready: '可写入 Canon',
    reference_errors: '引用问题',
  };
  return labels[value] ?? uiKey(value);
}

function CharacterCard({ row }: { row: SceneCharacterView }) {
  const stateRows = Object.entries(row.state);
  const knowledge = row.knowledge as SceneCharacterView['knowledge'] & {
    hidden_inactive?: number;
    hidden_nonmainline?: number;
  };
  const hiddenInactive = knowledge.hidden_inactive ?? 0;
  const hiddenNonmainline = knowledge.hidden_nonmainline ?? 0;
  const hiddenTotal = knowledge.hidden_by_reveal + hiddenInactive + hiddenNonmainline + knowledge.hidden_ungoverned;

  return (
    <article className="scene-context-card">
      <div className="scene-context-card-heading">
        <strong>{row.name}</strong>
        {row.scene_relevant && <span className="scene-context-tag">本章相关</span>}
      </div>
      <div className="scene-context-line"><span>位置</span><b>{text(row.location)}</b></div>
      {stateRows.length > 0 && (
        <div className="scene-context-stack">
          {stateRows.map(([key, value]) => (
            <div className="scene-context-line" key={key}><span title={key}>{uiKey(key)}</span><b>{text(value)}</b></div>
          ))}
        </div>
      )}
      <div className="scene-context-subtitle">当前已知信息</div>
      {knowledge.visible.length ? (
        <div className="scene-context-stack">
          {knowledge.visible.map((fact) => (
            <div className="scene-context-fact" key={fact.id}>
              <strong title={fact.predicate ?? fact.id}>{uiKey(fact.predicate ?? fact.id)}</strong>
              {fact.kind === 'canon_fact' && <span>{text(fact.value)}</span>}
            </div>
          ))}
        </div>
      ) : <div className="empty-inline">当前没有可见知识</div>}
      {hiddenTotal > 0 && (
        <div className="scene-context-warning">
          POV 已隐藏：未揭示 {knowledge.hidden_by_reveal} · 尚未生效 {hiddenInactive} · 非主线 {hiddenNonmainline} · 未治理 {knowledge.hidden_ungoverned}
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

  async function fetchView(nextPov: string) {
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
      if (!nextPov) setAuthorView(next);
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
          <span className="eyebrow">STORY CONTEXT</span>
          <h2>章节上下文</h2>
        </div>
        <button onClick={() => fetchView(povId)} disabled={loading}>{loading ? '读取中…' : '刷新'}</button>
      </div>

      <label className="scene-context-mode">
        <span>查看视角</span>
        <select value={povId} onChange={(event) => void changePov(event.target.value)} disabled={loading || !authorView}>
          <option value="">作者视角 · 全局</option>
          {povOptions.map((character) => (
            <option value={character.id} key={character.id}>POV · {character.name}</option>
          ))}
        </select>
      </label>

      {error && <div className="error-banner">上下文读取失败：{error}</div>}
      {!view && !error && <div className="empty-inline">正在构建当前章节状态…</div>}

      {view && (
        <>
          <div className="scene-context-boundary">
            <span>{view.mode === 'author' ? '作者视角' : `POV · ${view.pov?.name ?? '—'}`}</span>
            <strong>时间点 {view.timeline.effective_through_sequence ?? '—'}</strong>
            <small>{boundaryLabel(view.timeline.boundary_source)}</small>
          </div>

          {view.mode === 'pov' && (
            <div className="scene-context-safe-note">
              POV 安全模式已开启：只显示该角色在当前时间点可以知道和感知的信息。
            </div>
          )}

          <h3>人物状态与知识</h3>
          {characters.length ? characters.map((row) => <CharacterCard row={row} key={row.id} />) : (
            <div className="empty-inline">当前时间点没有可见人物状态</div>
          )}

          <h3>本章故事事件</h3>
          {view.episode_events.length ? (
            <div className="scene-context-stack">
              {view.episode_events.map((event) => (
                <div className="scene-context-event" key={event.id}>
                  <span>{event.sequence}</span>
                  <strong>{event.subject_name}</strong>
                  <code title={event.type}>{uiKey(event.type)}</code>
                </div>
              ))}
            </div>
          ) : <div className="empty-inline">当前时间点没有本章事件</div>}

          {view.mode === 'author' && (
            <>
              <h3>未回收伏笔</h3>
              {view.open_plots.length ? (
                <div className="scene-context-stack">
                  {view.open_plots.map((plot) => (
                    <div className="scene-context-fact" key={plot.id}>
                      <strong>{plot.name}</strong>
                      <span>{plot.scene_relevant ? '本章相关 · ' : ''}未回收</span>
                    </div>
                  ))}
                </div>
              ) : <div className="empty-inline">当前没有未回收伏笔</div>}

              <h3>Canon 一致性</h3>
              {view.canon_conflicts.length ? (
                <div className="scene-context-stack">
                  {view.canon_conflicts.map((conflict) => (
                    <div className="scene-context-conflict" key={`${conflict.subject}:${conflict.predicate}`}>
                      <strong>{conflict.subject_name} · {uiKey(conflict.predicate)}</strong>
                      <span>{conflict.facts.map((fact) => `${fact.authority}: ${text(fact.value)}`).join(' ↔ ')}</span>
                    </div>
                  ))}
                </div>
              ) : <div className="empty-inline">当前没有同权威 Canon 歧义</div>}

              <h3>需要处理</h3>
              <div className="scene-context-stack">
                {Object.entries(view.workflow_attention).map(([key, value]) => (
                  <div className="scene-context-line" key={key}><span>{attentionLabel(key)}</span><b>{value}</b></div>
                ))}
              </div>
            </>
          )}
        </>
      )}
    </section>
  );
}
