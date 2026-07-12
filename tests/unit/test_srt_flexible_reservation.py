from datetime import datetime, timedelta, timezone

from telegramBot.srtBackProcess import SrtBackgroundReservationProcess


KST = timezone(timedelta(hours=9), name="KST")


class FakeSrtService:
    def __init__(self, group_trains=None, single_trains=None):
        self.group_trains = list(group_trains or [])
        self.single_trains = list(single_trains or [])
        self.search_counts = []
        self.reserve_counts = []

    def _get_search_cutoff_time(self, **kwargs):
        return datetime(2099, 12, 31, 23, 59, tzinfo=KST)

    def _now_kst(self):
        return datetime(2099, 12, 31, 9, 0, tzinfo=KST)

    def search_trains(self, **kwargs):
        passenger_count = kwargs["passenger_count"]
        self.search_counts.append(passenger_count)
        if passenger_count > 1:
            return self.group_trains.pop(0) if self.group_trains else []
        return self.single_trains.pop(0) if self.single_trains else []

    def parse_seat_type(self, seat_type_str):
        return seat_type_str

    def reserve_train(self, train, seat_type=None, passenger_count=1):
        self.reserve_counts.append(passenger_count)
        return f"srt-reservation-{passenger_count}-{train}"


class FakeStorage:
    def __init__(self):
        self.partial_reservations = []
        self.current_seat_index = None
        self.multi_status = None

    def save_partial_reservation(self, chat_id, seat_index, reservation_data):
        self.partial_reservations.append(reservation_data)

    def get_partial_reservations(self, chat_id):
        return self.partial_reservations

    def set_current_seat_index(self, chat_id, seat_index):
        self.current_seat_index = seat_index

    def wait_for_payment(self, chat_id, seat_index, timeout):
        return True

    def get_multi_reservation_status(self, chat_id):
        return self.multi_status

    def save_multi_reservation_status(self, status):
        self.multi_status = status


def make_process(srt):
    process = SrtBackgroundReservationProcess.__new__(SrtBackgroundReservationProcess)
    process.username = "user"
    process.password = "pw"
    process.dep_date = "20991231"
    process.src_locate = "수서"
    process.dst_locate = "대전"
    process.dep_time = "0900"
    process.seat_type_str = "GENERAL_FIRST"
    process.chat_id = "12345"
    process.max_dep_time = "1000"
    process.passenger_count = 2
    process.seat_strategy = "flexible"
    process.srt = srt
    process.storage = FakeStorage()
    process.callbacks = []
    process._send_callback = (
        lambda message, status=0, is_multi=False, total_seats=1, seat_strategy="consecutive":
        process.callbacks.append({
            "message": message,
            "status": status,
            "is_multi": is_multi,
            "total_seats": total_seats,
            "seat_strategy": seat_strategy,
        })
    )
    return process


def test_srt_flexible_reservation_prefers_group_reservation():
    srt = FakeSrtService(group_trains=[["group-train"]])
    process = make_process(srt)

    process._run_flexible_reservation()

    assert srt.search_counts == [2]
    assert srt.reserve_counts == [2]
    assert len(process.callbacks) == 1
    assert process.callbacks[0]["status"] == 0
    assert process.callbacks[0]["seat_strategy"] == "flexible"
    assert "srt-reservation-2-group-train" in process.callbacks[0]["message"]


def test_srt_flexible_reservation_falls_back_to_single_seats():
    srt = FakeSrtService(
        group_trains=[[], []],
        single_trains=[["single-train-1"], ["single-train-2"]],
    )
    process = make_process(srt)

    process._run_flexible_reservation()

    assert srt.search_counts == [2, 1, 1]
    assert srt.reserve_counts == [1, 1]
    assert [callback["status"] for callback in process.callbacks] == [2, 2, 2, 0]
    assert process.callbacks[0]["is_multi"] is True
    assert process.storage.current_seat_index is None
    assert len(process.storage.partial_reservations) == 2
    assert process.storage.multi_status.reservations[0].train_info.startswith("SRT ")


def test_srt_random_reservation_uses_single_seat_search_only():
    srt = FakeSrtService(single_trains=[["single-train-1"], ["single-train-2"]])
    process = make_process(srt)
    process.seat_strategy = "random"

    process._run_random_reservation()

    assert srt.search_counts == [1, 1]
    assert srt.reserve_counts == [1, 1]
    assert [callback["status"] for callback in process.callbacks] == [2, 2, 2, 0]
