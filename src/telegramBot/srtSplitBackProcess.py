"""Background process for SRT split and direct+split reservation."""
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
import os
import re
import sys
import threading
import time
import requests

script_dir = os.path.dirname(os.path.abspath(__file__))
src_dir = os.path.dirname(script_dir)
sys.path.insert(0, src_dir)

from config.settings import settings
from services.srt_service import SrtService
from utils.logger import get_logger

logger = get_logger(__name__)
sys.setrecursionlimit(settings.RECURSION_LIMIT)


class SrtSplitBackgroundReservationProcess:
    """Reserve SRT direct and/or two same-train split segments."""

    def __init__(self):
        if len(sys.argv) < 14:
            logger.error("Insufficient arguments for SRT split reservation")
            sys.exit(1)

        self.username = sys.argv[1]
        self.password = sys.argv[2]
        self.dep_date = sys.argv[3]
        self.src_locate = sys.argv[4]
        self.dst_locate = sys.argv[5]
        self.dep_time = sys.argv[6]
        self.seat_type_str = sys.argv[8]
        self.chat_id = sys.argv[9]
        self.max_dep_time = sys.argv[10]
        self.passenger_count = int(sys.argv[11]) if len(sys.argv) > 11 else 1
        self.seat_strategy = sys.argv[12] if len(sys.argv) > 12 else "consecutive"
        self.via_station = sys.argv[13]
        self.split_mode = sys.argv[14] if len(sys.argv) > 14 else "split_only"
        self.reserve_lock = threading.Lock()

    def run(self):
        logger.info(
            "SRT reservation mode started: chat_id=%s, mode=%s, route=%s->%s via %s, "
            "dep_date=%s, dep_time=%s, max_dep_time=%s",
            self.chat_id,
            self.split_mode,
            self.src_locate,
            self.dst_locate,
            self.via_station,
            self.dep_date,
            self.dep_time,
            self.max_dep_time,
        )

        try:
            if self.split_mode == "direct_and_split":
                self._run_direct_and_split()
                return

            result = self._run_split_worker()
            self._send_callback(result["message"], status=0 if result["success"] else 1, is_multi=True)
        except Exception as e:
            logger.error(f"SRT split reservation process error: {e}", exc_info=True)
            self._send_callback(
                f"❌ SRT 분할 예매 처리 중 오류가 발생했습니다.\n\n오류: {e}",
                status=1,
            )

    def _run_direct_and_split(self) -> None:
        """Run direct and split reservation workers concurrently."""
        results = []
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = {
                executor.submit(self._run_direct_worker): "direct",
                executor.submit(self._run_split_worker): "split",
            }

            pending = set(futures)
            while pending:
                done, pending = wait(pending, return_when=FIRST_COMPLETED)
                for future in done:
                    mode = futures[future]
                    result = future.result()
                    logger.info(
                        "SRT %s worker completed: chat_id=%s, success=%s",
                        mode,
                        self.chat_id,
                        result["success"],
                    )
                    results.append((mode, result))
                    if result["success"]:
                        self._send_callback(result["message"], status=0, is_multi=(mode == "split"))
                        # End this background process immediately so the other worker stops too.
                        os._exit(0)

        failure_message = self._build_combined_failure_message(results)
        self._send_callback(failure_message, status=1, is_multi=False)

    def _run_direct_worker(self) -> dict:
        service = SrtService()
        if not service.login(self.username, self.password):
            return {"success": False, "message": "SRT 로그인 실패"}

        reservation = service.search_and_reserve_loop(
            dep_date=self.dep_date,
            src_locate=self.src_locate,
            dst_locate=self.dst_locate,
            dep_time=self.dep_time,
            max_dep_time=self.max_dep_time,
            seat_type=service.parse_seat_type(self.seat_type_str),
            passenger_count=self.passenger_count,
            reserve_lock=self.reserve_lock,
        )
        if reservation:
            return {
                "success": True,
                "message": self._build_direct_success_message(reservation),
            }

        reason = service.last_stop_reason or "예약 가능한 직통 열차 없음"
        return {"success": False, "message": f"직통 예매 실패: {reason}"}

    def _run_split_worker(self) -> dict:
        compatible_numbers = self._get_split_compatible_train_numbers()
        if not compatible_numbers:
            logger.info(
                "SRT split reservation stopped: no compatible trains, chat_id=%s, route=%s->%s via %s",
                self.chat_id,
                self.src_locate,
                self.dst_locate,
                self.via_station,
            )
            return {
                "success": False,
                "message": (
                    "선택한 경유역으로 분할 예매 가능한 대상 열차가 없습니다.\n\n"
                    f"경로: {self.src_locate} -> {self.via_station} -> {self.dst_locate}\n"
                    "직통 대상 열차가 경유역에 정차하지 않거나, 두 구간에서 같은 열차번호로 조회되지 않습니다."
                ),
            }

        first_service = SrtService()
        second_service = SrtService()
        if not first_service.login(self.username, self.password):
            return {"success": False, "message": "SRT 1구간 로그인 실패"}
        if not second_service.login(self.username, self.password):
            return {"success": False, "message": "SRT 2구간 로그인 실패"}

        cutoff_at = first_service._get_search_cutoff_time(
            dep_date=self.dep_date,
            src_locate=self.src_locate,
            dst_locate=self.via_station,
            dep_time=self.dep_time,
            max_dep_time=self.max_dep_time,
            passenger_count=self.passenger_count,
            target_train_numbers=compatible_numbers,
        )

        while True:
            if cutoff_at and first_service._now_kst() >= cutoff_at:
                reason = (
                    "분할 예매 감시 종료: 마지막 대상 열차 출발 시간이 지났습니다 "
                    f"({cutoff_at.strftime('%Y-%m-%d %H:%M')})"
                )
                logger.info("SRT split worker stopped: chat_id=%s, reason=%s", self.chat_id, reason)
                return {"success": False, "message": reason}

            first_trains = first_service.search_trains(
                dep_date=self.dep_date,
                src_locate=self.src_locate,
                dst_locate=self.via_station,
                dep_time=self.dep_time,
                max_dep_time=self.max_dep_time,
                passenger_count=self.passenger_count,
                verbose=False,
            )
            second_trains = second_service.search_trains(
                dep_date=self.dep_date,
                src_locate=self.via_station,
                dst_locate=self.dst_locate,
                dep_time=self.dep_time,
                max_dep_time="2400",
                passenger_count=self.passenger_count,
                verbose=False,
            )

            first_by_number = self._train_map(first_trains, compatible_numbers)
            second_by_number = self._train_map(second_trains, compatible_numbers)
            common_numbers = sorted(
                set(first_by_number) & set(second_by_number),
                key=lambda number: self._train_departure_sort_key(first_by_number[number]),
            )

            if common_numbers:
                logger.info(
                    "SRT split same-train candidates: chat_id=%s, candidates=%s",
                    self.chat_id,
                    common_numbers,
                )

            for train_number in common_numbers:
                result = self._try_reserve_same_train_pair(
                    train_number,
                    first_by_number[train_number],
                    second_by_number[train_number],
                    first_service,
                    second_service,
                )
                if result["success"] or result.get("partial"):
                    return result

            time.sleep(settings.SRT_SEARCH_INTERVAL)

    def _try_reserve_same_train_pair(
        self,
        train_number: str,
        first_train,
        second_train,
        first_service: SrtService,
        second_service: SrtService,
    ) -> dict:
        seat_type1 = first_service.parse_seat_type(self.seat_type_str)
        seat_type2 = second_service.parse_seat_type(self.seat_type_str)
        first_reservation = self._reserve_train_with_lock(
            first_service,
            first_train,
            seat_type1,
            "1구간",
            train_number,
        )
        second_reservation = self._reserve_train_with_lock(
            second_service,
            second_train,
            seat_type2,
            "2구간",
            train_number,
        )

        if first_reservation and second_reservation:
            logger.info(
                "SRT split same-train reservation succeeded: chat_id=%s, train_number=%s",
                self.chat_id,
                train_number,
            )
            return {
                "success": True,
                "message": self._build_split_success_message(
                    train_number,
                    first_reservation,
                    second_reservation,
                ),
            }

        if first_reservation or second_reservation:
            logger.warning(
                "SRT split same-train reservation partially succeeded: chat_id=%s, train_number=%s",
                self.chat_id,
                train_number,
            )
            return {
                "success": False,
                "partial": True,
                "message": self._build_split_partial_failure_message(
                    train_number,
                    first_reservation,
                    second_reservation,
                ),
            }

        return {"success": False, "message": "no reservation"}

    def _reserve_train_with_lock(
        self,
        service: SrtService,
        train,
        seat_type,
        segment_label: str,
        train_number: str,
    ):
        logger.info(
            "SRT reserve request waiting for lock: chat_id=%s, segment=%s, train_number=%s",
            self.chat_id,
            segment_label,
            train_number,
        )
        with self.reserve_lock:
            logger.info(
                "SRT reserve request started: chat_id=%s, segment=%s, train_number=%s",
                self.chat_id,
                segment_label,
                train_number,
            )
            reservation = service.reserve_train(
                train,
                seat_type=seat_type,
                passenger_count=self.passenger_count,
            )
            logger.info(
                "SRT reserve request finished: chat_id=%s, segment=%s, train_number=%s, success=%s",
                self.chat_id,
                segment_label,
                train_number,
                bool(reservation),
            )
            return reservation

    def _get_split_compatible_train_numbers(self) -> set[str]:
        service = SrtService()
        if not service.login(self.username, self.password):
            logger.warning("SRT split compatibility login failed: chat_id=%s", self.chat_id)
            return set()

        base_kwargs = {
            "dep_date": self.dep_date,
            "dep_time": self.dep_time,
            "max_dep_time": self.max_dep_time,
            "passenger_count": self.passenger_count,
            "verbose": False,
            "available_only": False,
        }
        second_segment_kwargs = {
            **base_kwargs,
            "max_dep_time": "2400",
        }
        direct_numbers = self._train_number_set(service.search_trains(
            src_locate=self.src_locate,
            dst_locate=self.dst_locate,
            **base_kwargs,
        ))
        first_numbers = self._train_number_set(service.search_trains(
            src_locate=self.src_locate,
            dst_locate=self.via_station,
            **base_kwargs,
        ))
        second_numbers = self._train_number_set(service.search_trains(
            src_locate=self.via_station,
            dst_locate=self.dst_locate,
            **second_segment_kwargs,
        ))
        compatible_numbers = direct_numbers & first_numbers & second_numbers
        logger.info(
            "SRT split compatibility checked: chat_id=%s, direct=%s, first=%s, second=%s, compatible=%s",
            self.chat_id,
            sorted(direct_numbers),
            sorted(first_numbers),
            sorted(second_numbers),
            sorted(compatible_numbers),
        )
        return compatible_numbers

    def _train_map(self, trains: list, allowed_numbers: set[str]) -> dict:
        result = {}
        for train in trains:
            number = self._extract_train_number(train)
            if number in allowed_numbers and number not in result:
                result[number] = train
        return result

    def _train_number_set(self, trains: list) -> set[str]:
        return {
            number
            for number in (self._extract_train_number(train) for train in trains)
            if number
        }

    def _extract_train_number(self, train) -> str:
        for attr in (
            "train_no", "trainnum", "train_num", "train_number", "number",
            "train_name", "name", "train", "type"
        ):
            value = getattr(train, attr, None)
            if value:
                text = str(value).strip()
                if text and text.lower() not in ("none", "null"):
                    return self._normalize_train_number(text)

        text = str(train)
        match = re.search(r"\b(SRT)[\s-]*(\d{1,4})\b", text, re.IGNORECASE)
        if match:
            return f"SRT{match.group(2)}"
        match = re.search(r"\b(\d{1,4})\s*(?:열차|호차|호)\b", text)
        if match:
            return f"SRT{match.group(1)}"
        return ""

    def _normalize_train_number(self, text: str) -> str:
        match = re.search(r"\b(SRT)[\s-]*(\d{1,4})\b", text, re.IGNORECASE)
        if match:
            return f"SRT{match.group(2)}"
        if text.isdigit():
            return f"SRT{text}"
        return text

    def _train_departure_sort_key(self, train) -> int:
        try:
            return SrtService()._extract_departure_time(train)
        except Exception:
            return 0

    def _build_direct_success_message(self, reservation) -> str:
        return (
            "🎉 SRT 직통 예약에 성공했습니다!\n\n"
            "예약 정보는 다음과 같습니다.\n"
            f"===================\n{reservation}\n===================\n\n"
            f"중요: {settings.PAYMENT_TIMEOUT_MINUTES}분 이내에 SRT 사이트에서 결제를 완료해주세요.\n"
            f"결제 링크: {settings.SRT_PAYMENT_URL}"
        )

    def _build_split_success_message(self, train_number: str, first_reservation, second_reservation) -> str:
        return "\n".join([
            "🎉 SRT 분할 예매가 성공했습니다!",
            "",
            f"대상 열차: {train_number}",
            "===================",
            f"[1구간] {self.src_locate} -> {self.via_station}",
            str(first_reservation),
            "-------------------",
            f"[2구간] {self.via_station} -> {self.dst_locate}",
            str(second_reservation),
            "===================",
            f"중요: {settings.PAYMENT_TIMEOUT_MINUTES}분 이내에 SRT 사이트에서 결제를 완료해주세요.",
            f"결제 링크: {settings.SRT_PAYMENT_URL}",
        ])

    def _build_split_partial_failure_message(self, train_number: str, first_reservation, second_reservation) -> str:
        return "\n".join([
            "⚠️ SRT 분할 예매가 일부만 성공했습니다.",
            "",
            f"대상 열차: {train_number}",
            f"1구간: {'예약됨' if first_reservation else '실패'}",
            f"2구간: {'예약됨' if second_reservation else '실패'}",
            "",
            "한 구간만 예약된 경우 SRT 사이트에서 직접 결제 또는 취소 상태를 확인해주세요.",
            f"결제 링크: {settings.SRT_PAYMENT_URL}",
        ])

    def _build_combined_failure_message(self, results: list[tuple[str, dict]]) -> str:
        lines = ["❌ SRT 직통/분할 병행 예매가 완료되지 않았습니다.", ""]
        for mode, result in results:
            label = "직통" if mode == "direct" else "분할"
            lines.append(f"[{label}] {result['message']}")
        return "\n".join(lines)

    def _send_callback(self, message: str, status: int = 0, is_multi: bool = True):
        try:
            params = {
                "chatId": self.chat_id,
                "msg": message,
                "status": status,
                "provider": "SRT",
                "isMulti": "1" if is_multi else "0",
                "seatStrategy": self.seat_strategy,
            }
            if is_multi:
                params.update({
                    "totalSeats": "2",
                    "split": "1",
                })
            response = requests.session().get(
                f"{settings.CALLBACK_BASE_URL}/telebot",
                params=params,
                verify=False,
                timeout=10,
            )
            if response.status_code != 200:
                logger.warning(
                    "SRT split callback returned %s: chat_id=%s, status=%s",
                    response.status_code,
                    self.chat_id,
                    status,
                )
            else:
                logger.info(
                    "SRT split callback sent: chat_id=%s, status=%s",
                    self.chat_id,
                    status,
                )
        except Exception as e:
            logger.error(f"Failed to send SRT split callback: {e}")


if __name__ == "__main__":
    process = SrtSplitBackgroundReservationProcess()
    process.run()
