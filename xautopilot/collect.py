"""収集層: Grok (xAI) の Live Search で海外+日本のリアルタイム・シグナルを集める."""
import os
import requests
from .config import log

XAI_URL = "https://api.x.ai/v1/chat/completions"


def collect_signals(cfg: dict, theme: str) -> dict:
    """テーマに対する直近の重要シグナルを Grok Live Search で収集して返す。"""
    key = os.environ.get("XAI_API_KEY")
    if not key:
        raise RuntimeError("XAI_API_KEY が未設定です")
    c = cfg["collect"]
    sources = [{"type": s} for s in c.get("sources", ["web", "x", "news"])]

    sys_prompt = (
        "あなたは金融×不動産×AI×地方創生に精通した海外情報リサーチャー。"
        "海外（米欧アジア）と日本のリアルタイム情報源から、与えられたテーマに関する"
        "直近の重要な動き・具体的な数字・事例を、検索で得た一次情報に基づいて収集する。"
        "古い知識や推測ではなく、検索結果のソースを正確に引く。"
        "金融に触れる場合も、個別銘柄の売買推奨は一切しない。"
    )
    user = (
        f"テーマ: {theme}\n\n"
        f"直近{c.get('recency_hours', 36)}時間を中心に、海外で先行している動き・データ・事例を"
        "5〜8件収集してください。各件を必ず\n"
        "『・何が起きたか（事実と数字）／出典（媒体名とURL）／日本への示唆の種』\n"
        "の形で、日本語で簡潔にまとめてください。"
    )
    body = {
        "model": c["grok_model"],
        "messages": [
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": user},
        ],
        "search_parameters": {
            "mode": "on",
            "sources": sources,
            "max_search_results": c.get("max_search_results", 20),
            "return_citations": True,
        },
        "temperature": 0.3,
    }
    r = requests.post(
        XAI_URL,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json=body, timeout=180,
    )
    r.raise_for_status()
    data = r.json()
    content = data["choices"][0]["message"]["content"]
    # xAI は版により citations の場所が異なるため両対応
    citations = data.get("citations") or \
        data["choices"][0]["message"].get("citations") or []
    log(f"Grok収集 完了（本文{len(content)}字 / 出典{len(citations)}件 / theme={theme[:24]}…）")
    return {"raw": content, "citations": citations}
