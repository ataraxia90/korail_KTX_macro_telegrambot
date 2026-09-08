"""Probe whether korail2 can search unified KTX/SRT routes.

This script is intentionally read-only: it logs in and searches trains, but it
never calls reserve().
"""
from __future__ import annotations

import argparse
import importlib.util
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


ROOT_DIR = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT_DIR / "src"
sys.path.insert(0, str(SRC_DIR))
os.environ.setdefault("LOG_LEVEL", "CRITICAL")


@dataclass(frozen=True)
class ProbeRoute:
    src: str
    dst: str
    label: str


DEFAULT_ROUTES = [
    ProbeRoute("수서", "부산", "수서발 KTX/SRT 통합 가능성"),
    ProbeRoute("수서", "대전", "기존 SRT 핵심 구간"),
    ProbeRoute("서울", "부산", "서울발 SRT 포함 가능성"),
]


def _iter_train_type_candidates(train_type_cls) -> Iterable[tuple[str, object]]:
    """Yield likely korail2 TrainType candidates without assuming enum shape."""
    seen: set[str] = set()
    for name in ("KTX", "ALL", "SRT"):
        if hasattr(train_type_cls, name):
            value = getattr(train_type_cls, name)
            key = repr(value)
            if key not in seen:
                seen.add(key)
                yield name, value


def _train_summary(train) -> dict:
    attrs = {}
    for name in (
        "train_no",
        "trainnum",
        "train_num",
        "train_number",
        "train_type",
        "train_name",
        "dep_time",
        "arr_time",
        "general_seat",
        "special_seat",
        "seat_available",
    ):
        if hasattr(train, name):
            attrs[name] = str(getattr(train, name))
    attrs["text"] = str(train)
    return attrs


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Read-only korail2 probe for KTX/SRT unified search support."
    )
    parser.add_argument("--date", required=True, help="Departure date, YYYYMMDD")
    parser.add_argument("--time", default="000000", help="Departure time, HHMMSS")
    parser.add_argument("--max-time", default="2400", help="Exclusive max departure time, HHMM")
    parser.add_argument("--username", default=os.environ.get("USERID"))
    parser.add_argument("--password", default=os.environ.get("USERPW"))
    parser.add_argument(
        "--route",
        action="append",
        metavar="SRC:DST",
        help="Route to probe. Can be repeated. Default probes 수서:부산, 수서:대전, 서울:부산.",
    )
    parser.add_argument("--limit", type=int, default=10, help="Max trains to print per query")
    return parser.parse_args()


def _routes_from_args(route_args: list[str] | None) -> list[ProbeRoute]:
    if not route_args:
        return DEFAULT_ROUTES

    routes = []
    for raw in route_args:
        if ":" not in raw:
            raise ValueError(f"Invalid route format: {raw}. Use SRC:DST")
        src, dst = (part.strip() for part in raw.split(":", 1))
        if not src or not dst:
            raise ValueError(f"Invalid route format: {raw}. Use SRC:DST")
        routes.append(ProbeRoute(src, dst, f"{src}->{dst}"))
    return routes


def main() -> int:
    args = _parse_args()
    if not args.username or not args.password:
        print("ERROR: USERID/USERPW env vars or --username/--password are required.")
        return 2

    try:
        from korail2 import TrainType
        service_path = SRC_DIR / "services" / "korail_service.py"
        spec = importlib.util.spec_from_file_location("korail_service_probe", service_path)
        if spec is None or spec.loader is None:
            raise ImportError(f"Cannot load {service_path}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        KorailService = module.KorailService
    except ImportError as exc:
        print(f"ERROR: required dependency is missing: {exc}")
        print("Install project dependencies first, for example: pipenv install")
        return 2

    service = KorailService()
    if not service.login(args.username, args.password):
        print("ERROR: korail2 login failed.")
        return 1

    routes = _routes_from_args(args.route)
    train_types = list(_iter_train_type_candidates(TrainType))
    if not train_types:
        print("ERROR: no usable korail2 TrainType candidates found.")
        return 1

    print("Read-only probe. reserve() will not be called.")
    print(f"date={args.date}, time={args.time}, max_time={args.max_time}")
    print(f"train_type_candidates={[name for name, _ in train_types]}")
    print()

    for route in routes:
        print(f"## {route.label}: {route.src} -> {route.dst}")
        for type_name, train_type in train_types:
            trains = service.search_trains(
                dep_date=args.date,
                src_locate=route.src,
                dst_locate=route.dst,
                dep_time=args.time,
                max_dep_time=args.max_time,
                train_type=train_type,
                passenger_count=1,
                verbose=False,
                include_no_seats=True,
                include_waiting_list=True,
            )
            print(f"- TrainType.{type_name}: {len(trains)} train(s)")
            for index, train in enumerate(trains[: args.limit], start=1):
                print(f"  {index}. {_train_summary(train)}")
        print()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
