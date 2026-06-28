'use client';

import { useEffect, useState } from 'react';
import { useParams, useRouter } from 'next/navigation';
import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid,
  Tooltip, Legend, ResponsiveContainer
} from 'recharts';

// ─── Utilities ────────────────────────────────────────────────────────────────

function toMarcapDisplay(val) {
  if (!val) return '-';
  if (val >= 1e12) return (val / 1e12).toFixed(1) + '조';
  if (val >= 1e8)  return Math.round(val / 1e8).toLocaleString() + '억';
  return val.toLocaleString() + '원';
}

function factText(f)  { return typeof f === 'string' ? f : f?.fact; }
function factQuote(f) { return typeof f === 'object' ? f?.source_quote : null; }
function factImp(f)   { return typeof f === 'object' ? (f?.importance || 2) : 2; }

function alphaOpinion(score) {
  if (score >= 90) return { label: 'Elite',      bg: '#059669' };
  if (score >= 80) return { label: 'Strong Buy', bg: '#16a34a' };
  if (score >= 70) return { label: 'Buy',        bg: '#22c55e' };
  if (score >= 50) return { label: 'Hold',       bg: '#d97706' };
  if (score >= 30) return { label: 'Caution',    bg: '#ea580c' };
  return               { label: 'High Risk',    bg: '#dc2626' };
}

function barScoreColor(val) {
  if (val >= 80) return '#16a34a';
  if (val >= 60) return '#84cc16';
  if (val >= 40) return '#eab308';
  if (val >= 20) return '#f97316';
  return '#ef4444';
}

function peerStars(pct) {
  if (pct == null) return null;
  const filled = pct >= 85 ? 5 : pct >= 70 ? 4 : pct >= 50 ? 3 : pct >= 30 ? 2 : 1;
  const color  = filled >= 4 ? '#f59e0b' : filled >= 3 ? '#94a3b8' : '#d1d5db';
  return { filled, color, str: '★'.repeat(filled) + '☆'.repeat(5 - filled) };
}

const METRIC_LABEL = { roe: 'ROE', roa: 'ROA', op_margin: '영업이익률', per: 'PER', pbr: 'PBR' };
const METRIC_UNIT  = { roe: '%',   roa: '%',   op_margin: '%',          per: 'x',   pbr: 'x'  };

// ─── Hero (단순화) ────────────────────────────────────────────────────────────

function HeroSection({ data, stageb }) {
  return (
    <div className="bg-white rounded-xl shadow mb-5 px-6 py-4 flex items-start justify-between">
      <div>
        <h1 className="text-2xl font-bold text-gray-900">{data.name}</h1>
        <p className="text-sm text-gray-400 mt-0.5">
          {data.code} · KOSPI · {data.latest_year}년 기준
          {stageb?.peer_group ? ` · ${stageb.peer_group}` : ''}
        </p>
      </div>
      <div className="text-right shrink-0 ml-4">
        <p className="text-xl font-bold text-gray-900 tabular-nums">
          {data.price ? data.price.toLocaleString() + '원' : '-'}
        </p>
        <p className="text-sm text-gray-400 mt-0.5">시가총액 {toMarcapDisplay(data.marcap)}</p>
      </div>
    </div>
  );
}

// ─── Confidence Badge ─────────────────────────────────────────────────────────

function ConfidenceBadge({ confidence }) {
  if (!confidence) return null;
  const { level, reasons, caution, quant_count, dart_count } = confidence;
  const color         = level === 'High' ? '#4ade80' : level === 'Medium' ? '#fbbf24' : '#f87171';
  const dot           = level === 'High' ? '🟢' : level === 'Medium' ? '🟡' : '🔴';
  const totalEvidence = (quant_count || 0) + (dart_count || 0);

  return (
    <div style={{
      background: 'rgba(255,255,255,0.07)',
      border: `1px solid ${color}40`,
      borderRadius: 10,
      padding: '10px 14px',
      minWidth: 180,
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 5, marginBottom: 6 }}>
        <span style={{ fontSize: 12 }}>{dot}</span>
        <span style={{ fontSize: 12, fontWeight: 700, color }}>Confidence {level}</span>
        {totalEvidence > 0 && (
          <span style={{ fontSize: 10, color: 'rgba(255,255,255,0.3)', marginLeft: 2 }}>
            · 근거 {totalEvidence}개
          </span>
        )}
      </div>
      {reasons?.map((r, i) => (
        <p key={i} style={{ fontSize: 10, color: 'rgba(255,255,255,0.55)', marginBottom: 2 }}>
          ✓ {r}
        </p>
      ))}
      {caution && (
        <p style={{ fontSize: 10, color: '#f87171', marginTop: 4 }}>⚠ {caution}</p>
      )}
    </div>
  );
}

// ─── Reason Graph ─────────────────────────────────────────────────────────────

function ReasonGraph({ reasonGraph }) {
  const [open, setOpen] = useState(false);
  if (!reasonGraph) return null;

  const TYPE_COLOR = { growth: '#16a34a', moat: '#7c3aed', risk: '#dc2626' };
  const TYPE_LABEL = { growth: '성장', moat: '해자', risk: '리스크' };

  return (
    <div style={{ marginTop: 14, borderTop: '1px solid #f1f5f9', paddingTop: 12 }}>
      <button
        onClick={() => setOpen(o => !o)}
        style={{
          fontSize: 12, color: '#64748b', cursor: 'pointer',
          background: 'none', border: 'none', padding: 0,
          display: 'flex', alignItems: 'center', gap: 5,
        }}
      >
        <span style={{ fontSize: 10 }}>{open ? '▼' : '▶'}</span>
        <span>왜 이런 결론이 나왔나요? (근거 추적)</span>
      </button>

      {open && (
        <div style={{ marginTop: 12, paddingLeft: 4 }}>
          {/* Root */}
          <div style={{ fontSize: 14, fontWeight: 700, color: '#0f172a', marginBottom: 10 }}>
            Alpha Score {reasonGraph.alpha_score}
            {reasonGraph.rank_label && (
              <span style={{ fontSize: 11, fontWeight: 400, color: '#94a3b8', marginLeft: 8 }}>
                {reasonGraph.rank_label}
              </span>
            )}
          </div>

          {/* Quant drivers */}
          {reasonGraph.quant_drivers?.length > 0 && (
            <div style={{ marginBottom: 12 }}>
              <p style={{ fontSize: 10, fontWeight: 700, color: '#64748b', letterSpacing: 0.5, marginBottom: 6, textTransform: 'uppercase' }}>
                정량 근거  V신호 {reasonGraph.v_score}  ·  S신호 {reasonGraph.s_score}
              </p>
              {reasonGraph.quant_drivers.map((d, i) => {
                const unit = METRIC_UNIT[d.metric] || '';
                return (
                  <div key={i} style={{ display: 'flex', alignItems: 'baseline', gap: 8, marginBottom: 5, paddingLeft: 12 }}>
                    <span style={{ fontSize: 11, color: '#cbd5e1' }}>└</span>
                    <span style={{ fontSize: 13, fontWeight: 600, color: '#1e293b' }}>{d.factor}</span>
                    <span style={{ fontSize: 11, color: '#94a3b8' }}>
                      ({d.value}{unit}
                      {d.sector_avg != null ? `, 업종 중앙값 ${d.sector_avg}${unit}` : ''})
                    </span>
                  </div>
                );
              })}
            </div>
          )}

          {/* DART drivers */}
          {reasonGraph.dart_drivers?.length > 0 && (
            <div>
              <p style={{ fontSize: 10, fontWeight: 700, color: '#64748b', letterSpacing: 0.5, marginBottom: 6, textTransform: 'uppercase' }}>
                사업보고서 근거 (DART)
              </p>
              {reasonGraph.dart_drivers.map((d, i) => {
                const tagColor = TYPE_COLOR[d.type] || '#64748b';
                const tagLabel = TYPE_LABEL[d.type] || d.type;
                return (
                  <div key={i} style={{ display: 'flex', alignItems: 'start', gap: 8, marginBottom: 6, paddingLeft: 12 }}>
                    <span style={{ fontSize: 11, color: '#cbd5e1', marginTop: 2 }}>└</span>
                    <span style={{
                      fontSize: 10, fontWeight: 700, color: '#fff',
                      background: tagColor, padding: '2px 6px', borderRadius: 4,
                      flexShrink: 0, marginTop: 1,
                    }}>
                      {tagLabel}
                    </span>
                    <span style={{ fontSize: 12, color: '#374151', lineHeight: 1.5 }}>{d.factor}</span>
                  </div>
                );
              })}
            </div>
          )}

          {/* 생성 근거 요약 */}
          <div style={{ marginTop: 14, paddingTop: 10, borderTop: '1px dashed #e2e8f0' }}>
            <p style={{ fontSize: 11, color: '#94a3b8' }}>
              이 결론은 정량 {reasonGraph.quant_drivers?.length || 0}개
              {' + '}DART {reasonGraph.dart_drivers?.length || 0}개 근거 기반으로 생성되었습니다.
            </p>
          </div>
        </div>
      )}
    </div>
  );
}

// ─── Investment Thesis Card (메인) ────────────────────────────────────────────

function InvestmentThesisCard({ thesis, alpha }) {
  const [scoreInfo, setScoreInfo] = useState(false);
  if (!thesis?.summary && !thesis?.investment_point) return null;

  const score = alpha?.score;
  const op    = score != null ? alphaOpinion(score) : null;

  return (
    <div className="rounded-xl shadow mb-5 overflow-hidden">
      {/* Dark header: Alpha Score + Confidence */}
      <div style={{
        background: 'linear-gradient(135deg, #0f172a 0%, #1e3a5f 100%)',
        padding: '18px 24px',
        display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 16,
      }}>
        <div style={{ flex: 1 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 6, position: 'relative' }}>
            <p style={{ fontSize: 10, fontWeight: 700, letterSpacing: 3, color: 'rgba(255,255,255,0.4)', margin: 0 }}>
              ALPHA SCORE
            </p>
            <span
              style={{ fontSize: 12, color: 'rgba(255,255,255,0.3)', cursor: 'help', userSelect: 'none' }}
              onMouseEnter={() => setScoreInfo(true)}
              onMouseLeave={() => setScoreInfo(false)}
            >
              ⓘ
            </span>
            {scoreInfo && (
              <div style={{
                position: 'absolute', left: 0, top: '130%',
                background: '#1e293b', border: '1px solid rgba(255,255,255,0.12)',
                borderRadius: 8, padding: '12px 16px', zIndex: 50,
                boxShadow: '0 6px 24px rgba(0,0,0,0.5)', minWidth: 220,
              }}>
                <p style={{ fontSize: 10, fontWeight: 700, color: '#94a3b8', marginBottom: 6, letterSpacing: 1 }}>
                  ALPHA SCORE란?
                </p>
                <p style={{ fontSize: 11, color: 'rgba(255,255,255,0.7)', lineHeight: 1.65, marginBottom: 6 }}>
                  수익성(ROE·ROA·영업이익률)과 재무 안전성을 종합한 0–100점 점수입니다.
                </p>
                <p style={{ fontSize: 11, color: 'rgba(255,255,255,0.45)', lineHeight: 1.5 }}>
                  업종 내 상대 순위로 계산되며, 현재 모델은 이 요인들을 기반으로 백테스트를 수행했습니다.
                </p>
              </div>
            )}
          </div>
          <div style={{ display: 'flex', alignItems: 'baseline', gap: 8 }}>
            <span style={{ fontSize: 48, fontWeight: 900, color: '#fff', lineHeight: 1 }}>{score ?? '-'}</span>
            <span style={{ fontSize: 15, color: 'rgba(255,255,255,0.3)', marginBottom: 2 }}>/100</span>
            {op && (
              <span style={{
                fontSize: 12, fontWeight: 700, color: '#fff',
                background: op.bg, padding: '3px 10px', borderRadius: 6, marginBottom: 2,
              }}>
                {op.label}
              </span>
            )}
          </div>
          {alpha?.rank_sector != null && alpha?.total_sector != null && (
            <p style={{ fontSize: 11, color: 'rgba(255,255,255,0.35)', marginTop: 5 }}>
              업종 내 {alpha.rank_sector}/{alpha.total_sector}위
            </p>
          )}
        </div>
        <ConfidenceBadge confidence={thesis.confidence} />
      </div>

      {/* White body: Thesis + Strengths + Risks + Reason Graph */}
      <div style={{ background: '#fff', padding: '20px 24px' }}>
        {/* Thesis text */}
        <div style={{ marginBottom: 18 }}>
          <p style={{ fontSize: 10, fontWeight: 700, color: '#64748b', letterSpacing: 1, textTransform: 'uppercase', marginBottom: 8 }}>
            Investment Thesis
          </p>
          {thesis.summary && (
            <p style={{ fontSize: 14, fontWeight: 500, color: '#0f172a', lineHeight: 1.75 }}>
              {thesis.summary}
            </p>
          )}
          {thesis.investment_point && thesis.investment_point !== thesis.summary && (
            <p style={{ fontSize: 13, color: '#475569', lineHeight: 1.65, marginTop: 7 }}>
              {thesis.investment_point}
            </p>
          )}
        </div>

        {/* Strengths */}
        {thesis.strengths?.length > 0 && (
          <div style={{ marginBottom: 16 }}>
            <p style={{ fontSize: 10, fontWeight: 700, color: '#16a34a', letterSpacing: 1, textTransform: 'uppercase', marginBottom: 8 }}>
              핵심 강점
            </p>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              {thesis.strengths.map((s, i) => (
                <div key={i} style={{ display: 'flex', gap: 8, alignItems: 'flex-start' }}>
                  <span style={{ fontSize: 13, color: '#16a34a', flexShrink: 0, marginTop: 1 }}>•</span>
                  <div>
                    <p style={{ fontSize: 13, color: '#1e293b', lineHeight: 1.5 }}>{s.point}</p>
                    {s.evidence && (
                      <p style={{ fontSize: 11, color: '#94a3b8', marginTop: 2, fontStyle: 'italic' }}>
                        &ldquo;{s.evidence}&rdquo;
                      </p>
                    )}
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Risks */}
        {thesis.risks?.length > 0 && (
          <div style={{ marginBottom: 4 }}>
            <p style={{ fontSize: 10, fontWeight: 700, color: '#dc2626', letterSpacing: 1, textTransform: 'uppercase', marginBottom: 8 }}>
              핵심 리스크
            </p>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              {thesis.risks.map((r, i) => (
                <div key={i} style={{ display: 'flex', gap: 8, alignItems: 'flex-start' }}>
                  <span style={{ fontSize: 13, color: '#dc2626', flexShrink: 0, marginTop: 1 }}>•</span>
                  <div>
                    <p style={{ fontSize: 13, color: '#1e293b', lineHeight: 1.5 }}>{r.point}</p>
                    {r.evidence && (
                      <p style={{ fontSize: 11, color: '#94a3b8', marginTop: 2, fontStyle: 'italic' }}>
                        &ldquo;{r.evidence}&rdquo;
                      </p>
                    )}
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Reason Graph (expandable) */}
        <ReasonGraph reasonGraph={thesis.reason_graph} />
      </div>
    </div>
  );
}

// ─── Peer Summary (별점 요약 + 전체 확장) ─────────────────────────────────────

const PEER_METRICS = [
  { key: 'roe',       label: 'ROE',       unit: '%', note: '수익성' },
  { key: 'op_margin', label: '영업이익률', unit: '%', note: '영업 효율' },
  { key: 'roa',       label: 'ROA',       unit: '%', note: '자산 효율' },
  { key: 'per',       label: 'PER',       unit: 'x', note: '저평가 기준' },
  { key: 'pbr',       label: 'PBR',       unit: 'x', note: '저평가 기준' },
];

function PeerBar({ label, displayValue, note, pct, sectorAvgDisplay }) {
  const topPct   = pct != null ? 100 - pct : null;
  const barColor = pct >= 70 ? '#22c55e' : pct >= 40 ? '#f59e0b' : '#ef4444';
  const txtColor = pct >= 70 ? '#15803d' : pct >= 40 ? '#b45309' : '#b91c1c';
  return (
    <div className="py-3 border-b border-gray-50 last:border-0">
      <div className="flex items-center gap-4">
        <div style={{ width: 68 }}>
          <p className="text-xs text-gray-400">{label}</p>
          <p className="text-base font-semibold text-gray-900 tabular-nums">{displayValue}</p>
        </div>
        {pct != null ? (
          <>
            <div className="flex-1 h-1.5 bg-gray-100 rounded-full overflow-hidden">
              <div style={{ width: `${pct}%`, height: '100%', background: barColor, borderRadius: 9999 }} />
            </div>
            <div style={{ width: 80, textAlign: 'right' }}>
              <p className="text-xs font-semibold tabular-nums" style={{ color: txtColor }}>상위 {topPct}%</p>
              <p className="text-xs text-gray-400">{note}</p>
            </div>
          </>
        ) : (
          <p className="text-xs text-gray-300 flex-1">비교 데이터 없음</p>
        )}
      </div>
      {sectorAvgDisplay && (
        <p className="text-xs text-gray-400 mt-0.5" style={{ paddingLeft: 72 }}>
          업종 중앙값 {sectorAvgDisplay}
        </p>
      )}
    </div>
  );
}

function PeerSummaryCard({ stageb }) {
  const [expanded, setExpanded] = useState(false);
  if (!stageb?.peer_group) return null;

  // 별점 보여줄 상위 3개 지표 (percentile 높은 순)
  const topThree = PEER_METRICS
    .filter(m => stageb[m.key]?.percentile != null)
    .sort((a, b) => (stageb[b.key]?.percentile || 0) - (stageb[a.key]?.percentile || 0))
    .slice(0, 3);

  return (
    <div className="bg-white rounded-xl shadow mb-5 p-6">
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-base font-semibold text-gray-800">Peer Benchmark</h2>
        <span className="text-xs text-gray-400">{stageb.peer_group} {stageb.peer_count}개사 대비</span>
      </div>

      {/* 별점 요약 카드 */}
      <div style={{ display: 'flex', gap: 10, marginBottom: 14 }}>
        {topThree.map(m => {
          const data = stageb[m.key];
          const s    = peerStars(data.percentile);
          const topPct = 100 - data.percentile;
          return (
            <div key={m.key} style={{
              flex: 1, textAlign: 'center',
              background: '#f8fafc', borderRadius: 10, padding: '10px 6px',
            }}>
              <p style={{ fontSize: 10, color: '#64748b', marginBottom: 3 }}>{m.label}</p>
              <p style={{ fontSize: 16, fontWeight: 700, color: '#0f172a', marginBottom: 2, tabularNums: 'tabular-nums' }}>
                {data.value}{m.unit}
              </p>
              <p style={{ fontSize: 13, letterSpacing: 2, color: s.color, marginBottom: 2 }}>{s.str}</p>
              <p style={{ fontSize: 10, color: '#94a3b8' }}>상위 {topPct}%</p>
            </div>
          );
        })}
      </div>

      {/* 전체 확장 */}
      <button
        onClick={() => setExpanded(e => !e)}
        style={{
          fontSize: 12, color: '#64748b', cursor: 'pointer',
          background: 'none', border: 'none', padding: '4px 0',
          display: 'flex', alignItems: 'center', gap: 5,
        }}
      >
        <span style={{ fontSize: 10 }}>{expanded ? '▼' : '▶'}</span>
        <span>전체 {PEER_METRICS.length}개 지표 보기</span>
      </button>

      {expanded && (
        <div style={{ marginTop: 8 }}>
          {PEER_METRICS.map(m => {
            const d = stageb[m.key];
            if (!d) return null;
            const avg = d.sector_avg != null ? `${d.sector_avg}${m.unit}` : null;
            return (
              <PeerBar
                key={m.key}
                label={m.label}
                displayValue={`${d.value}${m.unit}`}
                note={m.note}
                pct={d.percentile}
                sectorAvgDisplay={avg}
              />
            );
          })}
        </div>
      )}
    </div>
  );
}

// ─── Alpha Score Card (상세 점수 분해) ────────────────────────────────────────

const ALPHA_COMPONENTS = [
  { key: 'valuation', label: '밸류에이션', tip: { sub: '업종 내 상대 순위', items: ['ROE', 'ROA', 'PER', 'PBR', '영업이익률'] } },
  { key: 'growth',    label: '성장성',     tip: { sub: 'AI 사업보고서 추출', items: ['핵심 성장 Fact', '중요도 가중 합산'] } },
  { key: 'moat',      label: '경제적 해자', tip: { sub: '해자 유형 AI 분석', items: ['특허·브랜드', '네트워크', '진입장벽'] } },
  { key: 'safety',    label: '안전성',      tip: { sub: '재무 리스크 평가', items: ['부채·현금', '실적 안정성'] } },
];

function AlphaScoreCard({ alpha, sectorName }) {
  if (!alpha?.score) return null;
  const { score, rank_global, total_global, rank_sector, total_sector, breakdown } = alpha;
  const barColor = score >= 70 ? '#22c55e' : score >= 50 ? '#f59e0b' : '#ef4444';
  const [tooltip, setTooltip] = useState(null);

  return (
    <div
      style={{ background: 'linear-gradient(135deg, #0f172a 0%, #1e3a5f 100%)', color: '#fff' }}
      className="rounded-xl shadow mb-5 overflow-hidden"
    >
      <div className="px-6 pt-5 pb-4">
        <p style={{ fontSize: 10, fontWeight: 700, letterSpacing: 2, opacity: 0.4, marginBottom: 8 }}>
          SCORE BREAKDOWN
        </p>
        <div style={{ display: 'flex', gap: 24, alignItems: 'flex-start' }}>
          {/* 랭킹 */}
          <div style={{ flexShrink: 0, display: 'flex', flexDirection: 'column', gap: 10 }}>
            {rank_global != null && (
              <div>
                <div style={{ display: 'flex', alignItems: 'baseline', gap: 4 }}>
                  <span style={{ fontSize: 20, fontWeight: 800 }}>{rank_global}</span>
                  <span style={{ fontSize: 11, opacity: 0.35 }}>/ {total_global}</span>
                </div>
                <p style={{ fontSize: 10, opacity: 0.35, marginTop: 2 }}>전체 순위</p>
              </div>
            )}
            {rank_sector != null && sectorName && (
              <div>
                <div style={{ display: 'flex', alignItems: 'baseline', gap: 4 }}>
                  <span style={{ fontSize: 20, fontWeight: 800 }}>{rank_sector}</span>
                  <span style={{ fontSize: 11, opacity: 0.35 }}>/ {total_sector}</span>
                </div>
                <p style={{ fontSize: 10, opacity: 0.35, marginTop: 2 }}>{sectorName}</p>
              </div>
            )}
          </div>

          <div style={{ width: 1, background: 'rgba(255,255,255,0.08)', alignSelf: 'stretch' }} />

          {/* 구성 요소 바 */}
          <div style={{ flex: 1, display: 'flex', flexDirection: 'column', gap: 10 }}>
            {ALPHA_COMPONENTS.map(({ key, label, tip }) => {
              const val    = breakdown?.[key] ?? 0;
              const pct    = Math.round(val / 100 * 100);
              const fill   = barScoreColor(val);
              const isOpen = tooltip === key;
              return (
                <div key={key} style={{ display: 'flex', alignItems: 'center', gap: 10, position: 'relative' }}>
                  <span
                    style={{ fontSize: 11, opacity: 0.55, width: 70, flexShrink: 0, cursor: 'help', borderBottom: '1px dotted rgba(255,255,255,0.2)' }}
                    onMouseEnter={() => setTooltip(key)}
                    onMouseLeave={() => setTooltip(null)}
                  >
                    {label}
                    {isOpen && (
                      <div style={{
                        position: 'absolute', left: 0, bottom: '110%',
                        background: '#1e293b', border: '1px solid rgba(255,255,255,0.12)',
                        borderRadius: 8, padding: '10px 14px', zIndex: 50,
                        boxShadow: '0 4px 20px rgba(0,0,0,0.4)', minWidth: 150,
                      }}>
                        <p style={{ fontSize: 10, fontWeight: 700, color: fill, marginBottom: 4 }}>{label.toUpperCase()}</p>
                        <p style={{ fontSize: 9, color: 'rgba(255,255,255,0.35)', marginBottom: 6 }}>{tip.sub}</p>
                        {tip.items.map(t => (
                          <p key={t} style={{ fontSize: 10, color: 'rgba(255,255,255,0.6)', marginBottom: 2 }}>· {t}</p>
                        ))}
                      </div>
                    )}
                  </span>
                  <div style={{ flex: 1, height: 5, background: 'rgba(255,255,255,0.1)', borderRadius: 9999 }}>
                    <div style={{ width: `${pct}%`, height: '100%', background: fill, borderRadius: 9999 }} />
                  </div>
                  <span style={{ fontSize: 11, fontWeight: 600, width: 32, textAlign: 'right', color: fill }}>
                    {val}
                  </span>
                </div>
              );
            })}
          </div>
        </div>
      </div>
      <div style={{ height: 3, background: 'rgba(255,255,255,0.06)' }}>
        <div style={{ width: `${score}%`, height: '100%', background: barColor }} />
      </div>
    </div>
  );
}

// ─── DART 상세 근거 (기존 섹션) ───────────────────────────────────────────────

const IMP_DOT  = { 3: '#d97706', 2: '#9ca3af', 1: '#d1d5db' };
const IMP_STAR = { 3: '●●●', 2: '●●○', 1: '●○○' };

function FactRow({ f, dimmed = false }) {
  const txt   = factText(f);
  const quote = factQuote(f);
  const imp   = factImp(f);
  return (
    <div className={`py-2.5 border-b border-gray-50 last:border-0 flex gap-3 items-start ${dimmed ? 'opacity-60' : ''}`}>
      <div className="flex-1">
        <p className={`text-sm ${dimmed ? 'text-gray-400' : 'text-gray-800'}`}>{txt}</p>
        {quote && (
          <p className="text-xs text-gray-400 italic mt-0.5 pl-2 border-l-2 border-gray-200">&ldquo;{quote}&rdquo;</p>
        )}
      </div>
      <span className="text-xs tracking-widest shrink-0 mt-0.5" style={{ color: IMP_DOT[imp] }}>
        {IMP_STAR[imp]}
      </span>
    </div>
  );
}

function DartGrowthSection({ sections }) {
  const facts          = sections?.growth_drivers?.facts || [];
  const interpretation = sections?.growth_drivers?.interpretation;
  if (!facts.length && !interpretation) return null;

  return (
    <div className="bg-white rounded-xl shadow p-6 mb-5">
      <div className="flex items-center justify-between mb-3">
        <h2 className="text-base font-semibold text-gray-800">성장 근거 (DART 원문)</h2>
        <span className="text-xs text-gray-400">투자 권유 아님</span>
      </div>
      {interpretation && (
        <div className="mb-4 px-4 py-3 rounded-lg" style={{ background: '#f0f9ff', borderLeft: '3px solid #38bdf8' }}>
          <p className="text-xs font-semibold mb-1" style={{ color: '#0284c7' }}>Stage C 요약</p>
          <p className="text-sm text-gray-700 leading-relaxed">{interpretation}</p>
        </div>
      )}
      <div>{facts.map((f, i) => <FactRow key={i} f={f} />)}</div>
    </div>
  );
}

function RiskSection({ sections }) {
  const facts          = sections?.risks?.facts || [];
  const interpretation = sections?.risks?.interpretation;
  const neutralFacts   = sections?.risks?.neutral_facts || [];
  if (!facts.length && !interpretation) return null;

  return (
    <div className="bg-white rounded-xl shadow p-6 mb-5">
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-base font-semibold text-gray-800">리스크 근거 (DART 원문)</h2>
        <span className="text-xs text-gray-400">투자 권유 아님</span>
      </div>
      <div>{facts.map((f, i) => <FactRow key={i} f={f} />)}</div>
      {interpretation && (
        <div className="mt-4 pt-4 border-t border-gray-100">
          <p className="text-sm text-gray-700 leading-relaxed">{interpretation}</p>
        </div>
      )}
      {neutralFacts.length > 0 && (
        <div className="mt-4 pt-4 border-t border-gray-50">
          <p className="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-2">참고 사실</p>
          <div>{neutralFacts.map((f, i) => <FactRow key={i} f={f} dimmed />)}</div>
        </div>
      )}
    </div>
  );
}

function MoatSection({ sections }) {
  const facts          = sections?.moat?.facts || [];
  const interpretation = sections?.moat?.interpretation;
  if (!facts.length && !interpretation) return null;

  return (
    <div className="bg-white rounded-xl shadow p-6 mb-5">
      <h2 className="text-base font-semibold text-gray-800 mb-4">해자 근거 (DART 원문)</h2>
      <div>{facts.map((f, i) => <FactRow key={i} f={f} />)}</div>
      {interpretation && (
        <div className="mt-4 pt-4 border-t border-gray-100">
          <p className="text-sm text-gray-700 leading-relaxed">{interpretation}</p>
        </div>
      )}
    </div>
  );
}

// ─── 동종업체 비교표 ───────────────────────────────────────────────────────────

function NamedPeerTable({ stageb }) {
  const peers = stageb?.named_peers;
  if (!peers || peers.length === 0) return null;

  const fmtRoe = v => v != null ? v.toFixed(1) + '%' : '-';
  const fmtPer = v => (v != null && v <= 200) ? v.toFixed(1) + '배' : '-';
  const fmtPbr = v => v != null ? v.toFixed(2) + '배' : '-';
  const roeColor = v => {
    if (v == null) return 'text-gray-300';
    if (v >= 15)   return 'text-green-600';
    if (v >= 5)    return 'text-gray-700';
    return 'text-red-500';
  };

  return (
    <div className="bg-white rounded-xl shadow p-6 mb-5">
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-base font-semibold text-gray-800">동종업체 비교</h2>
        {stageb?.peer_group && (
          <span className="text-xs text-gray-400">{stageb.peer_group} · 시총 기준</span>
        )}
      </div>
      <div className="grid text-xs font-semibold text-gray-400 pb-2 border-b border-gray-100"
        style={{ gridTemplateColumns: '1fr 72px 68px 68px 68px' }}>
        <span>회사</span>
        <span className="text-right">시총</span>
        <span className="text-right">ROE</span>
        <span className="text-right">PER</span>
        <span className="text-right">PBR</span>
      </div>
      <div className="divide-y divide-gray-50">
        {peers.map(p => (
          <div key={p.code} className="grid items-center py-2.5 text-sm rounded"
            style={{
              gridTemplateColumns: '1fr 72px 68px 68px 68px',
              background: p.is_self ? '#eff6ff' : 'transparent',
              margin:     p.is_self ? '2px -8px' : '0',
              padding:    p.is_self ? '10px 8px' : '10px 0',
            }}>
            <span className={`font-medium truncate ${p.is_self ? 'text-blue-700' : 'text-gray-800'}`}>
              {p.is_self && <span className="mr-1 text-blue-400">▶</span>}
              {p.name}
            </span>
            <span className="text-right text-xs text-gray-500 tabular-nums">{toMarcapDisplay(p.marcap)}</span>
            <span className={`text-right tabular-nums font-medium ${roeColor(p.roe)}`}>{fmtRoe(p.roe)}</span>
            <span className="text-right tabular-nums text-gray-600">{fmtPer(p.per)}</span>
            <span className="text-right tabular-nums text-gray-500">{fmtPbr(p.pbr)}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

// ─── 기업 개요 ────────────────────────────────────────────────────────────────

const SIGNAL_DOT = { green: '🟢', yellow: '🟡', red: '🔴', gray: '⚪' };

function CompanyOverview({ report }) {
  if (!report?.overview) return null;
  return (
    <div className="bg-white rounded-xl shadow p-6 mb-5">
      <div className="flex items-center justify-between mb-3">
        <h2 className="text-base font-semibold text-gray-800">기업 개요</h2>
        <span className="text-xs text-gray-400">AI 자동 생성 · 투자 권유 아님</span>
      </div>
      <p className="text-sm text-gray-700 leading-relaxed">{report.overview}</p>
      {report.valuation_opinion && (
        <div className="mt-4 pt-4 border-t border-gray-100">
          <p className="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-2">밸류에이션 해석</p>
          <p className="text-sm text-gray-700 leading-relaxed">{report.valuation_opinion}</p>
        </div>
      )}
      {report.signals?.length > 0 && (
        <div className="mt-4 pt-4 border-t border-gray-100">
          <p className="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-2">판단 신호</p>
          <ul className="space-y-1.5">
            {report.signals.map((s, i) => (
              <li key={i} className="text-sm text-gray-700 flex gap-2 items-start">
                <span className="shrink-0">{SIGNAL_DOT[s.color] ?? '⚪'}</span>
                <span>{s.text}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

// ─── 재무 차트 ────────────────────────────────────────────────────────────────

function mkLegend(items) {
  return () => (
    <div style={{ display: 'flex', justifyContent: 'center', gap: 16, fontSize: 12, marginTop: 4, color: '#374151' }}>
      {items.map(([name, color]) => (
        <span key={name} style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
          <span style={{ width: 10, height: 10, background: color, borderRadius: 2, display: 'inline-block' }} />
          {name}
        </span>
      ))}
    </div>
  );
}

function Charts({ chartData }) {
  const { years, revenue, operating, net_income, assets, liabilities, equity } = chartData;

  const incomeData = years.map((y, i) => ({
    year: String(y),
    매출액:     revenue[i]    != null ? Math.round(revenue[i] / 1e8)    : null,
    영업이익:   operating[i]  != null ? Math.round(operating[i] / 1e8)  : null,
    당기순이익: net_income[i] != null ? Math.round(net_income[i] / 1e8) : null,
  }));

  const balanceData = years.map((y, i) => ({
    year: String(y),
    자산총계: assets[i]      != null ? Math.round(assets[i] / 1e8)      : null,
    부채총계: liabilities[i] != null ? Math.round(liabilities[i] / 1e8) : null,
    자본총계: equity[i]      != null ? Math.round(equity[i] / 1e8)      : null,
  }));

  return (
    <>
      <div className="bg-white rounded-xl shadow p-6 mb-5">
        <h2 className="text-base font-semibold text-gray-800 mb-4">손익 추이 (억 원)</h2>
        <ResponsiveContainer width="100%" height={260}>
          <BarChart data={incomeData} margin={{ top: 5, right: 10, left: 10, bottom: 5 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" />
            <XAxis dataKey="year" tick={{ fontSize: 12 }} />
            <YAxis tick={{ fontSize: 12 }} tickFormatter={v => v.toLocaleString()} />
            <Tooltip formatter={v => v != null ? v.toLocaleString() + '억' : '-'} />
            <Legend content={mkLegend([['매출액', '#93c5fd'], ['영업이익', '#34d399'], ['당기순이익', '#818cf8']])} />
            <Bar dataKey="매출액"     fill="#93c5fd" radius={[4, 4, 0, 0]} />
            <Bar dataKey="영업이익"   fill="#34d399" radius={[4, 4, 0, 0]} />
            <Bar dataKey="당기순이익" fill="#818cf8" radius={[4, 4, 0, 0]} />
          </BarChart>
        </ResponsiveContainer>
      </div>
      <div className="bg-white rounded-xl shadow p-6">
        <h2 className="text-base font-semibold text-gray-800 mb-4">재무상태 추이 (억 원)</h2>
        <ResponsiveContainer width="100%" height={260}>
          <BarChart data={balanceData} margin={{ top: 5, right: 10, left: 10, bottom: 5 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" />
            <XAxis dataKey="year" tick={{ fontSize: 12 }} />
            <YAxis tick={{ fontSize: 12 }} tickFormatter={v => v.toLocaleString()} />
            <Tooltip formatter={v => v != null ? v.toLocaleString() + '억' : '-'} />
            <Legend content={mkLegend([['자산총계', '#fbbf24'], ['부채총계', '#f87171'], ['자본총계', '#34d399']])} />
            <Bar dataKey="자산총계" fill="#fbbf24" radius={[4, 4, 0, 0]} />
            <Bar dataKey="부채총계" fill="#f87171" radius={[4, 4, 0, 0]} />
            <Bar dataKey="자본총계" fill="#34d399" radius={[4, 4, 0, 0]} />
          </BarChart>
        </ResponsiveContainer>
      </div>
    </>
  );
}

// ─── Main ─────────────────────────────────────────────────────────────────────

export default function StockDetail() {
  const { code }  = useParams();
  const router    = useRouter();
  const [data,          setData]          = useState(null);
  const [loading,       setLoading]       = useState(true);
  const [error,         setError]         = useState(null);
  const [report,        setReport]        = useState(null);
  const [reportLoading, setReportLoading] = useState(true);

  useEffect(() => {
    fetch(`http://localhost:8000/api/stock/${code}`)
      .then(res => { if (!res.ok) throw new Error('종목 없음'); return res.json(); })
      .then(d  => { setData(d);   setLoading(false); })
      .catch(e => { setError(e.message); setLoading(false); });
  }, [code]);

  useEffect(() => {
    fetch(`http://localhost:8000/api/stock/${code}/report`)
      .then(res => res.ok ? res.json() : null)
      .then(d  => { setReport(d); setReportLoading(false); })
      .catch(() => setReportLoading(false));
  }, [code]);

  if (loading) return (
    <main className="min-h-screen bg-gray-50 flex items-center justify-center">
      <p className="text-gray-400">불러오는 중...</p>
    </main>
  );
  if (error) return (
    <main className="min-h-screen bg-gray-50 flex items-center justify-center">
      <p className="text-red-500">종목을 찾을 수 없습니다</p>
    </main>
  );

  const stageb = report?.stage_b_sections || {};
  const stagec = report?.stage_c_sections || {};
  const thesis = report?.thesis_sections  || null;

  return (
    <main className="min-h-screen bg-gray-50 p-8">
      <div className="max-w-3xl mx-auto">

        <button
          onClick={() => router.push('/')}
          className="text-sm text-gray-400 hover:text-gray-700 mb-5 flex items-center gap-1 transition-colors"
        >
          ← 목록으로
        </button>

        {/* 1. Hero: 종목명 + 가격 */}
        <HeroSection data={data} stageb={stageb} />

        {/* 2. Investment Thesis — 메인 카드 (thesis 있을 때) */}
        {!reportLoading && thesis && (
          <InvestmentThesisCard thesis={thesis} alpha={report?.alpha_score} />
        )}

        {/* 3. Peer Benchmark — 별점 요약 + 확장 가능 전체 표 */}
        {!reportLoading && <PeerSummaryCard stageb={stageb} />}

        {/* 4. Alpha Score Detail — V/G/M/S 구성 요소 (thesis 없을 때는 더 두드러지게) */}
        {!reportLoading && (
          <AlphaScoreCard
            alpha={report?.alpha_score}
            sectorName={stageb?.peer_group}
          />
        )}

        {/* 5. DART 상세 근거 — 성장/리스크/해자 원문 */}
        {!reportLoading && <DartGrowthSection sections={stagec} />}
        {!reportLoading && <RiskSection       sections={stagec} />}
        {!reportLoading && <MoatSection       sections={stagec} />}

        {/* 6. 동종업체 비교표 */}
        <NamedPeerTable stageb={stageb} />

        {/* 7. 기업 개요 + 판단 신호 */}
        {!reportLoading && <CompanyOverview report={report} />}

        {/* 8. 재무 차트 */}
        <Charts chartData={data.chart_data} />

      </div>
    </main>
  );
}
