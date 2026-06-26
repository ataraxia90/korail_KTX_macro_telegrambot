"""Unit tests for SRT service wrapper."""
from types import SimpleNamespace

from services.srt_service import SrtService
from services import srt_service


class FakeSeatType:
    GENERAL_FIRST = "general_first"
    GENERAL_ONLY = "general_only"
    SPECIAL_FIRST = "special_first"
    SPECIAL_ONLY = "special_only"
    GENERAL = "general"
    SPECIAL = "special"


class FakeAdult:
    def __init__(self, count=1):
        self.count = count


class FakeSRT:
    def __init__(self, username, password, auto_login=False):
        self.username = username
        self.password = password
        self.auto_login = auto_login

    def login(self):
        return self.password == "ok"

    def search_train(self, src, dst, date, time):
        return [
            SimpleNamespace(dep_time="090000", name=f"{src}-{dst}-{date}-{time}"),
            SimpleNamespace(dep_time="190000", name="too-late"),
        ]

    def reserve(self, train, passengers=None, special_seat=None):
        return f"reserved:{train.name}:{special_seat}:{passengers[0].count}"


class FakeStringTrain:
    def __init__(self, dep_time, text, name):
        self.dep_time = dep_time
        self.name = name
        self._text = text

    def __str__(self):
        return self._text


class FakeSRTWithOvernightResult(FakeSRT):
    def search_train(self, src, dst, date, time):
        return [
            FakeStringTrain("000600", "[SRT] 07월 02일, 대전~수서(00:06~01:06)", "next-day"),
            SimpleNamespace(dep_date="20260701", dep_time="082500", name="same-day"),
        ]


class FakeSRTWithAvailabilityOption(FakeSRT):
    def __init__(self, username, password, auto_login=False):
        super().__init__(username, password, auto_login=auto_login)
        self.available_only = None

    def search_train(self, src, dst, date, time, available_only=True):
        self.available_only = available_only
        return [SimpleNamespace(dep_date=date, dep_time="152000", train_number="362")]


def test_srt_login_success_and_failure():
    service = SrtService(srt_cls=FakeSRT, seat_type_cls=FakeSeatType, adult_cls=FakeAdult)

    assert service.login("user", "bad") is False
    assert service.login("user", "ok") is True


def test_srtrain_package_imports_runtime_classes():
    assert srt_service.SRT is not None
    assert srt_service.SeatType is not None
    assert srt_service.Adult is not None


def test_srt_parse_seat_type_matches_actual_srtrain_names():
    service = SrtService(srt_cls=FakeSRT, seat_type_cls=FakeSeatType, adult_cls=FakeAdult)

    assert service.parse_seat_type("ReserveOption.GENERAL_FIRST") == FakeSeatType.GENERAL_FIRST
    assert service.parse_seat_type("ReserveOption.GENERAL_ONLY") == FakeSeatType.GENERAL_ONLY
    assert service.parse_seat_type("ReserveOption.SPECIAL_FIRST") == FakeSeatType.SPECIAL_FIRST
    assert service.parse_seat_type("ReserveOption.SPECIAL_ONLY") == FakeSeatType.SPECIAL_ONLY


def test_srt_search_filters_by_max_departure_time():
    service = SrtService(srt_cls=FakeSRT, seat_type_cls=FakeSeatType, adult_cls=FakeAdult)
    service.login("user", "ok")

    trains = service.search_trains("20991231", "Suseo", "Busan", "080000", "1200")

    assert len(trains) == 1
    assert trains[0].name.startswith("Suseo-Busan")


def test_srt_search_filters_out_next_day_overnight_trains():
    service = SrtService(srt_cls=FakeSRTWithOvernightResult, seat_type_cls=FakeSeatType, adult_cls=FakeAdult)
    service.login("user", "ok")

    trains = service.search_trains("20260701", "대전", "수서", "082500", "0830")

    assert [train.name for train in trains] == ["same-day"]


def test_srt_search_can_include_unavailable_trains_for_target_summary():
    service = SrtService(srt_cls=FakeSRTWithAvailabilityOption, seat_type_cls=FakeSeatType, adult_cls=FakeAdult)
    service.login("user", "ok")

    trains = service.search_trains("20260701", "대전", "수서", "150000", "1530", available_only=False)

    assert service._srt_instance.available_only is False
    assert [train.train_number for train in trains] == ["362"]


def test_srt_search_and_reserve_loop_uses_seat_type_and_passengers():
    service = SrtService(srt_cls=FakeSRT, seat_type_cls=FakeSeatType, adult_cls=FakeAdult)
    service.login("user", "ok")

    reservation = service.search_and_reserve_loop(
        dep_date="20991231",
        src_locate="Suseo",
        dst_locate="Busan",
        dep_time="080000",
        max_dep_time="1200",
        seat_type=service.parse_seat_type("ReserveOption.SPECIAL_FIRST"),
        passenger_count=2,
        max_attempts=1,
    )

    assert reservation == "reserved:Suseo-Busan-20991231-0800:special_first:2"
