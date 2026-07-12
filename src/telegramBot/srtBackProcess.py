"""Background process for SRT reservation."""
import os
import sys
import requests
import time
from datetime import datetime, timedelta

script_dir = os.path.dirname(os.path.abspath(__file__))
src_dir = os.path.dirname(script_dir)
sys.path.insert(0, src_dir)

from config.settings import settings
from models import MultiReservationStatus, ReservationPaymentStatus, SingleReservationInfo
from services.srt_service import SrtService
from storage.redis import RedisStorage
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
        self.seat_strategy = sys.argv[12] if len(sys.argv) > 12 else "consecutive"
        self.srt = SrtService()
        self.storage = RedisStorage()

    def run(self):
        try:
            if not self.srt.login(self.username, self.password):
                self._send_callback(
                    "❌ SRT 로그인에 실패했습니다.\n\n아이디/비밀번호를 확인한 뒤 /cancel 후 다시 시도해주세요.",
                    status=1
                )
                return

            if self.seat_strategy == "random":
                self._run_random_reservation()
                return
            if self.seat_strategy == "flexible":
                self._run_flexible_reservation()
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
                    status=0,
                    seat_strategy=self.seat_strategy
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

    def _run_flexible_reservation(self):
        """Try all SRT seats together first, then fall back to one-by-one booking."""
        total_seats = self.passenger_count
        seat_index = 0
        group_cutoff_at = self._get_cutoff_time(total_seats)
        single_cutoff_at = self._get_cutoff_time(1)

        while seat_index < total_seats:
            active_cutoff_at = group_cutoff_at if seat_index == 0 else single_cutoff_at
            if active_cutoff_at and self.srt._now_kst() >= active_cutoff_at:
                self.storage.set_current_seat_index(self.chat_id, None)
                self._send_callback(
                    "🚫 SRT 예약 감시가 종료되었습니다.\n\n"
                    "마지막 대상 열차의 출발 시간이 지나 더 이상 예약을 시도할 수 없습니다.",
                    status=1,
                    seat_strategy=self.seat_strategy,
                )
                return

            remaining = total_seats - seat_index
            if remaining > 1 and seat_index == 0:
                group_reservation = self._try_reserve_group_once(remaining)
                if group_reservation:
                    self._send_callback(
                        self._build_group_success_message(group_reservation, remaining),
                        status=0,
                        seat_strategy=self.seat_strategy,
                    )
                    return

            reservation = self._try_reserve_single_once(seat_index)
            if not reservation:
                time.sleep(settings.SRT_SEARCH_INTERVAL)
                continue

            self._save_partial_reservation(seat_index, reservation, total_seats)
            self.storage.set_current_seat_index(self.chat_id, seat_index)
            self._send_callback(
                self._build_partial_reservation_message(seat_index, total_seats, reservation),
                status=2,
                is_multi=True,
                total_seats=total_seats,
                seat_strategy=self.seat_strategy,
            )

            seat_index += 1
            if seat_index < total_seats:
                payment_confirmed = self.storage.wait_for_payment(
                    self.chat_id,
                    seat_index - 1,
                    timeout=600,
                )
                if payment_confirmed:
                    self._send_callback(
                        f"✅ {seat_index}번째 좌석 결제 확인!\n\n다음 좌석 예약을 시작합니다...",
                        status=2,
                        is_multi=True,
                        total_seats=total_seats,
                        seat_strategy=self.seat_strategy,
                    )
                else:
                    self._send_callback(
                        f"⏰ {seat_index}번째 좌석 결제 시간 초과\n\n"
                        "10분이 지났습니다. 다음 좌석 예약을 진행합니다.\n\n"
                        "⚠️ 미결제 좌석은 자동 취소될 수 있으니 빠르게 결제해주세요!",
                        status=2,
                        is_multi=True,
                        total_seats=total_seats,
                        seat_strategy=self.seat_strategy,
                    )
                time.sleep(3)

        self.storage.set_current_seat_index(self.chat_id, None)
        all_reservations = self.storage.get_partial_reservations(self.chat_id)
        self._send_callback(
            self._build_final_individual_message(all_reservations, total_seats),
            status=0,
            is_multi=True,
            total_seats=total_seats,
            seat_strategy=self.seat_strategy,
        )

    def _run_random_reservation(self):
        """Reserve SRT tickets one at a time."""
        total_seats = self.passenger_count
        cutoff_at = self._get_cutoff_time(1)

        for seat_index in range(total_seats):
            while True:
                if cutoff_at and self.srt._now_kst() >= cutoff_at:
                    self.storage.set_current_seat_index(self.chat_id, None)
                    self._send_callback(
                        "🚫 SRT 예약 감시가 종료되었습니다.\n\n"
                        "마지막 대상 열차의 출발 시간이 지나 더 이상 예약을 시도할 수 없습니다.",
                        status=1,
                        seat_strategy=self.seat_strategy,
                    )
                    return

                reservation = self._try_reserve_single_once(seat_index)
                if reservation:
                    break
                time.sleep(settings.SRT_SEARCH_INTERVAL)

            self._save_partial_reservation(seat_index, reservation, total_seats)
            self.storage.set_current_seat_index(self.chat_id, seat_index)
            self._send_callback(
                self._build_partial_reservation_message(seat_index, total_seats, reservation),
                status=2,
                is_multi=True,
                total_seats=total_seats,
                seat_strategy=self.seat_strategy,
            )

            if seat_index < total_seats - 1:
                payment_confirmed = self.storage.wait_for_payment(
                    self.chat_id,
                    seat_index,
                    timeout=600,
                )
                if payment_confirmed:
                    self._send_callback(
                        f"✅ {seat_index + 1}번째 좌석 결제 확인!\n\n다음 좌석 예약을 시작합니다...",
                        status=2,
                        is_multi=True,
                        total_seats=total_seats,
                        seat_strategy=self.seat_strategy,
                    )
                else:
                    self._send_callback(
                        f"⏰ {seat_index + 1}번째 좌석 결제 시간 초과\n\n"
                        "10분이 지났습니다. 다음 좌석 예약을 진행합니다.\n\n"
                        "⚠️ 미결제 좌석은 자동 취소될 수 있으니 빠르게 결제해주세요!",
                        status=2,
                        is_multi=True,
                        total_seats=total_seats,
                        seat_strategy=self.seat_strategy,
                    )
                time.sleep(3)

        self.storage.set_current_seat_index(self.chat_id, None)
        all_reservations = self.storage.get_partial_reservations(self.chat_id)
        self._send_callback(
            self._build_final_individual_message(all_reservations, total_seats),
            status=0,
            is_multi=True,
            total_seats=total_seats,
            seat_strategy=self.seat_strategy,
        )

    def _get_cutoff_time(self, passenger_count: int):
        return self.srt._get_search_cutoff_time(
            dep_date=self.dep_date,
            src_locate=self.src_locate,
            dst_locate=self.dst_locate,
            dep_time=self.dep_time,
            max_dep_time=self.max_dep_time,
            passenger_count=passenger_count,
        )

    def _try_reserve_group_once(self, passenger_count: int):
        return self._try_reserve_once(passenger_count=passenger_count)

    def _try_reserve_single_once(self, seat_index: int):
        return self._try_reserve_once(passenger_count=1)

    def _try_reserve_once(self, passenger_count: int):
        trains = self.srt.search_trains(
            dep_date=self.dep_date,
            src_locate=self.src_locate,
            dst_locate=self.dst_locate,
            dep_time=self.dep_time,
            max_dep_time=self.max_dep_time,
            passenger_count=passenger_count,
            verbose=False,
        )
        seat_type = self.srt.parse_seat_type(self.seat_type_str)
        for train in trains:
            reservation = self.srt.reserve_train(
                train,
                seat_type=seat_type,
                passenger_count=passenger_count,
            )
            if reservation:
                return reservation
        return None

    def _save_partial_reservation(self, seat_index: int, reservation, total_seats: int) -> None:
        reservation_data = {
            "seat_index": seat_index,
            "train_info": str(reservation),
            "reserved_at": datetime.now().isoformat(),
        }
        self.storage.save_partial_reservation(self.chat_id, seat_index, reservation_data)
        self._update_multi_reservation_status(seat_index, reservation, total_seats)

    def _update_multi_reservation_status(self, seat_index: int, reservation, total_seats: int) -> None:
        now = datetime.now()
        expires_at = now + timedelta(minutes=settings.PAYMENT_TIMEOUT_MINUTES)
        multi_status = self.storage.get_multi_reservation_status(self.chat_id)
        if multi_status is None:
            multi_status = MultiReservationStatus(
                chat_id=int(self.chat_id),
                reservations=[],
                total_seats=total_seats,
                seat_strategy=self.seat_strategy,
                created_at=now,
            )

        rsv_id = getattr(reservation, "rsv_id", f"srt_seat_{seat_index + 1}")
        multi_status.reservations.append(SingleReservationInfo(
            reservation_id=rsv_id,
            reservation_obj=reservation,
            reserved_at=now,
            expires_at=expires_at,
            status=ReservationPaymentStatus.PENDING,
            seat_number=seat_index + 1,
            train_info=f"SRT {reservation}",
        ))
        self.storage.save_multi_reservation_status(multi_status)

    def _build_group_success_message(self, reservation, passenger_count: int) -> str:
        return (
            "🎉 SRT 예약에 성공했습니다!\n\n"
            "예약 정보는 다음과 같습니다.\n"
            f"===================\n{reservation}\n===================\n\n"
            f"👥 인원: {passenger_count}명\n\n"
            f"⚠️ 중요: {settings.PAYMENT_TIMEOUT_MINUTES}분 내에 SRT 사이트에서 결제를 완료해주세요.\n"
            f"🔗 결제 링크: {settings.SRT_PAYMENT_URL}"
        )

    def _build_partial_reservation_message(self, seat_index: int, total_seats: int, reservation) -> str:
        return f"""
✅ SRT {seat_index + 1}/{total_seats}번째 좌석 예약 성공

예약 정보:
===================
{reservation}
===================

⏰ 예약 후 {settings.PAYMENT_TIMEOUT_MINUTES}분 이내 결제하세요.
🔗 결제: {settings.SRT_PAYMENT_URL}

💡 결제 후 아무 메시지나 보내면 다음 좌석 예약이 시작됩니다.
⚠️ 10분 동안 메시지가 없으면 자동으로 다음 좌석 예약을 진행합니다.
"""

    def _build_final_individual_message(self, all_reservations: list, total_seats: int) -> str:
        reservation_details = "\n".join([
            f"좌석 {i + 1}: {reservation.get('train_info', 'N/A')}"
            for i, reservation in enumerate(all_reservations)
        ])
        return f"""
🎉🎉 모든 SRT 좌석 예약 완료! 🎉🎉

총 {total_seats}명의 좌석이 개별적으로 예약되었습니다.
(개별 예약: 좌석이 떨어져 있을 수 있습니다)

===================
{reservation_details}
===================

⚠️ 중요: 모든 좌석을 {settings.PAYMENT_TIMEOUT_MINUTES}분 내 결제해야 합니다.
🔗 결제 링크: {settings.SRT_PAYMENT_URL}
"""

    def _send_callback(
        self,
        message: str,
        status: int = 0,
        is_multi: bool = False,
        total_seats: int = 1,
        seat_strategy: str = "consecutive",
    ):
        try:
            response = requests.session().get(
                f"{settings.CALLBACK_BASE_URL}/telebot",
                params={
                    "chatId": self.chat_id,
                    "msg": message,
                    "status": status,
                    "provider": "SRT",
                    "isMulti": str(is_multi).lower(),
                    "totalSeats": total_seats,
                    "seatStrategy": seat_strategy,
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
