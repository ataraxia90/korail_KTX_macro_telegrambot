"""Unit tests for SRT split reservation MVP."""
from unittest.mock import Mock, patch

from handlers.conversation_handler import ConversationHandler
from models import TrainSearchParams, UserCredentials, UserProgress, UserSession
from services.reservation_service import ReservationService
from telegramBot.srtSplitBackProcess import SrtSplitBackgroundReservationProcess


class FakeConversationStorage:
    def __init__(self, session):
        self.session = session

    def get_user_session(self, chat_id):
        return self.session

    def save_user_session(self, session):
        self.session = session


class FakeReservationStorage:
    def __init__(self):
        self.running = None

    def get_running_reservation(self, chat_id):
        return self.running

    def save_running_reservation(self, reservation):
        self.running = reservation

    def get_user_session(self, chat_id):
        return None

    def get_all_subscribers(self):
        return []


def _srt_session():
    session = UserSession(chat_id=12345, in_progress=True)
    session.last_action = UserProgress.SPECIAL_INPUT_SUCCESS
    session.credentials = UserCredentials(korail_id="010-1234-5678", korail_pw="password")
    session.train_info = {
        "provider": "SRT",
        "depDate": "20991231",
        "srcLocate": "수서",
        "dstLocate": "대전",
        "depTime": "082500",
        "maxDepTime": "0830",
        "trainType": "SRT",
        "trainTypeShow": "SRT",
        "specialInfo": "ReserveOption.GENERAL_FIRST",
        "specialInfoShow": "GENERAL_FIRST",
    }
    return session


def test_srt_conversation_collects_manual_split_station_and_starts_split_search():
    storage = FakeConversationStorage(_srt_session())
    telegram = Mock()
    reservation = Mock()
    reservation.start_reservation_process.return_value = True
    handler = ConversationHandler(storage, telegram, reservation)

    with patch.object(handler, "_get_target_train_summary", return_value="SRT362(08:25)"):
        handler.handle_message(12345, "1")
        assert storage.session.last_action == UserProgress.AWAITING_SPLIT_OPTION

        handler.handle_message(12345, "2")
        assert storage.session.last_action == UserProgress.AWAITING_SPLIT_VIA_STATION

        handler.handle_message(12345, "평택지제")
        assert storage.session.last_action == UserProgress.SEAT_STRATEGY_INPUT_SUCCESS
        assert storage.session.train_info["splitEnabled"] is True
        assert storage.session.train_info["splitViaStation"] == "평택지제"

        handler.handle_message(12345, "Y")

    search_params = reservation.start_reservation_process.call_args.kwargs["search_params"]
    assert search_params.split_enabled is True
    assert search_params.split_via_station == "평택지제"
    assert search_params.split_mode == "manual"


@patch("services.reservation_service.subprocess.Popen")
def test_reservation_service_uses_srt_split_process_when_enabled(mock_popen):
    mock_popen.return_value.pid = 1234
    storage = FakeReservationStorage()
    telegram = Mock()
    service = ReservationService(storage, telegram)
    params = TrainSearchParams(
        provider="SRT",
        dep_date="20991231",
        src_locate="수서",
        dst_locate="대전",
        dep_time="082500",
        max_dep_time="0830",
        train_type="SRT",
        train_type_display="SRT",
        special_option="ReserveOption.GENERAL_FIRST",
        special_option_display="GENERAL_FIRST",
        passenger_count=1,
        seat_strategy="consecutive",
        split_enabled=True,
        split_via_station="평택지제",
        split_mode="manual",
    )

    assert service.start_reservation_process(12345, "user", "password", params) is True

    popen_args = mock_popen.call_args.args[0]
    assert popen_args[1:3] == ["-m", "telegramBot.srtSplitBackProcess"]
    assert popen_args[-1] == "평택지제"
    assert storage.running.search_params.split_enabled is True


class FakeSplitSrtService:
    def login(self, username, password):
        return True

    def parse_seat_type(self, option):
        return "seat-type"

    def search_and_reserve_loop(self, **kwargs):
        return f"reserved:{kwargs['src_locate']}->{kwargs['dst_locate']}"


@patch("telegramBot.srtSplitBackProcess.sys.argv", [
    "srtSplitBackProcess.py",
    "user",
    "password",
    "20991231",
    "수서",
    "대전",
    "082500",
    "SRT",
    "ReserveOption.GENERAL_FIRST",
    "12345",
    "0830",
    "1",
    "consecutive",
    "평택지제",
])
@patch("telegramBot.srtSplitBackProcess.SrtService", FakeSplitSrtService)
@patch("telegramBot.srtSplitBackProcess.requests.session")
def test_srt_split_process_sends_success_callback_only_after_both_segments_succeed(mock_session):
    response = Mock()
    response.status_code = 200
    requester = Mock()
    requester.get.return_value = response
    mock_session.return_value = requester

    process = SrtSplitBackgroundReservationProcess()
    process.run()

    requester.get.assert_called_once()
    params = requester.get.call_args.kwargs["params"]
    assert params["status"] == 0
    assert "reserved:수서->평택지제" in params["msg"]
    assert "reserved:평택지제->대전" in params["msg"]
