# X Autopilot — 海外インテリジェンス自動発信エンジン

**Grok(Live Search) で海外＋日本のシグナルを収集 → Claude で翻訳・分析・ツリー生成 → 自動安全ゲート → X API で1日4回ツリー投稿**する、完全自動の発信ライン。

- 投稿先: **@daichi631056**（daichi/DC 統一人格・X主戦場 / `57_アカウント群総合戦略`）
- テーマ: **金融 × 不動産 × AI × 地方創生**（海外で先行する情報を、日本の読者が今日使える形に翻訳）
- モード: **完全自動・直接投稿**（生成も投稿もスクリプトが行う＝Claude本体がブラウザで押すわけではないので安全層のブロック対象外。これがnote/IGと違いXだけ完全自動にできる理由）

---

## なぜ「完全自動」がXだけ可能なのか
note・IGは「人間が公開ボタンを押す」設計（安全層）。本システムは**独立したPythonがX API経由で投稿**するため、人手ゼロで回せる。`sns-autopilot` SKILL にも「X API登録後はXのみ自動投稿を別途検討」と想定済みのルート。

---

## アーキテクチャ（3層・Daichi-Fund方式）

```
[自動化層]  GitHub Actions cron（朝2・夜2 / クラウド実行＝PCオフでも動く）
                │  ┌─ morning1 07:00 海外金融×不動産
                │  ├─ morning2 08:30 AI×地方創生
                │  ├─ evening1 19:00 日米情報格差（実務手法）
                │  └─ evening2 21:00 本日の海外ニュース×日本 深掘り
                ▼
[生成パイプライン] collect(Grok Live Search) → analyze(Claude翻訳/分析/ツリー)
                → gates(経歴正本NG / 投資助言ブロック / 重複 / 字数) → publish(X API)
                ▼
[学習層]    weekly learn（インプレ/エンゲージ取得 → 執筆指針 guidance.json を更新）
```

| ファイル | 役割 |
|---|---|
| `config.yaml` | スロット・テーマ・スレ構造・ゲート・投稿モードの全設定（**ここだけ編集すれば挙動が変わる**） |
| `data/persona.md` | 人格・経歴正本・トーンの正典（NG語の本体） |
| `xautopilot/collect.py` | Grok Live Search で海外+日本のシグナル収集 |
| `xautopilot/analyze.py` | Claude で翻訳・分析・ツリー生成（JSON出力） |
| `xautopilot/gates.py` | 自動安全ゲート |
| `xautopilot/publish.py` | ツイート整形＋X API v2 ツリー投稿 |
| `xautopilot/run.py` | 1スロットの司令塔（収集→生成→ゲート→投稿） |
| `xautopilot/learn.py` | 週次学習 |

---

## 必要なAPI（3つ・オーナー手配）
| API | 用途 | 月額目安 | 取得先 |
|---|---|---|---|
| **Claude (Anthropic)** | 翻訳・分析・スレ生成 | $5〜15 | console.anthropic.com |
| **Grok (xAI)** | 海外/日本のリアルタイム収集 | 実質$0（月$175無料枠内） | console.x.ai |
| **X API v2（書き込み）** | 投稿 | $10〜30（従量・下記参照） | developer.x.com |

> ⚠️ **X API は2026年2月で無料枠廃止**。新規は従量課金（要クレカ登録）。テキスト投稿 $0.015/件、**URL入り投稿 $0.20/件**。本システムは本文にURLを貼らず、note誘導はセルフリプ1本のみ＝そのCTAだけURL課金。コスト最小化したい場合は `config.yaml` の `thread.cta_text` をURLなし（「プロフィールのリンクから」）に変えると月のURL課金がほぼゼロになる。

### X API のセットアップ手順（@daichi631056 で）
1. developer.x.com で開発者登録 → 従量課金（pay-per-use）を有効化（支払い方法登録）
2. Project + App を作成
3. App の **User authentication settings** で **OAuth 1.0a / Read and Write** を有効化
4. **Keys and tokens** で API Key/Secret と、**@daichi631056 の Access Token/Secret** を発行
   （※ Access Token はログイン中アカウントのもの＝必ず @daichi631056 でログインして発行）
5. 取得した値は**チャットに貼らず**、下記いずれかに直接設定（02-Mistakes 2026-06-10 恒久ルール）

---

## セットアップ（2つの運用方法）

### A. GitHub Actions（推奨・クラウド・PCオフでも動く）
1. このフォルダを git リポジトリ化して GitHub に push（**`.env` は push しない**＝.gitignore済）
2. リポジトリ **Settings → Secrets and variables → Actions** で以下を登録（値は画面に直接入力）:
   `ANTHROPIC_API_KEY` / `XAI_API_KEY` / `X_API_KEY` / `X_API_SECRET` / `X_ACCESS_TOKEN` / `X_ACCESS_TOKEN_SECRET` /（任意）`SLACK_BOT_TOKEN` / `SLACK_DM_CHANNEL`
3. Actions タブ → **X Autopilot Post** → Run workflow（slot=morning1, dry_run=✓）で**ドライラン確認**
4. 問題なければ以後 cron（朝2夜2）で自動稼働。`learn.yml` が日曜に指針更新。

### B. Windows タスクスケジューラ（ローカル・PC起動時のみ）
1. `pip install -r requirements.txt`
2. `.env.example` を `.env` にコピーし、PowerShellで環境変数 or `.env` に実値を設定
3. `powershell -ExecutionPolicy Bypass -File scripts\register_windows_tasks.ps1`（4スロット＋週次を登録）

---

## ローカルでの動作確認
```powershell
pip install -r requirements.txt
python tests\smoke.py                          # ネット不要・ロジック検証（投稿なし）
python -m xautopilot.run --slot morning1 --dry-run   # 実収集・実生成、投稿だけしない
```
`--dry-run` は Grok/Claude を実際に呼んで**完成ツリーをログ＆Slackに出すが投稿しない**。本番投入前に必ず数回回して内容の質を確認する。

---

## 設定のツボ（config.yaml）
- `slots.*.theme` … 4スロットの切り口。ここを変えれば発信テーマが変わる
- `thread.max_tweets` / `max_chars_per_tweet` … ツリーの長さ・1ツイート字数（X重み近似）
- `thread.cta_text` … セルフリプのCTA（URL課金を避けたいならURLを外す）
- `publish.enabled` … `false`で全スロットがドライラン化（緊急停止スイッチ）
- `publish.mode` … `direct`（即投稿）/ `buffer`（Slack予告後に投稿）/ `draft`（下書き配信のみ）
- `gates.*` … NG語・投資助言・重複しきい値

---

## 自動安全ゲート（完全自動の保険）
| ゲート | 動作 |
|---|---|
| 経歴正本NG語（コンサル8年/社内最優秀賞/実社名 等） | 検知→再生成→消えなければ**投稿中止＋Slack通知** |
| 投資助言リスク（必ず儲かる/今すぐ買い 等） | **免責文を自動付与**して中立化（Daichi-Fund原則: AIは推薦しない） |
| 重複（過去30日と類似） | 別角度で再生成→ダメならスロットスキップ |
| 字数/本数 | 連番付与・自動切り詰めで吸収 |
| 同日同スロット二重投稿 | 台帳でブロック（02-Mistakes 2026-06-09） |

---

## コスト試算（@4スレ/日）
- Grok収集: ~120回/月 × Live Search $5/1k ≈ **$0.6**（月$175無料枠内＝実質$0）
- Claude生成: ~120スレ/月 ≈ **$5〜15**
- X投稿: 本文~480件 × $0.015 ≈ $7＋CTA URL 120件 × $0.20 ≈ $24 → **$10〜31**（CTAをURLなしにすれば~$7）
- **合計 月$15〜45**

---

## トラブルシュート
| 症状 | 対処 |
|---|---|
| `XAI_API_KEY 未設定` | Secrets/.env を確認 |
| X投稿が 403 | App権限が Read and Write か / Access Tokenが@daichi631056のものか / 従量課金が有効か |
| 文字数オーバーで切れる | `config.yaml` の `max_chars_per_tweet` を下げる |
| 重複ばかりでスキップ | `gates.dedup_similarity` を上げる（厳しく）/ テーマを増やす |
| 内容の質が低い | `analyze.compose_model` を `claude-opus-4-8` に / persona.md を調整 |
| 即止めたい | `config.yaml` `publish.enabled: false`（次回からドライラン化） |
