"""Unit tests for SRT split reservation."""
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
        "srcLocate": "Suseo",
        "dstLocate": "Daejeon",
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

    with patch.object(
        handler,
        "_get_split_compatible_train_summary",
        return_value=(True, "SRT362(08:25)", ""),
    ):
        handler.handle_message(12345, "1")
        assert storage.session.last_action == UserProgress.AWAITING_SPLIT_OPTION

        handler.handle_message(12345, "2")
        assert storage.session.last_action == UserProgress.AWAITING_SPLIT_VIA_STATION

        handler.handle_message(12345, "PyeongtaekJije")
        assert storage.session.last_action == UserProgress.SEAT_STRATEGY_INPUT_SUCCESS
        assert storage.session.train_info["splitEnabled"] is True
        assert storage.session.train_info["splitViaStation"] == "PyeongtaekJije"

        handler.handle_message(12345, "Y")

    search_params = reservation.start_reservation_process.call_args.kwargs["search_params"]
    assert search_params.split_enabled is True
    assert search_params.split_via_station == "PyeongtaekJije"
    assert search_params.split_mode == "manual"


def test_srt_split_compatibility_rejects_via_station_without_matching_train():
    class FakeSrtService:
        def __init__(self):
            self.calls = 0

        def login(self, username, password):
            return True

        def search_trains(self, src_locate, dst_locate, **kwargs):
            self.calls += 1
            if self.calls == 1:
                return [Mock(train_no="362")]
            if self.calls == 2:
                return [Mock(train_no="362")]
            if self.calls == 3:
                return [Mock(train_no="364")]
            return []

    storage = FakeConversationStorage(_srt_session())
    storage.session.train_info["splitEnabled"] = True
    storage.session.train_info["splitViaStation"] = "PyeongtaekJije"
    handler = ConversationHandler(storage, Mock(), Mock())

    with patch("handlers.conversation_handler.SrtService", FakeSrtService):
        is_valid, summary, message = handler._get_split_compatible_train_summary(storage.session)

    assert is_valid is False
    assert summary == ""
    assert "분할 예매 가능한 대상 열차가 없습니다" in message


@patch("services.reservation_service.subprocess.Popen")
def test_reservation_service_uses_srt_split_process_when_enabled(mock_popen):
    mock_popen.return_value.pid = 1234
    storage = FakeReservationStorage()
    telegram = Mock()
    service = ReservationService(storage, telegram)
    params = TrainSearchParams(
        provider="SRT",
        dep_date="20991231",
        src_locate="Suseo",
        dst_locate="Daejeon",
        dep_time="082500",
        max_dep_time="0830",
        train_type="SRT",
        train_type_display="SRT",
        special_option="ReserveOption.GENERAL_FIRST",
        special_option_display="GENERAL_FIRST",
        passenger_count=1,
        seat_strategy="consecutive",
        split_enabled=True,
        split_via_station="PyeongtaekJije",
        split_mode="manual",
    )

    assert service.start_reservation_process(12345, "user", "password", params) is True

    popen_args = mock_popen.call_args.args[0]
    assert popen_args[1:3] == ["-m", "telegramBot.srtSplitBackProcess"]
    assert popen_args[-1] == "PyeongtaekJije"
    assert storage.running.search_params.split_enabled is True


class FakeSplitSrtService:
    calls = []

    def login(self, username, password):
        return True

    def parse_seat_type(self, option):
        return "seat-type"

    def search_trains(self, src_locate, dst_locate, **kwargs):
        return [Mock(train_no="362")]

    def search_and_reserve_loop(self, **kwargs):
        self.calls.append(kwargs)
        return f"reserved:{kwargs['src_locate']}->{kwargs['dst_locate']}"


@patch("telegramBot.srtSplitBackProcess.sys.argv", [
    "srtSplitBackProcess.py",
    "user",
    "password",
    "20991231",
    "Suseo",
    "Daejeon",
    "082500",
    "SRT",
    "ReserveOption.GENERAL_FIRST",
    "12345",
    "0830",
    "1",
    "consecutive",
    "PyeongtaekJije",
])
@patch("telegramBot.srtSplitBackProcess.SrtService", FakeSplitSrtService)
@patch("telegramBot.srtSplitBackProcess.requests.session")
def test_srt_split_process_sends_success_callback_only_after_both_segments_succeed(mock_session):
    FakeSplitSrtService.calls = []
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
    assert all(call["target_train_numbers"] == {"SRT362"} for call in FakeSplitSrtService.calls)
    assert "reserved:Suseo->PyeongtaekJije" in params["msg"]
    assert "reserved:PyeongtaekJije->Daejeon" in params["msg"]
