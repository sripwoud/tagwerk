#!/usr/bin/python3 -I
import argparse
import json
import os
import re
import subprocess
import sys
import tomllib
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from time import monotonic, sleep
from typing import Any, NamedTuple

Event = dict[str, Any]
REPOLL_SEC = 60


class Bucket(NamedTuple):
    kind: str
    project: str

    @property
    def is_repo(self) -> bool:
        return self.project not in ("general", "other")


OTHER = Bucket("personal", "other")
TitleRule = tuple[re.Pattern[str], str, str | None]


@dataclass(frozen=True)
class Config:
    data_dir: Path
    poll_stale: timedelta
    beat_lease: timedelta
    focus_lease: timedelta
    roots: list[tuple[Path, str]]
    titles: list[TitleRule]
    poll_sec: float
    kitty_socket: str


def default_config_path() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME", "~/.config")).expanduser() / "tagwerk" / "config.toml"


def default_data_dir() -> Path:
    return Path(os.environ.get("XDG_DATA_HOME", "~/.local/share")).expanduser() / "tagwerk"


def load_config(path: Path) -> Config:
    if not path.is_file():
        raise SystemExit(f"config file not found: {path}")
    raw = tomllib.loads(path.read_text())
    data_dir = Path(os.environ.get("TAGWERK_DATA_DIR") or raw.get("data_dir") or default_data_dir()).expanduser()
    roots = [(Path(root).expanduser(), kind) for root, kind in raw.get("roots", {}).items()]
    roots.sort(key=lambda root: len(root[0].parts), reverse=True)
    return Config(
        data_dir=data_dir,
        poll_sec=raw.get("poll_sec", 15),
        poll_stale=timedelta(minutes=raw.get("poll_stale_min", 2)),
        beat_lease=timedelta(minutes=raw.get("beat_lease_min", 10)),
        focus_lease=timedelta(minutes=raw.get("focus_lease_min", 1)),
        roots=roots,
        titles=[(re.compile(rule["pattern"]), rule["kind"], rule.get("project")) for rule in raw.get("title", [])],
        kitty_socket=raw.get("kitty_socket", "unix:${XDG_RUNTIME_DIR}/omarchy-kitty-{pid}"),
    )


def resolve_cwd(config: Config, cwd: str | None) -> Bucket | None:
    if cwd is None:
        return None
    path = Path(cwd)
    for root, kind in config.roots:
        if path.is_relative_to(root):
            below = path.relative_to(root).parts
            # ponytail: the project ends at the first dot, so assets.8467 is assets; a dotted repo name needs its own root
            return Bucket(kind, below[0].split(".")[0] if below else "general")
    return None


def resolve_title(config: Config, title: str | None) -> Bucket | None:
    if title is None:
        return None
    for pattern, kind, project in config.titles:
        match = pattern.search(title)
        if match:
            return Bucket(kind, project or match.group("project"))
    return None


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


def attribute(config: Config, events: list[Event], start: datetime, end: datetime) -> dict[Bucket, float]:
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
    stream = [(datetime.fromisoformat(event["ts"]), event) for event in events if event["ev"] != "span"]
    minutes: defaultdict[Bucket, float] = defaultdict(float)
    idle = False
    last_poll: datetime | None = None
    ambient: Bucket | None = None
    leases: dict[Bucket, datetime] = {}
    applied = 0
    minute = start
    # ponytail: O(minutes x spans) scan per report; a month is 43k minutes, fine for years of data
    while minute < end:
        while applied < len(stream) and stream[applied][0] <= minute:
            ts, event = stream[applied]
            applied += 1
            if event["ev"] == "idle":
                idle = True
            elif event["ev"] == "active":
                idle = False
            elif event["ev"] == "focus":
                last_poll = ts
                bucket = resolve_cwd(config, event["cwd"]) or resolve_title(config, event["title"])
                if bucket is not None and bucket.is_repo:
                    leases[bucket] = ts + config.focus_lease
                ambient = bucket if bucket is not None and not bucket.is_repo else None
            elif event["ev"] == "beat":
                bucket = resolve_cwd(config, event["cwd"])
                if bucket is not None and bucket.is_repo:
                    leases[bucket] = ts + config.beat_lease
        leases = {bucket: expiry for bucket, expiry in leases.items() if expiry > minute}
        # ponytail: the latest span covering a minute wins wholesale; no partial merge with sensor minutes
        booked = next((bucket for span_start, span_end, bucket in spans if span_start <= minute < span_end), None)
        if booked is not None:
            if booked.kind != "off":
                minutes[booked] += 1.0
        elif not idle and last_poll is not None and minute - last_poll < config.poll_stale:
            if leases:
                for bucket in leases:
                    minutes[bucket] += 1 / len(leases)
            else:
                minutes[ambient or OTHER] += 1.0
        minute += timedelta(minutes=1)
    return minutes


def local_today() -> date:
    return datetime.now(UTC).astimezone().date()


def local_range(start: date, stop: date) -> tuple[datetime, datetime]:
    return datetime.combine(start, time.min).astimezone(UTC), datetime.combine(stop, time.min).astimezone(UTC)


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


def append_span(config: Config, start: datetime, end: datetime, kind: str, project: str, src: str) -> None:
    span = {
        "ev": "span",
        "start": format_utc(start),
        "end": format_utc(end),
        "kind": kind,
        "project": project,
        "src": src,
    }
    append_event(config, span)


def cmd_fix(config: Config, start: datetime, end: datetime, project: str, kind: str) -> None:
    if end <= start:
        raise SystemExit(
            f"end must be after start: {start.astimezone():%Y-%m-%dT%H:%M} to {end.astimezone():%Y-%m-%dT%H:%M}"
        )
    append_span(config, start, end, kind, project, "fix")


def active_window() -> tuple[str, str, int] | None:
    output = subprocess.run(["hyprctl", "activewindow", "-j"], capture_output=True, text=True, check=True, timeout=2)
    window = json.loads(output.stdout)
    if not window:
        return None
    return window["class"], window["title"], window["pid"]


def active_item(items: list[dict[str, Any]]) -> dict[str, Any] | None:
    return next((item for item in items if item["is_active"]), None)


def parse_kitty_ls(os_windows: list[dict[str, Any]]) -> str | None:
    os_window = active_item(os_windows)
    tab = active_item(os_window["tabs"]) if os_window else None
    window = active_item(tab["windows"]) if tab else None
    if window is None:
        return None
    foreground = window["foreground_processes"]
    cwd: str = foreground[0]["cwd"] if foreground else window["cwd"]
    return cwd


def kitty_cwd(config: Config, pid: int) -> str | None:
    socket = os.path.expandvars(config.kitty_socket).format(pid=pid)
    output = subprocess.run(
        ["kitten", "@", "--to", socket, "ls"], capture_output=True, text=True, check=False, timeout=2
    )
    if output.returncode:
        return None
    return parse_kitty_ls(json.loads(output.stdout))


def cmd_focus(config: Config, once: bool) -> None:
    last: tuple[str, str, str | None] | None = None
    last_write = 0.0
    while True:
        window = active_window()
        if window:
            window_class, title, pid = window
            cwd = kitty_cwd(config, pid) if window_class == "kitty" else None
            current = (window_class, title, cwd)
            # ponytail: poll_sec granularity; Hyprland socket2 events would be finer but cannot see cd
            if current != last or monotonic() - last_write >= REPOLL_SEC:
                append_event(config, {"ev": "focus", "class": window_class, "title": title, "cwd": cwd})
                last, last_write = current, monotonic()
        if once:
            return
        sleep(config.poll_sec)


def report(config: Config, start: datetime, end: datetime) -> None:
    print(render_table(attribute(config, read_events(config, start, end), start, end)))


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
    focus = commands.add_parser("focus", help="poll the focused window and kitty cwd into the ledger")
    focus.add_argument("--once", action="store_true", help="one poll, then exit")
    args = parser.parse_args(argv)
    config = load_config(Path(os.environ.get("TAGWERK_CONFIG") or default_config_path()).expanduser())
    if args.command == "fix":
        cmd_fix(config, args.start, args.end, args.project, args.kind)
    elif args.command == "month":
        report(config, *local_month(args.month or local_today().replace(day=1)))
    elif args.command == "focus":
        cmd_focus(config, args.once)
    else:
        report(config, *local_day(local_today()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
