"""スモークテスト（ネットワーク不要）.

config・ゲート・整形・台帳ロジックがエラーなく動くかを検証する。
APIキーの有無もチェックして表示する。投稿は一切しない。

実行: python tests/smoke.py
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from xautopilot.config import load_config, load_env, log, read_persona
from xautopilot import gates, publish, ledger

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
    check("config.slots が4スロット", len(cfg["slots"]) == 4, str(list(cfg["slots"])))
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

    # 6) 認証情報の有無（警告のみ）
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
