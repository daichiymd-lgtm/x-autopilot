"""投稿台帳（冪等性・重複投稿防止・学習データ）."""
import json
import re
from datetime import datetime, timedelta
from .config import ROOT, now_jst, log

LEDGER = ROOT / "data" / "_post_ledger.json"


def _load() -> dict:
    try:
        return json.loads(LEDGER.read_text(encoding="utf-8"))
    except Exception:
        return {"posts": []}


def _save(d: dict) -> None:
    LEDGER.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")


def _tokens(text: str) -> set:
    text = (text or "").lower()
    toks = set(re.findall(r"[a-z0-9]{2,}", text))
    # 日本語は文字bigramで近似
    for seg in re.findall(r"[ぁ-んァ-ヴー一-龥]+", text):
        for i in range(len(seg) - 1):
            toks.add(seg[i:i + 2])
    return toks


def jaccard(a: str, b: str) -> float:
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0
    inter = len(ta & tb)
    union = len(ta | tb)
    return inter / union if union else 0.0


def is_duplicate(topic_text: str, days: int, threshold: float):
    d = _load()
    cutoff = now_jst() - timedelta(days=days)
    for p in d["posts"]:
        try:
            ts = datetime.fromisoformat(p["ts"])
        except Exception:
            continue
        if ts < cutoff:
            continue
        sim = jaccard(topic_text, (p.get("topic", "") + " " + p.get("theme", "")))
        if sim >= threshold:
            return True, p, sim
    return False, None, 0.0


def record(slot: str, theme: str, topic: str, hook: str, tweet_ids: list,
           url: str, status: str = "posted") -> None:
    d = _load()
    d["posts"].insert(0, {
        "ts": now_jst().isoformat(),
        "slot": slot,
        "theme": theme,
        "topic": topic,
        "hook": hook,
        "tweet_ids": [str(t) for t in (tweet_ids or [])],
        "url": url,
        "status": status,
    })
    d["posts"] = d["posts"][:500]
    _save(d)
    log(f"台帳に記録: {status} / {topic[:30]}")


def recent_topics(days: int = 7) -> list:
    """直近N日のトピック一覧（話題選定の重複回避に使う）."""
    d = _load()
    cutoff = now_jst() - timedelta(days=days)
    out = []
    for p in d["posts"]:
        try:
            if datetime.fromisoformat(p["ts"]) >= cutoff and p.get("topic"):
                out.append(p["topic"])
        except Exception:
            continue
    return out


def already_posted_this_slot_today(slot: str) -> bool:
    """同日同スロットの二重投稿を防ぐ（02-Mistakes 2026-06-09 ad-hoc二重配信の教訓）."""
    d = _load()
    today = now_jst().strftime("%Y-%m-%d")
    for p in d["posts"]:
        if p.get("slot") == slot and p.get("status") == "posted" \
                and p.get("ts", "").startswith(today):
            return True
    return False
