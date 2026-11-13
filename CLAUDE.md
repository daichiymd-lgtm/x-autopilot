# x-autopilot — AI向けの前提（自動で読み込まれる）

## いまの状態（2026-11-13）
- **凍結中**。Grok も X 投稿も呼ばない（`config.yaml` の `publish.frozen: true` と、workflow の `X_AUTOPILOT_FROZEN=1`）
- cron は止めてあり、`post.yml`・`learn.yml` は手動実行だけ
- 本番のX投稿は別ライン `note-x-daily-post`。このリポジトリで投稿を再開しない
- 凍結の理由：2026-08-24 に xAI から $10 の課金（日曜22:00 JST の evening2 と一致）
- 台帳の事実：最後の投稿成功は 2026-06-25。そのあと 08-24 までの約2か月、**投稿0件のまま272回 API を呼んでいた**（error 301件／posted 64件）

## 触る前に守ること
- 凍結を解く（frozen=false / FROZEN=0）、cron を戻す、`publish.enabled=true` にする → **本人の承認が要る**
- APIキーはチャット・コミット・ログに出さない（`.env` は gitignore 済み。Actions では Secrets）
- 経歴の正本とNG語は `data/persona.md` と `config.yaml` の `gates.ng_words`。勝手に緩めない
- 数値は台帳（`data/_post_ledger.json`）か実行ログで確かめたものだけ書く。確かめられないものは「未確認」と書く

## 出費ガード（`xautopilot/budget.py`）
- 直近3回が連続で `error` → API を1回も呼ばずに停止し、Slack に通知（`budget.max_consecutive_errors`）
- Grok／Claude の呼び出し回数を日・月で数え、上限で停止（`data/_budget.json`）
- `BudgetExceeded` は `BaseException`。`except Exception` のフォールバックで握りつぶさせないため。**`Exception` に変えない**
- 新しく API を呼ぶ箇所を足すときは、Grok は `budget.charge(cfg, "grok")`、Claude は `budget.anthropic_client()` を通す

## 構成
`run.py`（司令塔）→ `collect.py`（Grok）→ `analyze.py`（Claude）→ `gates.py`（安全ゲート）→ `publish.py`（X API）→ `ledger.py`（台帳）。週次の学習は `learn.py`。挙動は `config.yaml` で変える。

## 検証
```bash
python tests/smoke.py          # ネットワーク不要。全項目 OK であること
python -m xautopilot.run --slot morning1 --dry-run   # 凍結中は「凍結中」と出て終わるのが正しい
```
