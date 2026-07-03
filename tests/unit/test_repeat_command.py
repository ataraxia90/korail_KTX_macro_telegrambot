"""Unit tests for repeating the previous reservation search."""
from unittest.mock import Mock

from handlers.command_handler import CommandHandler
from models import TrainSearchParams, UserCredentials, UserProgress, UserSession


class FakeStorage:
    def __init__(self, session=None, running=None):
        self.session = session
        self.running = running
        self.saved_session = None

    def get_running_reservation(self, chat_id):
        return self.running

    def get_user_session(self, chat_id):
        return self.session

    def save_user_session(self, session):
        self.saved_session = session
        self.session = session


def _search_params(**overrides):
    params = {
        "provider": "SRT",
        "dep_date": "20991231",
        "src_locate": "대전",
        "dst_locate": "수서",
        "dep_time": "082500",
        "max_dep_time": "0830",
        "train_type": "SRT",
        "train_type_display": "SRT",
        "special_option": "ReserveOption.GENERAL_FIRST",
        "special_option_display": "GENERAL_FIRST",
        "passenger_count": 1,
        "seat_strategy": "consecutive",
    }
    params.update(overrides)
    return TrainSearchParams(**params)


def test_repeat_command_restores_last_search_for_confirmation():
    session = UserSession(chat_id=12345)
    session.credentials = UserCredentials(korail_id="010-1234-5678", korail_pw="password")
    session.last_search_params = _search_params()
    storage = FakeStorage(session=session)
    telegram = Mock()

    handler = CommandHandler(storage, telegram, Mock(), Mock())
    handler.handle_repeat(12345)

    assert storage.saved_session.last_action == UserProgress.SEAT_STRATEGY_INPUT_SUCCESS
    assert storage.saved_session.in_progress is True
    assert storage.saved_session.train_info["provider"] == "SRT"
    assert storage.saved_session.train_info["srcLocate"] == "대전"
    assert storage.saved_session.train_info["dstLocate"] == "수서"
    assert storage.saved_session.search_params == session.last_search_params
    telegram.send_message.assert_called_once()
    assert "직전 예약 조건" in telegram.send_message.call_args[0][1]


def test_repeat_command_requires_previous_search():
    session = UserSession(chat_id=12345)
    session.credentials = UserCredentials(korail_id="010-1234-5678", korail_pw="password")
    storage = FakeStorage(session=session)
    telegram = Mock()

    handler = CommandHandler(storage, telegram, Mock(), Mock())
    handler.handle_repeat(12345)

    assert storage.saved_session is None
    telegram.send_message.assert_called_once()
    assert "반복할 예약 조건" in telegram.send_message.call_args[0][1]


def test_user_session_reset_preserves_last_search_params():
    session = UserSession(chat_id=12345)
    session.search_params = _search_params(provider="KTX", dst_locate="서울")
    session.last_search_params = session.search_params
    session.in_progress = True
    session.last_action = UserProgress.FINDING_TICKET

    session.reset()

    assert session.search_params is None
    assert session.last_search_params is not None
    assert session.last_search_params.dst_locate == "서울"
