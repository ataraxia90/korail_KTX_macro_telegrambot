"""Timezone boundary tests for reservation parameter validation."""
from datetime import datetime, timedelta, timezone

from models import TrainSearchParams
from models import reservation as reservation_model


def _params(dep_date: str) -> TrainSearchParams:
    return TrainSearchParams(
        dep_date=dep_date,
        src_locate="대전",
        dst_locate="수서",
        dep_time="200000",
        provider="SRT",
        max_dep_time="2200",
    )


def test_train_search_params_validates_dates_against_kst(monkeypatch):
    """The Korean calendar date must win while the server is still on UTC yesterday."""
    kst = timezone(timedelta(hours=9), name="KST")
    monkeypatch.setattr(
        reservation_model,
        "now_kst",
        lambda: datetime(2026, 7, 26, 8, 21, tzinfo=kst),
    )

    assert _params("20260726").validate()[0] is True
    assert _params("20260725").validate()[0] is False
