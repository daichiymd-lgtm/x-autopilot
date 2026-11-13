"""collect._claude_search の単体テスト（ネットワーク不要・budget.anthropic_client を差し替える）.

実行: python tests/test_collect_claude.py
"""
import sys
from pathlib import Path
from types import SimpleNamespace as NS

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from xautopilot import collect, budget

CFG = {"collect": {"provider": "claude", "claude_model": "m", "claude_max_searches": 2},
       "analyze": {"compose_model": "m"}}


def fake_client(responses, calls):
    def create(**kwargs):
        calls.append(kwargs)
        return responses[len(calls) - 1]
    return lambda cfg=None: NS(messages=NS(create=create))


def text(t, urls=()):
    return NS(type="text", text=t, citations=[NS(url=u) for u in urls])


def results(urls):
    return NS(type="web_search_tool_result", content=[NS(url=u) for u in urls])


def main():
    real = budget.anthropic_client
    try:
        # 1) 通常: 本文と出典（重複は1つにまとまる）
        calls = []
        budget.anthropic_client = fake_client([NS(stop_reason="end_turn", content=[
            results(["https://a.example", "https://b.example"]),
            text("米国の金利は6.48%。", ["https://a.example"]),
        ])], calls)
        body, cits = collect._search(CFG, "sys", "user")
        assert body == "米国の金利は6.48%。", body
        assert cits == ["https://a.example", "https://b.example"], cits
        assert calls[0]["tools"][0] == {"type": "web_search_20260209", "name": "web_search", "max_uses": 2}

        # 2) pause_turn: アシスタント応答を付けて再送し、本文をつなぐ
        calls = []
        first = NS(stop_reason="pause_turn", content=[text("前半。")])
        budget.anthropic_client = fake_client([first, NS(stop_reason="end_turn", content=[text("後半。")])], calls)
        body, _ = collect._search(CFG, "sys", "user")
        assert body == "前半。後半。", body
        assert len(calls) == 2 and calls[1]["messages"][1] == {"role": "assistant", "content": first.content}

        # 3) 検索エラー（content が単一オブジェクト）で本文なし → 例外
        calls = []
        budget.anthropic_client = fake_client([NS(stop_reason="end_turn", content=[
            NS(type="web_search_tool_result", content=NS(error_code="max_uses_exceeded"))])], calls)
        try:
            collect._search(CFG, "sys", "user")
            raise AssertionError("例外が出るはず")
        except RuntimeError:
            pass

        # 4) 例外は collect_signals が fallback に変える（run 側で見送りになる）
        def boom(cfg=None):
            raise RuntimeError("API 不通")
        budget.anthropic_client = boom
        sig = collect.collect_signals({**CFG, "collect": {**CFG["collect"], "recency_hours": 36}}, "テーマ")
        assert sig.get("fallback") is True, sig

        # 5) 出費ガードの停止（BaseException）はフォールバックで握りつぶされない
        def over(cfg=None):
            raise budget.BudgetExceeded("上限")
        budget.anthropic_client = over
        try:
            collect.collect_signals({**CFG, "collect": {**CFG["collect"], "recency_hours": 36}}, "テーマ")
            raise AssertionError("BudgetExceeded が出るはず")
        except budget.BudgetExceeded:
            pass

        # 6) 未知の provider は例外
        try:
            collect._search({"collect": {"provider": "x"}}, "s", "u")
            raise AssertionError("例外が出るはず")
        except RuntimeError:
            pass
    finally:
        budget.anthropic_client = real
    print("collect._claude_search テスト全項目OK")


if __name__ == "__main__":
    main()
