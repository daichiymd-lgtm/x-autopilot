"""スモークテスト（ネットワーク不要）.

config・ゲート・整形・台帳ロジックがエラーなく動くかを検証する。
APIキーの有無もチェックして表示する。投稿は一切しない。

実行: python tests/smoke.py
"""
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from xautopilot.config import load_config, load_env, log, read_persona
from xautopilot import gates, publish, ledger, budget

FAIL = []


def check(name, cond, detail=""):
    mark = "OK " if cond else "NG "
    print(f"[{mark}] {name} {detail}")
    if not cond:
        FAIL.append(name)


def main():
    load_env()
    cfg = load_config()

    # 1) config
    slots = cfg["slots"]
    check("config.slots が1つ以上・全てthemeあり",
          bool(slots) and all(v.get("theme") for v in slots.values()), str(list(slots)))
    wf = (Path(__file__).resolve().parent.parent / ".github/workflows/post.yml").read_text(encoding="utf-8")
    m = re.search(r"options:\s*\[([^\]]*)\]", wf)
    wf_slots = [x.strip() for x in m.group(1).split(",")] if m else []
    check("post.yml の選択肢 = config.slots", wf_slots == list(slots), str(wf_slots))

    # 1b) 凍結ガード（2026-08-24）: 既定で凍結、両方解除で初めて解凍
    from xautopilot.config import is_frozen
    saved = os.environ.pop("X_AUTOPILOT_FROZEN", None)
    check("既定設定は凍結中", is_frozen(cfg))
    os.environ["X_AUTOPILOT_FROZEN"] = "0"
    check("env解除だけでは凍結維持(config frozen=true)", is_frozen(cfg) == bool(cfg["publish"].get("frozen", True)))
    thawed = {**cfg, "publish": {**cfg["publish"], "frozen": False}}
    check("config+env両方解除で解凍", not is_frozen(thawed))
    os.environ["X_AUTOPILOT_FROZEN"] = "1"
    check("env=1ならconfig解除でも凍結", is_frozen(thawed))
    if saved is None:
        os.environ.pop("X_AUTOPILOT_FROZEN", None)
    else:
        os.environ["X_AUTOPILOT_FROZEN"] = saved
    check("persona 読み込み", len(read_persona(cfg)) > 100)

    # 2) ゲート: NG語検知
    bad = {"topic": "t", "hook": "h", "tweets": ["私はコンサル8年の実績があります", "本論", "締め"]}
    issues = gates.run_gates(cfg, bad, "テーマ")
    check("NG語(コンサル8年)を検知", gates.has(issues, "NG_WORD"), str(issues))

    # 3) ゲート: 投資助言検知
    adv = {"topic": "t", "hook": "h", "tweets": ["この銘柄は必ず儲かる", "本論本論", "締め締め"]}
    issues = gates.run_gates(cfg, adv, "テーマ")
    check("投資助言リスク(必ず儲かる)を検知", gates.has(issues, "ADVICE_RISK"), str(issues))

    # 4) 整形: 連番・字数
    good = {"topic": "海外REITの動き", "hook": "海外で動きが",
            "tweets": ["米国REITの延滞率が上昇している。" * 3,
                       "背景には金利がある。", "日本への含意はこう。"],
            "is_finance": True}
    prepared = publish.prepare_tweets(cfg, good)
    check("整形後も最大本数以内", len(prepared) <= cfg["thread"]["max_tweets"] + 1, f"{len(prepared)}本")
    over = [t for t in prepared if gates.xlen(t) > cfg["thread"]["max_chars_per_tweet"]]
    check("全ツイートが字数上限以内", not over, f"超過{len(over)}本")
    check("連番が付与される", prepared[0].startswith("1/"), prepared[0][:6])
    check("金融トピックに免責が付く", any(cfg["gates"]["finance_disclaimer"] in t for t in prepared))

    # 5) 台帳: jaccard
    sim = ledger.jaccard("海外REITの延滞率上昇", "海外REITの延滞率が上がっている")
    check("類似トピックのjaccardが高い", sim > 0.3, f"sim={sim:.2f}")
    sim2 = ledger.jaccard("海外REITの延滞率", "猫の写真の撮り方")
    check("無関係トピックのjaccardが低い", sim2 < 0.2, f"sim={sim2:.2f}")

    # 6) 出費ガード（一時ファイルで検証・本物の _budget.json は触らない）
    import tempfile
    orig = budget.BUDGET_FILE
    with tempfile.TemporaryDirectory() as td:
        budget.BUDGET_FILE = Path(td) / "_budget.json"
        tiny = {"budget": {"max_grok_calls_per_day": 2, "max_grok_calls_per_month": 99,
                           "max_claude_calls_per_day": 99, "max_claude_calls_per_month": 99,
                           "max_consecutive_errors": 3}}
        budget.charge(tiny, "grok"); budget.charge(tiny, "grok")
        try:
            budget.charge(tiny, "grok"); stopped = False
        except budget.BudgetExceeded:
            stopped = True
        check("Grok日次上限で停止する", stopped, budget.summary(tiny))
        check("上限超過は except Exception で握りつぶされない",
              not issubclass(budget.BudgetExceeded, Exception))
    budget.BUDGET_FILE = orig
    errs = [{"status": "error"}] * 3
    check("連続3回失敗で自動停止", budget.circuit_open(tiny, errs))
    check("成功を挟めば停止しない", not budget.circuit_open(tiny, [{"status": "error"}, {"status": "posted"}, {"status": "error"}]))
    check("品質スキップは失敗に数えない",
          not budget.circuit_open(tiny, [{"status": "error"}, {"status": "skipped_quality"}, {"status": "error"}]))
    check("現行の台帳では停止状態（解凍しても即課金しない）",
          budget.circuit_open(cfg, ledger._load().get("posts", [])))

    # 7) ヘルスチェック: 連続errorでRED / 正常はGREEN
    from datetime import timedelta
    from xautopilot import health
    from xautopilot.config import now_jst
    now = now_jst()
    live = {"publish": {"frozen": False, "enabled": True}}
    errs = [{"ts": (now - timedelta(hours=i)).isoformat(), "status": "error"} for i in range(6)]
    check("error 6連続で RED", health.analyze(errs, live, now)["level"] == "RED")
    ok = [{"ts": (now - timedelta(hours=i)).isoformat(), "status": "posted"} for i in range(3)]
    check("直近投稿成功で GREEN", health.analyze(ok, live, now)["level"] == "GREEN")
    stale = [{"ts": (now - timedelta(days=10)).isoformat(), "status": "posted"}]
    check("10日投稿なしで RED", health.analyze(stale, live, now)["level"] == "RED")
    frz = {"publish": {"frozen": True, "enabled": False}}
    check("凍結中は RED を鳴らさない", health.analyze(errs, frz, now)["level"] == "FROZEN")

    # 8) 認証情報の有無（警告のみ）
    print("\n--- 認証情報（投稿前に要設定） ---")
    for k in ["ANTHROPIC_API_KEY", "XAI_API_KEY", "X_API_KEY", "X_API_SECRET",
              "X_ACCESS_TOKEN", "X_ACCESS_TOKEN_SECRET"]:
        print(f"  {k}: {'設定済み' if os.environ.get(k) else '未設定'}")

    print()
    if FAIL:
        print(f"スモーク失敗: {FAIL}")
        sys.exit(1)
    print("スモーク全項目OK（ロジックは健全。あとはAPIキー設定→ドライラン）")


if __name__ == "__main__":
    main()
