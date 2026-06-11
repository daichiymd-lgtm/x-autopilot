"""学習層: 週次でエンゲージメントを取得し、執筆指針(guidance.json)を更新する.

使い方:
    python -m xautopilot.learn
"""
import os
import json
from .config import load_config, load_env, log, ROOT, now_jst
from . import ledger, publish, notify


def _score(metrics: dict) -> int:
    if not metrics:
        return 0
    # インプレッションが取れればそれを主指標、無ければ反応の合計
    imp = metrics.get("impression_count")
    if imp:
        return int(imp)
    return (metrics.get("like_count", 0) * 3
            + metrics.get("retweet_count", 0) * 5
            + metrics.get("reply_count", 0) * 2
            + metrics.get("quote_count", 0) * 4)


def run_learning() -> None:
    load_env()
    cfg = load_config()
    if not cfg["learn"].get("pull_engagement", True):
        log("学習: pull_engagement=false のためスキップ")
        return

    d = ledger._load()
    posted = [p for p in d["posts"] if p.get("status") == "posted" and p.get("tweet_ids")]
    recent = posted[:25]
    ids = [p["tweet_ids"][0] for p in recent if p.get("tweet_ids")]
    if not ids:
        log("学習対象の投稿なし。終了。")
        return

    # X API でエンゲージ取得
    client = publish._get_client()
    metrics_by_id = {}
    try:
        resp = client.get_tweets(ids=ids[:100], tweet_fields=["public_metrics"])
        for tw in (resp.data or []):
            metrics_by_id[str(tw.id)] = dict(tw.public_metrics or {})
    except Exception as e:
        log(f"エンゲージ取得失敗（権限/プラン要確認）: {e}")

    for p in recent:
        p["_score"] = _score(metrics_by_id.get(p["tweet_ids"][0], {}))
    ranked = sorted(recent, key=lambda p: p["_score"], reverse=True)
    top = ranked[:5]
    bottom = [p for p in ranked if p["_score"] > 0][-5:]

    def fmt(items):
        return "\n".join(f"- score={p['_score']} | {p.get('topic', '')} | hook: {p.get('hook', '')[:50]}"
                         for p in items)

    # Claude に指針更新を依頼
    guidance_path = ROOT / cfg["learn"]["guidance_file"]
    try:
        cur = json.loads(guidance_path.read_text(encoding="utf-8"))
    except Exception:
        cur = {"version": 0}

    new_guidance = cur
    try:
        from anthropic import Anthropic
        c = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
        prompt = (
            "あなたはXグロース分析者。以下は同一アカウントの投稿スコア。\n\n"
            f"【上位】\n{fmt(top)}\n\n【下位】\n{fmt(bottom)}\n\n"
            f"【現在の指針】\n{json.dumps(cur, ensure_ascii=False)}\n\n"
            "上位と下位の差からフック/トピックの法則を更新し、次の指針JSONだけ返す。"
            'JSON: {"version":N+1,"updated":"YYYY-MM-DD","confirmed_hooks":[...],'
            '"trying":[...],"avoid":[...],"notes":"1行"}'
        )
        msg = c.messages.create(
            model=cfg["analyze"]["compose_model"], max_tokens=1200, temperature=0.4,
            messages=[{"role": "user", "content": prompt}])
        from .analyze import _strip_json
        new_guidance = json.loads(_strip_json(msg.content[0].text))
        new_guidance["updated"] = now_jst().strftime("%Y-%m-%d")
        guidance_path.write_text(json.dumps(new_guidance, ensure_ascii=False, indent=2), encoding="utf-8")
        log(f"指針を v{new_guidance.get('version')} に更新")
    except Exception as e:
        log(f"指針更新スキップ: {e}")

    notify.slack(
        cfg,
        f":books: X Autopilot 週次学習 完了\n対象{len(recent)}本 / 指針 v{new_guidance.get('version', '?')}\n"
        f"トップ: {top[0].get('topic', '') if top else '-'} (score={top[0]['_score'] if top else 0})")


if __name__ == "__main__":
    run_learning()
