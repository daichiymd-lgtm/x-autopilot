"""収益ゲートの判定（ネットワーク不要・時計なし・手で実行する）.

lanes.yaml を読み、レーンごとに
  1. 詰まっている段（露出→訪問→購入手前→着金の順で、最初に0か小さい段）
  2. いま回している作業のうち、段に合っていないもの（止める提案）
  3. 判定日の超過
を決め、本人待ちを1本の列に並べ直す。数字は台帳のものだけを使い、無いものは「未確認」と出す。

実行:
  python -m revenue_gate.check                    # 表示
  python -m revenue_gate.check --write            # revenue_gate/TODAY.md に書き出す
  python -m revenue_gate.check --lane etsy        # 1レーンだけ（作業の着手前に使う）
  python -m revenue_gate.check --strict           # 段に合わない作業があれば終了コード1
"""
import argparse
import sys
from datetime import date
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
LEDGER = HERE / "lanes.yaml"            # 実台帳（.gitignore 済み・公開しない）
EXAMPLE = HERE / "lanes.example.yaml"   # 見本（架空の数字）
ORDER = ["exposure", "visit", "prepurchase", "cash"]
JA = {"exposure": "露出", "visit": "訪問", "prepurchase": "購入手前", "cash": "着金", "none": "どの段にも直接効かない"}
# 詰まっている段ごとに、やってよい作業の段（スキル §2 の表）
ALLOWED = {
    "exposure": {"exposure"},
    "visit": {"visit", "exposure"},      # 訪問30未満は「露出か訪問」
    "prepurchase": {"prepurchase"},
    "cash": {"cash"},
}
MAX_JUDGMENT_DAYS = 14


def _d(s):
    return date.fromisoformat(s) if s else None


def weekly(stage):
    """7日あたりの値。期間が分からない数字は None（＝閾値と比べない）."""
    v, per = stage.get("value"), stage.get("per_days")
    if v is None or not per:
        return None
    return v * 7 / per


def stuck_stage(lane, floor):
    """(段, 確定か, 理由) を返す。確定でない＝数字が無く、まず数字を取る段."""
    if lane.get("stuck_override"):
        if not lane.get("override_reason"):
            raise ValueError(f"{lane['id']}: stuck_override には override_reason が要る")
        return lane["stuck_override"], True, lane["override_reason"]
    st = lane["stages"]
    first_unknown = None
    for key in ORDER:
        s = st[key]
        v = s.get("value")
        if v is None:
            first_unknown = first_unknown or key
            continue
        if key == "visit":
            w = weekly(s)
            if w is not None and w < floor:
                return key, True, f"{s['unit']} 週{w:.1f}（{floor}未満）"
            continue
        if v == 0:
            return key, True, f"{s['unit']} 0"
    if first_unknown:
        return first_unknown, False, f"{st[first_unknown]['unit']} が未確認"
    return "cash", True, "全段に数字あり"


def audit_running(lane, stage, confirmed, floor):
    """段に合わない作業を (作業, 理由, 本人の指示か) で返す."""
    out = []
    w = weekly(lane["stages"]["visit"])
    for r in lane.get("running") or []:
        why = None
        if r.get("effect_test") and w is not None and w < floor:
            why = f"週の訪問{w:.1f}で効果を判定できない"
        elif confirmed and r["stage"] not in ALLOWED[stage]:
            why = f"効く段は「{JA[r['stage']]}」、詰まりは「{JA[stage]}」"
        if why:
            out.append((r["work"], why, r.get("mandate")))
    return out


def judgment_flags(lane, today):
    j = lane.get("judgment")
    if not j:
        return ["判定日なし"]
    flags = []
    if _d(j["date"]) < today:
        flags.append(f"判定日 {j['date']} を超過")
    if (_d(j["date"]) - _d(j["set"])).days > MAX_JUDGMENT_DAYS:
        flags.append(f"判定まで{(_d(j['date']) - _d(j['set'])).days}日（上限{MAX_JUDGMENT_DAYS}日）")
    return flags


def rank_asks(asks, stuck, today, limit):
    """本人待ちを並べ直す。詰まっている段を外す依頼 → 所要時間の短い順 → 古い順."""
    def blocking(a):
        return [u["lane"] for u in a.get("unlocks") or []
                if u["lane"] in stuck and (u["stage"] == stuck[u["lane"]][0] or not stuck[u["lane"]][1])]

    rows = []
    for a in asks:
        b = blocking(a)
        rows.append({**a, "blocks": b, "age": (today - _d(a["since"])).days})
    hot = [r for r in rows if r["blocks"] and r["minutes"] is not None]
    hot.sort(key=lambda r: (r["minutes"], -r["age"]))
    security = [r for r in rows if r["kind"] == "security"]
    now = hot[:limit]
    later = [r for r in rows if r not in now and r not in security]
    return now, security, later


def evaluate(data, today):
    floor = data["visit_floor_per_week"]
    lanes, stuck = [], {}
    for lane in data["lanes"]:
        stage, confirmed, reason = stuck_stage(lane, floor)
        stuck[lane["id"]] = (stage, confirmed)
        stale = [f"{JA[k]} {(today - _d(s['date'])).days}日前"
                 for k, s in lane["stages"].items()
                 if s.get("date") and (today - _d(s["date"])).days > data["stale_after_days"]]
        lanes.append({
            "lane": lane, "stage": stage, "confirmed": confirmed, "reason": reason,
            "mismatch": audit_running(lane, stage, confirmed, floor),
            "flags": judgment_flags(lane, today) if lane.get("judgment") else [],
            "stale": stale,
        })
    now, security, later = rank_asks(data["owner_asks"], stuck, today, data["owner_queue_max"])
    names = {x["id"]: x["name"].split("（")[0].split(" ¥")[0].split(" @")[0] for x in data["lanes"]}
    for a in now:
        a["blocks"] = [names[b] for b in a["blocks"]]
    return {"lanes": lanes, "now": now, "security": security, "later": later}


def render(res, today, only=None):
    L = [f"# 収益ゲート {today.isoformat()}", ""]
    L += ["## レーン別（詰まっている段 → 今やる作業）", "",
          "| レーン | 詰まっている段 | 根拠 | 止める提案 | 次にやる作業 | 判定日 |",
          "|---|---|---|---|---|---|"]
    for r in res["lanes"]:
        lane = r["lane"]
        if only and lane["id"] != only:
            continue
        stage = JA[r["stage"]] + ("" if r["confirmed"] else "（未確認・先に数字を取る）")
        stop = "<br>".join(f"{w}：{why}" + (f"［本人の指示で継続中＝減らすかは本人裁定：{m}］" if m else "")
                           for w, why, m in r["mismatch"]) or "—"
        j = lane.get("judgment")
        jd = (f"{j['date']} {j['metric']}" if j else "—") + "".join(f"<br>⚠ {f}" for f in r["flags"])
        reason = r["reason"] + "".join(f"<br>⚠ 古い数字：{s}" for s in r["stale"])
        L.append(f"| {lane['name']} | {stage} | {reason} | {stop} | {'<br>'.join(lane.get('next') or ['—'])} | {jd} |")
    if only:
        return "\n".join(L) + "\n"
    total = len(res["now"]) + len(res["security"]) + len(res["later"])
    L += ["", f"## 本人のボタン（全{total}件のうち、今日はこの{len(res['now'])}件だけ）", "",
          "| 順 | 押すもの | 所要 | 待ち日数 | 動き出すレーン |", "|---|---|---|---|---|"]
    for i, a in enumerate(res["now"], 1):
        L.append(f"| {i} | {a['ask']} | {a['minutes']}分 | {a['age']}日 | {'・'.join(a['blocks'])} |")
    for a in res["security"]:
        L.append(f"| 安全 | {a['ask']} | {a['minutes']}分 | {a['age']}日 | — |")
    L += ["", "## 今は押さなくてよい（詰まっている段に効かない・または所要が未確認）", ""]
    L += [f"- {a['ask']}（{a['age']}日待ち）" for a in res["later"]]
    return "\n".join(L) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description="収益ゲートの判定")
    ap.add_argument("--ledger", help="台帳のパス（既定は lanes.yaml、無ければ見本）")
    ap.add_argument("--today", help="判定日 YYYY-MM-DD（既定は台帳の as_of）")
    ap.add_argument("--lane", help="1レーンだけ表示")
    ap.add_argument("--write", action="store_true", help="revenue_gate/TODAY.md に書き出す")
    ap.add_argument("--strict", action="store_true", help="段に合わない作業があれば終了コード1")
    args = ap.parse_args(argv)
    path = Path(args.ledger) if args.ledger else (LEDGER if LEDGER.exists() else EXAMPLE)
    if path == EXAMPLE:
        print("※ lanes.yaml が無いため見本（架空の数字）で判定しています", file=sys.stderr)
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    today = _d(args.today or data["as_of"])
    if args.lane and args.lane not in {x["id"] for x in data["lanes"]}:
        ap.error(f"レーン {args.lane} は台帳に無い")
    res = evaluate(data, today)
    text = render(res, today, args.lane)
    if args.write:
        (HERE / "TODAY.md").write_text(text, encoding="utf-8")
    sys.stdout.write(text)
    bad = [r for r in res["lanes"] if r["mismatch"] and (not args.lane or r["lane"]["id"] == args.lane)]
    return 1 if (args.strict and bad) else 0


if __name__ == "__main__":
    sys.exit(main())
