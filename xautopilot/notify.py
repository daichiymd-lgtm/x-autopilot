"""Slack通知（任意・SLACK_BOT_TOKEN があれば動く）."""
import os
import requests
from .config import log


def slack(cfg: dict, text: str, thread_ts: str = None):
    if not cfg.get("notify", {}).get("slack_enabled", True):
        return None
    token = os.environ.get("SLACK_BOT_TOKEN")
    channel = os.environ.get("SLACK_DM_CHANNEL") or cfg.get("notify", {}).get("slack_dm", "")
    if not token or not channel:
        log("Slack: token/channel 未設定のため通知スキップ")
        return None
    try:
        payload = {"channel": channel, "text": text}
        if thread_ts:
            payload["thread_ts"] = thread_ts
        r = requests.post(
            "https://slack.com/api/chat.postMessage",
            headers={"Authorization": f"Bearer {token}"},
            json=payload, timeout=20,
        )
        data = r.json()
        if not data.get("ok"):
            log(f"Slack通知失敗: {data.get('error')}")
        return data.get("ts")
    except Exception as e:
        log(f"Slack例外: {e}")
        return None
