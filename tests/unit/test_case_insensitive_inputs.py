"""Unit tests for case-insensitive control inputs."""
from unittest.mock import Mock, patch

from handlers.command_handler import CommandHandler
from handlers.conversation_handler import ConversationHandler
from models import UserProgress, UserSession


class FakeStorage:
    def __init__(self, session=None):
        self.session = session
        self.saved_session = None

    def get_user_session(self, chat_id):
        return self.session

    def save_user_session(self, session):
        self.saved_session = session
        self.session = session


def test_start_command_is_case_insensitive_and_allows_spaces():
    storage = FakeStorage()
    telegram = Mock()
    handler = CommandHandler(storage, telegram, Mock(), Mock())

    handled = handler.route_command(12345, "  /Start  ")

    assert handled is True
    assert storage.saved_session.last_action == UserProgress.STARTED
    assert storage.saved_session.in_progress is True
    telegram.send_message.assert_called_once()


@patch("handlers.conversation_handler.SrtService.login", return_value=True)
@patch("handlers.conversation_handler.settings.SRT_USERID", "010-1234-5678")
@patch("handlers.conversation_handler.settings.SRT_USERPW", "password")
def test_srt_magic_login_is_case_insensitive(mock_login):
    session = UserSession(chat_id=12345)
    session.last_action = UserProgress.PROVIDER_INPUT_SUCCESS
    session.train_info["provider"] = "SRT"
    storage = FakeStorage(session)
    telegram = Mock()
    handler = ConversationHandler(storage, telegram, Mock())

    handler.handle_message(12345, "  Yubi  ")

    assert storage.saved_session.last_action == UserProgress.PW_INPUT_SUCCESS
    assert storage.saved_session.credentials.korail_id == "010-1234-5678"
    mock_login.assert_called_once_with("010-1234-5678", "password")
    telegram.send_message.assert_called_once()
