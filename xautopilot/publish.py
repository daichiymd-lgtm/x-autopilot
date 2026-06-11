"""投稿層: ツイート整形 + X API v2 でツリー投稿."""
import os
from .config import log
from .gates import xlen


def _truncate(s: str, max_weight: int) -> str:
    if xlen(s) <= max_weight:
        return s
    out, w = [], 0
    for ch in s:
        cw = 2 if ord(ch) > 0x2000 else 1
        if w + cw > max_weight - 1:  # 末尾「…」の分1
            break
        out.append(ch)
        w += cw
    return "".join(out).rstrip() + "…"


def prepare_tweets(cfg: dict, thread: dict) -> list:
    """連番付与・字数オーバーの切り詰め・本数調整・免責付与を行い、最終ツイート列を返す。"""
    t = cfg["thread"]
    g = cfg["gates"]
    tweets = list(thread.get("tweets", []))

    # 本数が多すぎたら末尾を削る
    if len(tweets) > t["max_tweets"]:
        tweets = tweets[:t["max_tweets"]]

    # 投資助言リスク or 金融トピックなら免責を最終ツイートに追加
    full = "\n".join(tweets)
    needs_disc = thread.get("is_finance") or any(
        w in full for w in g.get("advice_risk_words", []))
    if needs_disc and g.get("finance_disclaimer"):
        if g["finance_disclaimer"] not in full:
            tweets.append(g["finance_disclaimer"])

    # 連番 + 字数調整
    total = len(tweets)
    prepared = []
    for i, tw in enumerate(tweets):
        prefix = f"{i + 1}/{total} " if t.get("number_tweets") else ""
        budget = t["max_chars_per_tweet"] - xlen(prefix)
        prepared.append(prefix + _truncate(tw, budget))
    return prepared


def _get_client():
    import tweepy
    return tweepy.Client(
        consumer_key=os.environ["X_API_KEY"],
        consumer_secret=os.environ["X_API_SECRET"],
        access_token=os.environ["X_ACCESS_TOKEN"],
        access_token_secret=os.environ["X_ACCESS_TOKEN_SECRET"],
    )


def post_thread(cfg: dict, tweets: list, cta_text: str = None) -> dict:
    """ツリー投稿。先頭ツイートに本文、以降は直前ツイートへのリプライで連結。
    CTA（noteリンク）は最後にセルフリプ（本文にURLを貼らずデブースト回避）。"""
    client = _get_client()
    ids = []
    reply_to = None
    for tw in tweets:
        resp = client.create_tweet(text=tw, in_reply_to_tweet_id=reply_to)
        tid = resp.data["id"]
        ids.append(tid)
        reply_to = tid
        log(f"投稿: {tid} / {tw[:24]}…")
    if cta_text:
        resp = client.create_tweet(text=cta_text, in_reply_to_tweet_id=reply_to)
        ids.append(resp.data["id"])
        log(f"CTAセルフリプ: {resp.data['id']}")

    handle = cfg.get("account", {}).get("handle", "").lstrip("@")
    url = f"https://x.com/{handle}/status/{ids[0]}" if ids else ""
    return {"ids": ids, "url": url}
