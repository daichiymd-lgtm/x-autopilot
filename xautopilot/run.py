"""オーケストレーター: 1スロット分を 収集→生成→ゲート→整形→投稿 まで実行.

使い方:
    python -m xautopilot.run --slot morning1
    python -m xautopilot.run --slot evening2 --dry-run   # 生成のみ・投稿しない
"""
import argparse
import json
import traceback

from .config import load_config, load_env, log, ROOT
from . import collect, analyze, gates, publish, ledger, notify


def load_guidance(cfg: dict) -> dict:
    p = ROOT / cfg["learn"]["guidance_file"]
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def run_slot(slot_key: str, dry_run: bool = False) -> None:
    load_env()
    cfg = load_config()
    if slot_key not in cfg["slots"]:
        raise SystemExit(f"未知のスロット: {slot_key}（{list(cfg['slots'])}）")

    slot = cfg["slots"][slot_key]
    theme = slot["theme"]
    pub = dict(cfg["publish"])
    if dry_run:
        pub["enabled"] = False
    log(f"=== スロット {slot_key} 開始 / mode={pub['mode']} / enabled={pub['enabled']} ===")
    log(f"テーマ: {theme}")

    # 同日同スロット二重投稿ガード（02-Mistakes 2026-06-09）
    if pub["enabled"] and ledger.already_posted_this_slot_today(slot_key):
        log("同日同スロットで投稿済み。スキップ。")
        return

    # 1) 収集（Grok Live Search）
    signals = collect.collect_signals(cfg, theme)
    guidance = load_guidance(cfg)

    # 2) 生成 + ゲート再生成ループ
    max_regen = cfg["gates"].get("max_regenerate", 1)
    thread, regen_hint = None, ""
    for attempt in range(max_regen + 1):
        thread = analyze.compose_thread(cfg, theme, signals, guidance, regen_hint)
        issues = gates.run_gates(cfg, thread, theme)
        log(f"ゲート(試行{attempt + 1}): {issues if issues else 'クリーン'}")

        if gates.has(issues, "NG_WORD"):
            regen_hint = "前回は経歴正本のNG語が混入。コンサル8年/社内最優秀賞/実社名等を一切使わず作り直す。"
            if attempt < max_regen:
                continue
            notify.slack(cfg, f":no_entry: X Autopilot 中止({slot_key}) 経歴正本NG語が除去できず / topic={thread.get('topic')}")
            ledger.record(slot_key, theme, thread.get("topic", ""), thread.get("hook", ""), [], "", status="blocked_ng")
            return

        if gates.has(issues, "DUPLICATE"):
            regen_hint = "前回は過去投稿と重複。同テーマでも別の角度・別事例・別の数字で作り直す。"
            if attempt < max_regen:
                continue
            log("重複が解消できず。スロットをスキップ。")
            ledger.record(slot_key, theme, thread.get("topic", ""), thread.get("hook", ""), [], "", status="skipped_dup")
            return

        if gates.has(issues, "THREAD_TOO_SHORT"):
            regen_hint = f"ツイート数が不足。最低{cfg['thread']['min_tweets']}本のツリーにする。"
            if attempt < max_regen:
                continue
        break  # 長さ/本数過多/ADVICE は整形で吸収

    # 3) 整形（連番・字数調整・免責付与）
    tweets = publish.prepare_tweets(cfg, thread)
    cta = cfg["thread"]["cta_text"] if cfg["thread"].get("cta_self_reply") else None
    preview = "\n\n".join(tweets) + (f"\n\n[CTA] {cta}" if cta else "")
    log("---- プレビュー ----\n" + preview + "\n-------------------")

    # 4) 投稿モード分岐
    if not pub["enabled"]:
        notify.slack(cfg, f":mag: X Autopilot ドライラン({slot_key}/{thread.get('topic', '')})\n\n{preview}")
        log("enabled=false（ドライラン）: 投稿せず終了。")
        return

    if pub["mode"] == "draft":
        notify.slack(cfg, f":memo: X Autopilot 下書き({slot_key}/{thread.get('topic', '')})\n\n{preview}")
        ledger.record(slot_key, theme, thread.get("topic", ""), thread.get("hook", ""), [], "", status="draft")
        return

    if pub["mode"] == "buffer":
        notify.slack(cfg, f":hourglass: X Autopilot 予告({slot_key}) {pub['buffer_minutes']}分後に自動投稿\n\n{preview}")
        import time
        time.sleep(int(pub["buffer_minutes"]) * 60)

    # direct（または buffer 経過後）→ 投稿
    try:
        result = publish.post_thread(cfg, tweets, cta)
    except Exception as e:
        notify.slack(cfg, f":x: X Autopilot 投稿失敗({slot_key}): {e}")
        ledger.record(slot_key, theme, thread.get("topic", ""), thread.get("hook", ""), [], "", status="error")
        raise
    ledger.record(slot_key, theme, thread.get("topic", ""), thread.get("hook", ""),
                  result["ids"], result["url"], status="posted")
    if cfg["notify"].get("on_success"):
        notify.slack(cfg, f":white_check_mark: X Autopilot 投稿完了({slot_key})\n{thread.get('topic', '')}\n{result['url']}")
    log(f"=== 完了: {result['url']} ===")


def main():
    ap = argparse.ArgumentParser(description="X Autopilot — 1スロット実行")
    ap.add_argument("--slot", required=True, help="morning1|morning2|evening1|evening2")
    ap.add_argument("--dry-run", action="store_true", help="生成のみ・投稿しない")
    args = ap.parse_args()
    try:
        run_slot(args.slot, dry_run=args.dry_run)
    except SystemExit:
        raise
    except Exception as e:
        log("致命的エラー:\n" + traceback.format_exc())
        try:
            cfg = load_config()
            notify.slack(cfg, f":rotating_light: X Autopilot 例外({args.slot}): {e}")
        except Exception:
            pass
        raise


if __name__ == "__main__":
    main()
