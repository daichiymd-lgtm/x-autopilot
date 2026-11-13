"""収集層: web検索つきLLMでトレンド発見と多段深掘りリサーチ.

検索の担い手は config.yaml の collect.provider で切り替える。
  - "claude": Claude API の web_search サーバーツール（xAI の課金なし。2026-11 以降の既定）
  - "grok"  : Grok (xAI) Responses API + web_search（旧来。XAI_API_KEY が必要）

v2 (2026-06-11): 「おっさんのつぶやき」排除のための3段収集に拡張。
  1) scan_trends()   : いま熱い話題を発見（固定テーマではなくトレンド起点）
  2) deep_research() : 選定話題を2パスで深掘り（パスA=事実・数字 / パスB=日本への実務含意）
  3) collect_signals(): 旧来のテーマ収集（トレンドスキャン失敗時のフォールバック）
失敗時は次の段に安全にフォールバックし、パイプラインを止めない。
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
            if not content and block.get("type") == "output_text":
                content += block.get("text", "")
            for ann in block.get("annotations", []):
                if ann.get("type") == "url_citation" and ann.get("url"):
                    citations.append(ann["url"])

    return content, citations


def _grok(cfg: dict, sys_prompt: str, user: str, timeout: int = 180) -> tuple[str, list]:
    """Grok web_search 1コールの共通ラッパー。失敗は例外で返す。"""
    key = os.environ.get("XAI_API_KEY")
    if not key:
        raise RuntimeError("XAI_API_KEY が未設定です")
    body = {
        "model": cfg["collect"]["grok_model"],
        "input": [
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": user},
        ],
        "tools": [{"type": "web_search"}],
    }
    r = requests.post(
        XAI_RESPONSES_URL,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json=body, timeout=timeout,
    )
    if not r.ok:
        log(f"Grok API エラー {r.status_code}: {r.text[:300]}")
        r.raise_for_status()
    return _extract_text_and_citations(r.json())


def _claude_search(cfg: dict, sys_prompt: str, user: str) -> tuple[str, list]:
    """Claude の web_search サーバーツール 1コールの共通ラッパー。失敗は例外で返す。

    検索は Anthropic 側で実行される。サーバー側のループが上限に達すると
    stop_reason="pause_turn" で返るので、アシスタントの応答を付けて再送し続きを受け取る。
    """
    from anthropic import Anthropic
    c = cfg["collect"]
    client = Anthropic()
    tools = [{
        "type": "web_search_20260209",
        "name": "web_search",
        "max_uses": int(c.get("claude_max_searches", 5)),
    }]
    messages = [{"role": "user", "content": user}]
    texts, citations = [], []
    for _ in range(int(c.get("claude_max_continuations", 3)) + 1):
        resp = client.messages.create(
            model=c.get("claude_model", cfg["analyze"]["compose_model"]),
            max_tokens=4000,
            system=sys_prompt,
            tools=tools,
            messages=messages,
        )
        for block in resp.content:
            if block.type == "text":
                texts.append(block.text)
                for cit in getattr(block, "citations", None) or []:
                    url = getattr(cit, "url", None)
                    if url:
                        citations.append(url)
            elif block.type == "web_search_tool_result":
                # 成功時は結果のリスト、失敗時は error_code を持つ単一オブジェクト
                if isinstance(block.content, list):
                    citations.extend(r.url for r in block.content if getattr(r, "url", None))
                else:
                    log(f"web_search エラー: {getattr(block.content, 'error_code', block.content)}")
        if resp.stop_reason != "pause_turn":
            break
        messages = [{"role": "user", "content": user},
                    {"role": "assistant", "content": resp.content}]
    if resp.stop_reason == "refusal":
        raise RuntimeError("Claude が検索リクエストを拒否しました")
    content = "".join(texts).strip()
    if not content:
        raise RuntimeError("Claude web_search の本文が空です")
    return content, list(dict.fromkeys(citations))


def _search(cfg: dict, sys_prompt: str, user: str) -> tuple[str, list]:
    """collect.provider に応じて検索つきLLMを呼ぶ。返り値は (本文, 出典URLのリスト)。"""
    provider = cfg["collect"].get("provider", "grok")
    if provider == "claude":
        return _claude_search(cfg, sys_prompt, user)
    if provider == "grok":
        return _grok(cfg, sys_prompt, user)
    raise RuntimeError(f"未知の collect.provider: {provider}")


# ---------------------------------------------------------------
# 1) トレンドスキャン: 「いま何が熱いか」を発見する
# ---------------------------------------------------------------

def scan_trends(cfg: dict, lens: str) -> dict:
    """直近24-36hで日本の読者が実際に反応している話題を発見する。

    lens = スロットの守備領域（例: 海外金融×不動産）。固定テーマではなく
    「いま熱い × 読者层に刺さる × 実用に変換できる」話題候補を返す。
    """
    sys_prompt = (
        "あなたは日本のSNSトレンドアナリスト兼編集者。"
        "Xや経済ニュースで『いま実際に話題になっている・検索されている』ことを正確に把握し、"
        "感想ではなく一次情報（数字・固有名詞・日付）で語る。"
    )
    user = (
        f"対象読者: 日本の不動産・金融・AIに関心がある実務家/投資家/副業層。\n"
        f"守備領域レンズ: {lens}\n\n"
        "直近24〜36時間で、この読者層が高確率で反応する『いま熱い話題』を6件、web検索で発見してください。\n"
        "選定基準: ①いま話題性が立ち上がっている（発表・急変・バズ）②読者の金や仕事に直結 ③具体的な数字や固有名詞がある。\n"
        "各件、必ず次の形式で:\n"
        "【話題N】タイトル（数字・固有名詞入り）\n"
        "・何が起きた: 事実1-2行（数字必須）\n"
        "・なぜ今熱い: 話題性の根拠（誰が騒いでいるか/何がトリガーか）\n"
        "・実用の切り口: 読者が『役に立った』と感じる変換方法\n"
        "・鮮度: 何時間前の話か\n"
        "・出典: 媒体名"
    )
    try:
        content, citations = _search(cfg, sys_prompt, user)
        log(f"トレンドスキャン 完了（{len(content)}字 / 出典{len(citations)}件）")
        return {"raw": content, "citations": citations}
    except Exception as e:
        log(f"トレンドスキャン 失敗（{e}）→ テーマ収集にフォールバック")
        return {"raw": "", "citations": [], "fallback": True}


# ---------------------------------------------------------------
# 2) 深掘りリサーチ: 選定話題を2パスで掘る
# ---------------------------------------------------------------

def deep_research(cfg: dict, topic: str, useful_angle: str) -> dict:
    """選定された1話題を2パスで深掘り。

    パスA = 事実・数字・固有名詞・タイムライン（厚い一次情報）
    パスB = 日本の読者への実務含意・使い方・注意点・反対意見
    """
    sys_prompt = (
        "あなたは金融×不動産×AIに精通したリサーチャー。"
        "web検索で一次情報に当たり、数字・固有名詞・日付を正確に引く。"
        "推測と事実を区別し、感想を書かない。個別銘柄の売買推奨はしない。"
    )

    facts, cits_a = "", []
    practical, cits_b = "", []

    # パスA: 事実と数字
    try:
        facts, cits_a = _search(cfg, sys_prompt, (
            f"話題: {topic}\n\n"
            "この話題について、web検索で以下を徹底的に集めてください:\n"
            "1. 具体的な数字を最低10個（金額・%・件数・日付・期間。出典つき）\n"
            "2. 関係する固有名詞（企業名・人名・制度名・サービス名）\n"
            "3. 時系列（何がいつ起きて今どの段階か）\n"
            "4. 海外と日本それぞれの状況の違い\n"
            "確認できなかったことは『不明』と明記。"
        ))
        log(f"深掘りA(事実) 完了（{len(facts)}字 / 出典{len(cits_a)}件）")
    except Exception as e:
        log(f"深掘りA 失敗（{e}）")

    # パスB: 実務への変換
    try:
        practical, cits_b = _search(cfg, sys_prompt, (
            f"話題: {topic}\n"
            f"読者に役立てる切り口: {useful_angle}\n\n"
            "日本の不動産・金融・AI実務家/投資家がこの話題を『自分ごと』にするための材料をweb検索で集めてください:\n"
            "1. 読者が今日からできる具体的アクション・チェック項目（3-5個）\n"
            "2. 数字で語れる判断基準（『◯◯が△△を超えたら』のような閾値）\n"
            "3. 見落としがちなリスク・反対意見・うまくいかないケース\n"
            "4. 関連して読者が次に調べるべきこと\n"
            "一般論ではなく、検索で得た実例・実数に基づくこと。"
        ))
        log(f"深掘りB(実務) 完了（{len(practical)}字 / 出典{len(cits_b)}件）")
    except Exception as e:
        log(f"深掘りB 失敗（{e}）")

    return {
        "facts": facts,
        "practical": practical,
        "citations": (cits_a + cits_b)[:10],
        "fallback": not (facts or practical),
    }


# ---------------------------------------------------------------
# 3) 旧来のテーマ収集（フォールバック用に維持）
# ---------------------------------------------------------------

def collect_signals(cfg: dict, theme: str) -> dict:
    """テーマに対する直近の重要シグナルを収集（トレンドスキャン失敗時の保険）。"""
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
    try:
        content, citations = _search(cfg, sys_prompt, user)
        log(f"テーマ収集 完了（本文{len(content)}字 / 出典{len(citations)}件）")
        return {"raw": content, "citations": citations}
    except Exception as e:
        log(f"テーマ収集 失敗（{e}）→ Claude単体モードにフォールバック")
        return {"raw": "", "citations": [], "fallback": True}
