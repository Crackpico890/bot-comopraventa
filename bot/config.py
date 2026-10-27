import os
from pathlib import Path

import yaml


def load(path: str = None) -> dict:
    path = path or os.environ.get("BOT_CONFIG", "config.yaml")
    cfg = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    cfg["telegram_token"] = os.environ.get("TELEGRAM_TOKEN", "")
    cfg["telegram_chat_id"] = os.environ.get("TELEGRAM_CHAT_ID", "")
    cfg["db_path"] = os.environ.get("DB_PATH", "data/bot.db")
    cfg["dry_run"] = os.environ.get("DRY_RUN", "0") == "1"
    enabled = cfg.get("enabled_platforms", ["wallapop", "vinted"])
    for p in cfg["products"]:
        p["platforms"] = [x for x in p.get("platforms", ["wallapop", "vinted"]) if x in enabled]
        p.setdefault("must_include", [])
        p.setdefault("exclude", [])
    return cfg
