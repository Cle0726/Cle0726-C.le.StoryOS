import { useEffect, useMemo, useState } from 'react';
import {
  commitCanon,
  decideClaim,
  loadCanonCommitPlan,
  loadClaimReviewQueue,
  loadMaterializationPlan,
  stageMaterialization,
} from './storyos';
import type {
  CanonCommitItem,
  CanonCommitPlan,
  ClaimDecision,
  ClaimReviewItem,
  ClaimReviewQueue,
  MaterializationItem,
  MaterializationPlan,
} from './productTypes';
import './governance.css';

interface Props {
  project: string;
  onChanged?: () => void | Promise<void>;
}

const ACTOR_KEY = 'cle.storyos.canon-actor.v1';

function valueText(value: unknown): string {
  if (value == null) return '—';
  if (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') {
    return String(value);
  }
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

function decisionLabel(value: ClaimDecision | undefined): string {
  switch (value) {
    case 'accept_event_candidate': return '接受为事件候选';
    case 'accept_fact_candidate': return '接受为事实候选';
    case 'reject': return '已拒绝';
    case 'defer': return '稍后处理';
    default: return '待审核';
  }
}

function reasonLabel(reason: string): string {
  const labels: Record<string, string> = {
    unreviewed: '尚未审核',
    stale_review: '审核已过期',
    current_conflict: '与当前状态冲突',
    future_interval_conflict: '未来有效期会冲突',
    already_canonical_duplicate: 'Canon 中已有相同内容',
    candidate_not_staged: '尚未进入隔离候选区',
    already_committed: '已经写入 Canon',
    materialization_not_ready: '候选尚未准备好',
  };
  return labels[reason] ?? reason.replaceAll('_', ' ');
}

export default function GovernanceWorkbench({ project, onChanged }: Props) {
  const [queue, setQueue] = useState<ClaimReviewQueue | null>(null);
  const [materialization, setMaterialization] = useState<MaterializationPlan | null>(null);
  const [commitPlan, setCommitPlan] = useState<CanonCommitPlan | null>(null);
  const [loading, setLoading] = useState(false);
  const [busyClaim, setBusyClaim] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [filter, setFilter] = useState<'attention' | 'all' | 'reviewed'>('attention');
  const [confirmClaim, setConfirmClaim] = useState<string | null>(null);
  const [actor, setActor] = useState(() => window.localStorage.getItem(ACTOR_KEY) || 'author');
  const [commitNote, setCommitNote] = useState('');

  async function refresh() {
    setLoading(true);
    setError(null);
    try {
      const [nextQueue, nextMaterialization, nextCommit] = await Promise.all([
        loadClaimReviewQueue(project),
        loadMaterializationPlan(project),
        loadCanonCommitPlan(project),
      ]);
      setQueue(nextQueue);
      setMaterialization(nextMaterialization);
      setCommitPlan(nextCommit);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void refresh();
  }, [project]);

  const materializationByClaim = useMemo(() => new Map(
    (materialization?.items ?? []).map((item) => [item.claim_id, item]),
  ), [materialization]);
  const commitByClaim = useMemo(() => new Map(
    (commitPlan?.items ?? []).map((item) => [item.claim_id, item]),
  ), [commitPlan]);

  const items = useMemo(() => {
    const rows = queue?.items ?? [];
    if (filter === 'all') return rows;
    if (filter === 'reviewed') return rows.filter((item) => !!item.review && !item.review_stale);
    return rows.filter((item) => {
      const staged = materializationByClaim.get(item.claim.id);
      const commit = commitByClaim.get(item.claim.id);
      return !item.review || item.review_stale || staged?.ready || commit?.ready || item.check.issues.length > 0;
    });
  }, [commitByClaim, filter, materializationByClaim, queue]);

  async function mutate(claimId: string, action: () => Promise<unknown>, success: string) {
    setBusyClaim(claimId);
    setError(null);
    setMessage(null);
    try {
      await action();
      setMessage(success);
      setConfirmClaim(null);
      await refresh();
      await onChanged?.();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusyClaim(null);
    }
  }

  function review(item: ClaimReviewItem, decision: ClaimDecision) {
    const accepted = decision === 'accept_event_candidate' || decision === 'accept_fact_candidate';
    return mutate(
      item.claim.id,
      () => decideClaim(project, item.claim.id, decision, {
        normalizedPredicate: accepted ? item.claim.predicate : undefined,
        normalizedValue: accepted ? item.claim.value : undefined,
        replace: !!item.review,
      }),
      accepted ? '审核已保存，可以继续生成隔离候选。' : '审核决定已保存。',
    );
  }

  function stage(item: ClaimReviewItem) {
    return mutate(
      item.claim.id,
      () => stageMaterialization(project, item.claim.id),
      '已生成隔离候选；Canon 仍未改变。',
    );
  }

  function commit(item: ClaimReviewItem, plan: CanonCommitItem) {
    const sha = plan.candidate_sha256;
    if (!sha) {
      setError('当前候选缺少可确认的 SHA-256，拒绝提交。');
      return;
    }
    const trimmedActor = actor.trim();
    if (!trimmedActor) {
      setError('请填写 Canon 提交者名称。');
      return;
    }
    window.localStorage.setItem(ACTOR_KEY, trimmedActor);
    void mutate(
      item.claim.id,
      () => commitCanon(project, item.claim.id, sha, trimmedActor, commitNote),
      'Canon 已通过明确确认写入，并保留审计记录。',
    );
  }

  function renderPipeline(item: ClaimReviewItem, mat?: MaterializationItem, canon?: CanonCommitItem) {
    const reviewed = !!item.review && !item.review_stale;
    const staged = !!canon?.candidate_sha256;
    const committed = canon?.state === 'committed';
    return (
      <div className="governance-pipeline" aria-label="Claim 处理进度">
        <span className={reviewed ? 'done' : 'active'}>1 审核</span>
        <i />
        <span className={staged || committed ? 'done' : mat?.ready ? 'active' : ''}>2 隔离候选</span>
        <i />
        <span className={committed ? 'done' : canon?.ready ? 'active' : ''}>3 Canon</span>
      </div>
    );
  }

  return (
    <section className="governance-workbench">
      <div className="governance-hero">
        <div>
          <span className="eyebrow">STORY GOVERNANCE</span>
          <h1>事实审核与 Canon</h1>
          <p>AI/导入结果先作为 Claim。只有你明确审核、生成隔离候选并再次确认 SHA 后，才允许进入 Canon。</p>
        </div>
        <button onClick={() => void refresh()} disabled={loading}>{loading ? '检查中…' : '重新检查'}</button>
      </div>

      <div className="governance-summary">
        <div><span>待审核</span><strong>{queue?.summary.unreviewed ?? 0}</strong></div>
        <div><span>审核过期</span><strong>{queue?.summary.stale_reviews ?? 0}</strong></div>
        <div><span>可生成候选</span><strong>{materialization?.summary.ready ?? 0}</strong></div>
        <div><span>可写入 Canon</span><strong>{commitPlan?.summary.ready ?? 0}</strong></div>
      </div>

      <div className="governance-toolbar">
        <div className="segmented compact">
          <button className={filter === 'attention' ? 'active' : ''} onClick={() => setFilter('attention')}>需要处理</button>
          <button className={filter === 'all' ? 'active' : ''} onClick={() => setFilter('all')}>全部</button>
          <button className={filter === 'reviewed' ? 'active' : ''} onClick={() => setFilter('reviewed')}>已审核</button>
        </div>
        <span className="muted">共 {queue?.summary.claims ?? 0} 条 Claim</span>
      </div>

      {error && <div className="error-banner">{error}</div>}
      {message && <div className="success-banner">{message}</div>}
      {!loading && queue && items.length === 0 && (
        <div className="empty-state governance-empty">当前没有需要处理的 Claim。</div>
      )}

      <div className="governance-list">
        {items.map((item) => {
          const mat = materializationByClaim.get(item.claim.id);
          const canon = commitByClaim.get(item.claim.id);
          const busy = busyClaim === item.claim.id;
          const reviewDecision = item.review?.decision;
          const canAccept = item.check.can_approve && !item.review_stale;
          const reasons = Array.from(new Set([...(mat?.reasons ?? []), ...(canon?.reasons ?? [])]));
          return (
            <article className="governance-card" key={item.claim.id}>
              <div className="governance-card-head">
                <div>
                  <span className="governance-subject">{item.subject_name ?? item.claim.subject}</span>
                  <h3>{item.claim.predicate}</h3>
                </div>
                <div className="governance-confidence">{Math.round(item.claim.confidence * 100)}%</div>
              </div>

              <div className="governance-value">{valueText(item.claim.value)}</div>
              <div className="governance-meta">
                <span>sequence {item.claim.at.sequence}</span>
                <span>{item.claim.proposed_authority}</span>
                <span>{decisionLabel(reviewDecision)}</span>
              </div>
              {renderPipeline(item, mat, canon)}

              {item.review_stale && <div className="governance-warning">原 Claim 已变化，旧审核失效，需要重新确认。</div>}
              {item.check.issues.length > 0 && (
                <div className="governance-issues">
                  {item.check.issues.map((issue, index) => (
                    <div key={`${issue.code}-${index}`}><strong>{issue.severity}</strong><span>{issue.message}</span></div>
                  ))}
                </div>
              )}
              {reasons.length > 0 && canon?.state !== 'committed' && (
                <div className="governance-reasons">
                  {reasons.slice(0, 4).map((reason) => <span key={reason}>{reasonLabel(reason)}</span>)}
                </div>
              )}

              <div className="governance-actions">
                {!item.review || item.review_stale ? (
                  <>
                    <button onClick={() => void review(item, 'accept_event_candidate')} disabled={busy || !canAccept}>接受为事件</button>
                    <button onClick={() => void review(item, 'accept_fact_candidate')} disabled={busy || !canAccept}>接受为事实</button>
                    <button onClick={() => void review(item, 'defer')} disabled={busy}>稍后处理</button>
                    <button className="quiet-danger" onClick={() => void review(item, 'reject')} disabled={busy}>拒绝</button>
                  </>
                ) : (
                  <>
                    {mat?.ready && !canon?.candidate_sha256 && (
                      <button onClick={() => void stage(item)} disabled={busy}>生成隔离候选</button>
                    )}
                    {canon?.ready && canon.candidate_sha256 && (
                      <button className="primary-action" onClick={() => setConfirmClaim(item.claim.id)} disabled={busy}>准备写入 Canon</button>
                    )}
                    {canon?.state === 'committed' && <span className="governance-committed">已进入 Canon</span>}
                    <button onClick={() => void review(item, 'defer')} disabled={busy}>改为稍后处理</button>
                  </>
                )}
              </div>

              {confirmClaim === item.claim.id && canon?.candidate_sha256 && (
                <div className="canon-confirm">
                  <div>
                    <span className="eyebrow">EXPLICIT CANON COMMIT</span>
                    <strong>最后确认</strong>
                    <p>这一步会创建 Canon 文件。候选 SHA：<code>{canon.candidate_sha256}</code></p>
                  </div>
                  <label><span>提交者</span><input value={actor} onChange={(event) => setActor(event.target.value)} /></label>
                  <label><span>备注（可选）</span><input value={commitNote} onChange={(event) => setCommitNote(event.target.value)} /></label>
                  <div className="canon-confirm-actions">
                    <button onClick={() => setConfirmClaim(null)}>取消</button>
                    <button className="primary-action" onClick={() => commit(item, canon)} disabled={busy || !actor.trim()}>{busy ? '写入中…' : '确认写入 Canon'}</button>
                  </div>
                </div>
              )}
            </article>
          );
        })}
      </div>
    </section>
  );
}
