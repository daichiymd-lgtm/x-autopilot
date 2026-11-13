"""課金ブレーカー: X API のクレジット切れ（402）で空回りしないための安全装置.

2026-06-25〜08-24 に X API が 402 Payment Required / credits depleted を返し続け、
毎回 Grok 収集＋Claude 生成まで走ってから投稿だけ失敗していた（300回・投稿0本）。
その再発を防ぐ3段構え:

  1) preflight : 収集・生成の前に X API を1回だけ叩き、402 なら Grok/Claude を呼ばずに止める
  2) 自動停止   : 台帳上で 402 が N回（既定2）連続したら以後のスロットを実行しない
  3) health連携 : 402 連続を health.py が RED として扱う（payment_streak を共有）

状態は台帳（data/_post_ledger.json）から導出するので、GitHub Actions でも永続化される。
解除は人が支払い状況を確認したうえで:
    python -m xautopilot.breaker            # 状態表示
    python -m xautopilot.breaker --reset    # 解除（台帳に breaker_reset を記録）
"""
import argparse
from datetime import datetime

from .config import log

PAYMENT_MARKERS = ("402", "payment required", "credits depleted")
RESET_STATUS = "breaker_reset"


class PaymentRequired(RuntimeError):
    """X API が 402（クレジット切れ）を返した."""


def is_payment_error(err) -> bool:
    s = str(err or "").lower()
    return any(m in s for m in PAYMENT_MARKERS)


def _ts(p: dict):
    try:
        return datetime.fromisoformat(p["ts"])
    except Exception:
        return None


def payment_streak(posts: list) -> int:
    """新しい順に見て、402 由来の error が何件連続しているか。

    402 以外の記録（posted / 別理由の error / skipped 等）か breaker_reset で途切れる。
    """
    rows = sorted((p for p in posts if _ts(p)), key=_ts, reverse=True)
    n = 0
    for p in rows:
        if p.get("status") == "error" and is_payment_error(p.get("error")):
            n += 1
        else:
            break
    return n


def threshold(cfg: dict) -> int:
    return int(cfg.get("circuit_breaker", {}).get("consecutive_402", 2))


def is_tripped(cfg: dict, posts: list = None) -> bool:
    if not cfg.get("circuit_breaker", {}).get("enabled", True):
        return False
    if posts is None:
        from . import ledger
        posts = ledger._load().get("posts", [])
    return payment_streak(posts) >= threshold(cfg)


def preflight(cfg: dict) -> None:
    """収集・生成の前に X API が使えるか確認する。402 なら PaymentRequired を投げる。

    402 以外の失敗（認証エラー等）も投稿できない前兆なので RuntimeError で止める。
    投稿系の課金（生成コスト）を守るのが目的のため、確認自体は読み取り1回だけ。
    """
    if not cfg.get("circuit_breaker", {}).get("preflight", True):
        return
    from . import publish
    try:
        publish._get_client().get_me()
    except Exception as e:
        if is_payment_error(e):
            raise PaymentRequired(f"preflight: {type(e).__name__}: {e}") from e
        raise RuntimeError(f"preflight: {type(e).__name__}: {e}") from e


def reset(note: str = "manual") -> None:
    from . import ledger
    ledger.record("-", "", f"breaker reset ({note})", "", [], "", status=RESET_STATUS)


def main():
    from .config import load_config
    from . import ledger
    ap = argparse.ArgumentParser(description="X Autopilot 課金ブレーカー")
    ap.add_argument("--reset", action="store_true", help="支払い確認後にブレーカーを解除する")
    args = ap.parse_args()
    cfg = load_config()
    if args.reset:
        reset()
        log("ブレーカーを解除しました（台帳に breaker_reset を記録）。")
    posts = ledger._load().get("posts", [])
    log(f"402連続 {payment_streak(posts)}件 / しきい値 {threshold(cfg)} / "
        f"状態: {'作動中（スロット実行停止）' if is_tripped(cfg, posts) else '正常'}")


if __name__ == "__main__":
    main()
