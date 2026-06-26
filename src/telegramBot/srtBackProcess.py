"""Background process for SRT reservation."""
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


class SrtBackgroundReservationProcess:
    """Background process for SRT reservation."""

    def __init__(self):
        if len(sys.argv) < 11:
            logger.error("Insufficient arguments")
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
        self.srt = SrtService()

    def run(self):
        try:
            if not self.srt.login(self.username, self.password):
                self._send_callback(
                    "❌ SRT 로그인에 실패했습니다.\n\n아이디/비밀번호를 확인한 뒤 /cancel 후 다시 시도해주세요.",
                    status=1
                )
                return

            reservation = self.srt.search_and_reserve_loop(
                dep_date=self.dep_date,
                src_locate=self.src_locate,
                dst_locate=self.dst_locate,
                dep_time=self.dep_time,
                max_dep_time=self.max_dep_time,
                seat_type=self.srt.parse_seat_type(self.seat_type_str),
                passenger_count=self.passenger_count
            )

            if reservation:
                self._send_callback(
                    "🎉 SRT 예약에 성공했습니다!\n\n"
                    "예약 정보는 다음과 같습니다.\n"
                    f"===================\n{reservation}\n===================\n\n"
                    f"⚠️ 중요: {settings.PAYMENT_TIMEOUT_MINUTES}분 내에 SRT 사이트에서 결제를 완료해주세요.\n"
                    f"🔗 결제 링크: {settings.SRT_PAYMENT_URL}",
                    status=0
                )
            else:
                if self.srt.last_stop_reason:
                    self._send_callback(
                        "🚫 SRT 예약 감시가 종료되었습니다.\n\n"
                        "마지막 대상 열차의 출발 시간이 지나 더 이상 예약을 시도할 수 없습니다.",
                        status=1
                    )
                else:
                    self._send_callback("❌ 예약 가능한 SRT 열차를 찾지 못했습니다.", status=1)
        except Exception as e:
            logger.error(f"SRT reservation process error: {e}", exc_info=True)
            self._send_callback(f"❌ SRT 예약 처리 중 오류가 발생했습니다.\n\n오류: {e}", status=1)

    def _send_callback(self, message: str, status: int = 0):
        try:
            response = requests.session().get(
                f"{settings.CALLBACK_BASE_URL}/telebot",
                params={
                    "chatId": self.chat_id,
                    "msg": message,
                    "status": status,
                    "provider": "SRT"
                },
                verify=False,
                timeout=10
            )
            if response.status_code != 200:
                logger.warning(f"SRT callback returned {response.status_code}")
        except Exception as e:
            logger.error(f"Failed to send SRT callback: {e}")


if __name__ == "__main__":
    process = SrtBackgroundReservationProcess()
    process.run()
