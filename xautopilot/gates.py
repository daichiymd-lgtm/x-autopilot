"""自動安全ゲート（完全自動なので必須）.

重大度:
  HARD   : 経歴正本NG語 → 絶対に投稿しない（再生成→ダメなら中止+通知）
  DUP    : 過去と重複 → そのスロットはスキップ（再生成で別角度を試す）
  ADVICE : 投資助言リスク → 免責付与で中立化（ブロックではない）
  SOFT   : 文字数/本数 → 自動整形で吸収
"""
from . import ledger


def xlen(s: str) -> int:
    """X の文字数重みを近似（CJK等の全角=2, 半角=1）。"""
    n = 0
    for ch in s:
        n += 2 if ord(ch) > 0x2000 else 1
    return n


def run_gates(cfg: dict, thread: dict, theme: str) -> list:
    g = cfg["gates"]
    t = cfg["thread"]
    issues = []
    tweets = thread.get("tweets", [])
    full = "\n".join(tweets)

    # 1) 経歴正本 NG語（HARD）
    for w in g.get("ng_words", []):
        if w and w in full:
            issues.append(("NG_WORD", w))

    # 2) 投資助言リスク（ADVICE）
    if g.get("block_investment_advice"):
        for w in g.get("advice_risk_words", []):
            if w and w in full:
                issues.append(("ADVICE_RISK", w))

    # 3) スレッド本数（SOFT: 多すぎは整形で削る / 少なすぎは再生成）
    if len(tweets) < t["min_tweets"]:
        issues.append(("THREAD_TOO_SHORT", str(len(tweets))))
    if len(tweets) > t["max_tweets"]:
        issues.append(("THREAD_TOO_LONG", str(len(tweets))))

    # 4) 各ツイート長（SOFT: 整形で切り詰め）
    for idx, tw in enumerate(tweets):
        if xlen(tw) > t["max_chars_per_tweet"]:
            issues.append(("TWEET_TOO_LONG", f"{idx + 1}本目={xlen(tw)}"))

    # 5) 重複（DUP）
    dup, prev, sim = ledger.is_duplicate(
        thread.get("topic", "") + " " + theme,
        g["dedup_days"], g["dedup_similarity"])
    if dup:
        issues.append(("DUPLICATE", f"{prev.get('topic', '')[:24]} (sim={sim:.2f})"))

    return issues


def has(issues, *codes) -> bool:
    return any(i[0] in codes for i in issues)
