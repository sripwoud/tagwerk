#!/usr/bin/python3 -I
import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tomllib
import zlib
from collections import defaultdict
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from itertools import groupby
from pathlib import Path
from time import monotonic, sleep
from typing import Any, NamedTuple

Event = dict[str, Any]
VERSION = "master"
REPOLL_SEC = 60
BAR_WIDTH = 24
DAY_SCALE_H = 12
WEEK_SCALE_H = 60
RED = "\033[31m"
RESET = "\033[0m"
BLUE = 33
DIM = 238
GREYS = (240, 245, 250)
# ponytail: five hues pass the dataviz validator on every pair, so a crc32 over them collides past a handful of repos;
# render_bar shades only an adjacent same-hue neighbour; two colliders apart in a bar still match, a legend would fix it
PALETTE = (166, 36, 176, 61, 142)


class Bucket(NamedTuple):
    kind: str
    project: str

    @property
    def is_repo(self) -> bool:
        return self.project not in ("general", "other")


OTHER = Bucket("personal", "other")
PAID = ("work", "fixed")
RULE_KINDS = (*PAID, "personal")
KINDS = (*RULE_KINDS, "off")
TitleRule = tuple[re.Pattern[str], str, str | None]


@dataclass(frozen=True)
class Config:
    data_dir: Path
    poll_stale: timedelta
    beat_lease: timedelta
    beat_throttle: timedelta
    focus_lease: timedelta
    roots: list[tuple[Path, str]]
    titles: list[TitleRule]
    poll_sec: float
    kitty_socket: str
    day_cap_h: float
    week_cap_h: float


def default_config_path() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME", "~/.config")).expanduser() / "tagwerk" / "config.toml"


def default_data_dir() -> Path:
    return Path(os.environ.get("XDG_DATA_HOME", "~/.local/share")).expanduser() / "tagwerk"


CONFIG_TEMPLATE = r"""data_dir = "~/.local/share/tagwerk" # --data-dir and TAGWERK_DATA_DIR override this
poll_sec = 15
poll_stale_min = 2 # a poll this recent proves the machine was on
beat_lease_min = 10 # an agent beat leases its repo this long
beat_throttle_sec = 60 # an agent appends at most one beat per cwd this often
focus_lease_min = 1 # a focused kitty cwd or GitHub repo title leases its repo this long
kitty_socket = "unix:${XDG_RUNTIME_DIR}/omarchy-kitty-{pid}" # Omarchy default; {pid} is the focused kitty's pid
day_cap_h = 8 # week labels turn red above this; caps change colours, never numbers
week_cap_h = 40 # the week footer and month week bars turn red above this

[roots] # longest match wins; the project is the first directory below the root, cut at its first dot
"~/code/work-org" = "work"
"~/memories/work" = "work"
# "~/code/fixed-price-client" = "fixed" # paid, so it counts toward the caps, but never invoiced
"~/code" = "personal"

[[title]] # first match wins; consulted only when the cwd resolves to nothing
pattern = 'work-org/(?P<project>[\w.-]+)'
kind = "work"

[[title]]
pattern = '(?i)slack|work-org|zoom|meet\.google|bitbucket'
kind = "work"
project = "general"
"""


def cmd_init(path: Path) -> None:
    if path.exists():
        raise SystemExit(f"config already exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(CONFIG_TEMPLATE)
    print(f"wrote {path}", file=sys.stderr)


def load_config(path: Path, data_dir: Path | None) -> Config:
    if not path.is_file():
        raise SystemExit(f"config file not found: {path}; run tagwerk init")
    raw = tomllib.loads(path.read_text())
    rules = [*raw.get("roots", {}).items(), *((rule["pattern"], rule["kind"]) for rule in raw.get("title", []))]
    for rule, kind in rules:
        if kind not in RULE_KINDS:
            raise SystemExit(
                f"{path}: {rule}: kind must be one of {', '.join(RULE_KINDS)}; "
                "book off time with tagwerk fix --kind off"
            )
    chosen = data_dir or os.environ.get("TAGWERK_DATA_DIR") or raw.get("data_dir") or default_data_dir()
    roots = [(Path(root).expanduser(), kind) for root, kind in raw.get("roots", {}).items()]
    roots.sort(key=lambda root: len(root[0].parts), reverse=True)
    return Config(
        data_dir=Path(chosen).expanduser(),
        poll_sec=raw.get("poll_sec", 15),
        poll_stale=timedelta(minutes=raw.get("poll_stale_min", 2)),
        beat_lease=timedelta(minutes=raw.get("beat_lease_min", 10)),
        beat_throttle=timedelta(seconds=raw.get("beat_throttle_sec", 60)),
        focus_lease=timedelta(minutes=raw.get("focus_lease_min", 1)),
        roots=roots,
        titles=[(re.compile(rule["pattern"]), rule["kind"], rule.get("project")) for rule in raw.get("title", [])],
        kitty_socket=raw.get("kitty_socket", "unix:${XDG_RUNTIME_DIR}/omarchy-kitty-{pid}"),
        day_cap_h=raw.get("day_cap_h", 8),
        week_cap_h=raw.get("week_cap_h", 40),
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


def attribute(config: Config, events: list[Event], start: datetime, end: datetime) -> dict[date, dict[Bucket, float]]:
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
    days: defaultdict[date, dict[Bucket, float]] = defaultdict(lambda: defaultdict(float))
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
                days[minute.astimezone().date()][booked] += 1.0
        elif not idle and last_poll is not None and minute - last_poll < config.poll_stale:
            credited = days[minute.astimezone().date()]
            if leases:
                for bucket in leases:
                    credited[bucket] += 1 / len(leases)
            else:
                credited[ambient or OTHER] += 1.0
        minute += timedelta(minutes=1)
    return days


def merge(parts: Iterable[dict[Bucket, float]]) -> dict[Bucket, float]:
    total: defaultdict[Bucket, float] = defaultdict(float)
    for part in parts:
        for bucket, credited in part.items():
            total[bucket] += credited
    return total


def local_today() -> date:
    return datetime.now(UTC).astimezone().date()


def local_range(start: date, stop: date) -> tuple[datetime, datetime]:
    return datetime.combine(start, time.min).astimezone(UTC), datetime.combine(stop, time.min).astimezone(UTC)


def local_day(day: date) -> tuple[datetime, datetime]:
    return local_range(day, day + timedelta(days=1))


def local_month(first: date) -> tuple[datetime, datetime]:
    return local_range(first, next_month(first))


def days_between(start: date, stop: date) -> Iterator[date]:
    return (start + timedelta(days=offset) for offset in range((stop - start).days))


def parse_month(text: str) -> date:
    return date.fromisoformat(f"{text}-01")


def parse_local(text: str) -> datetime:
    local = datetime.fromisoformat(text) if "T" in text else datetime.combine(local_today(), time.fromisoformat(text))
    return local.astimezone(UTC)


def format_hours(minutes: float) -> str:
    whole = round(minutes)
    return f"{whole // 60}:{whole % 60:02d}"


def ranked(minutes: dict[Bucket, float]) -> list[tuple[Bucket, float]]:
    return sorted(
        minutes.items(), key=lambda row: (row[0].kind not in PAID, row[0].kind != "work", -row[1], row[0].project)
    )


def paid_minutes(minutes: dict[Bucket, float]) -> float:
    return sum(credited for bucket, credited in minutes.items() if bucket.kind in PAID)


def render_table(minutes: dict[Bucket, float]) -> str:
    cells = [(f"{bucket.kind}/{bucket.project}", format_hours(credited)) for bucket, credited in ranked(minutes)]
    cells.append(("work", format_hours(paid_minutes(minutes))))
    cells.append(("total", format_hours(sum(minutes.values()))))
    name_width = max(len(name) for name, _ in cells)
    hours_width = max(len(hours) for _, hours in cells)
    return "\n".join(f"{name:<{name_width}}  {hours:>{hours_width}}" for name, hours in cells)


def quarter_hours(minutes: dict[str, float]) -> dict[str, float]:
    quarters = {project: int(credited // 15) for project, credited in minutes.items()}
    spare = round(sum(minutes.values()) / 15) - sum(quarters.values())
    for project in sorted(minutes, key=lambda project: (-(minutes[project] % 15), project))[:spare]:
        quarters[project] += 1
    return {project: count / 4 for project, count in quarters.items()}


def render_invoice(minutes: dict[Bucket, float]) -> str:
    hours = quarter_hours({bucket.project: credited for bucket, credited in minutes.items() if bucket.kind == "work"})
    rows = sorted(hours.items(), key=lambda row: (-row[1], row[0]))
    lines = ["| Project | Hours |", "| --- | ---: |"]
    lines += [f"| {project} | {booked:.2f} |" for project, booked in rows]
    lines.append(f"| **Total** | **{sum(hours.values()):.2f}** |")
    return "\n".join(lines)


def colored() -> bool:
    if os.environ.get("FORCE_COLOR"):
        return True
    if os.environ.get("NO_COLOR") or os.environ.get("TERM") == "dumb":
        return False
    return sys.stdout.isatty() and sys.stderr.isatty()


def paint(text: str, code: str) -> str:
    return f"{code}{text}{RESET}" if colored() else text


def ansi(index: int) -> str:
    return f"\033[38;5;{index}m"


def color(bucket: Bucket) -> str:
    digest = zlib.crc32(bucket.project.encode())
    if bucket.kind == "personal":
        return ansi(GREYS[digest % len(GREYS)])
    if bucket.is_repo:
        return ansi(PALETTE[digest % len(PALETTE)])
    return ansi(BLUE)


def render_bar(minutes: dict[Bucket, float], scale_h: float, cap_h: float) -> str:
    cells: list[tuple[str, str]] = []
    for bucket, credited in ranked(minutes):
        code = color(bucket)
        char = "▓" if cells and cells[-1] == ("█", code) else "█"
        cells += [(char, code)] * round(credited / (scale_h * 60) * BAR_WIDTH)
    cells = cells[:BAR_WIDTH]
    cells += [("·", ansi(DIM))] * (BAR_WIDTH - len(cells))
    cells.insert(round(cap_h / scale_h * BAR_WIDTH), ("│", ansi(DIM)))
    return "".join(paint(char * len(list(run)), code) for (char, code), run in groupby(cells))


def bar_line(label: str, minutes: dict[Bucket, float], scale_h: float, cap_h: float, weekend: bool = False) -> str:
    total = sum(minutes.values())
    over_cap = total > cap_h * 60 or (weekend and total > 0)
    return (
        f"{paint(label, RED) if over_cap else label}  {render_bar(minutes, scale_h, cap_h)}  {format_hours(total):>5}"
    )


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


def cmd_import_timew(config: Config, work_tag: str, export: Path | None) -> None:
    if any(
        event["ev"] == "span" and event["src"] == "timew"
        for path in config.data_dir.glob("*.jsonl")
        for event in read_ledger_file(path)
    ):
        raise SystemExit(f"{config.data_dir} already holds timew spans; import-timew runs once")
    try:
        text = export.read_text() if export else subprocess.check_output(["timew", "export"], text=True)
        intervals: list[dict[str, Any]] = json.loads(text)
    except (OSError, subprocess.CalledProcessError, json.JSONDecodeError) as err:
        raise SystemExit(f"cannot read timew export: {err}") from err
    closed = [interval for interval in intervals if "end" in interval]
    for interval in closed:
        tags = interval.get("tags", [])
        project = next((tag.removeprefix("project:") for tag in tags if tag.startswith("project:")), "general")
        kind = "work" if work_tag in tags else "personal"
        start, end = datetime.fromisoformat(interval["start"]), datetime.fromisoformat(interval["end"])
        append_span(config, start, end, kind, project, "timew")
    print(len(closed))


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


def cmd_beat(config: Config, src: str, cwd: str | None) -> None:
    if cwd is None and not sys.stdin.isatty():
        payload = sys.stdin.read()
        cwd = json.loads(payload).get("cwd") if payload.strip() else None
    cwd = cwd or os.getcwd()
    if resolve_cwd(config, cwd) is None:
        return
    stamp = Path(os.environ["XDG_RUNTIME_DIR"]) / "tagwerk" / f"{src}-{hashlib.sha1(cwd.encode()).hexdigest()[:12]}"
    if stamp.exists() and datetime.now(UTC) - datetime.fromtimestamp(stamp.stat().st_mtime, UTC) < config.beat_throttle:
        return
    append_event(config, {"ev": "beat", "src": src, "cwd": cwd})
    stamp.parent.mkdir(parents=True, exist_ok=True)
    stamp.touch()


def credited_days(config: Config, start: datetime, end: datetime) -> dict[date, dict[Bucket, float]]:
    return attribute(config, read_events(config, start, end), start, end)


def report(config: Config, start: datetime, end: datetime, render: Callable[[dict[Bucket, float]], str]) -> None:
    print(render(merge(credited_days(config, start, end).values())))


def cmd_week(config: Config, weeks_back: int) -> None:
    today = local_today()
    monday = today - timedelta(days=today.weekday(), weeks=weeks_back)
    days = credited_days(config, *local_range(monday, monday + timedelta(days=7)))
    out = [
        bar_line(f"{day:%a %d}", days.get(day, {}), DAY_SCALE_H, config.day_cap_h, weekend=day.weekday() >= 5)
        for day in days_between(monday, monday + timedelta(days=7))
    ]
    paid = paid_minutes(merge(days.values()))
    footer = f"work {format_hours(paid)} / {format_hours(config.week_cap_h * 60)}"
    out.append(paint(footer, RED) if paid > config.week_cap_h * 60 else footer)
    print("\n".join(out))


def cmd_month(config: Config, first: date) -> None:
    days = credited_days(config, *local_month(first))
    weeks: defaultdict[tuple[int, int], list[dict[Bucket, float]]] = defaultdict(list)
    for day in days_between(first, next_month(first)):
        weeks[(day.isocalendar().year, day.isocalendar().week)].append(days.get(day, {}))
    bars = [
        bar_line(f"W{week:02d}", merge(parts), WEEK_SCALE_H, config.week_cap_h) for (_, week), parts in weeks.items()
    ]
    print("\n".join([*bars, "", render_table(merge(days.values()))]))


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="tagwerk", description="Passive work-hours ledger for one Linux desktop.")
    parser.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    parser.add_argument("--config", type=Path, metavar="PATH", help="config file; overrides TAGWERK_CONFIG")
    parser.add_argument("--data-dir", type=Path, metavar="PATH", help="ledger directory; overrides TAGWERK_DATA_DIR")
    parser.set_defaults(no_color=False)
    plain = argparse.ArgumentParser(add_help=False)
    plain.add_argument("--no-color", action="store_true", help="disable colour even on a terminal")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init", help="write the commented config template; refuses to overwrite an existing one")
    fix = commands.add_parser("fix", help="book a span by hand; it overrides the sensors for its range")
    fix.add_argument("start", type=parse_local, help="HH:MM today or YYYY-MM-DDTHH:MM, local time")
    fix.add_argument("end", type=parse_local, help="HH:MM today or YYYY-MM-DDTHH:MM, local time")
    fix.add_argument("project")
    fix.add_argument(
        "--kind",
        choices=KINDS,
        default="work",
        help="work is paid and invoiced, fixed is paid and charted only, personal is charted only, "
        "off removes the range from every report",
    )
    commands.add_parser("today", parents=[plain], help="hours per kind/project for the local day")
    week = commands.add_parser(
        "week", parents=[plain], help="one bar per day, Monday to Sunday, with the day and week caps"
    )
    week.add_argument("-n", type=int, default=0, metavar="N", help="weeks back, default 0")
    month = commands.add_parser(
        "month",
        parents=[plain],
        help="one bar per ISO week, counting only its days inside the month, then hours per kind/project",
    )
    month.add_argument("month", nargs="?", type=parse_month, default=None, help="YYYY-MM, default the current month")
    invoice = commands.add_parser(
        "invoice", parents=[plain], help="markdown table of work hours per project in quarter hours"
    )
    invoice.add_argument("month", type=parse_month, help="YYYY-MM")
    focus = commands.add_parser("focus", help="poll the focused window and kitty cwd into the ledger")
    focus.add_argument("--once", action="store_true", help="one poll, then exit")
    import_timew = commands.add_parser("import-timew", help="one-shot import of the timewarrior export as spans")
    import_timew.add_argument("--work-tag", required=True, metavar="TAG", help="tag that marks an interval as work")
    import_timew.add_argument("file", nargs="?", type=Path, help="timew export JSON; runs timew export when omitted")
    beat = commands.add_parser("beat", help="record an agent signal for its repo, one per source and cwd per throttle")
    beat.add_argument("src", help="the agent that fired the hook, such as claude or pi")
    beat.add_argument(
        "--cwd", help="the agent's working directory; default the cwd field of JSON on stdin, else the process cwd"
    )
    commands.add_parser("idle", help="mark the start of idle, from the hypridle listener or before sleep")
    commands.add_parser("active", help="mark the end of idle, from the hypridle listener or after sleep")
    args = parser.parse_args(argv)
    if args.no_color:
        os.environ["NO_COLOR"] = "1"
    config_path = (args.config or Path(os.environ.get("TAGWERK_CONFIG") or default_config_path())).expanduser()
    if args.command == "init":
        if args.data_dir:
            parser.error("--data-dir does not apply to init; set data_dir in the config it writes")
        cmd_init(config_path)
        return 0
    config = load_config(config_path, args.data_dir)
    if args.command == "fix":
        cmd_fix(config, args.start, args.end, args.project, args.kind)
    elif args.command == "beat":
        cmd_beat(config, args.src, args.cwd)
    elif args.command in ("idle", "active"):
        append_event(config, {"ev": args.command})
    elif args.command == "week":
        cmd_week(config, args.n)
    elif args.command == "month":
        cmd_month(config, args.month or local_today().replace(day=1))
    elif args.command == "invoice":
        report(config, *local_month(args.month), render_invoice)
    elif args.command == "focus":
        cmd_focus(config, args.once)
    elif args.command == "import-timew":
        cmd_import_timew(config, args.work_tag, args.file)
    else:
        report(config, *local_day(local_today()), render_table)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
