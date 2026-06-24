"""SRT API service wrapper."""
import time
from typing import Optional, List, Any

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
        verbose: bool = True
    ) -> List:
        """Search SRT trains and filter by max departure time."""
        if not self._logged_in or not self._srt_instance:
            raise ValueError("Must login before searching SRT trains")

        dep_time_hhmm = dep_time[:4]
        try:
            trains = self._srt_instance.search_train(
                src_locate,
                dst_locate,
                dep_date,
                dep_time_hhmm
            )
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

        trains = trains or []
        if max_dep_time != "2400":
            max_time = int(max_dep_time)
            trains = [
                train for train in trains
                if 0 < self._extract_departure_time(train) < max_time
            ]

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
        while True:
            attempts += 1
            if max_attempts and attempts > max_attempts:
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

    @property
    def is_logged_in(self) -> bool:
        return self._logged_in
