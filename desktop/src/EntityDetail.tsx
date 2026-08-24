import type { EntityView } from './types';
import './entity.css';

function record(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
}

function text(value: unknown): string {
  if (value == null) return '—';
  if (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') return String(value);
  try { return JSON.stringify(value); } catch { return String(value); }
}

function uiKey(value: string): string {
  const labels: Record<string, string> = {
    location: '位置',
    role: '角色',
    'identity.role': '身份 / 角色',
    'identity.real_name': '真实姓名',
    'goal.destination': '目标地点',
    'injury.left_hand': '左手伤势',
  };
  return labels[value] ?? value.replaceAll('_', ' ').replaceAll('.', ' · ');
}

function authorityLabel(value: unknown): string {
  const raw = String(value ?? '');
  const labels: Record<string, string> = {
    locked: '锁定 Canon',
    current: '当前 Canon',
    draft: '草案',
    alternate: '非主线',
  };
  return labels[raw] ?? (raw || '未标注权威');
}

function FieldRows({ value, empty = '暂无记录' }: { value: Record<string, unknown>; empty?: string }) {
  const rows = Object.entries(value);
  if (!rows.length) return <div className="empty-inline">{empty}</div>;
  return (
    <div className="entity-field-list">
      {rows.map(([key, item]) => (
        <div className="entity-field" key={key}>
          <span title={key}>{uiKey(key)}</span>
          <strong>{text(item)}</strong>
        </div>
      ))}
    </div>
  );
}

export default function EntityDetail({ view }: { view: EntityView }) {
  const facts = view.canon_facts.map(record);
  const claims = view.claims.map(record);
  return (
    <article className="entity-detail">
      <header className="entity-hero">
        <div>
          <span className="eyebrow">{view.entity.kind.toUpperCase()}</span>
          <h1>{view.entity.name}</h1>
          <div className="entity-aliases">
            {view.entity.aliases.length
              ? view.entity.aliases.map((alias) => <span key={alias}>{alias}</span>)
              : <span>无别名</span>}
          </div>
        </div>
        <div className="entity-id" title="StoryOS 稳定实体 ID">{view.entity.id}</div>
      </header>

      <section className="entity-section">
        <div className="entity-section-title">
          <div><span className="eyebrow">CURRENT STATE</span><h2>当前状态</h2></div>
          <span>故事时间点 {view.through_sequence ?? '—'}</span>
        </div>
        <FieldRows value={view.state.values} />
      </section>

      <div className="entity-two-column">
        <section className="entity-section">
          <div className="entity-section-title"><div><span className="eyebrow">PROFILE</span><h2>基础资料</h2></div></div>
          <FieldRows value={view.entity.data} empty="尚未填写人物资料" />
        </section>
        <section className="entity-section">
          <div className="entity-section-title"><div><span className="eyebrow">KNOWLEDGE</span><h2>当前已知信息</h2></div></div>
          <div className="entity-chip-list">
            {view.state.knowledge.length
              ? view.state.knowledge.map((item) => <span key={item} title={item}>{uiKey(item)}</span>)
              : <div className="empty-inline">当前时间点没有已知信息</div>}
          </div>
        </section>
      </div>

      <section className="entity-section">
        <div className="entity-section-title">
          <div><span className="eyebrow">CANON</span><h2>Canon 事实</h2></div>
          <span>{facts.length} 条</span>
        </div>
        <div className="entity-card-grid">
          {facts.length ? facts.map((fact, index) => (
            <div className="entity-canon-card" key={String(fact.id ?? index)}>
              <div className="entity-card-heading">
                <strong title={String(fact.predicate ?? '')}>{uiKey(String(fact.predicate ?? '未命名事实'))}</strong>
                <span className={fact.mainline_active ? 'entity-status active' : 'entity-status'}>
                  {fact.mainline_active ? '当前有效' : fact.active ? '非主线有效' : '当前未生效'}
                </span>
              </div>
              <div className="entity-card-value">{text(fact.value)}</div>
              <div className="entity-card-meta">
                <span>{authorityLabel(fact.authority)}</span>
                {typeof fact.valid_from === 'number' && <span>从时间点 {fact.valid_from}</span>}
                {typeof fact.valid_to === 'number' && <span>至时间点 {fact.valid_to}</span>}
                {fact.revealed === false && <span>尚未揭示</span>}
              </div>
            </div>
          )) : <div className="empty-state">这个实体还没有 Canon 事实。</div>}
        </div>
      </section>

      <section className="entity-section">
        <div className="entity-section-title">
          <div><span className="eyebrow">CANDIDATE CLAIMS</span><h2>待治理信息</h2></div>
          <span>{claims.length} 条</span>
        </div>
        <div className="entity-card-grid">
          {claims.length ? claims.map((raw, index) => {
            const claim = record(raw.claim);
            const review = record(raw.review);
            const materialization = record(raw.materialization);
            const commit = record(raw.canon_commit);
            return (
              <div className="entity-claim-card" key={String(claim.id ?? index)}>
                <div className="entity-card-heading">
                  <strong title={String(claim.predicate ?? '')}>{uiKey(String(claim.predicate ?? '未命名 Claim'))}</strong>
                  <span className="entity-status">{review.decision ? String(review.decision).replaceAll('_', ' ') : '待审核'}</span>
                </div>
                <div className="entity-card-value">{text(claim.value)}</div>
                <div className="entity-card-meta">
                  {typeof claim.confidence === 'number' && <span>置信度 {Math.round(claim.confidence * 100)}%</span>}
                  {materialization.ready === true && <span>可生成隔离候选</span>}
                  {commit.ready === true && <span>可提交 Canon</span>}
                </div>
              </div>
            );
          }) : <div className="empty-state">当前没有与该实体相关的 Candidate Claim。</div>}
        </div>
      </section>
    </article>
  );
}
