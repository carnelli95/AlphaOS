"""
Alpha Score IC 계산 — alpha snapshot → forward return → Spearman IC.

QA 품질 IC(qa_ic.py)와 별도 트랙. 전체 유니버스 단면(cross-section) IC.

사전 조건:
  python pipeline/alpha_snapshot.py --ref-date YYYY-MM-DD

사용:
  python pipeline/alpha_ic.py --snapshot alpha_snapshot_20260715.csv --days 5 20 60
  python pipeline/alpha_ic.py --rolling 2026-07-01 2026-10-31 --step 7 --days 5 20 60

출력 (윈도우별):
  - Spearman IC (score / 구성요소별) + p-value
  - 섹터 중립 IC (섹터 내 순위 기준)
  - 5분위 평균 수익률 (단조성 확인)
  - Top10% − Bottom10% Long-Short spread
"""

from __future__ import annotations

import argparse
import csv
import io
import os
import sys
from collections import defaultdict
from datetime import date, timedelta
from statistics import mean

from scipy import stats

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(__file__))

from dotenv import load_dotenv
load_dotenv(override=True)

from generate_ai_report import get_engine
from sqlalchemy import text

SNAP_DIR = os.path.join(os.path.dirname(__file__), "..", "qa_snapshots")

SIGNAL_COLS = ["score", "valuation", "safety", "growth", "moat"]
MIN_SECTOR_N = 5


def _to_float(v: str | None) -> float | None:
    return float(v) if v not in (None, "") else None


def _forward_prices(conn, t0_date: date, days: int) -> dict[str, tuple[float, str]]:
    """t0_date + days(달력일) 이후 첫 종가 (종목별 1건)."""
    rows = conn.execute(text("""
        SELECT DISTINCT ON (code) code, date, close
        FROM price_history
        WHERE date >= :d
        ORDER BY code, date ASC
    """), {"d": t0_date + timedelta(days=days)})
    return {code: (float(close), str(d)) for code, d, close in rows}


def _sector_neutral_ic(records: list[dict]) -> tuple[float, int] | None:
    """섹터별 Spearman IC의 종목수 가중 평균."""
    by_sector = defaultdict(list)
    for r in records:
        by_sector[r["sector"] or "기타"].append(r)
    weighted, n_total = 0.0, 0
    for recs in by_sector.values():
        if len(recs) < MIN_SECTOR_N:
            continue
        ic, _ = stats.spearmanr([r["score"] for r in recs], [r["fwd_return"] for r in recs])
        if ic != ic:  # NaN (분산 없음)
            continue
        weighted += ic * len(recs)
        n_total += len(recs)
    return (weighted / n_total, n_total) if n_total else None


def compute_ic(window_days: int, snap_rows: list[dict], engine, snap_base: str) -> dict | None:
    print(f"\n{'='*64}")
    print(f"Forward Return Window: {window_days}D")
    print(f"{'='*64}")

    valid = [r for r in snap_rows if r.get("t0_price") and r.get("score")]
    t0_dates = {r["t0_price_date"] for r in valid}
    # 대부분 동일 t0 — 날짜별로 한 번씩 조회
    fwd_by_t0 = {}
    with engine.connect() as conn:
        for d in t0_dates:
            fwd_by_t0[d] = _forward_prices(conn, date.fromisoformat(d), window_days)

    records, no_future = [], 0
    for row in valid:
        fwd = fwd_by_t0[row["t0_price_date"]].get(row["code"])
        if fwd is None:
            no_future += 1
            continue
        t0_price = float(row["t0_price"])
        t1_price, t1_date = fwd
        if t0_price <= 0:
            continue
        records.append({
            "code": row["code"], "name": row["name"], "sector": row["sector"],
            **{c: _to_float(row.get(c)) for c in SIGNAL_COLS},
            "t0_price": t0_price, "t0_date": row["t0_price_date"],
            "t1_price": t1_price, "t1_date": t1_date,
            "fwd_return": round((t1_price - t0_price) / t0_price * 100, 2),
        })

    print(f"Universe {len(snap_rows)} / 사용 {len(records)} / "
          f"제외 {len(snap_rows) - len(records)} (미래가격 없음 {no_future})")
    if len(records) < 30:
        print("  유효 종목 30건 미만 — IC 계산 보류")
        return None

    rets = [r["fwd_return"] for r in records]
    print(f"수익률 분포: 평균 {mean(rets):+.2f}%  중앙값 {sorted(rets)[len(rets)//2]:+.2f}%")

    print(f"\n[Spearman IC]")
    summary = {"window": window_days, "n": len(records)}
    for col in SIGNAL_COLS:
        pairs = [(r[col], r["fwd_return"]) for r in records if r[col] is not None]
        if len(set(v for v, _ in pairs)) < 2:
            print(f"  {col:<10} n/a (분산 없음)")
            continue
        ic, p = stats.spearmanr([v for v, _ in pairs], [x for _, x in pairs])
        summary[f"ic_{col}"] = round(ic, 4)
        mark = " ←" if col == "score" else ""
        print(f"  {col:<10} IC={ic:+.3f}  p={p:.3f}  n={len(pairs)}{mark}")

    sn = _sector_neutral_ic(records)
    if sn:
        summary["ic_sector_neutral"] = round(sn[0], 4)
        print(f"  섹터중립   IC={sn[0]:+.3f}  (섹터 n≥{MIN_SECTOR_N}, 종목 {sn[1]})")

    ranked = sorted(records, key=lambda r: -r["score"])
    n = len(ranked)
    print(f"\n[5분위 평균 수익률]  (Q1 = 고득점)")
    for q in range(5):
        chunk = ranked[q * n // 5:(q + 1) * n // 5]
        s = [r["score"] for r in chunk]
        print(f"  Q{q+1}  score {min(s):>3.0f}~{max(s):>3.0f}  n={len(chunk):>3}  "
              f"평균 {mean(r['fwd_return'] for r in chunk):+6.2f}%")

    k = max(1, n // 10)
    top = mean(r["fwd_return"] for r in ranked[:k])
    bot = mean(r["fwd_return"] for r in ranked[-k:])
    summary.update(top10=round(top, 2), bot10=round(bot, 2), ls=round(top - bot, 2))
    print(f"\n[Long-Short]  Top10% {top:+.2f}%  Bottom10% {bot:+.2f}%  Spread {top - bot:+.2f}%  (각 {k}종목)")

    out_path = os.path.join(SNAP_DIR, f"alpha_ic_{snap_base}_{window_days}d.csv")
    with open(out_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=list(records[0].keys()))
        writer.writeheader()
        writer.writerows(records)
    print(f"\n결과 저장: {out_path}")
    return summary


def _load_alpha(conn) -> list[dict]:
    rows = conn.execute(text("""
        SELECT r.stock_code, s.sector, r.alpha_score
        FROM ai_reports r JOIN stocks s ON s.code = r.stock_code
        WHERE r.alpha_score IS NOT NULL
    """)).fetchall()
    out = []
    for code, sector, a in rows:
        bd = a.get("breakdown", {})
        out.append({"code": code, "sector": sector, "score": a.get("score"),
                    "valuation": bd.get("valuation"), "safety": bd.get("safety"),
                    "growth": bd.get("growth"), "moat": bd.get("moat")})
    return out


def _ic_at(conn, alpha: list[dict], t0: date, days: int) -> dict | None:
    """기준일 t0(그 이하 마지막 거래일)의 window IC 한 건."""
    t0_trade = conn.execute(text("SELECT max(date) FROM price_history WHERE date <= :d"),
                            {"d": t0}).scalar()
    p0 = {c: float(p) for c, p in conn.execute(text("""
        SELECT DISTINCT ON (code) code, close FROM price_history
        WHERE date <= :d ORDER BY code, date DESC
    """), {"d": t0_trade})}
    p1 = _forward_prices(conn, t0_trade, days)
    recs = []
    for a in alpha:
        if a["code"] in p0 and a["code"] in p1 and p0[a["code"]] > 0:
            recs.append({**a, "fwd_return": (p1[a["code"]][0] / p0[a["code"]] - 1) * 100})
    if len(recs) < 30:
        return None
    res = {"t0": str(t0_trade), "n": len(recs)}
    for col in ("score", "valuation", "safety"):
        res[col] = stats.spearmanr([r[col] for r in recs], [r["fwd_return"] for r in recs])[0]
    sn = _sector_neutral_ic(recs)
    res["sector"] = sn[0] if sn else float("nan")
    ranked = sorted(recs, key=lambda r: -r["score"])
    k = max(1, len(ranked) // 10)
    res["ls"] = mean(r["fwd_return"] for r in ranked[:k]) - mean(r["fwd_return"] for r in ranked[-k:])
    return res


def rolling(start: date, end: date, step: int, days_list: list[int]) -> None:
    """기준일을 step 간격으로 옮기며 IC 시계열 → 평균/표준편차/t-stat.

    주의: alpha_score는 현재 값 1개를 모든 기준일에 적용 — 기간 내 재무 연도가
    바뀌지 않았을 때만 유효. 또한 window > step이면 수익률 구간이 겹쳐 IC들이
    독립이 아니므로 naive t-stat은 과대평가된다 (비중첩 t도 함께 표시).
    """
    engine = get_engine()
    with engine.connect() as conn:
        alpha = _load_alpha(conn)
        last_price = conn.execute(text("SELECT max(date) FROM price_history")).scalar()
        print(f"Rolling IC  {start} ~ {end}, step {step}일, 종목 {len(alpha)}, 가격 최종일 {last_price}")

        t0s = []
        d = start
        while d <= end:
            t0s.append(d)
            d += timedelta(days=step)

        all_rows = []
        for days in days_list:
            usable = [t for t in t0s if t + timedelta(days=days) <= last_price]
            results = [r for t in usable if (r := _ic_at(conn, alpha, t, days))]
            print(f"\n{'='*72}\n{days}D window — 기준일 {len(results)}개\n{'='*72}")
            print(f"  {'t0':<11} {'n':>4} {'IC':>7} {'IC(V)':>7} {'IC(S)':>7} {'섹터중립':>8} {'L-S':>7}")
            for r in results:
                print(f"  {r['t0']:<11} {r['n']:>4} {r['score']:>+7.3f} {r['valuation']:>+7.3f} "
                      f"{r['safety']:>+7.3f} {r['sector']:>+8.3f} {r['ls']:>+6.2f}%")
                all_rows.append({"window": days, **r})
            if len(results) < 2:
                continue

            def _stat(vals: list[float]) -> str:
                m = mean(vals)
                sd = stats.tstd(vals) if len(vals) > 1 else float("nan")
                t = m / (sd / len(vals) ** 0.5) if sd and sd == sd else float("nan")
                return f"평균 {m:+.3f}  sd {sd:.3f}  IR {m / sd if sd else float('nan'):+.2f}  t {t:+.2f}"

            print(f"\n  [IC 총점]   {_stat([r['score'] for r in results])}  "
                  f"양(+) 비율 {sum(r['score'] > 0 for r in results)}/{len(results)}")
            print(f"  [IC V]      {_stat([r['valuation'] for r in results])}")
            print(f"  [IC S]      {_stat([r['safety'] for r in results])}")
            print(f"  [섹터중립]  {_stat([r['sector'] for r in results if r['sector'] == r['sector']])}")
            print(f"  [L-S]       평균 {mean(r['ls'] for r in results):+.2f}%p")
            stride = -(-days // step)  # 겹치지 않도록 건너뛸 기준일 수
            if stride > 1:
                nov = [r["score"] for r in results[::stride]]
                if len(nov) >= 2:
                    print(f"  [비중첩 {len(nov)}개] IC 총점 {_stat(nov)}")
                else:
                    print(f"  [비중첩] 기준일 {len(nov)}개 — t-stat 산출 불가")

    out_path = os.path.join(SNAP_DIR, f"alpha_ic_rolling_{start:%Y%m%d}_{end:%Y%m%d}_s{step}.csv")
    os.makedirs(SNAP_DIR, exist_ok=True)
    with open(out_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=list(all_rows[0].keys()))
        writer.writeheader()
        writer.writerows(all_rows)
    print(f"\n결과 저장: {out_path}")


def main(snapshot_name: str, days_list: list[int]) -> None:
    snap_path = os.path.join(SNAP_DIR, snapshot_name)
    if not os.path.exists(snap_path):
        print(f"오류: {snap_path} 없음")
        sys.exit(1)

    with open(snap_path, encoding="utf-8-sig") as f:
        snap_rows = list(csv.DictReader(f))

    snap_base = os.path.splitext(snapshot_name)[0]
    print(f"Snapshot: {snapshot_name}  ({len(snap_rows)}종목)")
    engine = get_engine()
    summaries = [s for d in days_list if (s := compute_ic(d, snap_rows, engine, snap_base))]

    if len(summaries) > 1:
        print(f"\n{'='*64}\n[요약]")
        print(f"  {'윈도우':<6} {'n':>4} {'IC(score)':>10} {'IC(V)':>7} {'IC(S)':>7} {'섹터중립':>8} {'L-S':>7}")
        for s in summaries:
            print(f"  {str(s['window'])+'D':<6} {s['n']:>4} {s.get('ic_score', float('nan')):>+10.3f} "
                  f"{s.get('ic_valuation', float('nan')):>+7.3f} {s.get('ic_safety', float('nan')):>+7.3f} "
                  f"{s.get('ic_sector_neutral', float('nan')):>+8.3f} {s['ls']:>+6.2f}%")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot", help="qa_snapshots/ 내 파일명 (단일 기준일 모드)")
    parser.add_argument("--rolling", nargs=2, metavar=("START", "END"),
                        help="롤링 모드: 기준일 범위 YYYY-MM-DD YYYY-MM-DD (현재 alpha_score 사용)")
    parser.add_argument("--step", type=int, default=7, help="롤링 기준일 간격 (달력일, 기본 7)")
    parser.add_argument("--days", type=int, nargs="+", default=[5, 20, 60],
                        help="Forward return 윈도우 (달력일, 기본 5 20 60)")
    args = parser.parse_args()
    if args.rolling:
        rolling(date.fromisoformat(args.rolling[0]), date.fromisoformat(args.rolling[1]),
                args.step, args.days)
    elif args.snapshot:
        main(args.snapshot, args.days)
    else:
        parser.error("--snapshot 또는 --rolling 중 하나가 필요합니다")
