"""SRT API service wrapper."""
import re
import time
from datetime import datetime
from typing import Optional, List, Any
from zoneinfo import ZoneInfo

from config.settings import settings
from utils.logger import get_logger

logger = get_logger(__name__)

try:
    from SRT import SRT, SeatType, Adult
except ImportError:  # pragma: no cover - exercised when dependency is absent locally
    SRT = None
    SeatType = None
    Adult = None


class SrtService:
    """Service for interacting with the SRTrain SRT API."""

    def __init__(self, srt_cls=None, seat_type_cls=None, adult_cls=None):
        self._srt_cls = srt_cls or SRT
        self._seat_type_cls = seat_type_cls or SeatType
        self._adult_cls = adult_cls or Adult
        self._srt_instance: Optional[Any] = None
        self._logged_in = False
        self._search_interval = settings.SRT_SEARCH_INTERVAL
        self.last_stop_reason: Optional[str] = None

    def login(self, username: str, password: str) -> bool:
        """Login to SRT with credentials."""
        if not self._srt_cls:
            logger.error("SRTrain is not installed")
            return False

        try:
            try:
                self._srt_instance = self._srt_cls(username, password, auto_login=False)
                result = self._srt_instance.login()
            except TypeError:
                self._srt_instance = self._srt_cls(username, password)
                login = getattr(self._srt_instance, "login", None)
                result = login() if callable(login) else True

            self._logged_in = bool(result)
            return self._logged_in
        except Exception as e:
            logger.error(f"SRT login error for user {username}: {e}")
            self._logged_in = False
            return False

    def search_trains(
        self,
        dep_date: str,
        src_locate: str,
        dst_locate: str,
        dep_time: str = "000000",
        max_dep_time: str = "2400",
        passenger_count: int = 1,
        verbose: bool = True,
        available_only: Optional[bool] = None
    ) -> List:
        """Search SRT trains and filter by max departure time."""
        if not self._logged_in or not self._srt_instance:
            raise ValueError("Must login before searching SRT trains")

        dep_time_hhmm = dep_time[:4]
        try:
            if available_only is None:
                trains = self._srt_instance.search_train(
                    src_locate,
                    dst_locate,
                    dep_date,
                    dep_time_hhmm
                )
            else:
                trains = self._srt_instance.search_train(
                    src_locate,
                    dst_locate,
                    dep_date,
                    dep_time_hhmm,
                    available_only=available_only
                )
        except TypeError:
            try:
                keyword_args = {
                    "dep": src_locate,
                    "arr": dst_locate,
                    "date": dep_date,
                    "time": dep_time_hhmm,
                }
                if available_only is not None:
                    keyword_args["available_only"] = available_only
                trains = self._srt_instance.search_train(**keyword_args)
            except TypeError:
                trains = self._srt_instance.search_train(
                    dep=src_locate,
                    arr=dst_locate,
                    date=dep_date,
                    time=dep_time_hhmm
                )
        except Exception as e:
            logger.error(f"SRT search error: {e}", exc_info=verbose)
            return []

        trains = self._filter_trains(trains or [], dep_date, dep_time_hhmm, max_dep_time)

        return trains

    def reserve_train(self, train, seat_type=None, passenger_count: int = 1):
        """Attempt to reserve one SRT train."""
        if not self._logged_in or not self._srt_instance:
            raise ValueError("Must login before reserving SRT trains")

        passengers = self._build_passengers(passenger_count)
        try:
            return self._srt_instance.reserve(train, passengers=passengers, special_seat=seat_type)
        except TypeError:
            try:
                return self._srt_instance.reserve(train, passengers=passengers, seat_type=seat_type)
            except TypeError:
                return self._srt_instance.reserve(train, passengers=passengers)
        except Exception as e:
            logger.error(f"SRT reservation error: {e}")
            return None

    def search_and_reserve_loop(
        self,
        dep_date: str,
        src_locate: str,
        dst_locate: str,
        dep_time: str = "000000",
        max_dep_time: str = "2400",
        seat_type=None,
        passenger_count: int = 1,
        max_attempts: Optional[int] = None
    ):
        """Search and reserve until successful."""
        attempts = 0
        cutoff_at = self._get_search_cutoff_time(
            dep_date=dep_date,
            src_locate=src_locate,
            dst_locate=dst_locate,
            dep_time=dep_time,
            max_dep_time=max_dep_time,
            passenger_count=passenger_count
        )

        while True:
            attempts += 1
            if max_attempts and attempts > max_attempts:
                return None

            if cutoff_at and self._now_kst() >= cutoff_at:
                self.last_stop_reason = (
                    f"search window expired after last target train departure "
                    f"({cutoff_at.strftime('%Y-%m-%d %H:%M')})"
                )
                logger.info(f"SRT reservation loop stopped: {self.last_stop_reason}")
                return None

            trains = self.search_trains(
                dep_date=dep_date,
                src_locate=src_locate,
                dst_locate=dst_locate,
                dep_time=dep_time,
                max_dep_time=max_dep_time,
                passenger_count=passenger_count,
                verbose=attempts % 60 == 0
            )

            for train in trains:
                reservation = self.reserve_train(train, seat_type=seat_type, passenger_count=passenger_count)
                if reservation:
                    return reservation

            time.sleep(self._search_interval)

    def _get_search_cutoff_time(
        self,
        dep_date: str,
        src_locate: str,
        dst_locate: str,
        dep_time: str,
        max_dep_time: str,
        passenger_count: int
    ) -> Optional[datetime]:
        """Return when the reservation loop should stop in Asia/Seoul time."""
        candidate_times = []
        try:
            target_trains = self.search_trains(
                dep_date=dep_date,
                src_locate=src_locate,
                dst_locate=dst_locate,
                dep_time=dep_time,
                max_dep_time=max_dep_time,
                passenger_count=passenger_count,
                verbose=False,
                available_only=False
            )
            candidate_times = [
                self._extract_departure_time(train)
                for train in target_trains
            ]
            candidate_times = [time_value for time_value in candidate_times if time_value > 0]
        except Exception as e:
            logger.warning(f"Failed to calculate SRT target train cutoff: {e}")

        if candidate_times:
            cutoff_hhmm = max(candidate_times)
        elif max_dep_time and max_dep_time != "2400":
            cutoff_hhmm = int(max_dep_time)
        else:
            cutoff_hhmm = 2359

        return self._build_kst_datetime(dep_date, cutoff_hhmm)

    def _build_kst_datetime(self, dep_date: str, hhmm: int) -> Optional[datetime]:
        """Build an Asia/Seoul datetime from YYYYMMDD and HHMM."""
        try:
            hour = hhmm // 100
            minute = hhmm % 100
            if not (0 <= hour <= 23 and 0 <= minute <= 59):
                return None
            date_part = datetime.strptime(dep_date[:8], "%Y%m%d")
            return date_part.replace(
                hour=hour,
                minute=minute,
                second=0,
                microsecond=0,
                tzinfo=ZoneInfo("Asia/Seoul")
            )
        except ValueError:
            return None

    def _now_kst(self) -> datetime:
        """Current time in Asia/Seoul."""
        return datetime.now(ZoneInfo("Asia/Seoul"))

    def parse_seat_type(self, option_str: str):
        """Map KTX-style option strings to SRTrain SeatType values."""
        if not self._seat_type_cls:
            return None

        option = option_str.upper()
        if "SPECIAL_ONLY" in option:
            names = ["SPECIAL_ONLY", "SPECIAL", "SPECIAL_SEAT", "FIRST", "PREMIUM"]
        elif "SPECIAL" in option:
            names = ["SPECIAL_FIRST", "SPECIAL", "SPECIAL_SEAT", "FIRST", "PREMIUM"]
        elif "GENERAL_ONLY" in option:
            names = ["GENERAL_ONLY", "GENERAL", "GENERAL_SEAT", "STANDARD"]
        else:
            names = ["GENERAL_FIRST", "GENERAL", "GENERAL_SEAT", "STANDARD"]

        for name in names:
            if hasattr(self._seat_type_cls, name):
                return getattr(self._seat_type_cls, name)
        return None

    def _build_passengers(self, passenger_count: int):
        if not self._adult_cls:
            return None

        try:
            return [self._adult_cls(passenger_count)]
        except TypeError:
            return [self._adult_cls() for _ in range(passenger_count)]

    def _extract_departure_time(self, train) -> int:
        for attr in ("dep_time", "departure_time", "time"):
            value = getattr(train, attr, None)
            if value:
                digits = "".join(ch for ch in str(value) if ch.isdigit())
                if len(digits) >= 4:
                    return int(digits[:4])

        try:
            time_part = str(train).rsplit("(", 1)[1].split("~")[0]
            return int("".join(time_part.split(":"))[:4])
        except (IndexError, ValueError):
            return 0

    def _filter_trains(self, trains: List, dep_date: str, dep_time: str, max_dep_time: str) -> List:
        requested_date = dep_date[:8]
        min_time = int(dep_time[:4]) if dep_time and dep_time[:4].isdigit() else None
        max_time = int(max_dep_time) if max_dep_time != "2400" else None
        filtered = []

        for train in trains:
            train_date = self._extract_departure_date(train, requested_date)
            if train_date and train_date != requested_date:
                logger.debug(f"Filtered SRT train outside requested date: {train}")
                continue

            train_time = self._extract_departure_time(train)
            if train_time <= 0:
                continue
            if min_time is not None and train_time < min_time:
                continue
            if max_time is not None and train_time >= max_time:
                continue

            filtered.append(train)

        return filtered

    def _extract_departure_date(self, train, requested_date: str) -> Optional[str]:
        for attr in ("dep_date", "departure_date", "date", "depDate"):
            value = getattr(train, attr, None)
            if value:
                digits = "".join(ch for ch in str(value) if ch.isdigit())
                if len(digits) >= 8:
                    return digits[:8]
                if len(digits) == 4:
                    return f"{requested_date[:4]}{digits}"

        match = re.search(r"(\d{1,2})월\s*(\d{1,2})일", str(train))
        if match:
            month = int(match.group(1))
            day = int(match.group(2))
            return f"{requested_date[:4]}{month:02d}{day:02d}"

        return None

    @property
    def is_logged_in(self) -> bool:
        return self._logged_in
