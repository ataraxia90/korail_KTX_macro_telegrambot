"""Deployment health checks must reflect Redis availability."""
import importlib.util
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
from redis.exceptions import ConnectionError

from config.settings import Settings


@pytest.mark.parametrize("redis_error, expected", [(None, 200), (ConnectionError("offline"), 503)])
def test_health_checks_redis_without_sending_messages(monkeypatch, redis_error, expected):
    monkeypatch.setattr(Settings, "TELEGRAM_BOT_TOKEN", "12345:test-token")
    storage = Mock()
    storage.is_debug_mode.return_value = False
    storage.redis.ping.side_effect = redis_error
    with patch("storage.redis.RedisStorage", return_value=storage), patch("services.TelegramService") as telegram:
        spec = importlib.util.spec_from_file_location("health_test_app", Path(__file__).resolve().parents[2] / "src" / "app.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        response = module.application.test_client().get("/health")
    assert response.status_code == expected
    assert response.json == {"status": "ok" if expected == 200 else "unavailable"}
    storage.redis.ping.assert_called_once()
    telegram.return_value.send_message.assert_not_called()
