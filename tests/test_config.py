"""Тесты конфигурации: парсинг .env → Settings, валидация обязательных полей."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.config import Settings, load_settings, validate  # noqa: E402


def test_defaults_are_safe():
    s = load_settings({})
    assert s.polling is True          # по умолчанию — Long Polling
    assert s.target_chat_id == 0      # без канала — бот не настроен
    assert s.state_dir == "./state"
    assert s.vk_domain == "gorodtavda"  # VK — обязательный источник, дефолтный паблик
    assert s.rsch_chat_id == -69712963313704  # РСЧС встроен (Свердловская область)


def test_env_parsing():
    s = load_settings({
        "MAX_BOT_TOKEN": "tok123",
        "TARGET_CHAT_ID": "-69132449857615",
        "VK_DOMAIN": "gorodtavda",
        "VK_TOKEN": "vksecret",
        "RSCH_CHAT_ID": "-69712963313704",
        "NEWS_INTERVAL": "120",
        "POLLING": "false",
        "WEBHOOK_URL": "https://bot.example.com/webhook",
        "TZ": "Asia/Yekaterinburg",
    })
    assert s.token == "tok123"
    assert s.target_chat_id == -69132449857615
    assert s.vk_domain == "gorodtavda"
    assert s.news_interval == 120
    assert s.polling is False
    assert s.webhook_url == "https://bot.example.com/webhook"
    assert s.tz == "Asia/Yekaterinburg"


def test_validate_requires_token_and_target():
    errs = validate(Settings(token="", target_chat_id=0))
    assert any("MAX_BOT_TOKEN" in e for e in errs)
    assert any("TARGET_CHAT_ID" in e for e in errs)


def test_validate_requires_vk():
    # VK — обязательный источник (ядро репостера)
    errs = validate(Settings(token="t", target_chat_id=-1, vk_token=""))
    assert any("VK_TOKEN" in e for e in errs)
    errs = validate(Settings(token="t", target_chat_id=-1, vk_domain="", vk_token="t"))
    assert any("VK_DOMAIN" in e for e in errs)


def test_validate_webhook_requires_url():
    s = Settings(token="t", target_chat_id=-1, polling=False,
                 webhook_url="", vk_token="vksecret")
    errs = validate(s)
    assert any("WEBHOOK_URL" in e for e in errs)
    # с URL — ошибок нет
    s.webhook_url = "https://bot.example.com/webhook"
    assert validate(s) == []
