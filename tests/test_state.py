"""Тесты state-логики (дедупликация): запись/чтение, отсутствие файла."""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# bot.py валидирует конфиг при импорте — даём минимально валидный env
os.environ["MAX_BOT_TOKEN"] = "test-token"
os.environ["TARGET_CHAT_ID"] = "-1"
os.environ["VK_TOKEN"] = "test-vk-token"

from src import bot  # noqa: E402


def test_read_missing_state_returns_empty(tmp_path):
    missing = tmp_path / "nope/vk_last_id"
    assert bot._read_state(str(missing)) == ""


def test_state_roundtrip(tmp_path):
    f = tmp_path / "rsch_last_mid"
    bot._save_state(str(f), "mid_123")
    assert bot._read_state(str(f)) == "mid_123"
    # перезапись
    bot._save_state(str(f), "mid_456")
    assert bot._read_state(str(f)) == "mid_456"
