"""Unit tests for Korail service safeguards."""

from services.korail_service import KorailService


class FakeKorailTrain:
    def __init__(self, dep_date, dep_time, name):
        self.dep_date = dep_date
        self.dep_time = dep_time
        self.name = name

    def __str__(self):
        return f"[KTX] {self.dep_date[4:6]}월 {self.dep_date[6:8]}일 대전~서울({self.dep_time[:2]}:{self.dep_time[2:4]}~09:30)"


class FakeKorail:
    def search_train(self, src, dst, date, time, train_type=None, passengers=None):
        return [
            FakeKorailTrain("20260702", "000600", "next-day"),
            FakeKorailTrain("20260701", "082500", "same-day"),
        ]


def test_korail_search_filters_out_next_day_overnight_trains():
    service = KorailService()
    service._korail_instance = FakeKorail()
    service._logged_in = True

    trains = service.search_trains("20260701", "대전", "서울", "082500", "0830", verbose=False)

    assert [train.name for train in trains] == ["same-day"]
