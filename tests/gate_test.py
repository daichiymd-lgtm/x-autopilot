"""収益ゲートのテスト（ネットワーク不要）. 実行: python tests/gate_test.py"""
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import yaml
from revenue_gate import check as g

FAIL = []


def ok(name, cond, detail=""):
    print(f"[{'OK ' if cond else 'NG '}] {name} {detail}")
    if not cond:
        FAIL.append(name)


def lane(**stages):
    base = {k: {"value": None, "unit": k, "date": None} for k in g.ORDER}
    base.update(stages)
    return {"id": "t", "name": "t", "stages": base, "running": []}


def main():
    # 1) 段の決め方
    ok("露出0 → 露出", g.stuck_stage(lane(exposure={"value": 0, "unit": "u"}), 30)[0] == "exposure")
    v = lane(exposure={"value": 9, "unit": "u"}, visit={"value": 10, "per_days": 30, "unit": "u"})
    ok("週の訪問30未満 → 訪問", g.stuck_stage(v, 30)[0] == "visit")
    p = lane(exposure={"value": 9, "unit": "u"}, visit={"value": 400, "per_days": 30, "unit": "u"},
             prepurchase={"value": 0, "unit": "u"})
    ok("訪問あり・購入手前0 → 購入手前", g.stuck_stage(p, 30)[0] == "prepurchase")
    u = lane(exposure={"value": 9, "unit": "u"})
    st = g.stuck_stage(u, 30)
    ok("数字なし → 未確認として返す（推測しない）", st[0] == "visit" and st[1] is False, str(st))
    nop = lane(exposure={"value": 9, "unit": "u"}, visit={"value": 5, "per_days": None, "unit": "u"})
    ok("期間不明の訪問は閾値と比べない", g.stuck_stage(nop, 30)[0] != "visit" or g.stuck_stage(nop, 30)[1] is False)
    bad = dict(lane(), stuck_override="cash")
    try:
        g.stuck_stage(bad, 30)
        ok("理由なしの上書きは拒否", False)
    except ValueError:
        ok("理由なしの上書きは拒否", True)

    # 2) 数は止めない。未達と停止だけを挙げる
    v["running"] = [{"work": "言い換え", "stage": "none", "target_per_day": 3, "actual_per_day": 3},
                    {"work": "題", "stage": "visit", "target_per_day": 2, "actual_per_day": 1},
                    {"work": "量産", "stage": "exposure", "stalled": True},
                    {"work": "AB", "stage": "visit", "effect_test": True, "target_per_day": 3, "actual_per_day": 3}]
    vs = g.volume_status(v, 30)
    ok("段に効かない作業でも、数が出ていれば未達にしない", vs[0][2] is False, str(vs[0]))
    ok("目標未達と停止を挙げる", [x[2] for x in vs] == [False, True, True, False])
    ok("効果判定は保留でも数は続ける", "数は続ける" in vs[3][1] and vs[3][2] is False)
    ok("止まっている数が効く段を返す", g.starved_stages(v) == {"visit", "exposure"})
    ok("出力に「止める提案」を出さない", "止める提案" not in g.render(g.evaluate(yaml.safe_load(g.EXAMPLE.read_text(encoding="utf-8")), date(2026, 1, 15)), date(2026, 1, 15)))

    # 3) 判定日
    j = dict(lane(), judgment={"metric": "m", "set": "2026-11-01", "date": "2026-11-10"})
    ok("判定日の超過を出す", any("超過" in f for f in g.judgment_flags(j, date(2026, 11, 12))))
    j2 = dict(lane(), judgment={"metric": "m", "set": "2026-11-01", "date": "2026-11-30"})
    ok("2週間を超える判定日を出す", any("上限" in f for f in g.judgment_flags(j2, date(2026, 11, 12))))

    # 4) 見本の台帳（実台帳は公開しないため、見本で通しを確かめる）
    data = yaml.safe_load(g.EXAMPLE.read_text(encoding="utf-8"))
    today = date.fromisoformat(data["as_of"])
    res = g.evaluate(data, today)
    by = {r["lane"]["id"]: r for r in res["lanes"]}
    got = tuple(by[k]["stage"] for k in ("shop", "kit", "prints", "blog"))
    ok("見本: 段の判定", got == ("prepurchase", "exposure", "exposure", "visit"), str(got))
    mm = [any(x[2] for x in by[k]["volume"]) for k in ("shop", "kit", "prints", "blog")]
    ok("見本: 数の停止・未達を挙げる", mm == [True, False, False, True], str(mm))
    data4 = dict(data, owner_queue_max=4)
    ids = [a["id"] for a in g.evaluate(data4, today)["now"]]
    ok("見本: 止まっている数を動かす依頼もボタンに入る", ids == ["APPROVE", "LOGIN", "CSV", "MORE"], str(ids))
    n = len(res["now"]) + len(res["security"]) + len(res["later"])
    ok("見本: 本人待ちを落とさない", n == len(data["owner_asks"]), f"{n}/{len(data['owner_asks'])}")
    missing = [f"{ln['id']}.{k}" for ln in data["lanes"] for k, s2 in ln["stages"].items()
               if s2.get("value") is not None and not (s2.get("date") and s2.get("source"))]
    ok("見本: 数字には日付と出典がある", not missing, str(missing))
    out = g.render(res, today)
    ok("出力に生のレーンIDを出さない", "| shop |" not in out and "・kit" not in out)
    ignore = (Path(__file__).resolve().parent.parent / ".gitignore").read_text(encoding="utf-8")
    ok("実台帳と結果は .gitignore 済み", "revenue_gate/lanes.yaml" in ignore and "revenue_gate/TODAY.md" in ignore)

    print("\n" + ("ALL PASS" if not FAIL else f"FAILED: {FAIL}"))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
