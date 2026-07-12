"""Unit tests for reservation callback message handling."""
from unittest.mock import Mock

from api.telegram_webhook import TelegramWebhook


def test_success_callback_message_retries_with_fallback_when_original_send_fails():
    telegram = Mock()
    telegram.send_message.side_effect = [False, True]
    webhook = TelegramWebhook(
        storage=Mock(),
        telegram_service=telegram,
        reservation_service=Mock(),
        payment_reminder_service=Mock(),
    )

    assert webhook._send_callback_message(12345, "detail message", "0") is True

    assert telegram.send_message.call_count == 2
    fallback = telegram.send_message.call_args_list[1].args[1]
    assert "예약이 성공했습니다" in fallback
    assert "결제" in fallback


def test_failure_callback_message_does_not_send_success_fallback():
    telegram = Mock()
    telegram.send_message.return_value = False
    webhook = TelegramWebhook(
        storage=Mock(),
        telegram_service=telegram,
        reservation_service=Mock(),
        payment_reminder_service=Mock(),
    )

    assert webhook._send_callback_message(12345, "failure message", "1") is False

    telegram.send_message.assert_called_once_with(12345, "failure message")
