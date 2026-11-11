#!/usr/bin/env python3
"""Always-on Telegram listener for the research digest (instant votes + commands)."""
from pathlib import Path

from rd import bot, config
from rd.store import Store

HERE = Path(__file__).resolve().parent

if __name__ == "__main__":
    cfg = config.load()
    bot.serve(Store(HERE / "state.db"), cfg)
