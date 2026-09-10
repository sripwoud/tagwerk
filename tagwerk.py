#!/usr/bin/python3 -I
import argparse
import json
import os
import sys
import tomllib
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any, NamedTuple

Event = dict[str, Any]


class Bucket(NamedTuple):
    kind: str
    project: str


@dataclass(frozen=True)
class Config:
    data_dir: Path


def default_config_path() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME", "~/.config")).expanduser() / "tagwerk" / "config.toml"


def default_data_dir() -> Path:
    return Path(os.environ.get("XDG_DATA_HOME", "~/.local/share")).expanduser() / "tagwerk"


def load_config(path: Path) -> Config:
    if not path.is_file():
        raise SystemExit(f"config file not found: {path}")
    raw = tomllib.loads(path.read_text())
    data_dir = Path(os.environ.get("TAGWERK_DATA_DIR") or raw.get("data_dir") or default_data_dir()).expanduser()
    return Config(data_dir=data_dir)


def format_utc(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def month_file(config: Config, day: date) -> Path:
    return config.data_dir / f"{day:%Y-%m}.jsonl"


def append_event(config: Config, event: Event) -> None:
    now = datetime.now(UTC)
    event["ts"] = format_utc(now)
    filed = datetime.fromisoformat(event["start"]) if event["ev"] == "span" else now
    config.data_dir.mkdir(parents=True, exist_ok=True)
    with month_file(config, filed).open("a") as ledger:
        ledger.write(json.dumps(event) + "\n")


def read_ledger_file(path: Path) -> list[Event]:
    events: list[Event] = []
    with path.open() as ledger:
        for lineno, line in enumerate(ledger, 1):
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError as err:
                raise SystemExit(f"{path}:{lineno}: malformed ledger line: {err}") from err
    return events


def next_month(first: date) -> date:
    return (first + timedelta(days=32)).replace(day=1)


def read_events(config: Config, start: datetime, end: datetime) -> list[Event]:
    events: list[Event] = []
    month = (start - timedelta(days=1)).date().replace(day=1)
    while month <= end.date():
        path = month_file(config, month)
        if path.is_file():
            events += read_ledger_file(path)
        month = next_month(month)
    return sorted(events, key=lambda event: event["ts"])


def attribute(events: list[Event], start: datetime, end: datetime) -> dict[Bucket, float]:
    spans = [
        (
            datetime.fromisoformat(event["start"]),
            datetime.fromisoformat(event["end"]),
            Bucket(event["kind"], event["project"]),
        )
        for event in events
        if event["ev"] == "span"
    ]
    spans.reverse()
    minutes: defaultdict[Bucket, float] = defaultdict(float)
    minute = start
    # ponytail: O(minutes x spans) scan per report; a month is 43k minutes, fine for years of data
    while minute < end:
        for span_start, span_end, bucket in spans:
            if span_start <= minute < span_end:
                # ponytail: the latest span covering a minute wins wholesale; no partial merge with sensor minutes
                if bucket.kind != "off":
                    minutes[bucket] += 1.0
                break
        minute += timedelta(minutes=1)
    return minutes


def local_today() -> date:
    return datetime.now(UTC).astimezone().date()


def local_range(first: date, last: date) -> tuple[datetime, datetime]:
    return datetime.combine(first, time.min).astimezone(UTC), datetime.combine(last, time.min).astimezone(UTC)


def local_day(day: date) -> tuple[datetime, datetime]:
    return local_range(day, day + timedelta(days=1))


def local_month(first: date) -> tuple[datetime, datetime]:
    return local_range(first, next_month(first))


def parse_month(text: str) -> date:
    return date.fromisoformat(f"{text}-01")


def parse_local(text: str) -> datetime:
    local = datetime.fromisoformat(text) if "T" in text else datetime.combine(local_today(), time.fromisoformat(text))
    return local.astimezone(UTC)


def format_hours(minutes: float) -> str:
    whole = round(minutes)
    return f"{whole // 60}:{whole % 60:02d}"


def render_table(minutes: dict[Bucket, float]) -> str:
    rows = sorted(minutes.items(), key=lambda row: (row[0].kind != "work", -row[1], row[0].project))
    cells = [(bucket.project, format_hours(credited)) for bucket, credited in rows]
    cells.append(("work", format_hours(sum(credited for bucket, credited in rows if bucket.kind == "work"))))
    cells.append(("total", format_hours(sum(minutes.values()))))
    name_width = max(len(name) for name, _ in cells)
    hours_width = max(len(hours) for _, hours in cells)
    return "\n".join(f"{name:<{name_width}}  {hours:>{hours_width}}" for name, hours in cells)


def cmd_fix(config: Config, start: datetime, end: datetime, project: str, kind: str) -> None:
    if end <= start:
        raise SystemExit(
            f"end must be after start: {start.astimezone():%Y-%m-%dT%H:%M} to {end.astimezone():%Y-%m-%dT%H:%M}"
        )
    span = {
        "ev": "span",
        "start": format_utc(start),
        "end": format_utc(end),
        "kind": kind,
        "project": project,
        "src": "fix",
    }
    append_event(config, span)


def report(config: Config, start: datetime, end: datetime) -> None:
    print(render_table(attribute(read_events(config, start, end), start, end)))


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="tagwerk", description="Passive work-hours ledger for one Linux desktop.")
    commands = parser.add_subparsers(dest="command", required=True)
    fix = commands.add_parser("fix", help="book a span by hand; it overrides the sensors for its range")
    fix.add_argument("start", type=parse_local, help="HH:MM today or YYYY-MM-DDTHH:MM, local time")
    fix.add_argument("end", type=parse_local, help="HH:MM today or YYYY-MM-DDTHH:MM, local time")
    fix.add_argument("project")
    kind = fix.add_mutually_exclusive_group()
    kind.add_argument(
        "--personal", dest="kind", action="store_const", const="personal", help="chart only, never invoiced"
    )
    kind.add_argument(
        "--off", dest="kind", action="store_const", const="off", help="remove the range from every report"
    )
    fix.set_defaults(kind="work")
    commands.add_parser("today", help="hours per project for the local day")
    month = commands.add_parser("month", help="hours per project for a calendar month")
    month.add_argument("month", nargs="?", type=parse_month, default=None, help="YYYY-MM, default the current month")
    args = parser.parse_args(argv)
    config = load_config(Path(os.environ.get("TAGWERK_CONFIG") or default_config_path()).expanduser())
    if args.command == "fix":
        cmd_fix(config, args.start, args.end, args.project, args.kind)
    elif args.command == "month":
        report(config, *local_month(args.month or local_today().replace(day=1)))
    else:
        report(config, *local_day(local_today()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
