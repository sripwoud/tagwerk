import configparser
import io
import json
import os
import re
import subprocess
import time
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

import tagwerk

SCRIPT = Path(tagwerk.__file__)
CONTRIB = SCRIPT.parent / "contrib"
T0 = datetime(2026, 8, 5, 10, 0, tzinfo=UTC)
MINUTE = timedelta(minutes=1)
Event = dict[str, Any]


@pytest.fixture
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    data_dir = tmp_path / "data"
    config = tmp_path / "config.toml"
    config.write_text(f'data_dir = "{data_dir}"\npoll_sec = 0\n')
    monkeypatch.setenv("TAGWERK_CONFIG", str(config))
    monkeypatch.delenv("TAGWERK_DATA_DIR", raising=False)
    return data_dir


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("HOME", str(tmp_path))
    for name in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "TAGWERK_CONFIG", "TAGWERK_DATA_DIR"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture
def ledger(home: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("TAGWERK_CONFIG", str(SCRIPT.with_name("config.example.toml")))
    return home / ".local/share/tagwerk"


@pytest.fixture
def runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    runtime = tmp_path / "run"
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(runtime))
    return runtime


@pytest.fixture
def berlin() -> Iterator[None]:
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("TZ", "Europe/Berlin")
        time.tzset()
        yield
    time.tzset()


def run(capsys: pytest.CaptureFixture[str], *argv: str) -> list[list[str]]:
    assert tagwerk.main(list(argv)) == 0
    return [line.split() for line in capsys.readouterr().out.splitlines()]


def stamp(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def polls(
    start: datetime, count: int, window_class: str = "vivaldi", title: str = "New Tab - Vivaldi", cwd: str | None = None
) -> list[Event]:
    return [
        {"ts": stamp(start + i * MINUTE), "ev": "focus", "class": window_class, "title": title, "cwd": cwd}
        for i in range(count)
    ]


def mark(moment: datetime, ev: str) -> Event:
    return {"ts": stamp(moment), "ev": ev}


def beat(moment: datetime, cwd: str) -> Event:
    return {"ts": stamp(moment), "ev": "beat", "src": "claude", "cwd": cwd}


def present(start: datetime, count: int, **window: Any) -> list[Event]:
    return [mark(start, "active"), *polls(start, count, **window), mark(start + count * MINUTE, "idle")]


def span(start: datetime, end: datetime, project: str, kind: str = "work") -> Event:
    return {"ts": stamp(end), "ev": "span", "start": stamp(start), "end": stamp(end), "kind": kind, "project": project}


def seed(ledger: Path, *events: Event) -> None:
    ledger.mkdir(parents=True, exist_ok=True)
    for event in events:
        filed = event["start"] if event["ev"] == "span" else event["ts"]
        with (ledger / f"{filed[:7]}.jsonl").open("a") as month:
            month.write(json.dumps(event) + "\n")


def lines(ledger: Path) -> list[Event]:
    return [json.loads(line) for path in sorted(ledger.glob("*.jsonl")) for line in path.read_text().splitlines()]


@pytest.mark.parametrize(
    "command", [[], ["fix"], ["today"], ["month"], ["focus"], ["import-timew"], ["beat"], ["idle"], ["active"]]
)
def test_help_exits_zero_and_prints_usage(capsys: pytest.CaptureFixture[str], command: list[str]) -> None:
    with pytest.raises(SystemExit) as raised:
        tagwerk.main([*command, "--help"])
    assert raised.value.code == 0
    assert capsys.readouterr().out.startswith(f"usage: tagwerk {' '.join(command)}".rstrip())


def test_script_runs_through_shebang() -> None:
    result = subprocess.run([str(SCRIPT), "--help"], capture_output=True, text=True, check=False)
    assert result.returncode == 0
    assert result.stdout.startswith("usage: tagwerk")


def test_missing_config_exits_with_its_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    missing = tmp_path / "nope.toml"
    monkeypatch.setenv("TAGWERK_CONFIG", str(missing))
    with pytest.raises(SystemExit, match=re.escape(str(missing))):
        tagwerk.main(["today"])


def test_fix_then_today_shows_booked_hours(data_dir: Path, capsys: pytest.CaptureFixture[str]) -> None:
    run(capsys, "fix", "09:00", "10:30", "assets")
    assert run(capsys, "today") == [["assets", "1:30"], ["work", "1:30"], ["total", "1:30"]]


def test_fix_appends_one_utc_span_line_to_the_utc_month_file(
    data_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    run(capsys, "fix", "09:00", "10:30", "assets")
    [ledger] = data_dir.glob("*.jsonl")
    [line] = ledger.read_text().splitlines()
    span = json.loads(line)
    assert {key: span[key] for key in ("ev", "kind", "project", "src")} == {
        "ev": "span",
        "kind": "work",
        "project": "assets",
        "src": "fix",
    }
    assert all(span[key].endswith("Z") for key in ("ts", "start", "end"))
    start, end = datetime.fromisoformat(span["start"]), datetime.fromisoformat(span["end"])
    assert end - start == timedelta(minutes=90)
    assert start.astimezone().strftime("%H:%M") == "09:00"
    assert ledger.name == f"{start:%Y-%m}.jsonl"


def test_fix_for_a_past_month_is_filed_under_that_month(data_dir: Path, capsys: pytest.CaptureFixture[str]) -> None:
    past = datetime.now(UTC).astimezone().date() - timedelta(days=40)
    run(capsys, "fix", f"{past}T12:00", f"{past}T13:00", "assets")
    [ledger] = data_dir.glob("*.jsonl")
    assert ledger.name == f"{past:%Y-%m}.jsonl"


def test_latest_appended_span_wins_on_overlap(data_dir: Path, capsys: pytest.CaptureFixture[str]) -> None:
    run(capsys, "fix", "09:00", "10:00", "assets")
    run(capsys, "fix", "09:30", "10:00", "auberge", "--personal")
    assert run(capsys, "today") == [["assets", "0:30"], ["auberge", "0:30"], ["work", "0:30"], ["total", "1:00"]]


def test_personal_rows_follow_work_rows_regardless_of_minutes(
    data_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    run(capsys, "fix", "09:00", "09:30", "assets")
    run(capsys, "fix", "10:00", "12:00", "auberge", "--personal")
    assert run(capsys, "today") == [["assets", "0:30"], ["auberge", "2:00"], ["work", "0:30"], ["total", "2:30"]]


def test_off_span_removes_booked_minutes(data_dir: Path, capsys: pytest.CaptureFixture[str]) -> None:
    run(capsys, "fix", "09:00", "10:00", "assets")
    run(capsys, "fix", "09:30", "10:00", "lunch", "--off")
    assert run(capsys, "today") == [["assets", "0:30"], ["work", "0:30"], ["total", "0:30"]]


def test_fix_accepts_an_explicit_local_date(data_dir: Path, capsys: pytest.CaptureFixture[str]) -> None:
    today = datetime.now(UTC).astimezone().date()
    yesterday = today - timedelta(days=1)
    run(capsys, "fix", f"{today}T09:00", f"{today}T09:45", "assets")
    run(capsys, "fix", f"{yesterday}T09:00", f"{yesterday}T12:00", "assets")
    assert run(capsys, "today") == [["assets", "0:45"], ["work", "0:45"], ["total", "0:45"]]


@pytest.mark.parametrize(("start", "end"), [("15:00", "14:00"), ("14:00", "14:00")])
def test_fix_rejects_end_at_or_before_start_and_appends_nothing(data_dir: Path, start: str, end: str) -> None:
    with pytest.raises(SystemExit) as raised:
        tagwerk.main(["fix", start, end, "x"])
    assert raised.value.code
    assert "start" in str(raised.value)
    assert not data_dir.exists()


def test_today_fails_on_a_malformed_line_naming_file_and_line(
    data_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    run(capsys, "fix", "09:00", "10:00", "assets")
    [ledger] = data_dir.glob("*.jsonl")
    with ledger.open("a") as broken:
        broken.write("{not json\n")
    with pytest.raises(SystemExit, match=re.escape(f"{ledger}:2")):
        tagwerk.main(["today"])


def test_data_dir_env_overrides_config(
    data_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    elsewhere = tmp_path / "elsewhere"
    monkeypatch.setenv("TAGWERK_DATA_DIR", str(elsewhere))
    run(capsys, "fix", "09:00", "10:00", "assets")
    assert not data_dir.exists()
    assert len(list(elsewhere.glob("*.jsonl"))) == 1


def test_data_dir_defaults_under_home(
    home: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    (home / "config.toml").write_text("")
    monkeypatch.setenv("TAGWERK_CONFIG", str(home / "config.toml"))
    run(capsys, "fix", "09:00", "10:00", "assets")
    assert len(list((home / ".local/share/tagwerk").glob("*.jsonl"))) == 1


def test_data_dir_defaults_to_xdg_data_home(
    home: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    (home / "config.toml").write_text("")
    monkeypatch.setenv("TAGWERK_CONFIG", str(home / "config.toml"))
    monkeypatch.setenv("XDG_DATA_HOME", str(home / "xdg"))
    run(capsys, "fix", "09:00", "10:00", "assets")
    assert len(list((home / "xdg/tagwerk").glob("*.jsonl"))) == 1


def test_config_defaults_under_home(home: Path) -> None:
    with pytest.raises(SystemExit, match=re.escape(str(home / ".config/tagwerk/config.toml"))):
        tagwerk.main(["today"])


def test_config_defaults_to_xdg_config_home(
    home: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    config = home / "xdg/tagwerk/config.toml"
    config.parent.mkdir(parents=True)
    config.write_text(f'data_dir = "{home}/data"\n')
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / "xdg"))
    run(capsys, "fix", "09:00", "10:00", "assets")
    assert run(capsys, "today") == [["assets", "1:00"], ["work", "1:00"], ["total", "1:00"]]


def test_example_config_books_into_the_expanded_home(
    home: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("TAGWERK_CONFIG", str(SCRIPT.with_name("config.example.toml")))
    run(capsys, "fix", "09:00", "10:00", "assets")
    assert run(capsys, "today") == [["assets", "1:00"], ["work", "1:00"], ["total", "1:00"]]
    assert len(list((home / ".local/share/tagwerk").glob("*.jsonl"))) == 1


def test_month_with_an_empty_ledger_prints_zero_totals(ledger: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert run(capsys, "month", "2026-08") == [["work", "0:00"], ["total", "0:00"]]


def test_month_without_an_argument_uses_the_current_month(data_dir: Path, capsys: pytest.CaptureFixture[str]) -> None:
    run(capsys, "fix", "09:00", "10:00", "assets")
    assert run(capsys, "month") == [["assets", "1:00"], ["work", "1:00"], ["total", "1:00"]]


def test_month_credits_a_span_filed_in_that_month(ledger: Path, capsys: pytest.CaptureFixture[str]) -> None:
    seed(ledger, span(T0, T0 + 90 * MINUTE, "assets"))
    assert run(capsys, "month", "2026-08") == [["assets", "1:30"], ["work", "1:30"], ["total", "1:30"]]
    assert run(capsys, "month", "2026-07") == [["work", "0:00"], ["total", "0:00"]]


def test_month_reads_the_previous_file_for_a_span_crossing_into_its_first_local_minutes(
    ledger: Path, berlin: None, capsys: pytest.CaptureFixture[str]
) -> None:
    last_of_july_utc = datetime(2026, 7, 31, 21, 30, tzinfo=UTC)
    seed(ledger, span(last_of_july_utc, last_of_july_utc + 60 * MINUTE, "assets"))
    assert [path.name for path in ledger.glob("*.jsonl")] == ["2026-07.jsonl"]
    assert run(capsys, "month", "2026-08") == [["assets", "0:30"], ["work", "0:30"], ["total", "0:30"]]
    assert run(capsys, "month", "2026-07") == [["assets", "0:30"], ["work", "0:30"], ["total", "0:30"]]


def test_present_minutes_with_no_signal_land_on_personal_other(
    ledger: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    seed(ledger, *present(T0, 10))
    assert run(capsys, "month", "2026-08") == [["other", "0:10"], ["work", "0:00"], ["total", "0:10"]]


def test_idle_stops_credit_and_active_resumes_it(ledger: Path, capsys: pytest.CaptureFixture[str]) -> None:
    seed(ledger, *present(T0, 15), mark(T0 + 5 * MINUTE, "idle"), mark(T0 + 8 * MINUTE, "active"))
    assert run(capsys, "month", "2026-08") == [["other", "0:12"], ["work", "0:00"], ["total", "0:12"]]


def test_minutes_without_a_recent_poll_are_absent(ledger: Path, capsys: pytest.CaptureFixture[str]) -> None:
    seed(ledger, *polls(T0, 1), mark(T0 + 10 * MINUTE, "idle"))
    assert run(capsys, "month", "2026-08") == [["other", "0:02"], ["work", "0:00"], ["total", "0:02"]]


def test_month_credits_exactly_its_first_and_last_local_minute(
    ledger: Path, berlin: None, capsys: pytest.CaptureFixture[str]
) -> None:
    before_october = datetime(2026, 9, 30, 21, 59, tzinfo=UTC)
    before_november = datetime(2026, 10, 31, 22, 59, tzinfo=UTC)
    seed(ledger, *present(before_october, 2), *present(before_november, 2))
    assert run(capsys, "month", "2026-09")[-1] == ["total", "0:01"]
    assert run(capsys, "month", "2026-10")[-1] == ["total", "0:02"]
    assert run(capsys, "month", "2026-11")[-1] == ["total", "0:01"]


def test_the_repeated_dst_hour_credits_every_present_minute_once(
    ledger: Path, berlin: None, capsys: pytest.CaptureFixture[str]
) -> None:
    seed(ledger, *present(datetime(2026, 10, 25, 0, 30, tzinfo=UTC), 61))
    assert run(capsys, "month", "2026-10") == [["other", "1:01"], ["work", "0:00"], ["total", "1:01"]]


def test_one_beat_in_a_work_repo_takes_every_present_minute(
    home: Path, ledger: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    seed(ledger, *present(T0, 10), beat(T0, f"{home}/code/work-org/assets"))
    assert run(capsys, "month", "2026-08") == [["assets", "0:10"], ["work", "0:10"], ["total", "0:10"]]


def test_beats_in_two_repos_split_each_minute_evenly(
    home: Path, ledger: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    seed(ledger, *present(T0, 10), beat(T0, f"{home}/code/work-org/assets"), beat(T0, f"{home}/code/work-org/checkout"))
    assert run(capsys, "month", "2026-08") == [
        ["assets", "0:05"],
        ["checkout", "0:05"],
        ["work", "0:10"],
        ["total", "0:10"],
    ]


def test_a_beat_lease_expires_after_beat_lease_min(
    home: Path, ledger: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    seed(ledger, *present(T0, 15), beat(T0, f"{home}/code/work-org/assets"))
    assert run(capsys, "month", "2026-08") == [
        ["assets", "0:10"],
        ["other", "0:05"],
        ["work", "0:10"],
        ["total", "0:15"],
    ]


def test_active_resumes_credit_with_a_still_valid_lease(
    home: Path, ledger: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    seed(
        ledger,
        *present(T0, 15),
        beat(T0, f"{home}/code/work-org/assets"),
        mark(T0 + 5 * MINUTE, "idle"),
        mark(T0 + 8 * MINUTE, "active"),
    )
    assert run(capsys, "month", "2026-08") == [
        ["assets", "0:07"],
        ["other", "0:05"],
        ["work", "0:07"],
        ["total", "0:12"],
    ]


def test_beats_while_idle_book_nothing_but_renew_the_lease(
    home: Path, ledger: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = f"{home}/code/work-org/assets"
    seed(
        ledger,
        *present(T0, 15),
        beat(T0, repo),
        mark(T0 + 5 * MINUTE, "idle"),
        beat(T0 + 6 * MINUTE, repo),
        mark(T0 + 8 * MINUTE, "active"),
    )
    assert run(capsys, "month", "2026-08") == [["assets", "0:12"], ["work", "0:12"], ["total", "0:12"]]


def test_a_beat_outside_every_root_leases_nothing(home: Path, ledger: Path, capsys: pytest.CaptureFixture[str]) -> None:
    seed(ledger, *present(T0, 5), beat(T0, f"{home}/Downloads"))
    assert run(capsys, "month", "2026-08") == [["other", "0:05"], ["work", "0:00"], ["total", "0:05"]]


def test_a_focused_kitty_cwd_leases_its_repo_for_focus_lease_min(
    home: Path, ledger: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    seed(
        ledger,
        mark(T0, "active"),
        *polls(T0, 5, window_class="kitty", title="~/code/work-org/assets", cwd=f"{home}/code/work-org/assets"),
        *polls(T0 + 5 * MINUTE, 5),
        mark(T0 + 10 * MINUTE, "idle"),
    )
    assert run(capsys, "month", "2026-08") == [
        ["assets", "0:05"],
        ["other", "0:05"],
        ["work", "0:05"],
        ["total", "0:10"],
    ]


def test_the_ambient_bucket_is_credited_only_while_no_repo_holds_a_lease(
    home: Path, ledger: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    seed(ledger, *present(T0, 15, title="#next-lending - work-org - Slack"), beat(T0, f"{home}/code/work-org/assets"))
    assert run(capsys, "month", "2026-08") == [
        ["assets", "0:10"],
        ["general", "0:05"],
        ["work", "0:15"],
        ["total", "0:15"],
    ]


@pytest.mark.parametrize(
    ("cwd", "title", "kind", "project"),
    [
        ("code/work-org/assets.8467/src", "~/code/work-org/assets.8467/src", "work", "assets"),
        ("code/work-org/tools", "~/code/work-org/tools", "work", "tools"),
        ("code/work-org", "~/code/work-org", "work", "general"),
        ("code/auberge/site", "~/code/auberge/site", "personal", "auberge"),
        ("code", "~/code", "personal", "general"),
        ("Downloads", "~/Downloads", "personal", "other"),
        (None, "work-org/assets: Fix rounding · GitHub", "work", "assets"),
        (None, "#next-lending - work-org - Slack", "work", "general"),
        (None, "New Tab - Vivaldi", "personal", "other"),
    ],
)
def test_a_poll_resolves_its_cwd_by_the_longest_root_then_its_title_by_the_first_rule(
    home: Path, ledger: Path, capsys: pytest.CaptureFixture[str], cwd: str | None, title: str, kind: str, project: str
) -> None:
    seed(
        ledger,
        *present(T0, 5, window_class="kitty" if cwd else "vivaldi", title=title, cwd=f"{home}/{cwd}" if cwd else None),
    )
    work = "0:05" if kind == "work" else "0:00"
    assert run(capsys, "month", "2026-08") == [[project, "0:05"], ["work", work], ["total", "0:05"]]


def test_a_lease_taken_before_the_range_is_held_at_its_start(
    home: Path, ledger: Path, berlin: None, capsys: pytest.CaptureFixture[str]
) -> None:
    august = datetime(2026, 7, 31, 22, 0, tzinfo=UTC)
    seed(ledger, beat(august - 5 * MINUTE, f"{home}/code/work-org/assets"), *present(august, 5))
    assert [path.name for path in ledger.glob("*.jsonl")] == ["2026-07.jsonl"]
    assert run(capsys, "month", "2026-08") == [["assets", "0:05"], ["work", "0:05"], ["total", "0:05"]]


def test_a_span_overrides_beats_wholesale(home: Path, ledger: Path, capsys: pytest.CaptureFixture[str]) -> None:
    seed(
        ledger,
        *present(T0, 10),
        beat(T0, f"{home}/code/work-org/assets"),
        span(T0 + 5 * MINUTE, T0 + 10 * MINUTE, "meeting"),
    )
    assert run(capsys, "month", "2026-08") == [
        ["assets", "0:05"],
        ["meeting", "0:05"],
        ["work", "0:10"],
        ["total", "0:10"],
    ]


def test_an_off_span_removes_present_minutes(home: Path, ledger: Path, capsys: pytest.CaptureFixture[str]) -> None:
    seed(
        ledger,
        *present(T0, 10),
        beat(T0, f"{home}/code/work-org/assets"),
        span(T0 + 5 * MINUTE, T0 + 10 * MINUTE, "lunch", "off"),
    )
    assert run(capsys, "month", "2026-08") == [["assets", "0:05"], ["work", "0:05"], ["total", "0:05"]]


def test_lease_and_staleness_keys_are_read_from_the_config(
    home: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    (home / "config.toml").write_text(
        f'data_dir = "{home}/data"\npoll_stale_min = 5\nbeat_lease_min = 3\nfocus_lease_min = 2\n'
        f'[roots]\n"{home}/code/work-org" = "work"\n'
    )
    monkeypatch.setenv("TAGWERK_CONFIG", str(home / "config.toml"))
    kitty = polls(T0, 1, window_class="kitty", title="~/code/work-org/checkout", cwd=f"{home}/code/work-org/checkout")
    seed(home / "data", *kitty, beat(T0, f"{home}/code/work-org/assets"))
    assert run(capsys, "month", "2026-08") == [
        ["assets", "0:02"],
        ["checkout", "0:01"],
        ["other", "0:02"],
        ["work", "0:03"],
        ["total", "0:05"],
    ]


@pytest.fixture
def fake_bin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "run"))
    return bin_dir


def fake(bin_dir: Path, name: str, *replies: str) -> Path:
    queue = bin_dir / f"{name}.queue"
    queue.write_text("".join(f"{reply}\n" for reply in replies))
    script = bin_dir / name
    script.write_text(
        "#!/bin/sh\n"
        f'echo "$@" >> "{bin_dir / name}.args"\n'
        f'read -r reply < "{queue}" || exit 1\n'
        f'sed -i 1d "{queue}"\n'
        'printf "%s\\n" "$reply"\n'
    )
    script.chmod(0o755)
    return bin_dir / f"{name}.args"


def hypr_window(window_class: str = "kitty", title: str = "✳ Claude Code", pid: int = 4242) -> str:
    return json.dumps({"class": window_class, "title": title, "pid": pid, "address": "0x1"})


def kitty_window(cwd: str, *foreground: str, active: bool = True) -> dict[str, Any]:
    return {
        "is_active": active,
        "is_focused": False,
        "cwd": cwd,
        "foreground_processes": [{"cwd": path, "cmdline": ["claude"], "pid": 7} for path in foreground],
    }


def kitty_ls() -> list[dict[str, Any]]:
    return [
        {
            "is_active": True,
            "is_focused": False,
            "tabs": [
                {"is_active": False, "is_focused": False, "windows": [kitty_window("/home/s/code/other")]},
                {
                    "is_active": True,
                    "is_focused": False,
                    "windows": [
                        kitty_window("/home/s", active=False),
                        kitty_window("/home/s/code/assets.1", "/home/s/code/assets.1/src"),
                    ],
                },
            ],
        }
    ]


def ledger_lines(data_dir: Path) -> list[dict[str, Any]]:
    [ledger] = data_dir.glob("*.jsonl")
    return [json.loads(line) for line in ledger.read_text().splitlines()]


def test_focus_once_writes_the_active_kitty_windows_foreground_cwd(
    data_dir: Path, fake_bin: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    fake(fake_bin, "hyprctl", hypr_window())
    kitten_args = fake(fake_bin, "kitten", json.dumps(kitty_ls()))
    run(capsys, "focus", "--once")
    [poll] = ledger_lines(data_dir)
    assert {key: poll[key] for key in ("ev", "class", "title", "cwd")} == {
        "ev": "focus",
        "class": "kitty",
        "title": "✳ Claude Code",
        "cwd": "/home/s/code/assets.1/src",
    }
    assert poll["ts"].endswith("Z")
    assert kitten_args.read_text() == f"@ --to unix:{tmp_path}/run/omarchy-kitty-4242 ls\n"


def test_focus_at_prompt_uses_the_window_cwd(
    data_dir: Path, fake_bin: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    blob = kitty_ls()
    blob[0]["tabs"][1]["windows"][1]["foreground_processes"] = []
    fake(fake_bin, "hyprctl", hypr_window())
    fake(fake_bin, "kitten", json.dumps(blob))
    run(capsys, "focus", "--once")
    [poll] = ledger_lines(data_dir)
    assert poll["cwd"] == "/home/s/code/assets.1"


@pytest.mark.parametrize("level", ["os_window", "tab", "window"])
def test_focus_with_nothing_active_at_some_level_writes_null_cwd(
    data_dir: Path, fake_bin: Path, capsys: pytest.CaptureFixture[str], level: str
) -> None:
    blob = kitty_ls()
    os_window = blob[0]
    tab = os_window["tabs"][1]
    window = tab["windows"][1]
    {"os_window": os_window, "tab": tab, "window": window}[level]["is_active"] = False
    fake(fake_bin, "hyprctl", hypr_window())
    fake(fake_bin, "kitten", json.dumps(blob))
    run(capsys, "focus", "--once")
    [poll] = ledger_lines(data_dir)
    assert poll["class"] == "kitty"
    assert poll["cwd"] is None


def test_focus_kitten_failure_leaves_cwd_unknown(
    data_dir: Path, fake_bin: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    fake(fake_bin, "hyprctl", hypr_window())
    fake(fake_bin, "kitten")
    run(capsys, "focus", "--once")
    [poll] = ledger_lines(data_dir)
    assert poll["class"] == "kitty"
    assert poll["cwd"] is None


def test_focus_raises_on_malformed_kitten_json(data_dir: Path, fake_bin: Path) -> None:
    fake(fake_bin, "hyprctl", hypr_window())
    fake(fake_bin, "kitten", "not json")
    with pytest.raises(json.JSONDecodeError):
        tagwerk.main(["focus", "--once"])


def test_focus_skips_kitten_for_other_classes(
    data_dir: Path, fake_bin: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    fake(fake_bin, "hyprctl", hypr_window("vivaldi-stable", "Fix · Issue #1 · org/assets - Vivaldi"))
    kitten_args = fake(fake_bin, "kitten", json.dumps(kitty_ls()))
    run(capsys, "focus", "--once")
    [poll] = ledger_lines(data_dir)
    assert poll["class"] == "vivaldi-stable"
    assert poll["cwd"] is None
    assert not kitten_args.exists()


def test_focus_writes_nothing_when_nothing_is_focused(
    data_dir: Path, fake_bin: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    fake(fake_bin, "hyprctl", "{}")
    run(capsys, "focus", "--once")
    assert not data_dir.exists()


def test_focus_raises_when_hyprctl_fails(data_dir: Path, fake_bin: Path) -> None:
    fake(fake_bin, "hyprctl")
    with pytest.raises(subprocess.CalledProcessError):
        tagwerk.main(["focus", "--once"])
    assert not data_dir.exists()


def test_focus_loop_writes_a_poll_only_when_the_window_changes(data_dir: Path, fake_bin: Path) -> None:
    fake(fake_bin, "hyprctl", hypr_window("slack", "a"), hypr_window("slack", "a"), hypr_window("slack", "b"))
    with pytest.raises(subprocess.CalledProcessError):
        tagwerk.main(["focus"])
    assert [poll["title"] for poll in ledger_lines(data_dir)] == ["a", "b"]


def test_focus_loop_repolls_an_unchanged_window_once_the_repoll_interval_passed(
    data_dir: Path, fake_bin: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(tagwerk, "REPOLL_SEC", 0)
    fake(fake_bin, "hyprctl", hypr_window("slack", "a"), hypr_window("slack", "a"))
    with pytest.raises(subprocess.CalledProcessError):
        tagwerk.main(["focus"])
    assert [poll["title"] for poll in ledger_lines(data_dir)] == ["a", "a"]


@pytest.mark.skipif("KITTY_PID" not in os.environ, reason="needs a running kitty")
def test_focus_live_reads_the_real_kittys_cwd(
    data_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("PATH", f"{tmp_path}:{os.environ['PATH']}")
    fake(tmp_path, "hyprctl", hypr_window(pid=int(os.environ["KITTY_PID"])))
    run(capsys, "focus", "--once")
    [poll] = ledger_lines(data_dir)
    assert Path(poll["cwd"]).is_dir()


def test_focus_unit_restarts_on_failure_inside_the_graphical_session() -> None:
    unit = configparser.ConfigParser(interpolation=None)
    unit.read(SCRIPT.with_name("contrib") / "tagwerk-focus.service")
    assert unit["Service"]["ExecStart"] == "%h/.local/bin/tagwerk focus"
    assert unit["Service"]["Restart"] == "on-failure"
    assert unit["Unit"]["PartOf"] == unit["Install"]["WantedBy"] == "graphical-session.target"


def timew_export(path: Path, intervals: list[dict[str, object]]) -> Path:
    path.write_text(json.dumps(intervals))
    return path


def timew_stamp(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")


def test_import_timew_maps_tags_and_files_spans_under_their_utc_month(
    data_dir: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    export = timew_export(
        tmp_path / "export.json",
        [
            {"id": 4, "start": "20260303T073000Z", "end": "20260303T084842Z", "tags": ["acme", "project:x"]},
            {"id": 3, "start": "20260303T084842Z", "end": "20260303T090822Z", "tags": ["acme"]},
            {"id": 2, "start": "20260303T090822Z", "end": "20260303T100724Z", "tags": ["lunch"]},
            {"id": 1, "start": "20260303T100724Z", "end": "20260303T112606Z"},
        ],
    )
    assert run(capsys, "import-timew", "--work-tag", "acme", str(export)) == [["4"]]
    [ledger] = data_dir.glob("*.jsonl")
    assert ledger.name == "2026-03.jsonl"
    spans = [json.loads(line) for line in ledger.read_text().splitlines()]
    assert [(span["kind"], span["project"]) for span in spans] == [
        ("work", "x"),
        ("work", "general"),
        ("personal", "general"),
        ("personal", "general"),
    ]
    assert {span["src"] for span in spans} == {"timew"}
    assert (spans[0]["start"], spans[0]["end"]) == ("2026-03-03T07:30:00Z", "2026-03-03T08:48:42Z")


def test_imported_spans_are_reported_by_today(
    data_dir: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    noon = datetime.now(UTC).astimezone().replace(hour=12, minute=0, second=0, microsecond=0)
    export = timew_export(
        tmp_path / "export.json",
        [
            {"start": timew_stamp(noon), "end": timew_stamp(noon + timedelta(hours=1)), "tags": ["acme", "project:x"]},
            {
                "start": timew_stamp(noon + timedelta(hours=1)),
                "end": timew_stamp(noon + timedelta(hours=3)),
                "tags": ["project:y"],
            },
        ],
    )
    run(capsys, "import-timew", "--work-tag", "acme", str(export))
    assert run(capsys, "today") == [["x", "1:00"], ["y", "2:00"], ["work", "1:00"], ["total", "3:00"]]


def test_import_timew_skips_open_intervals(data_dir: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    export = timew_export(
        tmp_path / "export.json",
        [
            {"start": "20260715T073000Z", "end": "20260715T080000Z", "tags": ["project:x"]},
            {"start": "20260715T080000Z", "tags": ["project:x"]},
        ],
    )
    assert run(capsys, "import-timew", "--work-tag", "acme", str(export)) == [["1"]]
    [ledger] = data_dir.glob("*.jsonl")
    assert len(ledger.read_text().splitlines()) == 1


def test_import_timew_refuses_a_second_run_and_appends_nothing(
    data_dir: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    march = timew_export(
        tmp_path / "march.json", [{"start": "20260303T073000Z", "end": "20260303T080000Z", "tags": ["acme"]}]
    )
    run(capsys, "import-timew", "--work-tag", "acme", str(march))
    before = {ledger.name: ledger.read_text() for ledger in data_dir.glob("*.jsonl")}
    july = timew_export(
        tmp_path / "july.json", [{"start": "20260715T073000Z", "end": "20260715T080000Z", "tags": ["acme"]}]
    )
    with pytest.raises(SystemExit) as raised:
        tagwerk.main(["import-timew", "--work-tag", "acme", str(july)])
    assert raised.value.code
    assert "timew" in str(raised.value)
    assert {ledger.name: ledger.read_text() for ledger in data_dir.glob("*.jsonl")} == before


def test_import_timew_fails_on_a_missing_file_naming_it(data_dir: Path, tmp_path: Path) -> None:
    missing = tmp_path / "nope.json"
    with pytest.raises(SystemExit, match=re.escape(str(missing))):
        tagwerk.main(["import-timew", "--work-tag", "acme", str(missing)])
    assert not data_dir.exists()


def test_import_timew_runs_timew_export_without_a_file(
    data_dir: Path, fake_bin: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    export = json.dumps([{"start": "20260303T073000Z", "end": "20260303T080000Z", "tags": ["acme"]}])
    args = fake(fake_bin, "timew", export)
    assert run(capsys, "import-timew", "--work-tag", "acme") == [["1"]]
    assert args.read_text() == "export\n"


def test_beat_appends_one_line_with_src_and_cwd(
    home: Path, ledger: Path, runtime: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = f"{home}/code/work-org/assets.1"
    run(capsys, "beat", "claude", "--cwd", repo)
    [beat] = lines(ledger)
    assert {key: beat[key] for key in ("ev", "src", "cwd")} == {"ev": "beat", "src": "claude", "cwd": repo}
    assert beat["ts"].endswith("Z")


def test_a_second_beat_within_the_throttle_appends_nothing_until_the_stamp_ages(
    home: Path, ledger: Path, runtime: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = f"{home}/code/work-org/assets.1"
    run(capsys, "beat", "claude", "--cwd", repo)
    run(capsys, "beat", "claude", "--cwd", repo)
    assert len(lines(ledger)) == 1
    [stamp] = (runtime / "tagwerk").glob("claude-*")
    aged = stamp.stat().st_mtime - 61
    os.utime(stamp, (aged, aged))
    run(capsys, "beat", "claude", "--cwd", repo)
    assert len(lines(ledger)) == 2


def test_beats_from_two_sources_or_two_cwds_are_throttled_apart(
    home: Path, ledger: Path, runtime: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    run(capsys, "beat", "claude", "--cwd", f"{home}/code/work-org/assets")
    run(capsys, "beat", "pi", "--cwd", f"{home}/code/work-org/assets")
    run(capsys, "beat", "claude", "--cwd", f"{home}/code/work-org/checkout")
    assert len(lines(ledger)) == 3


def test_beat_reads_the_cwd_from_a_claude_hook_payload_on_stdin(
    home: Path, ledger: Path, runtime: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = f"{home}/code/work-org/assets"
    payload = {"session_id": "abc", "hook_event_name": "PostToolUse", "cwd": repo, "tool_name": "Bash"}
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    run(capsys, "beat", "claude")
    [beat] = lines(ledger)
    assert beat["cwd"] == repo


def test_beat_with_an_empty_stdin_uses_the_process_cwd(
    home: Path, ledger: Path, runtime: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = home / "code/work-org/assets"
    repo.mkdir(parents=True)
    monkeypatch.chdir(repo)
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    run(capsys, "beat", "pi")
    [beat] = lines(ledger)
    assert beat["cwd"] == str(repo)


def test_beat_outside_every_root_appends_nothing_and_exits_zero(
    home: Path, ledger: Path, runtime: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    run(capsys, "beat", "claude", "--cwd", f"{home}/Downloads")
    assert not ledger.exists()


def test_beat_throttle_is_read_from_the_config(
    home: Path, runtime: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    (home / "config.toml").write_text(
        f'data_dir = "{home}/data"\nbeat_throttle_sec = 0\n[roots]\n"{home}/code/work-org" = "work"\n'
    )
    monkeypatch.setenv("TAGWERK_CONFIG", str(home / "config.toml"))
    run(capsys, "beat", "claude", "--cwd", f"{home}/code/work-org/assets")
    run(capsys, "beat", "claude", "--cwd", f"{home}/code/work-org/assets")
    assert len(lines(home / "data")) == 2


@pytest.mark.parametrize("ev", ["idle", "active"])
def test_idle_and_active_each_append_one_bare_mark(ledger: Path, capsys: pytest.CaptureFixture[str], ev: str) -> None:
    run(capsys, ev)
    [event] = lines(ledger)
    assert event["ev"] == ev
    assert set(event) == {"ts", "ev"}


def test_hypridle_config_marks_sleep_and_one_150s_listener_without_locking() -> None:
    text = (CONTRIB / "hypridle.conf").read_text()
    assert text.count("listener {") == 1
    assert "timeout = 150" in text
    assert "before_sleep_cmd = tagwerk idle" in text
    assert "after_sleep_cmd = tagwerk active" in text
    assert "on-timeout = tagwerk idle" in text
    assert "on-resume = tagwerk active" in text
    assert "ignore_dbus_inhibit = false" in text
    assert "lock_cmd" not in text
