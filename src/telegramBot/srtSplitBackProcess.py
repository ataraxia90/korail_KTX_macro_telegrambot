"""Background process for two-segment SRT split reservation."""
from concurrent.futures import ThreadPoolExecutor, as_completed
import os
import re
import sys
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
    """Background process that reserves two SRT segments in parallel."""

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

    def run(self):
        segments = [
            ("1구간", self.src_locate, self.via_station),
            ("2구간", self.via_station, self.dst_locate),
        ]

        try:
            compatible_numbers = self._get_split_compatible_train_numbers()
            if not compatible_numbers:
                logger.info(
                    "SRT split reservation stopped: no compatible trains, chat_id=%s, "
                    "route=%s->%s via %s",
                    self.chat_id,
                    self.src_locate,
                    self.dst_locate,
                    self.via_station,
                )
                self._send_callback(
                    "선택한 경유역으로 분할 예매 가능한 대상 열차가 없습니다.\n\n"
                    f"경로: {self.src_locate} -> {self.via_station} -> {self.dst_locate}\n"
                    "직통 대상 열차가 경유역에 정차하지 않거나, 두 구간에서 같은 열차번호로 조회되지 않습니다.",
                    status=1,
                )
                return

            logger.info(
                "SRT split reservation started: chat_id=%s, route=%s->%s via %s, "
                "dep_date=%s, dep_time=%s, max_dep_time=%s, passenger_count=%s, "
                "seat_strategy=%s, compatible_train_numbers=%s",
                self.chat_id,
                self.src_locate,
                self.dst_locate,
                self.via_station,
                self.dep_date,
                self.dep_time,
                self.max_dep_time,
                self.passenger_count,
                self.seat_strategy,
                sorted(compatible_numbers),
            )
            with ThreadPoolExecutor(max_workers=2) as executor:
                futures = {}
                for label, src, dst in segments:
                    logger.info(
                        "SRT split segment submitted: chat_id=%s, label=%s, route=%s->%s",
                        self.chat_id,
                        label,
                        src,
                        dst,
                    )
                    future = executor.submit(self._reserve_segment, label, src, dst, compatible_numbers)
                    futures[future] = (label, src, dst)

                results = []
                for future in as_completed(futures):
                    label, src, dst = futures[future]
                    logger.info(
                        "SRT split segment future completed: chat_id=%s, label=%s, route=%s->%s",
                        self.chat_id,
                        label,
                        src,
                        dst,
                    )
                    results.append(future.result())

            results.sort(key=lambda item: item["label"])
            successes = [result for result in results if result["reservation"]]
            logger.info(
                "SRT split reservation completed: chat_id=%s, success_count=%s, total_segments=%s",
                self.chat_id,
                len(successes),
                len(results),
            )

            if len(successes) == 2:
                logger.info("SRT split reservation succeeded: chat_id=%s", self.chat_id)
                self._send_callback(self._build_success_message(successes), status=0)
                return

            logger.info("SRT split reservation failed or partial: chat_id=%s", self.chat_id)
            self._send_callback(self._build_failure_message(results), status=1)
        except Exception as e:
            logger.error(f"SRT split reservation process error: {e}", exc_info=True)
            self._send_callback(
                f"❌ SRT 분할 예매 처리 중 오류가 발생했습니다.\n\n오류: {e}",
                status=1
            )

    def _get_split_compatible_train_numbers(self) -> set[str]:
        """Return direct train numbers that can be split through the selected via station."""
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
        direct_trains = service.search_trains(
            src_locate=self.src_locate,
            dst_locate=self.dst_locate,
            **base_kwargs,
        )
        first_segment_trains = service.search_trains(
            src_locate=self.src_locate,
            dst_locate=self.via_station,
            **base_kwargs,
        )
        second_segment_trains = service.search_trains(
            src_locate=self.via_station,
            dst_locate=self.dst_locate,
            **base_kwargs,
        )

        direct_numbers = self._train_number_set(direct_trains)
        first_numbers = self._train_number_set(first_segment_trains)
        second_numbers = self._train_number_set(second_segment_trains)
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

    def _reserve_segment(self, label: str, src: str, dst: str, target_train_numbers: set[str]) -> dict:
        service = SrtService()
        logger.info(
            "SRT split segment started: chat_id=%s, label=%s, route=%s->%s",
            self.chat_id,
            label,
            src,
            dst,
        )
        result = {
            "label": label,
            "src": src,
            "dst": dst,
            "reservation": None,
            "error": "",
        }

        if not service.login(self.username, self.password):
            logger.warning(
                "SRT split segment login failed: chat_id=%s, label=%s, route=%s->%s",
                self.chat_id,
                label,
                src,
                dst,
            )
            result["error"] = "SRT 로그인 실패"
            return result

        logger.info(
            "SRT split segment login succeeded: chat_id=%s, label=%s, route=%s->%s",
            self.chat_id,
            label,
            src,
            dst,
        )
        try:
            reservation = service.search_and_reserve_loop(
                dep_date=self.dep_date,
                src_locate=src,
                dst_locate=dst,
                dep_time=self.dep_time,
                max_dep_time=self.max_dep_time,
                seat_type=service.parse_seat_type(self.seat_type_str),
                passenger_count=self.passenger_count,
                target_train_numbers=target_train_numbers
            )
            result["reservation"] = reservation
            if not reservation:
                result["error"] = service.last_stop_reason or "예약 가능한 열차 없음"
            if result["reservation"]:
                logger.info(
                    "SRT split segment reserved: chat_id=%s, label=%s, route=%s->%s",
                    self.chat_id,
                    label,
                    src,
                    dst,
                )
            else:
                logger.info(
                    "SRT split segment ended without reservation: chat_id=%s, "
                    "label=%s, route=%s->%s, reason=%s",
                    self.chat_id,
                    label,
                    src,
                    dst,
                    result["error"],
                )
        except Exception as e:
            logger.error(f"SRT split segment error ({label} {src}->{dst}): {e}", exc_info=True)
            result["error"] = str(e)

        return result

    def _build_success_message(self, results: list[dict]) -> str:
        lines = [
            "✅ SRT 분할 예매가 성공했습니다!",
            "",
            "두 구간 모두 예약되었습니다.",
            "===================",
        ]
        for result in results:
            lines.extend([
                f"[{result['label']}] {result['src']} -> {result['dst']}",
                str(result["reservation"]),
                "-------------------",
            ])
        lines.extend([
            "===================",
            f"중요: {settings.PAYMENT_TIMEOUT_MINUTES}분 이내에 SRT 사이트에서 결제를 완료해주세요.",
            f"Payment link: {settings.SRT_PAYMENT_URL}",
        ])
        return "\n".join(lines)

    def _build_failure_message(self, results: list[dict]) -> str:
        lines = [
            "❌ SRT 분할 예매가 완료되지 않았습니다.",
            "",
            "두 구간이 모두 예약되어야 성공으로 처리합니다.",
            "===================",
        ]
        for result in results:
            if result["reservation"]:
                status = "예약됨"
                detail = str(result["reservation"])
            else:
                status = "실패"
                detail = result["error"] or "예약 실패"
            lines.extend([
                f"[{result['label']}] {result['src']} -> {result['dst']}: {status}",
                detail,
                "-------------------",
            ])
        lines.extend([
            "한 구간만 예약된 경우 SRT 사이트에서 직접 결제 또는 취소 상태를 확인해주세요.",
            f"Payment link: {settings.SRT_PAYMENT_URL}",
        ])
        return "\n".join(lines)

    def _send_callback(self, message: str, status: int = 0):
        try:
            response = requests.session().get(
                f"{settings.CALLBACK_BASE_URL}/telebot",
                params={
                    "chatId": self.chat_id,
                    "msg": message,
                    "status": status,
                    "provider": "SRT",
                    "isMulti": "1",
                    "totalSeats": "2",
                    "seatStrategy": self.seat_strategy,
                    "split": "1",
                },
                verify=False,
                timeout=10
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
