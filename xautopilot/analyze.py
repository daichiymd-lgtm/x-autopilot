"""分析・生成層: 話題選定 → リサーチブリーフ統合 → スレッド生成 → 品質採点.

v2 (2026-06-11): 「おっさんのつぶやき」排除エンジン。
  select_topic()     : トレンド候補から「いま熱い×読者に刺さる×実用化できる」1本を選定
  synthesize_brief() : 深掘りリサーチ2パスを執筆用ブリーフに統合
  compose_thread()   : ブリーフ/シグナルからスレッド生成（フック=Opus / 本文=Sonnet の2フェーズ）
  score_thread()     : 有用性ルーブリック採点＋つぶやき検出（不合格は再生成）
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


def _client():
    from anthropic import Anthropic
    return Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])


def _loads_lenient(raw: str, cfg: dict = None) -> dict:
    """LLM出力JSONの寛容パース。壊れていたらHaikuで修復（本文中の未エスケープ引用符対策）."""
    text = _strip_json(raw)
    try:
        return json.loads(text)
    except Exception:
        pass
    try:
        return json.loads(text, strict=False)
    except Exception:
        pass
    try:  # 生の改行が文字列内に入っているケース
        return json.loads(text.replace("\r", "").replace("\n", "\\n"), strict=False)
    except Exception:
        pass
    # 最終手段: Haiku に修復させる（安価・確実）
    if cfg:
        try:
            c = _client()
            msg = c.messages.create(
                model=cfg["analyze"].get("translate_model", "claude-haiku-4-5-20251001"),
                max_tokens=2500,
                messages=[{"role": "user", "content":
                           "次のテキストを有効なJSONに修復し、JSONのみを返してください。"
                           "文字列内の引用符はエスケープし、内容は変更しないこと。\n\n" + text[:6000]}])
            fixed = _strip_json(msg.content[0].text)
            log("JSON修復: Haikuで復元成功")
            return json.loads(fixed, strict=False)
        except Exception as e:
            log(f"JSON修復失敗: {e}")
    raise json.JSONDecodeError("unrecoverable", text[:80], 0)


# ---------------------------------------------------------------
# 話題選定: トレンド候補 → 1本に絞る
# ---------------------------------------------------------------

def select_topic(cfg: dict, trends_raw: str, lens: str, style: str,
                 recent_topics: list) -> dict:
    """トレンドスキャン結果から、このスロットで書くべき1話題を選定する。"""
    c = _client()
    recent = "\n".join(f"- {t}" for t in recent_topics[:15]) or "（なし）"
    prompt = (
        "あなたはXで伸びるコンテンツの編集長。以下のトレンド候補から、"
        "このアカウントが今すぐ書くべき1本を選び、編集方針を決める。\n\n"
        f"# アカウントの読者\n日本の不動産・金融・AIの実務家/投資家/副業層\n\n"
        f"# スロットの守備領域レンズ\n{lens}\n\n"
        f"# スロットの形式\n{style}\n\n"
        f"# 直近に投稿済みの話題（重複禁止）\n{recent}\n\n"
        f"# トレンド候補\n{trends_raw[:4000]}\n\n"
        "選定基準（すべて満たすこと）:\n"
        "1. いま話題性が立ち上がっている（鮮度が高い）\n"
        "2. 読者の金・仕事・効率に直結し『役に立つ』形に変換できる\n"
        "3. 具体的な数字・固有名詞で語れる\n"
        "4. 直近投稿と重複しない\n\n"
        "出力JSONのみ:\n"
        '{"topic":"選定した話題（40字以内）",'
        '"why_now":"なぜ今これか（1行）",'
        '"useful_angle":"読者にどう役立てるか（具体的に1-2行）",'
        '"content_format":"保存版チェックリスト|速報解説|比較表|手順ガイド|データ解説 のいずれか",'
        '"research_keywords":"深掘り検索用キーワード（スペース区切り3-5語）"}'
    )
    msg = c.messages.create(
        model=cfg["analyze"]["compose_model"], max_tokens=600,
        messages=[{"role": "user", "content": prompt}])
    data = _loads_lenient(msg.content[0].text, cfg)
    log(f"話題選定: {data.get('topic', '')} / 形式={data.get('content_format', '')}")
    return data


# ---------------------------------------------------------------
# ブリーフ統合: リサーチ2パス → 執筆用ブリーフ
# ---------------------------------------------------------------

def synthesize_brief(cfg: dict, topic: dict, research: dict) -> str:
    """深掘りリサーチを、執筆に直接使える高密度ブリーフへ統合する。"""
    c = _client()
    prompt = (
        "あなたはリサーチエディター。以下のリサーチ素材を、Xスレッド執筆用の"
        "『リサーチブリーフ』に統合してください。\n\n"
        f"# 話題\n{topic.get('topic', '')}\n"
        f"# 実用の切り口\n{topic.get('useful_angle', '')}\n"
        f"# 形式\n{topic.get('content_format', '')}\n\n"
        f"# リサーチA（事実・数字）\n{research.get('facts', '')[:5000]}\n\n"
        f"# リサーチB（実務含意）\n{research.get('practical', '')[:5000]}\n\n"
        "ブリーフ要件:\n"
        "- 使うべき数字TOP8（数字＋単位＋何の数字か＋出典）\n"
        "- 話の骨子（事実→意味→日本の読者への含意 の3段）\n"
        "- 読者が保存したくなる実用要素（チェックリスト/判断基準/手順）\n"
        "- 反対意見・リスク（1つは必ず）\n"
        "- 締めの問い（読者がリプしたくなる具体的な二択or質問）\n"
        "Markdownで簡潔に。事実とリサーチにないことは書かない。"
    )
    msg = c.messages.create(
        model=cfg["analyze"]["compose_model"], max_tokens=1500,
        messages=[{"role": "user", "content": prompt}])
    brief = msg.content[0].text
    log(f"ブリーフ統合 完了（{len(brief)}字）")
    return brief


# ---------------------------------------------------------------
# スレッド生成（フック=Opus / 本文=Sonnet）
# ---------------------------------------------------------------

def _gen_hook(client, model: str, persona: str, theme: str, signals: dict,
              guidance: dict, jp_max: int, regen_hint: str = "") -> str:
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
        f"# リサーチブリーフ\n{signals.get('raw', '')[:2500]}\n\n"
        f"# 今週の執筆指針\n{json.dumps(guidance, ensure_ascii=False)}\n\n"
        + (f"# 再生成の指示\n{regen_hint}\n\n" if regen_hint else "")
        + "このテーマの『フック（1ツイート目）』だけを1文で書いてください。"
    )
    msg = client.messages.create(
        model=model, max_tokens=300,
        system=sys_prompt,
        messages=[{"role": "user", "content": user}],
    )
    return msg.content[0].text.strip()


def _gen_body(client, model: str, persona: str, theme: str, signals: dict,
              guidance: dict, hook: str, t: dict, jp_max: int,
              regen_hint: str = "") -> dict:
    body_count = t["max_tweets"] - 1
    sys_prompt = persona + "\n\n# 出力ルール\n" + (
        f"- フックに続く{max(t['min_tweets'] - 1, 2)}〜{body_count}ツイートを生成する\n"
        f"- 各ツイートは日本語で約{jp_max - 5}字以内（最大{jp_max}字）。連番は付けない（システムが付ける）\n"
        "- 1ツイート1論点。読者が必ず1つ持ち帰れるようにする\n"
        "- 各ツイートに数字・固有名詞・手順のいずれかを必ず入れる（感想だけのツイート禁止）\n"
        "- 本文=『何が起きたか（事実・数字）→ なぜ重要か（分析）→ 読者はこう使う（実用）』\n"
        "- 保存価値の要素（チェックリスト/判断基準/手順）を最低1ツイート入れる\n"
        "- 末尾ツイートに持ち帰り1行＋読者への具体的な問い（リプを誘発）\n"
        "- 本文中に外部URLは入れない\n"
        "- 海外原文の逐語転載は禁止。事実とデータのみ使い、文章は必ず自分の言葉で\n"
        "- 個別銘柄の売買推奨をしない。誇大・断定（必ず/絶対/今すぐ買い）を使わない\n"
        "- 指定のJSONだけを返す（前後に説明文を付けない）"
    )
    user = (
        f"テーマ: {theme}\n\n"
        f"# フック（1ツイート目・変更不可）\n{hook}\n\n"
        f"# リサーチブリーフ\n{signals.get('raw', '')}\n\n"
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
        model=model, max_tokens=2000,
        system=sys_prompt,
        messages=[{"role": "user", "content": user}],
    )
    return _loads_lenient(msg.content[0].text, {"analyze": {"translate_model": "claude-haiku-4-5-20251001"}})


def _compose_single(client, model: str, persona: str, theme: str, signals: dict,
                    guidance: dict, t: dict, jp_max: int, regen_hint: str = "") -> dict:
    sys_prompt = persona + "\n\n# 出力ルール\n" + (
        f"- {t['min_tweets']}〜{t['max_tweets']}ツイートのツリー（スレッド）にする\n"
        f"- 各ツイートは日本語で約{jp_max - 5}字以内（最大{jp_max}字）。連番は付けない（システムが付ける）\n"
        "- 1ツイート目=フック。一般論・定義から始めない。具体的な数字か逆張りで掴む\n"
        "- 各ツイートに数字・固有名詞・手順のいずれかを必ず入れる（感想だけのツイート禁止）\n"
        "- 本文=『何が起きたか（事実・数字）→ なぜ重要か（分析）→ 読者はこう使う（実用）』\n"
        "- 1ツイート1論点。読者が必ず1つ持ち帰れるようにする\n"
        "- 末尾ツイートに持ち帰り1行＋読者への具体的な問い。本文中に外部URLは入れない\n"
        "- 海外原文の逐語転載は禁止。事実とデータのみ使い、文章は必ず自分の言葉で\n"
        "- 個別銘柄の売買推奨をしない。誇大・断定（必ず/絶対/今すぐ買い）を使わない\n"
        "- 指定のJSONだけを返す（前後に説明文を付けない）"
    )
    user = (
        f"テーマ: {theme}\n\n"
        f"# リサーチブリーフ\n{signals.get('raw', '')}\n\n"
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
        model=model, max_tokens=2200,
        system=sys_prompt,
        messages=[{"role": "user", "content": user}],
    )
    data = _loads_lenient(msg.content[0].text, {"analyze": {"translate_model": "claude-haiku-4-5-20251001"}})
    tweets = data.get("tweets") or []
    if data.get("hook") and (not tweets or tweets[0].strip() != data["hook"].strip()):
        tweets = [data["hook"]] + tweets
    data["tweets"] = [tw.strip() for tw in tweets if tw and tw.strip()]
    return data


def compose_thread(cfg: dict, theme: str, signals: dict, guidance: dict,
                   regen_hint: str = "") -> dict:
    client = _client()
    persona = read_persona(cfg)
    t = cfg["thread"]
    compose_model = cfg["analyze"]["compose_model"]
    hook_model = cfg["analyze"].get("hook_model", compose_model)
    jp_max = int(t["max_chars_per_tweet"] / 2)

    use_split = hook_model != compose_model

    if use_split:
        hook = _gen_hook(client, hook_model, persona, theme, signals, guidance,
                         jp_max, regen_hint)
        log(f"フック生成完了（{hook_model}・{len(hook)}字）: {hook[:40]}…")

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
        data = _compose_single(client, compose_model, persona, theme, signals,
                               guidance, t, jp_max, regen_hint)
        data.setdefault("hook", data["tweets"][0] if data.get("tweets") else "")

    data.setdefault("topic", theme[:40])
    data.setdefault("is_finance", False)
    data.setdefault("sources", [])
    log(f"Claude生成 完了（{len(data['tweets'])}ツイート / topic={data['topic'][:28]}…）")
    return data


# ---------------------------------------------------------------
# 品質採点: 有用性ルーブリック＋つぶやき検出
# ---------------------------------------------------------------

def score_thread(cfg: dict, thread: dict) -> dict:
    """スレッドを有用性ルーブリックで採点する。

    返り値: {"score": 0-10, "tsubuyaki": bool, "fix_hint": str, "breakdown": {...}}
    tsubuyaki=true は『数字や実用がない感想ポエム』＝即再生成対象。
    """
    c = _client()
    tweets_text = "\n---\n".join(thread.get("tweets", []))
    prompt = (
        "あなたはXコンテンツの辛口品質審査員。以下のスレッドを採点する。\n"
        "審査の目的: 『おっさんのつぶやき』（数字のない感想・一般論・ニュースの薄い要約・自分語り）を"
        "市場に出さないこと。読者の時間を奪う価値があるものだけを通す。\n\n"
        f"# スレッド\n{tweets_text}\n\n"
        "採点ルーブリック（合計10点）:\n"
        "- 具体性 0-3: 数字・固有名詞・日付の密度。数字5個未満なら最大1\n"
        "- 実用性 0-3: 読者が今日から使える手順・判断基準・チェックリストがあるか\n"
        "- 保存価値 0-2: ブックマークして後で見返したくなる参照性\n"
        "- フック&会話 0-2: 1ツイート目が掴むか・末尾の問いがリプを誘うか\n\n"
        "つぶやき判定（tsubuyaki=true の条件・1つでも該当なら true）:\n"
        "- 全体が感想・印象論が主体で、検証可能な事実が2個以下\n"
        "- 『〜だと思う』『〜な気がする』だけで根拠の数字がない\n"
        "- ニュースを言い換えただけで読者のアクションに繋がらない\n\n"
        "出力JSONのみ:\n"
        '{"score":0-10,"breakdown":{"specificity":0-3,"utility":0-3,"save_value":0-2,"hook_conv":0-2},'
        '"tsubuyaki":true/false,"fix_hint":"最も効く改善指示を1行（再生成プロンプトに渡す）"}'
    )
    msg = c.messages.create(
        model=cfg["analyze"]["compose_model"], max_tokens=400,
        messages=[{"role": "user", "content": prompt}])
    data = _loads_lenient(msg.content[0].text, cfg)
    log(f"品質採点: {data.get('score')}/10 tsubuyaki={data.get('tsubuyaki')} "
        f"{json.dumps(data.get('breakdown', {}), ensure_ascii=False)}")
    return data
