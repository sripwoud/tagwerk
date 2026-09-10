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
from typing import Any

Bucket = tuple[str, str]
Event = dict[str, Any]


@dataclass(frozen=True)
class Config:
    data_dir: Path
    roots: list[tuple[Path, str]]


def load_config(path: Path) -> Config:
    if not path.is_file():
        raise SystemExit(f"config file not found: {path}")
    raw = tomllib.loads(path.read_text())
    data_dir = os.environ.get("TAGWERK_DATA_DIR") or raw.get("data_dir", "~/.local/share/tagwerk")
    roots = [(Path(prefix).expanduser(), kind) for prefix, kind in raw.get("roots", {}).items()]
    roots.sort(key=lambda root: len(str(root[0])), reverse=True)
    return Config(data_dir=Path(data_dir).expanduser(), roots=roots)


def iso_z(ts: datetime) -> str:
    return ts.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def month_file(cfg: Config, day: date) -> Path:
    return cfg.data_dir / f"{day:%Y-%m}.jsonl"


def append_event(cfg: Config, event: Event) -> None:
    now = datetime.now(UTC)
    event["ts"] = iso_z(now)
    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    with month_file(cfg, now).open("a") as ledger:
        ledger.write(json.dumps(event) + "\n")


def read_events(cfg: Config, start: datetime, end: datetime) -> list[Event]:
    events: list[Event] = []
    month = (start - timedelta(days=1)).date().replace(day=1)
    while month <= end.date():
        path = month_file(cfg, month)
        if path.is_file():
            with path.open() as ledger:
                for lineno, line in enumerate(ledger, 1):
                    try:
                        events.append(json.loads(line))
                    except json.JSONDecodeError as err:
                        raise SystemExit(f"{path}:{lineno}: malformed ledger line: {err}") from err
        month = (month + timedelta(days=32)).replace(day=1)
    return sorted(events, key=lambda event: event["ts"])


def attribute(events: list[Event], start: datetime, end: datetime) -> dict[Bucket, float]:
    spans = [
        (datetime.fromisoformat(e["start"]), datetime.fromisoformat(e["end"]), (e["kind"], e["project"]))
        for e in events
        if e["ev"] == "span"
    ]
    spans.reverse()
    minutes: defaultdict[Bucket, float] = defaultdict(float)
    minute = start
    while minute < end:
        for span_start, span_end, bucket in spans:
            if span_start <= minute < span_end:
                if bucket[0] != "off":
                    minutes[bucket] += 1.0
                break
        minute += timedelta(minutes=1)
    return minutes


def today_local() -> date:
    return datetime.now(UTC).astimezone().date()


def local_day(day: date) -> tuple[datetime, datetime]:
    start = datetime.combine(day, time.min).astimezone()
    end = datetime.combine(day + timedelta(days=1), time.min).astimezone()
    return start.astimezone(UTC), end.astimezone(UTC)


def local_time(text: str) -> datetime:
    local = datetime.fromisoformat(text) if "T" in text else datetime.combine(today_local(), time.fromisoformat(text))
    return local.astimezone().astimezone(UTC)


def hours_mm(minutes: float) -> str:
    whole = round(minutes)
    return f"{whole // 60}:{whole % 60:02d}"


def render_table(minutes: dict[Bucket, float]) -> str:
    rows = sorted(minutes.items(), key=lambda item: (item[0][0] != "work", -item[1], item[0][1]))
    cells = [(project, hours_mm(m)) for (_, project), m in rows]
    cells.append(("work", hours_mm(sum(m for (kind, _), m in rows if kind == "work"))))
    cells.append(("total", hours_mm(sum(minutes.values()))))
    name_width = max(len(name) for name, _ in cells)
    time_width = max(len(hhmm) for _, hhmm in cells)
    return "\n".join(f"{name:<{name_width}}  {hhmm:>{time_width}}" for name, hhmm in cells)


def cmd_fix(cfg: Config, start: datetime, end: datetime, project: str, kind: str) -> None:
    if end <= start:
        raise SystemExit(
            f"end must be after start: {start.astimezone():%Y-%m-%dT%H:%M} to {end.astimezone():%Y-%m-%dT%H:%M}"
        )
    append_event(
        cfg, {"ev": "span", "start": iso_z(start), "end": iso_z(end), "kind": kind, "project": project, "src": "fix"}
    )


def cmd_today(cfg: Config) -> None:
    start, end = local_day(today_local())
    print(render_table(attribute(read_events(cfg, start, end), start, end)))


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="tagwerk", description="Passive work-hours ledger for one Linux desktop.")
    commands = parser.add_subparsers(dest="command", required=True)
    fix = commands.add_parser("fix", help="book a span by hand; it overrides the sensors for its range")
    fix.add_argument("start", type=local_time, help="HH:MM today or YYYY-MM-DDTHH:MM, local time")
    fix.add_argument("end", type=local_time, help="HH:MM today or YYYY-MM-DDTHH:MM, local time")
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
    args = parser.parse_args(argv)
    cfg = load_config(Path(os.environ.get("TAGWERK_CONFIG", "~/.config/tagwerk/config.toml")).expanduser())
    if args.command == "fix":
        cmd_fix(cfg, args.start, args.end, args.project, args.kind)
    else:
        cmd_today(cfg)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
