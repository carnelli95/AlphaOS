"use client";

import { useEffect, useState, useCallback, useRef } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";

// ─── Alpha Rankings ──────────────────────────────────────────────────────────

// 6단계 등급 체계
function scoreGrade(s) {
  if (s >= 90) return { label: 'Elite',      text: '#065f46', bg: '#d1fae5' };
  if (s >= 80) return { label: 'Strong Buy', text: '#166534', bg: '#bbf7d0' };
  if (s >= 70) return { label: 'Buy',        text: '#15803d', bg: '#dcfce7' };
  if (s >= 50) return { label: 'Hold',       text: '#92400e', bg: '#fef3c7' };
  if (s >= 30) return { label: 'Caution',    text: '#c2410c', bg: '#ffedd5' };
  return        { label: 'High Risk',        text: '#991b1b', bg: '#fee2e2' };
}

// V/G/M 고정색, S는 안전 수준 반영
function factorColor(key, val) {
  if (key === 'S') {
    if (val == null) return '#d1d5db';
    if (val >= 45) return '#22c55e';
    if (val >= 35) return '#f59e0b';
    return '#ef4444';
  }
  return { V: '#3b82f6', G: '#10b981', M: '#8b5cf6' }[key] || '#9ca3af';
}

function BreakdownBars({ breakdown }) {
  const bd = breakdown || {};
  return (
    <div className="flex flex-col gap-0.5 min-w-0">
      {[['V', bd.valuation], ['G', bd.growth], ['M', bd.moat], ['S', bd.safety]].map(([k, v]) => (
        <div key={k} className="flex items-center gap-1.5">
          <span className="font-mono text-gray-400 shrink-0" style={{ fontSize: 9, width: 8 }}>{k}</span>
          <div className="flex-1" style={{ height: 3, background: '#f3f4f6', borderRadius: 9999 }}>
            <div style={{ width: `${v ?? 0}%`, height: '100%', background: factorColor(k, v), borderRadius: 9999 }} />
          </div>
          <span className="tabular-nums text-gray-500 shrink-0 text-right" style={{ fontSize: 9, width: 20 }}>
            {v ?? '-'}
          </span>
        </div>
      ))}
    </div>
  );
}

const RANKING_COLS = '36px 1fr 80px 176px 72px';

function AlphaRankings() {
  const [data,    setData]    = useState({ stocks: [], sectors: [] });
  const [sector,  setSector]  = useState('');
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    const url = sector
      ? `http://localhost:8000/api/ranking?sector=${encodeURIComponent(sector)}&limit=20`
      : 'http://localhost:8000/api/ranking?limit=20';
    fetch(url)
      .then(r => r.json())
      .then(d => { setData(d); setLoading(false); })
      .catch(() => setLoading(false));
  }, [sector]);

  const { stocks, sectors } = data;

  return (
    <div className="bg-white rounded-xl shadow mb-6 overflow-hidden">
      {/* 헤더 */}
      <div className="px-6 py-4 border-b border-gray-100 flex items-center justify-between">
        <div>
          <h2 className="text-base font-bold text-gray-900">Alpha Rankings</h2>
          <p className="text-xs text-gray-400 mt-0.5">AlphaOS 종합 투자 점수 기준 · 700개 종목</p>
        </div>
        <Link href="#screener" className="text-xs text-blue-500 hover:text-blue-700">스크리너 →</Link>
      </div>

      {/* 업종 탭 */}
      <div className="flex gap-1 px-4 py-2.5 border-b border-gray-50 overflow-x-auto" style={{ scrollbarWidth: 'none' }}>
        {['', ...sectors].map(s => (
          <button
            key={s || '__all__'}
            onClick={() => setSector(s)}
            className="shrink-0 px-3 py-1 rounded-full text-xs font-medium transition-colors"
            style={sector === s
              ? { background: '#1e3a5f', color: '#fff' }
              : { background: '#f3f4f6', color: '#6b7280' }}
          >
            {s || '전체'}
          </button>
        ))}
      </div>

      {/* 테이블 */}
      {loading ? (
        <div className="py-8 text-center text-sm text-gray-400">불러오는 중...</div>
      ) : (
        <div>
          {/* 헤더 행 */}
          <div className="grid text-xs font-semibold text-gray-400 uppercase tracking-wide px-5 py-2 border-b border-gray-50"
               style={{ gridTemplateColumns: RANKING_COLS }}>
            <span>#</span>
            <span>종목</span>
            <span>업종</span>
            <span className="text-xs text-gray-400 normal-case tracking-normal">
              <span className="text-blue-400">V</span>al ·{' '}
              <span className="text-emerald-500">G</span>ro ·{' '}
              <span className="text-violet-500">M</span>oat ·{' '}
              <span className="text-gray-400">S</span>afe
            </span>
            <span className="text-right">Score</span>
          </div>

          {stocks.map(s => {
            const grade = scoreGrade(s.score);
            return (
              <Link
                key={s.code}
                href={`/stock/${s.code}`}
                className="grid items-center px-5 py-3 border-b border-gray-50 last:border-0 hover:bg-gray-50 transition-colors"
                style={{ gridTemplateColumns: RANKING_COLS }}
              >
                {/* 순위 */}
                <span className="text-xs font-bold text-gray-400 tabular-nums">
                  {sector ? s.rank_sector : s.rank_global}
                </span>

                {/* 종목명 */}
                <div className="min-w-0">
                  <p className="text-sm font-semibold text-gray-900 truncate">{s.name}</p>
                  <p className="text-xs text-gray-400 font-mono">{s.code}</p>
                </div>

                {/* 업종 */}
                <span className="text-xs text-gray-500 truncate">{s.sector || '-'}</span>

                {/* V/G/M/S breakdown bars */}
                <BreakdownBars breakdown={s.breakdown} />

                {/* 점수 + 등급 */}
                <div className="text-right">
                  <div className="text-base font-bold tabular-nums leading-tight" style={{ color: grade.text }}>
                    {s.score}
                  </div>
                  <div className="inline-block text-center font-semibold rounded mt-0.5"
                       style={{ color: grade.text, background: grade.bg, fontSize: 9, padding: '1px 5px' }}>
                    {grade.label}
                  </div>
                </div>
              </Link>
            );
          })}
        </div>
      )}
    </div>
  );
}

const DEFAULT_FILTERS = {
  pbr_min: "",
  pbr_max: "",
  roe_min: "",
  roe_max: "",
  per_max: "",
  limit: 100,
};

// 지표별 좋음/보통/주의 색상 톤
// NOTE: 현재는 일반적인 가치투자 통념 기준의 "절대값" 임계값 (MVP 단계)
// TODO: 배포 후 실사용 데이터가 쌓이면 코스피 종목 전체 분포 기준
//       "퍼센타일 기준"으로 전환 (상위 30% 초록 / 중간 40% 회색 / 하위 30% 주황)
//       → 시장 전체 PER/PBR 수준이 바뀌어도 색상이 항상 상대적 의미를 유지하도록
function pbrTone(v) {
  if (v == null) return "bg-gray-100 text-gray-600";
  if (v <= 1) return "bg-green-100 text-green-700";
  if (v <= 2) return "bg-gray-100 text-gray-600";
  return "bg-orange-50 text-orange-700";
}

function roeTone(v) {
  if (v == null) return "bg-gray-100 text-gray-600";
  if (v >= 15) return "bg-green-100 text-green-700";
  if (v >= 5) return "bg-gray-100 text-gray-600";
  return "bg-orange-50 text-orange-700";
}

function perTone(v) {
  if (v == null) return "bg-gray-100 text-gray-600";
  if (v <= 10) return "bg-green-100 text-green-700";
  if (v <= 20) return "bg-gray-100 text-gray-600";
  return "bg-orange-50 text-orange-700";
}

function average(arr, key) {
  const valid = arr.map((s) => s[key]).filter((v) => v != null && !isNaN(v));
  if (valid.length === 0) return null;
  return valid.reduce((sum, v) => sum + Number(v), 0) / valid.length;
}


// 현재 결과셋 평균 대비 위/아래 — 순수 산술 비교 (해석/설명 아님, UI 신호 영역)
function vsAverage(value, avg, lowerIsBetter) {
  if (value == null || avg == null) return null;
  const diff = value - avg;
  if (Math.abs(diff) < avg * 0.02) return "≈"; // 거의 동일
  const isAbove = diff > 0;
  const isBetter = lowerIsBetter ? !isAbove : isAbove;
  return isBetter ? "▲" : "▼";
}

function SearchBar() {
  const router = useRouter();
  const [query, setQuery] = useState("");
  const [results, setResults] = useState([]);
  const [open, setOpen] = useState(false);
  const debounceRef = useRef(null);
  const wrapperRef = useRef(null);

  useEffect(() => {
    if (debounceRef.current) clearTimeout(debounceRef.current);
    if (!query.trim()) { setResults([]); setOpen(false); return; }
    debounceRef.current = setTimeout(() => {
      fetch(`http://localhost:8000/api/search?q=${encodeURIComponent(query)}`)
        .then(r => r.json())
        .then(d => { setResults(d); setOpen(d.length > 0); })
        .catch(() => {});
    }, 250);
    return () => clearTimeout(debounceRef.current);
  }, [query]);

  useEffect(() => {
    function onOutsideClick(e) {
      if (wrapperRef.current && !wrapperRef.current.contains(e.target)) setOpen(false);
    }
    document.addEventListener("mousedown", onOutsideClick);
    return () => document.removeEventListener("mousedown", onOutsideClick);
  }, []);

  function handleSelect(code) {
    setQuery("");
    setOpen(false);
    router.push(`/stock/${code}`);
  }

  return (
    <div ref={wrapperRef} className="relative mb-6">
      <input
        type="text"
        value={query}
        onChange={e => setQuery(e.target.value)}
        onFocus={() => results.length > 0 && setOpen(true)}
        onKeyDown={e => e.key === "Escape" && setOpen(false)}
        placeholder="종목명으로 검색 (예: 삼성전자)"
        className="w-full bg-white border border-gray-200 rounded-xl shadow px-4 py-3 text-sm text-gray-900 placeholder:text-gray-400 focus:outline-none focus:ring-2 focus:ring-blue-500"
      />
      {open && (
        <ul className="absolute z-10 w-full mt-1 bg-white border border-gray-200 rounded-xl shadow-lg overflow-hidden">
          {results.map(r => (
            <li key={r.code}>
              <button
                className="w-full text-left px-4 py-2.5 text-sm hover:bg-gray-50 flex justify-between items-center"
                onMouseDown={() => handleSelect(r.code)}
              >
                <span className="font-semibold text-gray-900">{r.name}</span>
                <span className="text-xs text-gray-400 font-mono">{r.code}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export default function Home() {
  const [stocks, setStocks] = useState([]);
  const [loading, setLoading] = useState(true);
  const [filters, setFilters] = useState(DEFAULT_FILTERS);
  const debounceRef = useRef(null);

  const fetchStocks = useCallback((f) => {
    setLoading(true);

    const params = new URLSearchParams();
    if (f.pbr_min !== "") params.set("pbr_min", f.pbr_min);
    if (f.pbr_max !== "") params.set("pbr_max", f.pbr_max);
    if (f.roe_min !== "") params.set("roe_min", f.roe_min);
    if (f.roe_max !== "") params.set("roe_max", f.roe_max);
    if (f.per_max !== "") params.set("per_max", f.per_max);
    params.set("limit", f.limit);

    fetch(`http://localhost:8000/api/screener?${params.toString()}`)
      .then((res) => res.json())
      .then((data) => {
        setStocks(data);
        setLoading(false);
      })
      .catch(() => setLoading(false));
  }, []);

  // 최초 로드
  useEffect(() => {
    fetchStocks(DEFAULT_FILTERS);
  }, [fetchStocks]);

  // 필터 변경 시 자동 반영 (Finviz 스타일: Apply 버튼 없이 즉시 반영)
  // limit(select)은 디바운스 없이 즉시, 숫자 입력은 300ms 디바운스로 타이핑 중 과도한 요청 방지
  useEffect(() => {
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => {
      fetchStocks(filters);
    }, 300);
    return () => clearTimeout(debounceRef.current);
  }, [filters, fetchStocks]);

  const handleReset = () => {
    setFilters(DEFAULT_FILTERS);
  };

  const handleChange = (e) => {
    const { name, value } = e.target;
    setFilters((prev) => ({ ...prev, [name]: value }));
  };

  const hasActiveFilter =
    filters.pbr_min !== "" || filters.pbr_max !== "" || filters.per_max !== "" ||
    filters.roe_min !== "" || filters.roe_max !== "";

  const avgPbr = average(stocks, "pbr");
  const avgPer = average(stocks, "per");
  const avgRoe = average(stocks, "roe");

  // 셋 중 하나라도 green이면 "긍정 신호", 셋 다 orange는 아니지만 orange가 하나라도 있으면 "주의 신호"로 집계
  const signalCounts = stocks.reduce(
    (acc, s) => {
      const tones = [pbrTone(s.pbr), perTone(s.per), roeTone(s.roe)];
      const hasGreen = tones.some((t) => t.includes("green"));
      const hasOrange = tones.some((t) => t.includes("orange"));
      if (hasGreen && !hasOrange) acc.positive++;
      else if (hasOrange) acc.caution++;
      return acc;
    },
    { positive: 0, caution: 0 }
  );

  return (
    <main className="min-h-screen bg-gray-50 p-8">
      <div className="max-w-4xl mx-auto">
        <h1 className="text-3xl font-bold text-gray-900 mb-2">AlphaOS</h1>
        <p className="text-gray-600 mb-6">코스피 700개 종목 · AI 기반 투자 분석</p>

        <SearchBar />

        {/* Alpha Rankings — 핵심 섹션 */}
        <AlphaRankings />

        {/* 기존 스크리너 */}
        <div id="screener">

        {/* 필터 패널 */}
        <div className="bg-white rounded-xl shadow p-5 mb-6">
          <h2 className="text-sm font-semibold text-gray-600 uppercase tracking-wide mb-4">
            퀀트 스크리너 (PBR / ROE / PER 필터)
          </h2>

          <div className="grid grid-cols-2 sm:grid-cols-3 gap-4 mb-4">
            {/* PBR */}
            <div>
              <label className="block text-xs text-gray-600 mb-1">
                PBR 최소
              </label>
              <input
                type="number"
                name="pbr_min"
                value={filters.pbr_min}
                onChange={handleChange}
                placeholder="예: 0.3"
                step="0.1"
                min="0"
                className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm text-gray-900 placeholder:text-gray-400 focus:outline-none focus:ring-2 focus:ring-blue-500"
              />
            </div>
            <div>
              <label className="block text-xs text-gray-600 mb-1">
                PBR 최대
              </label>
              <input
                type="number"
                name="pbr_max"
                value={filters.pbr_max}
                onChange={handleChange}
                placeholder="예: 1.5"
                step="0.1"
                min="0"
                className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm text-gray-900 placeholder:text-gray-400 focus:outline-none focus:ring-2 focus:ring-blue-500"
              />
            </div>

            {/* PER */}
            <div>
              <label className="block text-xs text-gray-600 mb-1">
                PER 최대
              </label>
              <input
                type="number"
                name="per_max"
                value={filters.per_max}
                onChange={handleChange}
                placeholder="예: 15"
                step="1"
                min="0"
                className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm text-gray-900 placeholder:text-gray-400 focus:outline-none focus:ring-2 focus:ring-blue-500"
              />
            </div>

            {/* ROE */}
            <div>
              <label className="block text-xs text-gray-600 mb-1">
                ROE 최소 (%)
              </label>
              <input
                type="number"
                name="roe_min"
                value={filters.roe_min}
                onChange={handleChange}
                placeholder="예: 10"
                step="1"
                min="0"
                className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm text-gray-900 placeholder:text-gray-400 focus:outline-none focus:ring-2 focus:ring-blue-500"
              />
            </div>
            <div>
              <label className="block text-xs text-gray-600 mb-1">
                ROE 최대 (%)
              </label>
              <input
                type="number"
                name="roe_max"
                value={filters.roe_max}
                onChange={handleChange}
                placeholder="예: 50"
                step="1"
                min="0"
                className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm text-gray-900 placeholder:text-gray-400 focus:outline-none focus:ring-2 focus:ring-blue-500"
              />
            </div>

            {/* 결과 개수 */}
            <div>
              <label className="block text-xs text-gray-600 mb-1">
                표시 개수
              </label>
              <select
                name="limit"
                value={filters.limit}
                onChange={handleChange}
                className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm text-gray-900 placeholder:text-gray-400 focus:outline-none focus:ring-2 focus:ring-blue-500"
              >
                <option value={20}>20개</option>
                <option value={50}>50개</option>
                <option value={100}>100개</option>
                <option value={200}>200개</option>
              </select>
            </div>
          </div>

          {/* 버튼 — Apply 없음, 입력 즉시 반영(디바운스). 초기화만 명시적 액션 */}
          <div className="flex items-center gap-3">
            <button
              onClick={handleReset}
              disabled={!hasActiveFilter}
              className="px-5 py-2 rounded-lg text-sm font-medium text-gray-600 hover:bg-gray-100 transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
            >
              초기화
            </button>
            <span className="text-xs text-gray-400">입력 즉시 결과에 반영됩니다</span>
          </div>
        </div>

        {/* SUMMARY STRIP — 사용자가 결과를 "해석"하지 않고 바로 이해하게 만드는 핵심 영역 */}
        <div className="bg-white rounded-xl shadow px-5 py-4 mb-4">
          <div className="flex flex-wrap items-baseline justify-between gap-3">
            <div className="flex items-baseline gap-2">
              <span className="text-2xl font-bold text-gray-900">
                {loading ? "···" : stocks.length}
              </span>
              <span className="text-base font-semibold text-gray-900">개 종목</span>
              <span className="text-sm text-gray-500 ml-1">
                {hasActiveFilter ? (
                  <>
                    {filters.pbr_min !== "" || filters.pbr_max !== "" ? (
                      <span className="font-medium text-gray-700">
                        PBR {filters.pbr_min !== "" && `≥${filters.pbr_min}`}
                        {filters.pbr_min !== "" && filters.pbr_max !== "" && " "}
                        {filters.pbr_max !== "" && `≤${filters.pbr_max}`}
                      </span>
                    ) : null}
                    {filters.per_max !== "" ? (
                      <span className="font-medium text-gray-700">
                        {(filters.pbr_min !== "" || filters.pbr_max !== "") && "  ·  "}
                        PER ≤{filters.per_max}
                      </span>
                    ) : null}
                    {filters.roe_min !== "" || filters.roe_max !== "" ? (
                      <span className="font-medium text-gray-700">
                        {(filters.pbr_min !== "" || filters.pbr_max !== "" || filters.per_max !== "") && "  ·  "}
                        ROE {filters.roe_min !== "" && `≥${filters.roe_min}%`}
                        {filters.roe_min !== "" && filters.roe_max !== "" && " "}
                        {filters.roe_max !== "" && `≤${filters.roe_max}%`}
                      </span>
                    ) : null}
                    {" 조건"}
                  </>
                ) : (
                  "전체 종목 (필터 없음)"
                )}
              </span>
            </div>

            {!loading && stocks.length > 0 && (
              <div className="flex gap-4 text-sm">
                <span className="text-gray-500">
                  평균 PBR <span className="font-bold text-gray-900">{avgPbr?.toFixed(2) ?? "-"}</span>
                </span>
                <span className="text-gray-500">
                  평균 PER <span className="font-bold text-gray-900">{avgPer?.toFixed(1) ?? "-"}</span>
                </span>
                <span className="text-gray-500">
                  평균 ROE <span className="font-bold text-gray-900">{avgRoe?.toFixed(1) ?? "-"}%</span>
                </span>
                <span className="text-gray-300">|</span>
                <span className="text-green-700 font-medium">
                  긍정 신호 {signalCounts.positive}
                </span>
                <span className="text-orange-700 font-medium">
                  주의 신호 {signalCounts.caution}
                </span>
              </div>
            )}
          </div>
          <p className="text-xs text-gray-400 mt-2">
            순위는 PBR·ROE 상대 순위 기준이며, 색상 배지는 절대 기준입니다 (1위라도 회색/주황 배지가 나올 수 있어요)
          </p>
        </div>

        {/* 테이블 */}
        {loading ? (
          <div className="text-center py-20 text-gray-500">
            데이터 불러오는 중...
          </div>
        ) : stocks.length === 0 ? (
          <div className="text-center py-20 text-gray-500">
            <p className="text-lg mb-1">조건에 맞는 종목이 없습니다</p>
            <p className="text-sm">필터 범위를 넓혀보세요</p>
          </div>
        ) : (
          <div className="bg-white rounded-xl shadow overflow-hidden">
            <table className="w-full">
              <thead>
                <tr className="bg-gray-900 text-white">
                  <th className="p-3 text-left text-xs font-semibold uppercase tracking-wide">
                    <span title="PBR 낮을수록 + ROE 높을수록 우선 (상대 순위). 색상 배지는 절대 기준이라 1위라도 회색/주황일 수 있습니다.">
                      순위 ⓘ
                    </span>
                  </th>
                  <th className="p-3 text-left text-xs font-semibold uppercase tracking-wide">종목명</th>
                  <th className="p-3 text-right text-xs font-semibold uppercase tracking-wide">ROE</th>
                  <th className="p-3 text-right text-xs font-semibold uppercase tracking-wide">PER</th>
                  <th className="p-3 text-right text-xs font-semibold uppercase tracking-wide">PBR</th>
                  <th className="p-3 text-left text-xs font-semibold uppercase tracking-wide text-gray-400">코드</th>
                </tr>
              </thead>
              <tbody>
                {stocks.map((stock, i) => (
                  <tr
                    key={stock.code}
                    className="border-t hover:bg-gray-50 transition-colors"
                  >
                    <td className="p-3 text-gray-900 font-bold">{i + 1}</td>
                    <td className="p-3 font-semibold text-gray-900">
                      <Link
                        href={`/stock/${stock.code}`}
                        className="hover:text-blue-600 hover:underline transition-colors cursor-pointer"
                      >
                        {stock.name}
                      </Link>
                    </td>
                    <td className="p-3 text-right">
                      <span className={`${roeTone(stock.roe)} px-2 py-0.5 rounded text-sm font-bold`}>
                        {stock.roe}%
                      </span>
                      {avgRoe != null && (
                        <span className="ml-1 text-xs text-gray-400" title={`결과셋 평균 ROE ${avgRoe.toFixed(1)}%`}>
                          {vsAverage(stock.roe, avgRoe, false)}
                        </span>
                      )}
                    </td>
                    <td className="p-3 text-right">
                      <span className={`${perTone(stock.per)} px-2 py-0.5 rounded text-sm font-bold`}>
                        {stock.per ?? "-"}
                      </span>
                    </td>
                    <td className="p-3 text-right">
                      <span className={`${pbrTone(stock.pbr)} px-2 py-0.5 rounded text-sm font-bold`}>
                        {stock.pbr}
                      </span>
                    </td>
                    <td className="p-3 text-gray-400 font-mono text-xs">{stock.code}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        </div>{/* end #screener */}
      </div>
    </main>
  );
}
