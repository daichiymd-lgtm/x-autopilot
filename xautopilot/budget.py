"""出費ガード（API呼び出し回数の上限＋連続失敗での自動停止）.

背景（2026-11 追加）:
  台帳 _post_ledger.json では 2026-06-25 を最後に投稿成功が0件のまま、
  約2か月で272回スロットが走り、毎回 Grok と Claude を呼んでいた
  （失敗は最後の投稿段だけ）。止める仕組みが無かったことが原因。

2つのガード:
  1) circuit_open(): 台帳の直近N件が連続 error なら、API を1回も呼ばずに止める
  2) charge(kind):   Grok / Claude の呼び出しを日・月で数え、上限を超えたら止める

BudgetExceeded は BaseException を継承する。collect/analyze の
`except Exception` によるフォールバックで握りつぶされず、必ず実行全体が止まるようにするため。
"""
import json
from .config import ROOT, now_jst, log

BUDGET_FILE = ROOT / "data" / "_budget.json"

# config.yaml に budget が無い場合の既定値（仮置き。変えるときは config.yaml 側で）
DEFAULTS = {
    "max_grok_calls_per_day": 25,
    "max_grok_calls_per_month": 300,
    "max_claude_calls_per_day": 80,
    "max_claude_calls_per_month": 1500,
    "max_consecutive_errors": 3,
}


class BudgetExceeded(BaseException):
    """上限超過・連続失敗。フォールバックで握りつぶさせないため BaseException。"""


def _limits(cfg: dict) -> dict:
    out = dict(DEFAULTS)
    out.update((cfg or {}).get("budget") or {})
    return out


def _load() -> dict:
    try:
        return json.loads(BUDGET_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {"days": {}, "months": {}}


def _save(d: dict) -> None:
    # 日別は直近40日だけ残す（ファイル肥大防止）
    days = d.get("days", {})
    for k in sorted(days)[:-40]:
        days.pop(k, None)
    BUDGET_FILE.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")


def usage(kind: str) -> tuple[int, int]:
    """(今日の回数, 今月の回数) を返す。"""
    d = _load()
    now = now_jst()
    day, month = now.strftime("%Y-%m-%d"), now.strftime("%Y-%m")
    return (d.get("days", {}).get(day, {}).get(kind, 0),
            d.get("months", {}).get(month, {}).get(kind, 0))


def charge(cfg: dict, kind: str) -> None:
    """API を1回呼ぶ直前に呼ぶ。上限を超えるなら呼ばずに BudgetExceeded。"""
    lim = _limits(cfg)
    day_cap = int(lim[f"max_{kind}_calls_per_day"])
    month_cap = int(lim[f"max_{kind}_calls_per_month"])
    d = _load()
    now = now_jst()
    day, month = now.strftime("%Y-%m-%d"), now.strftime("%Y-%m")
    dd = d.setdefault("days", {}).setdefault(day, {})
    mm = d.setdefault("months", {}).setdefault(month, {})
    if dd.get(kind, 0) >= day_cap:
        raise BudgetExceeded(f"{kind} の日次上限 {day_cap} 回に到達（{day}）")
    if mm.get(kind, 0) >= month_cap:
        raise BudgetExceeded(f"{kind} の月次上限 {month_cap} 回に到達（{month}）")
    dd[kind] = dd.get(kind, 0) + 1
    mm[kind] = mm.get(kind, 0) + 1
    _save(d)


def circuit_open(cfg: dict, posts: list) -> bool:
    """直近N件（新しい順）がすべて error なら True＝実行しない。

    品質スキップ・重複スキップ等は「API は呼んだが仕組みは健全」なので数えない。
    """
    n = int(_limits(cfg)["max_consecutive_errors"])
    if n <= 0:
        return False
    relevant = []
    for p in posts:
        if p.get("status") == "breaker_reset":  # 人が支払い等を確認して解除した地点より前は数えない
            break
        if p.get("status") in ("posted", "error"):
            relevant.append(p)
    recent = relevant[:n]
    return len(recent) >= n and all(p.get("status") == "error" for p in recent)


class BudgetedAnthropic:
    """Anthropic クライアントの薄いラッパー。messages.create の前に charge する。"""

    def __init__(self, inner, cfg: dict):
        self._inner = inner
        self._cfg = cfg
        self.messages = self

    def create(self, **kwargs):
        charge(self._cfg, "claude")
        return self._inner.messages.create(**kwargs)


def anthropic_client(cfg: dict = None):
    import os
    from anthropic import Anthropic
    if cfg is None:
        from .config import load_config
        cfg = load_config()
    return BudgetedAnthropic(Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"]), cfg)


def summary(cfg: dict) -> str:
    lim = _limits(cfg)
    parts = []
    for kind in ("grok", "claude"):
        d, m = usage(kind)
        parts.append(f"{kind}: 今日 {d}/{lim[f'max_{kind}_calls_per_day']}・"
                     f"今月 {m}/{lim[f'max_{kind}_calls_per_month']}")
    return " / ".join(parts)


def log_summary(cfg: dict) -> None:
    log("出費ガード: " + summary(cfg))
