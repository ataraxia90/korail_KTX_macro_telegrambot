"""Regression tests for running-reservation Redis serialization."""

from models import RunningReservation, TrainSearchParams
from storage.redis import RedisStorage


def _srt_general_only_reservation() -> RunningReservation:
    return RunningReservation(
        chat_id=12345,
        process_id=67890,
        search_params=TrainSearchParams(
            provider="SRT",
            dep_date="20991231",
            src_locate="대전",
            dst_locate="수서",
            dep_time="210000",
            max_dep_time="2130",
            train_type="SRT",
            train_type_display="SRT",
            special_option="ReserveOption.GENERAL_ONLY",
            special_option_display="GENERAL_ONLY",
        ),
    )


def test_running_reservation_round_trip_preserves_display_values():
    storage = RedisStorage.__new__(RedisStorage)
    reservation = _srt_general_only_reservation()

    restored = storage._deserialize_running_reservation(
        storage._serialize_running_reservation(reservation)
    )

    assert restored.search_params.train_type_display == "SRT"
    assert restored.search_params.special_option_display == "GENERAL_ONLY"


def test_legacy_running_reservation_derives_display_values():
    storage = RedisStorage.__new__(RedisStorage)
    data = storage._serialize_running_reservation(_srt_general_only_reservation())
    del data["search_params"]["train_type_display"]
    del data["search_params"]["special_option_display"]

    restored = storage._deserialize_running_reservation(data)

    assert restored.search_params.train_type_display == "SRT"
    assert restored.search_params.special_option_display == "GENERAL_ONLY"
