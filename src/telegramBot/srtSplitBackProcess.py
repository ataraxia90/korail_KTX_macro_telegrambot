"""Background process for two-segment SRT split reservation."""
from concurrent.futures import ThreadPoolExecutor, as_completed
import os
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
            with ThreadPoolExecutor(max_workers=2) as executor:
                futures = [
                    executor.submit(self._reserve_segment, label, src, dst)
                    for label, src, dst in segments
                ]
                results = [future.result() for future in as_completed(futures)]

            results.sort(key=lambda item: item["label"])
            successes = [result for result in results if result["reservation"]]

            if len(successes) == 2:
                self._send_callback(self._build_success_message(successes), status=0)
                return

            self._send_callback(self._build_failure_message(results), status=1)
        except Exception as e:
            logger.error(f"SRT split reservation process error: {e}", exc_info=True)
            self._send_callback(
                f"❌ SRT 분할 예매 처리 중 오류가 발생했습니다.\n\n오류: {e}",
                status=1
            )

    def _reserve_segment(self, label: str, src: str, dst: str) -> dict:
        service = SrtService()
        result = {
            "label": label,
            "src": src,
            "dst": dst,
            "reservation": None,
            "error": "",
        }

        if not service.login(self.username, self.password):
            result["error"] = "SRT 로그인 실패"
            return result

        try:
            reservation = service.search_and_reserve_loop(
                dep_date=self.dep_date,
                src_locate=src,
                dst_locate=dst,
                dep_time=self.dep_time,
                max_dep_time=self.max_dep_time,
                seat_type=service.parse_seat_type(self.seat_type_str),
                passenger_count=self.passenger_count
            )
            result["reservation"] = reservation
            if not reservation:
                result["error"] = service.last_stop_reason or "예약 가능한 열차 없음"
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
                logger.warning(f"SRT split callback returned {response.status_code}")
        except Exception as e:
            logger.error(f"Failed to send SRT split callback: {e}")


if __name__ == "__main__":
    process = SrtSplitBackgroundReservationProcess()
    process.run()
