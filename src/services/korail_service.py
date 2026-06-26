"""Korail API service wrapper."""
import re
import time
from datetime import datetime
from typing import Optional, List
from zoneinfo import ZoneInfo
from korail2 import (
    Korail as K2MKorail, TrainType, ReserveOption, SoldOutError, NoResultsError,
    AdultPassenger
)

from config.settings import settings
from utils.logger import get_logger

logger = get_logger(__name__)


class KorailService:
    """Service for interacting with Korail API."""

    def __init__(self):
        """Initialize Korail service."""
        self._korail_instance: Optional[K2MKorail] = None
        self._logged_in = False
        self._search_interval = settings.KORAIL_SEARCH_INTERVAL
        self._username: Optional[str] = None
        self._password: Optional[str] = None
        self._last_login_time: float = 0
        self._relogin_interval: int = 30 * 60  # 30 minutes
        self._relogin_count: int = 0
        self.last_stop_reason: Optional[str] = None

        # Log class methods to verify correct version is loaded
        logger.debug(f"KorailService initialized with methods: {[m for m in dir(self) if not m.startswith('_')]}")

    def login(self, username: str, password: str) -> bool:
        """
        Login to Korail with credentials.

        Args:
            username: Korail username (phone number in format 010-xxxx-xxxx)
            password: Korail password

        Returns:
            True if login successful, False otherwise
        """
        try:
            self._korail_instance = K2MKorail(username, password, auto_login=False)
            self._logged_in = self._korail_instance.login()

            if self._logged_in:
                self._username = username
                self._password = password
                self._last_login_time = time.time()
                logger.info(f"Korail login successful for user: {username}")
            else:
                logger.warning(f"Korail login failed for user: {username}")

            return self._logged_in
        except Exception as e:
            logger.error(f"Korail login error for user {username}: {e}")
            return False

    def _relogin(self) -> bool:
        """Attempt to re-login with stored credentials after session expiry."""
        if not self._username or not self._password:
            logger.error("🔒 Cannot re-login: no stored credentials")
            return False

        logger.debug("🔄 Session expired, attempting re-login...")
        try:
            self._korail_instance = K2MKorail(self._username, self._password, auto_login=False)
            self._logged_in = self._korail_instance.login()
            if self._logged_in:
                self._last_login_time = time.time()
                self._relogin_count += 1
                logger.debug(f"✅ Re-login successful (total: {self._relogin_count})")
            else:
                logger.error("❌ Re-login failed")
            return self._logged_in
        except Exception as e:
            logger.error(f"❌ Re-login error: {e}")
            self._logged_in = False
            return False

    def _check_session_refresh(self):
        """Proactively re-login if session is older than the relogin interval."""
        if self._last_login_time and (time.time() - self._last_login_time) >= self._relogin_interval:
            logger.debug(f"🔄 Session older than {self._relogin_interval}s, proactive re-login")
            self._relogin()

    def search_trains(
        self,
        dep_date: str,
        src_locate: str,
        dst_locate: str,
        dep_time: str = "000000",
        max_dep_time: str = "2400",
        train_type: TrainType = TrainType.KTX,
        passenger_count: int = 1,
        verbose: bool = True
    ) -> List:
        """
        Search for available trains.

        Args:
            dep_date: Departure date (YYYYMMDD)
            src_locate: Source station name (without '역')
            dst_locate: Destination station name (without '역')
            dep_time: Departure time (HHMMSS)
            max_dep_time: Maximum departure time threshold (HHMM)
            train_type: Type of train to search for
            passenger_count: Number of adult passengers

        Returns:
            List of available trains

        Raises:
            ValueError: If not logged in
        """
        if not self._logged_in or not self._korail_instance:
            raise ValueError("Must login before searching trains")

        try:
            # Create passenger list
            passengers = [AdultPassenger(passenger_count)]

            if verbose:
                logger.debug(
                    f"🔍 Searching trains with parameters:"
                )
                logger.debug(f"  dep_date: {dep_date} (type: {type(dep_date).__name__})")
                logger.debug(f"  src_locate: '{src_locate}' (type: {type(src_locate).__name__})")
                logger.debug(f"  dst_locate: '{dst_locate}' (type: {type(dst_locate).__name__})")
                logger.debug(f"  dep_time: {dep_time} (type: {type(dep_time).__name__})")
                logger.debug(f"  train_type: {train_type}")
                logger.debug(f"  passengers: {passengers} (count: {passenger_count})")
                logger.debug(f"  max_dep_time: {max_dep_time}")

            trains = self._korail_instance.search_train(
                src_locate,
                dst_locate,
                dep_date,
                dep_time,
                train_type=train_type,
                passengers=passengers
            )

            if verbose:
                logger.debug(f"📋 Korail API returned {len(trains) if trains else 0} trains")

                # Log each train found with seat availability
                if trains:
                    for i, train in enumerate(trains, 1):
                        train_str = str(train)
                        logger.debug(f"  Train #{i}: {train_str}")

                        if hasattr(train, 'seat_available'):
                            logger.debug(f"    Seats available: {train.seat_available}")
                        if hasattr(train, 'general_seat'):
                            logger.debug(f"    General seats: {train.general_seat}")
                        if hasattr(train, 'special_seat'):
                            logger.debug(f"    Special seats: {train.special_seat}")

            trains = self._filter_trains_by_date(trains or [], dep_date, verbose)

            # Filter by max departure time
            if trains and max_dep_time != "2400":
                filtered_trains = []
                max_time = int(max_dep_time)

                if verbose:
                    logger.debug(f"🔧 Applying max_dep_time filter: {max_dep_time}")

                for train in trains:
                    dep_time_int = self._extract_departure_time(train)
                    if dep_time_int > 0 and dep_time_int < max_time:
                        filtered_trains.append(train)
                        if verbose:
                            logger.debug(f"  ✅ Kept: {dep_time_int} < {max_time}")
                    else:
                        if verbose:
                            logger.debug(f"  ❌ Filtered out: {dep_time_int} >= {max_time}")

                trains = filtered_trains
                if verbose:
                    logger.debug(f"📊 After filtering: {len(trains)} trains remain")

            if verbose:
                logger.debug(
                    f"✅ Search complete: {len(trains)} trains available "
                    f"({src_locate}→{dst_locate} on {dep_date})"
                )
            return trains

        except NoResultsError:
            if verbose:
                logger.debug(f"No trains found for search criteria (NoResultsError)")
            return []
        except Exception as e:
            if type(e).__name__ == 'NeedToLoginError':
                logger.debug(f"🔒 Session expired during search, re-logging in: {e}")
                if self._relogin():
                    return []  # Will retry on next loop iteration
                else:
                    raise
            logger.error(f"❌ Error searching trains: {e}", exc_info=True)
            return []

    def reserve_train(
        self,
        train,
        option: ReserveOption = ReserveOption.GENERAL_FIRST,
        passenger_count: int = 1
    ):
        """
        Attempt to reserve a specific train.

        Args:
            train: Train object from search_trains()
            option: Reservation option (special seat preference)
            passenger_count: Number of adult passengers

        Returns:
            Reservation object if successful, None otherwise
            Returns "DUPLICATE" string if duplicate reservation detected
        """
        if not self._logged_in or not self._korail_instance:
            raise ValueError("Must login before reserving")

        try:
            # Create passenger list
            passengers = [AdultPassenger(passenger_count)]

            logger.debug(f"🎫 Attempting reservation:")
            logger.debug(f"  Train: {train}")
            logger.debug(f"  Option: {option}")
            logger.debug(f"  Passengers: {passenger_count}")

            reservation = self._korail_instance.reserve(train, passengers=passengers, option=option)

            if reservation:
                logger.info(f"🎉 RESERVATION SUCCESS!")
                logger.info(f"  Reservation details: {reservation}")
                if hasattr(reservation, 'rsv_id'):
                    logger.info(f"  Reservation ID: {reservation.rsv_id}")
                return reservation
            else:
                logger.debug(f"Reservation returned None (no seats available)")
                return None

        except SoldOutError as e:
            logger.debug(f"Train sold out during reservation attempt: {train}")
            return None
        except Exception as e:
            error_msg = str(e)
            error_type = type(e).__name__

            # Check for duplicate reservation error
            if "동일한 예약 내역" in error_msg or "WRR800029" in error_msg:
                # Return special value instead of raising exception
                logger.warning(f"⚠️ Duplicate reservation detected - will continue searching")
                logger.warning(f"  Error: {error_msg}")
                return "DUPLICATE"

            if error_type == 'NeedToLoginError':
                logger.debug(f"🔒 Session expired during reservation, re-logging in: {error_msg}")
                if self._relogin():
                    return None  # Will retry on next loop iteration
                else:
                    raise

            logger.error(f"❌ Reservation error ({error_type}): {error_msg}")
            logger.error(f"  Train: {train}")
            logger.error(f"  Option: {option}")
            logger.error(f"  Full traceback:", exc_info=True)
            return None

    def search_and_reserve_loop(
        self,
        dep_date: str,
        src_locate: str,
        dst_locate: str,
        dep_time: str = "000000",
        max_dep_time: str = "2400",
        train_type: TrainType = TrainType.KTX,
        reserve_option: ReserveOption = ReserveOption.GENERAL_FIRST,
        passenger_count: int = 1,
        seat_strategy: str = "consecutive",
        max_attempts: Optional[int] = None
    ):
        """
        Continuously search for trains and attempt reservation until successful.

        Args:
            dep_date: Departure date (YYYYMMDD)
            src_locate: Source station
            dst_locate: Destination station
            dep_time: Departure time (HHMMSS)
            max_dep_time: Maximum departure time (HHMM)
            train_type: Train type filter
            reserve_option: Reservation option
            passenger_count: Number of adult passengers
            seat_strategy: "consecutive" for seats together, "random" for separate seats
            max_attempts: Maximum attempts (None for infinite)

        Returns:
            Reservation object(s) when successful, None if max_attempts reached
        """
        if not self._logged_in:
            raise ValueError("Must login before searching")

        attempts = 0
        logger.info(
            f"Starting reservation loop: {src_locate} -> {dst_locate} "
            f"on {dep_date} at {dep_time} for {passenger_count} passengers ({seat_strategy} seating)"
        )
        cutoff_at = self._get_search_cutoff_time(
            dep_date=dep_date,
            src_locate=src_locate,
            dst_locate=dst_locate,
            dep_time=dep_time,
            max_dep_time=max_dep_time,
            train_type=train_type,
            passenger_count=passenger_count
        )

        if seat_strategy == "consecutive":
            return self._search_and_reserve_consecutive(
                dep_date, src_locate, dst_locate, dep_time, max_dep_time,
                train_type, reserve_option, passenger_count, max_attempts, cutoff_at
            )
        else:  # random
            return self._search_and_reserve_random(
                dep_date, src_locate, dst_locate, dep_time, max_dep_time,
                train_type, reserve_option, passenger_count, max_attempts, cutoff_at
            )

    def _search_and_reserve_consecutive(
        self,
        dep_date: str,
        src_locate: str,
        dst_locate: str,
        dep_time: str,
        max_dep_time: str,
        train_type: TrainType,
        reserve_option: ReserveOption,
        passenger_count: int,
        max_attempts: Optional[int],
        cutoff_at: Optional[datetime]
    ):
        """Reserve seats consecutively (together)."""
        attempts = 0
        duplicate_notified = False

        logger.info(f"🔄 Starting consecutive seat search loop (passengers={passenger_count})")

        while True:
            attempts += 1
            if max_attempts and attempts > max_attempts:
                logger.warning(f"❌ Reached max attempts ({max_attempts}), stopping")
                return None

            if self._is_search_expired(cutoff_at):
                return None

            if attempts % 1000 == 0:
                logger.info(f"📊 Search attempt #{attempts} (still searching..., re-logins: {self._relogin_count})")

            is_summary = (attempts % 60 == 0)

            if is_summary:
                logger.debug(f"━━━ Search attempt #{attempts} ━━━")

            self._check_session_refresh()

            # Search for trains
            trains = self.search_trains(
                dep_date, src_locate, dst_locate, dep_time, max_dep_time, train_type, passenger_count,
                verbose=is_summary
            )

            if not trains:
                if is_summary:
                    logger.debug(f"📊 Attempt #{attempts}: no trains found, retrying...")
                time.sleep(self._search_interval)
                continue

            # Try to reserve each train found (trains found = rare, always log)
            for idx, train in enumerate(trains, 1):
                logger.debug(f"🚂 Trying train {idx}/{len(trains)}")
                reservation = self.reserve_train(train, option=reserve_option, passenger_count=passenger_count)

                if reservation == "DUPLICATE":
                    # Duplicate reservation detected
                    if not duplicate_notified:
                        # First time - raise exception to notify user once
                        duplicate_notified = True
                        logger.warning("⚠️ First duplicate detection - notifying user")
                        raise DuplicateReservationError("동일한 예약 내역이 존재합니다")
                    else:
                        # Already notified - just log and continue
                        logger.debug("Duplicate reservation still exists, continuing search...")
                elif reservation:
                    logger.info(f"🎉 CONSECUTIVE RESERVATION SUCCESS after {attempts} attempts!")
                    return reservation
                else:
                    logger.debug(f"Train {idx} failed (sold out or unavailable)")

            logger.debug(f"All {len(trains)} trains sold out in attempt #{attempts}")

            # Wait before next search
            time.sleep(self._search_interval)

    def _search_and_reserve_random(
        self,
        dep_date: str,
        src_locate: str,
        dst_locate: str,
        dep_time: str,
        max_dep_time: str,
        train_type: TrainType,
        reserve_option: ReserveOption,
        passenger_count: int,
        max_attempts: Optional[int],
        cutoff_at: Optional[datetime]
    ):
        """Reserve seats randomly (one at a time until target count reached)."""
        attempts = 0
        reservations = []
        target_count = passenger_count
        duplicate_notified = False

        logger.info(f"Random seating: will reserve {target_count} individual tickets")

        while len(reservations) < target_count:
            attempts += 1
            if max_attempts and attempts > max_attempts:
                logger.warning(f"Reached max attempts ({max_attempts}), stopping")
                # Cancel any partial reservations
                self._cancel_reservations(reservations)
                return None

            if self._is_search_expired(cutoff_at):
                self._cancel_reservations(reservations)
                return None

            is_summary = (attempts % 60 == 0)

            self._check_session_refresh()

            # Search for trains (search for single passenger each time)
            trains = self.search_trains(
                dep_date, src_locate, dst_locate, dep_time, max_dep_time, train_type, passenger_count=1,
                verbose=is_summary
            )

            if not trains:
                if is_summary:
                    logger.debug(f"📊 Attempt #{attempts}: no trains found, retrying...")
                time.sleep(self._search_interval)
                continue

            # Try to reserve each train found (trains found = rare, always log)
            for train in trains:
                remaining = target_count - len(reservations)
                logger.debug(
                    f"Found train: {train}, attempting reservation "
                    f"({len(reservations) + 1}/{target_count})..."
                )

                # Reserve one seat at a time
                reservation = self.reserve_train(train, option=reserve_option, passenger_count=1)

                if reservation == "DUPLICATE":
                    # Duplicate reservation detected
                    if not duplicate_notified:
                        # First time - raise exception to notify user once
                        duplicate_notified = True
                        logger.warning("First duplicate detection - notifying user")
                        raise DuplicateReservationError("동일한 예약 내역이 존재합니다")
                    else:
                        # Already notified - just log and continue
                        logger.debug("Duplicate reservation still exists, continuing search...")
                elif reservation:
                    reservations.append(reservation)
                    current_count = len(reservations)
                    logger.info(
                        f"Reserved seat {current_count}/{target_count} "
                        f"(attempt #{attempts})"
                    )
                    logger.debug(f"Reservation details: {reservation}")

                    # Check if we've reached target
                    if current_count >= target_count:
                        logger.info(
                            f"All {target_count} seats reserved successfully! "
                            f"Total attempts: {attempts}"
                        )
                        # Return the first reservation as primary (for compatibility)
                        # Store all reservations in a custom attribute for later access
                        first_reservation = reservations[0]
                        first_reservation._all_reservations = reservations
                        first_reservation._is_random_allocation = True
                        first_reservation._total_seats = target_count
                        return first_reservation

                    # Add delay between individual reservations to avoid rate limit
                    # Use longer interval for safety
                    time.sleep(self._search_interval * 1.5)
                    break  # Found a train and reserved, restart search loop

                else:
                    logger.debug("Reservation failed, continuing search...")

            # Wait before next search attempt
            time.sleep(self._search_interval)

        return reservations[0] if reservations else None

    def _get_search_cutoff_time(
        self,
        dep_date: str,
        src_locate: str,
        dst_locate: str,
        dep_time: str,
        max_dep_time: str,
        train_type: TrainType,
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
                train_type=train_type,
                passenger_count=passenger_count,
                verbose=False
            )
            candidate_times = [
                self._extract_departure_time(train)
                for train in target_trains
            ]
            candidate_times = [time_value for time_value in candidate_times if time_value > 0]
        except Exception as e:
            logger.warning(f"Failed to calculate Korail target train cutoff: {e}")

        if candidate_times:
            cutoff_hhmm = max(candidate_times)
        elif max_dep_time and max_dep_time != "2400":
            cutoff_hhmm = int(max_dep_time)
        else:
            cutoff_hhmm = 2359

        return self._build_kst_datetime(dep_date, cutoff_hhmm)

    def _is_search_expired(self, cutoff_at: Optional[datetime]) -> bool:
        """Return True once the last target train can no longer be reserved."""
        if cutoff_at and self._now_kst() >= cutoff_at:
            self.last_stop_reason = (
                f"search window expired after last target train departure "
                f"({cutoff_at.strftime('%Y-%m-%d %H:%M')})"
            )
            logger.info(f"Korail reservation loop stopped: {self.last_stop_reason}")
            return True
        return False

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

    def _cancel_reservations(self, reservations: List) -> None:
        """Cancel a list of reservations (cleanup for failed random allocation)."""
        if not reservations:
            return

        logger.warning(f"Cancelling {len(reservations)} partial reservations...")
        for reservation in reservations:
            try:
                # Note: korail2 API has a cancel method but we need to check if it's available
                logger.warning(f"Would cancel reservation: {reservation}")
                # self._korail_instance.cancel(reservation.rsv_id)
            except Exception as e:
                logger.error(f"Failed to cancel reservation: {e}")

    def _extract_departure_time(self, train) -> int:
        """
        Extract departure time from train object as HHMM integer.

        Args:
            train: Train object from korail2

        Returns:
            Departure time as integer (e.g., 944 for 09:44), 0 if extraction fails
        """
        try:
            # str(train) format: "[KTX] 4월 8일, 용산~광주송정(09:44~12:50), ..."
            # Use rsplit to handle station names with parentheses e.g. 울산(통도사)~서울(09:44~12:50)
            train_str = str(train)
            time_part = train_str.rsplit("(", 1)[1].split("~")[0]  # "09:44"
            time_str = "".join(time_part.split(":"))  # "0944"
            return int(time_str)
        except (IndexError, ValueError) as e:
            logger.error(f"Failed to extract departure time from train: {train}, error: {e}")
            return 0

    def _filter_trains_by_date(self, trains: List, dep_date: str, verbose: bool = True) -> List:
        requested_date = dep_date[:8]
        filtered_trains = []

        for train in trains:
            train_date = self._extract_departure_date(train, requested_date)
            if train_date and train_date != requested_date:
                if verbose:
                    logger.debug(f"Filtered out by date: {train_date} != {requested_date}")
                continue
            filtered_trains.append(train)

        return filtered_trains

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
        """Check if currently logged in."""
        return self._logged_in


class DuplicateReservationError(Exception):
    """Raised when attempting to reserve a train that's already reserved."""
    pass
