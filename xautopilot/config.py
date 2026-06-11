"""設定・環境・共通ユーティリティ."""
import os
import sys
import io
from pathlib import Path
from datetime import datetime, timezone, timedelta

# Windows CP932 ターミナルでの文字化け/絵文字落ち回避（02-Mistakes 2026-05-15）
try:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
except Exception:
    pass

# 相対パス事故回避（02-Mistakes: 必ず __file__ 基準の絶対パス）
ROOT = Path(__file__).resolve().parent.parent
JST = timezone(timedelta(hours=9))


def now_jst() -> datetime:
    return datetime.now(JST)


def log(msg: str) -> None:
    print(f"[{now_jst().strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)


def load_env() -> None:
    """ローカル実行時のみ .env を読む。GitHub Actions では環境変数が既に注入済み。"""
    env_path = ROOT / ".env"
    if env_path.exists():
        try:
            from dotenv import load_dotenv
            load_dotenv(env_path)
        except Exception:
            # python-dotenv が無い環境でも手動パースで救う
            for line in env_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip())


def load_config() -> dict:
    import yaml
    with open(ROOT / "config.yaml", "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def get_env(key: str, required: bool = False, default=None):
    v = os.environ.get(key, default)
    if required and not v:
        raise RuntimeError(f"環境変数 {key} が未設定です（.env もしくは Secrets を確認）")
    return v


def read_persona(cfg: dict) -> str:
    p = ROOT / cfg.get("persona_file", "data/persona.md")
    return p.read_text(encoding="utf-8") if p.exists() else ""
