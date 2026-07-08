"""Unit tests for final confirmation target train summaries."""
from unittest.mock import Mock, patch

from handlers.conversation_handler import ConversationHandler
from models import UserCredentials, UserSession


class FakeStorage:
    pass


class FakeKorailService:
    last_kwargs = None

    def login(self, username, password):
        return True

    def search_trains(self, **kwargs):
        FakeKorailService.last_kwargs = kwargs
        return [Mock(train_no="247", dep_time="165800")]


def test_ktx_target_summary_includes_sold_out_and_waiting_list_trains():
    FakeKorailService.last_kwargs = None
    session = UserSession(chat_id=12345)
    session.credentials = UserCredentials(korail_id="user", korail_pw="password")
    session.train_info = {
        "provider": "KTX",
        "depDate": "20991231",
        "srcLocate": "서울",
        "dstLocate": "대전",
        "depTime": "165000",
        "maxDepTime": "1700",
        "passengerCount": 1,
        "trainType": "TrainType.KTX",
    }
    handler = ConversationHandler(FakeStorage(), Mock(), Mock())

    with patch("handlers.conversation_handler.KorailService", FakeKorailService):
        summary = handler._get_target_train_summary(session)

    assert "KTX247(16:58)" in summary
    assert FakeKorailService.last_kwargs["include_no_seats"] is True
    assert FakeKorailService.last_kwargs["include_waiting_list"] is True
