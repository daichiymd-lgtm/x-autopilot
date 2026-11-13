"""稼働ヘルスチェック（ネットワーク不要・API課金ゼロ）.

台帳 data/_post_ledger.json だけを読み、「止まっている／失敗が続いている」を
早期に検知する。2026-06-25 を最後に投稿が途絶え、7〜8月に error が272件
積み上がっても誰も気づかなかった反省から追加（見直し頻度を週次→日次に上げる）。

使い方:
    python -m xautopilot.health                 # レポートを標準出力
    python -m xautopilot.health --out report.md # Markdownで保存
    python -m xautopilot.health --strict        # 要対応(RED)なら exit 1

判定（RED/AMBER/GREEN）:
    RED   : 直近 error が連続 N件以上 / 凍結でないのに最終投稿から M日超
    AMBER : 直近7日の成功率 < 50% / error 理由が未記録
    GREEN : 上記なし
    FROZEN: publish 凍結中。RED/AMBER 要因は「解凍前に要解消」として表示のみ（exit 0）
"""
import argparse
import collections
import json
import sys
from datetime import datetime, timedelta

from .config import ROOT, load_config, now_jst
from . import breaker

LEDGER = ROOT / "data" / "_post_ledger.json"

ERROR_STREAK_RED = 5        # 連続 error がこの件数以上で RED
STALE_DAYS_RED = 3          # 凍結でないのに最終投稿からこの日数超で RED
SUCCESS_RATE_AMBER = 0.5    # 直近7日の成功率がこれ未満で AMBER


def _load_posts() -> list:
    try:
        return json.loads(LEDGER.read_text(encoding="utf-8")).get("posts", [])
    except Exception:
        return []


def _ts(p: dict):
    try:
        return datetime.fromisoformat(p["ts"])
    except Exception:
        return None


def analyze(posts: list, cfg: dict, now: datetime) -> dict:
    posts = [p for p in posts if _ts(p)]
    posts.sort(key=_ts, reverse=True)  # 新しい順

    pub = cfg.get("publish", {})
    frozen = bool(pub.get("frozen")) or not pub.get("enabled", True)

    streak = 0
    for p in posts:
        if p.get("status") == "error":
            streak += 1
        else:
            break

    posted = [p for p in posts if p.get("status") == "posted"]
    last_post = _ts(posted[0]) if posted else None
    last_run = _ts(posts[0]) if posts else None

    week = [p for p in posts if _ts(p) >= now - timedelta(days=7)]
    week_ok = sum(1 for p in week if p.get("status") == "posted")
    week_rate = (week_ok / len(week)) if week else None

    monthly = collections.OrderedDict()
    for p in sorted(posts, key=_ts):
        m = p["ts"][:7]
        monthly.setdefault(m, collections.Counter())[p.get("status", "?")] += 1

    no_reason = sum(1 for p in posts if p.get("status") == "error" and not p.get("error"))

    pay_streak = breaker.payment_streak(posts)
    reasons = []
    if pay_streak >= breaker.threshold(cfg):
        reasons.append(("RED", f"X API 402（クレジット切れ）が {pay_streak} 件連続。ブレーカー作動中"
                               "（支払い確認 → `python -m xautopilot.breaker --reset`）"))
    if streak >= ERROR_STREAK_RED:
        reasons.append(("RED", f"直近 error が {streak} 件連続（投稿が1本も通っていない）"))
    if not frozen and (last_post is None or now - last_post > timedelta(days=STALE_DAYS_RED)):
        days = (now - last_post).days if last_post else None
        reasons.append(("RED", f"凍結中でないのに最終投稿から {days} 日経過" if days is not None
                        else "凍結中でないのに投稿実績がない"))
    if week_rate is not None and week_rate < SUCCESS_RATE_AMBER:
        reasons.append(("AMBER", f"直近7日の成功率 {week_rate:.0%}（{week_ok}/{len(week)}）"))
    if no_reason:
        reasons.append(("AMBER", f"失敗理由が未記録の error が {no_reason} 件（原因を追えない）"))

    if frozen and reasons:
        # 凍結中は毎日REDを鳴らすとノイズ化するので鳴らさない。解凍前の必須解消事項として表示だけする。
        level = "FROZEN"
    elif any(r[0] == "RED" for r in reasons):
        level = "RED"
    elif reasons:
        level = "AMBER"
    else:
        level = "FROZEN" if frozen else "GREEN"

    return {
        "level": level,
        "frozen": frozen,
        "reasons": reasons,
        "error_streak": streak,
        "payment_streak": pay_streak,
        "last_post": last_post,
        "last_run": last_run,
        "week_total": len(week),
        "week_ok": week_ok,
        "monthly": monthly,
        "total": len(posts),
    }


def _fmt(dt) -> str:
    return dt.strftime("%Y-%m-%d %H:%M") if dt else "—"


def render(r: dict, now: datetime) -> str:
    icon = {"RED": "🔴", "AMBER": "🟡", "GREEN": "🟢", "FROZEN": "⚪"}[r["level"]]
    lines = [
        f"# X Autopilot ヘルスチェック {now.strftime('%Y-%m-%d')}",
        "",
        f"**判定: {icon} {r['level']}**" + ("（publish 凍結中）" if r["frozen"] else ""),
        "",
        "| 指標 | 値 |",
        "|---|---|",
        f"| 最終投稿成功 | {_fmt(r['last_post'])} |",
        f"| 最終実行 | {_fmt(r['last_run'])} |",
        f"| 直近 error 連続 | {r['error_streak']} 件 |",
        f"| うち X API 402 連続 | {r['payment_streak']} 件 |",
        f"| 直近7日 成功/実行 | {r['week_ok']} / {r['week_total']} |",
        f"| 台帳件数 | {r['total']} |",
        "",
    ]
    if r["reasons"]:
        lines += ["## 解凍前に要解消" if r["frozen"] else "## 要対応", ""]
        lines += [f"- {'🔴' if lv == 'RED' else '🟡'} {msg}" for lv, msg in r["reasons"]]
        lines.append("")
    lines += ["## 月次ステータス", "", "| 月 | posted | error | その他 |", "|---|---:|---:|---:|"]
    for m, c in r["monthly"].items():
        other = sum(v for k, v in c.items() if k not in ("posted", "error"))
        lines.append(f"| {m} | {c.get('posted', 0)} | {c.get('error', 0)} | {other} |")
    lines.append("")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description="X Autopilot 稼働ヘルスチェック（API不要）")
    ap.add_argument("--out", help="Markdownレポートの保存先")
    ap.add_argument("--strict", action="store_true", help="RED のとき exit 1")
    args = ap.parse_args()

    now = now_jst()
    r = analyze(_load_posts(), load_config(), now)
    md = render(r, now)
    print(md)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(md)
    if args.strict and r["level"] == "RED":
        sys.exit(1)


if __name__ == "__main__":
    main()
