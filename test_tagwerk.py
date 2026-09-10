import json
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
T0 = datetime(2026, 8, 5, 10, 0, tzinfo=UTC)
M = timedelta(minutes=1)
Event = dict[str, Any]


@pytest.fixture
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    data_dir = tmp_path / "data"
    config = tmp_path / "config.toml"
    config.write_text(f'data_dir = "{data_dir}"\n')
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
    start: datetime, count: int, cls: str = "vivaldi", title: str = "New Tab - Vivaldi", cwd: str | None = None
) -> list[Event]:
    return [{"ts": stamp(start + i * M), "ev": "focus", "class": cls, "title": title, "cwd": cwd} for i in range(count)]


def mark(moment: datetime, ev: str) -> Event:
    return {"ts": stamp(moment), "ev": ev}


def present(start: datetime, count: int, **window: Any) -> list[Event]:
    return [mark(start, "active"), *polls(start, count, **window), mark(start + count * M, "idle")]


def span(start: datetime, end: datetime, project: str, kind: str = "work") -> Event:
    return {"ts": stamp(end), "ev": "span", "start": stamp(start), "end": stamp(end), "kind": kind, "project": project}


def seed(ledger: Path, *events: Event) -> None:
    ledger.mkdir(parents=True, exist_ok=True)
    for event in events:
        filed = event["start"] if event["ev"] == "span" else event["ts"]
        with (ledger / f"{filed[:7]}.jsonl").open("a") as month:
            month.write(json.dumps(event) + "\n")


@pytest.mark.parametrize("command", [[], ["fix"], ["today"], ["month"]])
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
    seed(ledger, span(T0, T0 + 90 * M, "assets"))
    assert run(capsys, "month", "2026-08") == [["assets", "1:30"], ["work", "1:30"], ["total", "1:30"]]
    assert run(capsys, "month", "2026-07") == [["work", "0:00"], ["total", "0:00"]]


def test_month_reads_the_previous_file_for_a_span_crossing_into_its_first_local_minutes(
    ledger: Path, berlin: None, capsys: pytest.CaptureFixture[str]
) -> None:
    last_of_july_utc = datetime(2026, 7, 31, 21, 30, tzinfo=UTC)
    seed(ledger, span(last_of_july_utc, last_of_july_utc + 60 * M, "assets"))
    assert [path.name for path in ledger.glob("*.jsonl")] == ["2026-07.jsonl"]
    assert run(capsys, "month", "2026-08") == [["assets", "0:30"], ["work", "0:30"], ["total", "0:30"]]
    assert run(capsys, "month", "2026-07") == [["assets", "0:30"], ["work", "0:30"], ["total", "0:30"]]


def test_present_minutes_with_no_signal_land_on_personal_other(
    ledger: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    seed(ledger, *present(T0, 10))
    assert run(capsys, "month", "2026-08") == [["other", "0:10"], ["work", "0:00"], ["total", "0:10"]]


def test_idle_stops_credit_and_active_resumes_it(ledger: Path, capsys: pytest.CaptureFixture[str]) -> None:
    seed(ledger, *present(T0, 15), mark(T0 + 5 * M, "idle"), mark(T0 + 8 * M, "active"))
    assert run(capsys, "month", "2026-08") == [["other", "0:12"], ["work", "0:00"], ["total", "0:12"]]


def test_minutes_without_a_recent_poll_are_absent(ledger: Path, capsys: pytest.CaptureFixture[str]) -> None:
    seed(ledger, *polls(T0, 1), mark(T0 + 10 * M, "idle"))
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
