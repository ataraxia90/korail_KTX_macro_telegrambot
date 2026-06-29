"""Unit tests for Korail service safeguards."""

from datetime import datetime
from zoneinfo import ZoneInfo

from services.korail_service import KorailService


class FakeKorailTrain:
    def __init__(self, dep_date, dep_time, name):
        self.dep_date = dep_date
        self.dep_time = dep_time
        self.name = name

    def __str__(self):
        return f"[KTX] {self.dep_date[4:6]}월 {self.dep_date[6:8]}일 대전~서울({self.dep_time[:2]}:{self.dep_time[2:4]}~09:30)"


class FakeKorail:
    def __init__(self):
        self.reserve_called = False

    def search_train(self, src, dst, date, time, train_type=None, passengers=None):
        return [
            FakeKorailTrain("20260702", "000600", "next-day"),
            FakeKorailTrain("20260701", "082500", "same-day"),
        ]

    def reserve(self, train, passengers=None, option=None):
        self.reserve_called = True
        return None


class FakeKorailWithEarlierSameDayResults(FakeKorail):
    def search_train(self, src, dst, date, time, train_type=None, passengers=None):
        return [
            FakeKorailTrain("20260701", "000600", "too-early-midnight"),
            FakeKorailTrain("20260701", "063000", "too-early-morning"),
            FakeKorailTrain("20260701", "082500", "in-window"),
            FakeKorailTrain("20260701", "083000", "at-exclusive-end"),
        ]


def test_korail_search_filters_out_next_day_overnight_trains():
    service = KorailService()
    service._korail_instance = FakeKorail()
    service._logged_in = True

    trains = service.search_trains("20260701", "대전", "서울", "082500", "0830", verbose=False)

    assert [train.name for train in trains] == ["same-day"]


def test_korail_search_filters_out_same_day_trains_before_start_time():
    service = KorailService()
    service._korail_instance = FakeKorailWithEarlierSameDayResults()
    service._logged_in = True

    trains = service.search_trains("20260701", "대전", "서울", "082000", "0830", verbose=False)

    assert [train.name for train in trains] == ["in-window"]


def test_korail_loop_stops_after_last_target_train_departure():
    fake = FakeKorail()
    service = KorailService()
    service._korail_instance = fake
    service._logged_in = True
    service._now_kst = lambda: datetime(2026, 7, 1, 8, 26, tzinfo=ZoneInfo("Asia/Seoul"))

    reservation = service.search_and_reserve_loop(
        "20260701",
        "대전",
        "서울",
        "082500",
        "0830",
        max_attempts=1
    )

    assert reservation is None
    assert fake.reserve_called is False
    assert "last target train" in service.last_stop_reason
