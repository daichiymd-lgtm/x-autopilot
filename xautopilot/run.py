"""オーケストレーター: 1スロット分のフルパイプライン実行.

v2 (2026-06-11): 「おっさんのつぶやき」排除パイプライン。
  1) トレンドスキャン（いま熱い話題を発見）
  2) 話題選定（熱さ×読者適合×実用化可能性で1本に絞る）
  3) 深掘りリサーチ×2パス（事実・数字 / 実務含意）
  4) ブリーフ統合
  5) スレッド生成（フック=Opus / 本文=Sonnet）
  6) 品質採点ゲート（有用性10点ルーブリック＋つぶやき検出 → 不合格は再生成）
  7) 安全ゲート（NG語/重複/免責）→ 整形 → 投稿
各段に失敗時フォールバックがあり、止まらない。

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


def build_signals(cfg: dict, slot: dict, theme_lens: str, style: str) -> tuple:
    """収集パイプライン: トレンド発見→選定→深掘り→ブリーフ。

    返り値: (signals dict, topic_label str)
    失敗時は旧来のテーマ収集へフォールバック。
    """
    use_trend = cfg["collect"].get("trend_scan", True)

    if use_trend:
        trends = collect.scan_trends(cfg, theme_lens)
        if not trends.get("fallback"):
            try:
                topic = analyze.select_topic(
                    cfg, trends["raw"], theme_lens, style,
                    ledger.recent_topics(cfg["gates"].get("dedup_days", 30)))
                research = collect.deep_research(
                    cfg, topic.get("topic", ""), topic.get("useful_angle", ""))
                if not research.get("fallback"):
                    brief = analyze.synthesize_brief(cfg, topic, research)
                    signals = {
                        "raw": brief,
                        "citations": research.get("citations", []) + trends.get("citations", [])[:5],
                    }
                    label = (
                        f"{topic.get('topic', '')}\n"
                        f"【編集方針】{topic.get('useful_angle', '')}\n"
                        f"【形式】{topic.get('content_format', '')}\n"
                        f"【このスロットの様式】{style}"
                    )
                    return signals, label
                log("深掘り全滅 → トレンド生データで続行")
                return trends, f"{topic.get('topic', '')}\n【このスロットの様式】{style}"
            except Exception as e:
                log(f"話題選定/ブリーフ失敗（{e}）→ テーマ収集にフォールバック")

    # フォールバック: 旧来のテーマ起点収集
    signals = collect.collect_signals(cfg, theme_lens)
    if signals.get("fallback"):
        log("⚠️ Grok全滅: Claude単体でテーマからスレッドを生成")
    return signals, f"{theme_lens}\n\n【この投稿の形式・モード】{style}"


def run_slot(slot_key: str, dry_run: bool = False) -> None:
    load_env()
    cfg = load_config()
    if slot_key not in cfg["slots"]:
        raise SystemExit(f"未知のスロット: {slot_key}（{list(cfg['slots'])}）")

    slot = cfg["slots"][slot_key]
    theme_lens = slot["theme"]
    style = slot.get("style", "")
    pub = dict(cfg["publish"])
    if dry_run:
        pub["enabled"] = False
    log(f"=== スロット {slot_key} 開始 / mode={pub['mode']} / enabled={pub['enabled']} ===")
    log(f"レンズ: {theme_lens[:60]}…")

    # 同日同スロット二重投稿ガード（02-Mistakes 2026-06-09）
    if pub["enabled"] and ledger.already_posted_this_slot_today(slot_key):
        log("同日同スロットで投稿済み。スキップ。")
        return

    # 1) 収集: トレンド発見 → 話題選定 → 深掘り → ブリーフ
    signals, theme = build_signals(cfg, slot, theme_lens, style)
    guidance = load_guidance(cfg)

    # 2) 生成 + 品質ゲート + 安全ゲートの再生成ループ
    q = cfg.get("quality", {})
    q_enabled = q.get("enabled", True)
    min_score = q.get("min_score", 7)
    max_regen = max(cfg["gates"].get("max_regenerate", 1), q.get("max_regenerate", 2))

    thread, regen_hint, verdict = None, "", None
    for attempt in range(max_regen + 1):
        thread = analyze.compose_thread(cfg, theme, signals, guidance, regen_hint)

        # 2a) 品質採点（つぶやき検出）
        if q_enabled:
            try:
                verdict = analyze.score_thread(cfg, thread)
            except Exception as e:
                log(f"品質採点スキップ（{e}）")
                verdict = None
            if verdict and (verdict.get("tsubuyaki") or verdict.get("score", 10) < min_score):
                regen_hint = (
                    f"前回は品質不合格（score={verdict.get('score')}/10, つぶやき判定={verdict.get('tsubuyaki')}）。"
                    f"改善指示: {verdict.get('fix_hint', '数字と実用手順を増やす')}。"
                    "感想を削り、リサーチブリーフの数字・固有名詞・チェックリストを使い切ること。"
                )
                if attempt < max_regen:
                    log(f"品質ゲート不合格 → 再生成（{attempt + 1}/{max_regen}）")
                    continue
                log("品質ゲート: 再生成上限。このスロットはスキップ（質を守る）。")
                notify.slack(cfg, f":wastebasket: X Autopilot 品質スキップ({slot_key}) "
                                  f"score={verdict.get('score')}/10 topic={thread.get('topic', '')}")
                ledger.record(slot_key, theme[:80], thread.get("topic", ""),
                              thread.get("hook", ""), [], "", status="skipped_quality")
                return

        # 2b) 安全ゲート
        issues = gates.run_gates(cfg, thread, theme)
        log(f"安全ゲート(試行{attempt + 1}): {issues if issues else 'クリーン'}")

        if gates.has(issues, "NG_WORD"):
            regen_hint = "前回は経歴正本のNG語が混入。コンサル8年/社内最優秀賞/実社名等を一切使わず作り直す。"
            if attempt < max_regen:
                continue
            notify.slack(cfg, f":no_entry: X Autopilot 中止({slot_key}) 経歴正本NG語が除去できず / topic={thread.get('topic')}")
            ledger.record(slot_key, theme[:80], thread.get("topic", ""), thread.get("hook", ""), [], "", status="blocked_ng")
            return

        if gates.has(issues, "DUPLICATE"):
            regen_hint = "前回は過去投稿と重複。同テーマでも別の角度・別事例・別の数字で作り直す。"
            if attempt < max_regen:
                continue
            log("重複が解消できず。スロットをスキップ。")
            ledger.record(slot_key, theme[:80], thread.get("topic", ""), thread.get("hook", ""), [], "", status="skipped_dup")
            return

        if gates.has(issues, "THREAD_TOO_SHORT"):
            regen_hint = f"ツイート数が不足。最低{cfg['thread']['min_tweets']}本のツリーにする。"
            if attempt < max_regen:
                continue
        break  # 長さ/本数過多/ADVICE は整形で吸収

    # 3) 整形（連番・字数調整・免責付与）
    tweets = publish.prepare_tweets(cfg, thread)
    cta = cfg["thread"]["cta_text"] if cfg["thread"].get("cta_self_reply") else None
    score_note = f"（品質 {verdict.get('score')}/10）" if verdict else ""
    preview = "\n\n".join(tweets) + (f"\n\n[CTA] {cta}" if cta else "")
    log(f"---- プレビュー {score_note} ----\n" + preview + "\n-------------------")

    # 4) 投稿モード分岐
    if not pub["enabled"]:
        notify.slack(cfg, f":mag: X Autopilot ドライラン({slot_key}/{thread.get('topic', '')}){score_note}\n\n{preview}")
        log("enabled=false（ドライラン）: 投稿せず終了。")
        return

    if pub["mode"] == "draft":
        notify.slack(cfg, f":memo: X Autopilot 下書き({slot_key}/{thread.get('topic', '')})\n\n{preview}")
        ledger.record(slot_key, theme[:80], thread.get("topic", ""), thread.get("hook", ""), [], "", status="draft")
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
        ledger.record(slot_key, theme[:80], thread.get("topic", ""), thread.get("hook", ""), [], "", status="error")
        raise
    ledger.record(slot_key, theme[:80], thread.get("topic", ""), thread.get("hook", ""),
                  result["ids"], result["url"], status="posted")
    if cfg["notify"].get("on_success"):
        notify.slack(
            cfg,
            f":white_check_mark: X Autopilot 投稿完了({slot_key}){score_note}\n{thread.get('topic', '')}\n{result['url']}\n\n"
            ":fire: *いまから30分が勝負*：リプが来たら本人として返信して会話を伸ばす"
            "（会話＝いいねの約75倍／2026アルゴで最強シグナル）。初速のリプ・プロフクリックが拡散を決めます。"
        )
    log(f"=== 完了: {result['url']} ===")


def main():
    ap = argparse.ArgumentParser(description="X Autopilot — 1スロット実行")
    ap.add_argument("--slot", required=True, help="morning1|morning2|afternoon1|evening1|evening2")
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
