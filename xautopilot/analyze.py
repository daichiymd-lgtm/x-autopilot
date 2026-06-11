"""分析・生成層: Claude が翻訳・分析し、読まれるツリー（スレッド）を生成する.

hook_model != compose_model のとき:
  Phase 1 = hook_model（Opus等）でフック1本を全力生成
  Phase 2 = compose_model（Sonnet）でフック以降の本文ツイートを生成
hook_model が未設定 or compose_model と同じ場合は従来のシングルコール方式にフォールバック。
"""
import os
import json
from .config import read_persona, log


def _strip_json(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        body = text.split("```", 2)
        text = body[1] if len(body) > 1 else text
        if text.lstrip().lower().startswith("json"):
            text = text.lstrip()[4:]
        if text.rstrip().endswith("```"):
            text = text.rstrip()[:-3]
    i, j = text.find("{"), text.rfind("}")
    if i >= 0 and j >= 0:
        text = text[i:j + 1]
    return text.strip()


def _gen_hook(client, model: str, persona: str, theme: str, signals: dict,
              guidance: dict, jp_max: int, regen_hint: str = "") -> str:
    """フック（1ツイート目）だけを hook_model で全力生成."""
    sys_prompt = persona + "\n\n# 出力ルール（フック専用）\n" + (
        f"- 1ツイートだけ生成する（スレッドの冒頭フック）\n"
        f"- 日本語で{jp_max - 5}字以内（最大{jp_max}字）\n"
        "- 一般論・定義から始めない。具体的な数字か逆張りで読者を掴む\n"
        "- 結論を出し切らず、続きを読みたくさせる終わり方にする\n"
        "- URLは入れない\n"
        "- テキストのみ返す（JSON不要・説明文不要）"
    )
    user = (
        f"テーマ: {theme}\n\n"
        f"# 収集済みシグナル（Grok Live Search）\n{signals.get('raw', '')[:2500]}\n\n"
        f"# 今週の執筆指針\n{json.dumps(guidance, ensure_ascii=False)}\n\n"
        + (f"# 再生成の指示\n{regen_hint}\n\n" if regen_hint else "")
        + "このテーマの『フック（1ツイート目）』だけを1文で書いてください。"
    )
    msg = client.messages.create(
        model=model, max_tokens=300, temperature=0.8,
        system=sys_prompt,
        messages=[{"role": "user", "content": user}],
    )
    return msg.content[0].text.strip()


def _gen_body(client, model: str, persona: str, theme: str, signals: dict,
              guidance: dict, hook: str, t: dict, jp_max: int,
              regen_hint: str = "") -> dict:
    """フック以降のツイート本体 + メタ情報を compose_model で生成."""
    body_count = t["max_tweets"] - 1
    sys_prompt = persona + "\n\n# 出力ルール\n" + (
        f"- フックに続く{max(t['min_tweets'] - 1, 2)}〜{body_count}ツイートを生成する\n"
        f"- 各ツイートは日本語で約{jp_max - 5}字以内（最大{jp_max}字）。連番は付けない（システムが付ける）\n"
        "- 1ツイート1論点。読者が必ず1つ持ち帰れるようにする\n"
        "- 本文=『海外で何が起きたか（事実・数字）→ なぜ重要か（分析）→ 日本ではこう効く/こう違う（含意）』\n"
        "- 末尾ツイートに持ち帰り1行\n"
        "- 本文中に外部URLは入れない\n"
        "- 海外原文の逐語転載は禁止。事実とデータのみ使い、文章は必ず自分の言葉で\n"
        "- 個別銘柄の売買推奨をしない。誇大・断定（必ず/絶対/今すぐ買い）を使わない\n"
        "- 指定のJSONだけを返す（前後に説明文を付けない）"
    )
    user = (
        f"テーマ: {theme}\n\n"
        f"# フック（1ツイート目・変更不可）\n{hook}\n\n"
        f"# 収集済みシグナル（Grok Live Search）\n{signals.get('raw', '')}\n\n"
        f"# 出典候補\n{json.dumps(signals.get('citations', []), ensure_ascii=False)[:1500]}\n\n"
        f"# 今週の執筆指針\n{json.dumps(guidance, ensure_ascii=False)}\n\n"
        + (f"# 再生成の指示\n{regen_hint}\n\n" if regen_hint else "")
        + "上記フックに続く本文ツイートを生成してください。\n"
        "出力JSON:\n"
        '{"topic":"40字以内の主題",'
        '"tweets":["2ツイート目","3ツイート目","..."],'
        '"sources":["主要な出典URL 最大3"],"is_finance":true/false}'
    )
    msg = client.messages.create(
        model=model, max_tokens=2000, temperature=0.7,
        system=sys_prompt,
        messages=[{"role": "user", "content": user}],
    )
    raw = msg.content[0].text
    return json.loads(_strip_json(raw))


def _compose_single(client, model: str, persona: str, theme: str, signals: dict,
                    guidance: dict, t: dict, jp_max: int, regen_hint: str = "") -> dict:
    """従来のシングルコール方式（hook_model == compose_model のフォールバック）."""
    sys_prompt = persona + "\n\n# 出力ルール\n" + (
        f"- {t['min_tweets']}〜{t['max_tweets']}ツイートのツリー（スレッド）にする\n"
        f"- 各ツイートは日本語で約{jp_max - 5}字以内（最大{jp_max}字）。連番は付けない（システムが付ける）\n"
        "- 1ツイート目=フック。一般論・定義から始めない。具体的な数字か逆張りで掴む\n"
        "- 本文=『海外で何が起きたか（事実・数字）→ なぜ重要か（分析）→ 日本ではこう効く/こう違う（含意）』\n"
        "- 1ツイート1論点。読者が必ず1つ持ち帰れるようにする\n"
        "- 末尾ツイートに持ち帰り1行。本文中に外部URLは入れない\n"
        "- 海外原文の逐語転載は禁止。事実とデータのみ使い、文章は必ず自分の言葉で\n"
        "- 個別銘柄の売買推奨をしない。誇大・断定（必ず/絶対/今すぐ買い）を使わない\n"
        "- 指定のJSONだけを返す（前後に説明文を付けない）"
    )
    user = (
        f"テーマ: {theme}\n\n"
        f"# 収集済みシグナル（Grok Live Search）\n{signals.get('raw', '')}\n\n"
        f"# 出典候補\n{json.dumps(signals.get('citations', []), ensure_ascii=False)[:1500]}\n\n"
        f"# 今週の執筆指針\n{json.dumps(guidance, ensure_ascii=False)}\n\n"
        + (f"# 再生成の指示\n{regen_hint}\n\n" if regen_hint else "")
        + "上記から、日本の不動産・金融・AI関心層が『ためになった』と感じるツリーを1本作ってください。\n"
        "出力JSON:\n"
        '{"topic":"40字以内の主題","hook":"1ツイート目の本文",'
        '"tweets":["1ツイート目","2ツイート目","..."],'
        '"sources":["主要な出典URL 最大3"],"is_finance":true/false}'
    )
    msg = client.messages.create(
        model=model, max_tokens=2200, temperature=0.7,
        system=sys_prompt,
        messages=[{"role": "user", "content": user}],
    )
    raw = msg.content[0].text
    data = json.loads(_strip_json(raw))
    # hook が tweets[0] と重複/欠落するケースを正規化
    tweets = data.get("tweets") or []
    if data.get("hook") and (not tweets or tweets[0].strip() != data["hook"].strip()):
        tweets = [data["hook"]] + tweets
    data["tweets"] = [tw.strip() for tw in tweets if tw and tw.strip()]
    return data


def compose_thread(cfg: dict, theme: str, signals: dict, guidance: dict,
                   regen_hint: str = "") -> dict:
    from anthropic import Anthropic
    client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    persona = read_persona(cfg)
    t = cfg["thread"]
    compose_model = cfg["analyze"]["compose_model"]
    hook_model = cfg["analyze"].get("hook_model", compose_model)
    jp_max = int(t["max_chars_per_tweet"] / 2)

    use_split = hook_model != compose_model

    if use_split:
        # Phase 1: フックだけ hook_model（Opus）で全力生成
        hook = _gen_hook(client, hook_model, persona, theme, signals, guidance,
                         jp_max, regen_hint)
        log(f"フック生成完了（{hook_model}・{len(hook)}字）: {hook[:40]}…")

        # Phase 2: 本文ツイートを compose_model（Sonnet）で生成
        body = _gen_body(client, compose_model, persona, theme, signals, guidance,
                         hook, t, jp_max, regen_hint)
        log(f"本文生成完了（{compose_model}・{len(body.get('tweets', []))}ツイート）")

        body_tweets = [tw.strip() for tw in body.get("tweets", []) if tw and tw.strip()]
        data = {
            "topic": body.get("topic", theme[:40]),
            "hook": hook,
            "tweets": [hook] + body_tweets,
            "sources": body.get("sources", []),
            "is_finance": body.get("is_finance", False),
        }
    else:
        # フォールバック: 従来のシングルコール方式
        data = _compose_single(client, compose_model, persona, theme, signals,
                               guidance, t, jp_max, regen_hint)
        data.setdefault("hook", data["tweets"][0] if data.get("tweets") else "")

    data.setdefault("topic", theme[:40])
    data.setdefault("is_finance", False)
    data.setdefault("sources", [])
    log(f"Claude生成 完了（{len(data['tweets'])}ツイート / topic={data['topic'][:28]}…）")
    return data
