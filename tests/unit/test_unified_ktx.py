"""Unified deployment routing without contacting rail or Telegram servers."""
from unittest.mock import Mock, patch

import pytest

from config.settings import settings
from handlers.command_handler import CommandHandler
from handlers.conversation_handler import ConversationHandler
from models import TrainSearchParams, UserCredentials, UserProgress, UserSession
from services.reservation_service import ReservationService
from storage.redis import RedisStorage


@pytest.fixture
def unified(monkeypatch):
    monkeypatch.setattr(settings, "UNIFIED_KTX", True)
    session = UserSession(chat_id=12345)
    storage = Mock()
    storage.get_user_session.return_value = session
    storage.get_running_reservation.return_value = None
    telegram = Mock()
    return session, storage, telegram


def params(**overrides):
    values = dict(dep_date="20991231", src_locate="수서", dst_locate="부산", dep_time="080000")
    values.update(overrides)
    return TrainSearchParams(**values)


def test_start_discards_old_provider_and_skips_selection(unified):
    session, storage, telegram = unified
    session.train_info = {"provider": "SRT", "splitEnabled": True}
    CommandHandler(storage, telegram, Mock(), Mock()).handle_start(session.chat_id)
    assert session.train_info == {"provider": "KTX"}
    assert "통합 KTX" in telegram.send_message.call_args.args[1]
    ConversationHandler(storage, telegram, Mock()).handle_message(session.chat_id, "Y")
    assert session.last_action == UserProgress.PROVIDER_INPUT_SUCCESS
    assert session.train_info["provider"] == "KTX"


def test_legacy_mode_keeps_provider_selection(unified, monkeypatch):
    session, storage, telegram = unified
    monkeypatch.setattr(settings, "UNIFIED_KTX", False)
    session.last_action = UserProgress.STARTED
    handler = ConversationHandler(storage, telegram, Mock())
    handler.handle_message(session.chat_id, "Y")
    assert session.last_action == UserProgress.START_ACCEPTED
    handler.handle_message(session.chat_id, "2")
    assert session.train_info["provider"] == "SRT"


@pytest.mark.parametrize("magic", [False, True])
def test_unified_login_uses_korail_only(unified, monkeypatch, magic):
    session, storage, telegram = unified
    session.last_action = UserProgress.PROVIDER_INPUT_SUCCESS if magic else UserProgress.ID_INPUT_SUCCESS
    session.credentials = UserCredentials(korail_id="010-1234-5678", korail_pw="")
    monkeypatch.setattr(settings, "KORAIL_ADMIN_USER_ID", "010-1234-5678")
    monkeypatch.setattr(settings, "KORAIL_ADMIN_PASSWORD", "password")
    with patch("handlers.conversation_handler.KorailService") as korail, patch("handlers.conversation_handler.SrtService") as srt:
        korail.return_value.login.return_value = True
        ConversationHandler(storage, telegram, Mock()).handle_message(
            session.chat_id, settings.ADMIN_MAGIC_STRING if magic else "password"
        )
        korail.return_value.login.assert_called_once_with("010-1234-5678", "password")
        srt.assert_not_called()
    assert session.last_action == UserProgress.PW_INPUT_SUCCESS


def test_stale_srt_session_cannot_login(unified):
    session, storage, telegram = unified
    session.train_info["provider"] = "SRT"
    session.last_action = UserProgress.ID_INPUT_SUCCESS
    with patch("handlers.conversation_handler.SrtService") as srt:
        ConversationHandler(storage, telegram, Mock()).handle_message(session.chat_id, "password")
        srt.assert_not_called()
    assert "/start" in telegram.send_message.call_args.args[1]


def test_repeat_rejects_legacy_srt_conditions(unified):
    session, storage, telegram = unified
    session.last_search_params = params(provider="SRT", train_type="SRT")
    CommandHandler(storage, telegram, Mock(), Mock()).handle_repeat(session.chat_id)
    storage.save_user_session.assert_not_called()
    assert "/start" in telegram.send_message.call_args.args[1]


@pytest.mark.parametrize("search", [params(provider="SRT"), params(split_enabled=True)])
def test_unified_worker_rejects_legacy_requests(unified, search):
    session, storage, telegram = unified
    with patch("services.reservation_service.subprocess.Popen") as popen:
        assert not ReservationService(storage, telegram).start_reservation_process(
            session.chat_id, "user", "password", search
        )
        popen.assert_not_called()


def test_suseo_reservation_uses_korail_worker(unified):
    session, storage, telegram = unified
    with patch("services.reservation_service.subprocess.Popen") as popen:
        popen.return_value.pid = 42
        storage.get_all_subscribers.return_value = []
        assert ReservationService(storage, telegram).start_reservation_process(
            session.chat_id, "user", "password", params()
        )
        args = popen.call_args.args[0]
        assert args[2] == "telegramBot.telebotBackProcess"
        assert args[6:8] == ["수서", "부산"]
        assert "TrainType.KTX" in args


@pytest.mark.parametrize("url", ["redis://localhost/0", "redis://localhost/1?db=0", "redis://localhost"])
def test_explicit_redis_db_isolates_bot_even_when_url_contains_db(monkeypatch, url):
    monkeypatch.setattr(settings, "REDIS_URL", url)
    monkeypatch.setattr(settings, "REDIS_DB", 2)
    with patch("redis.Redis.ping", return_value=True):
        storage = RedisStorage()
    assert storage.redis.connection_pool.connection_kwargs["db"] == 2
    storage.redis.close()
