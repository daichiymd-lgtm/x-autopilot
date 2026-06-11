"""収集層: Grok (xAI) の Responses API + web_search tool でリアルタイムシグナルを集める.

2026-06-11: xAI が Live Search (search_parameters) を廃止。
新 Responses API (POST /v1/responses) + tools=[{"type":"web_search"}] に移行。
失敗時は Claude 単体モードにフォールバック。
"""
import os
import requests
from .config import log

XAI_RESPONSES_URL = "https://api.x.ai/v1/responses"


def _extract_text_and_citations(data: dict) -> tuple[str, list]:
    """Responses API のレスポンスからテキストと引用URLを取り出す."""
    content = data.get("output_text", "")
    citations = []

    for item in data.get("output", []):
        if item.get("type") != "message":
            continue
        for block in item.get("content", []):
            # テキスト抽出（output_text が空のフォールバック）
            if not content and block.get("type") == "output_text":
                content += block.get("text", "")
            # 引用情報
            for ann in block.get("annotations", []):
                if ann.get("type") == "url_citation" and ann.get("url"):
                    citations.append(ann["url"])

    return content, citations


def collect_signals(cfg: dict, theme: str) -> dict:
    """テーマに対する直近の重要シグナルを Grok web_search tool で収集して返す。"""
    key = os.environ.get("XAI_API_KEY")
    if not key:
        raise RuntimeError("XAI_API_KEY が未設定です")
    c = cfg["collect"]

    sys_prompt = (
        "あなたは金融×不動産×AI×地方創生に精通した海外情報リサーチャー。"
        "海外（米欧アジア）と日本のリアルタイム情報源から、与えられたテーマに関する"
        "直近の重要な動き・具体的な数字・事例を、web検索で得た一次情報に基づいて収集する。"
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
        "input": [
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": user},
        ],
        "tools": [{"type": "web_search"}],
    }

    try:
        r = requests.post(
            XAI_RESPONSES_URL,
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json=body, timeout=180,
        )
        if not r.ok:
            log(f"Grok API エラー {r.status_code}: {r.text[:500]}")
            r.raise_for_status()
        data = r.json()
        content, citations = _extract_text_and_citations(data)
        log(f"Grok収集 完了（本文{len(content)}字 / 出典{len(citations)}件 / theme={theme[:24]}…）")
        return {"raw": content, "citations": citations}
    except Exception as e:
        log(f"Grok収集 失敗（{e}）→ Claude単体モードにフォールバック")
        return {"raw": "", "citations": [], "fallback": True}
