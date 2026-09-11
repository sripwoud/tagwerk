import configparser
import io
import json
import os
import re
import subprocess
import time
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

import tagwerk

SCRIPT = Path(tagwerk.__file__)
CONTRIB = SCRIPT.parent / "contrib"
T0 = datetime(2026, 8, 5, 10, 0, tzinfo=UTC)
MINUTE = timedelta(minutes=1)
Event = dict[str, Any]


@pytest.fixture(autouse=True)
def plain_stdout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.delenv("FORCE_COLOR", raising=False)


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
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "run"))
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture
def ledger(home: Path) -> Path:
    config = home / ".config/tagwerk/config.toml"
    config.parent.mkdir(parents=True)
    config.write_text(tagwerk.CONFIG_TEMPLATE)
    return home / ".local/share/tagwerk"


@pytest.fixture
def berlin() -> Iterator[None]:
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("TZ", "Europe/Berlin")
        time.tzset()
        yield
    time.tzset()


def lines(capsys: pytest.CaptureFixture[str], *argv: str) -> list[str]:
    assert tagwerk.main(list(argv)) == 0
    return capsys.readouterr().out.splitlines()


def run(capsys: pytest.CaptureFixture[str], *argv: str) -> list[list[str]]:
    return [line.split() for line in lines(capsys, *argv)]


def table(capsys: pytest.CaptureFixture[str], *argv: str) -> list[list[str]]:
    rows = run(capsys, *argv)
    return rows[rows.index([]) + 1 :]


def weeks_back(day: date) -> str:
    today = datetime.now(UTC).astimezone().date()
    return str((today - timedelta(days=today.weekday()) - (day - timedelta(days=day.weekday()))).days // 7)


def hours(day: datetime, count: float, project: str = "assets", kind: str = "work") -> Event:
    return span(day, day + timedelta(hours=count), project, kind)


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


@pytest.mark.parametrize(
    "command",
    [
        [],
        ["fix"],
        ["today"],
        ["week"],
        ["month"],
        ["invoice"],
        ["focus"],
        ["import-timew"],
        ["beat"],
        ["idle"],
        ["active"],
    ],
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


def test_version_prints_the_version_and_exits_zero_without_a_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("TAGWERK_CONFIG", str(tmp_path / "nope.toml"))
    with pytest.raises(SystemExit) as raised:
        tagwerk.main(["--version"])
    assert raised.value.code == 0
    assert capsys.readouterr().out == f"tagwerk {tagwerk.VERSION}\n"


def test_the_package_stamps_the_git_revision_over_the_master_version(tmp_path: Path) -> None:
    installed = tmp_path / "usr/bin/tagwerk"
    installed.parent.mkdir(parents=True)
    installed.write_text(SCRIPT.read_text())
    [stamp_line] = [
        line.strip() for line in (CONTRIB / "aur/PKGBUILD").read_text().splitlines() if line.strip().startswith("sed")
    ]
    subprocess.run(
        ["bash", "-c", stamp_line],
        check=True,
        env={**os.environ, "pkgdir": str(tmp_path), "pkgver": "r99.abc1234"},
    )
    installed.chmod(0o755)
    stamped = subprocess.run([str(installed), "--version"], capture_output=True, text=True, check=False)
    assert (stamped.returncode, stamped.stdout) == (0, "tagwerk r99.abc1234\n")


def test_missing_config_exits_with_its_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    missing = tmp_path / "nope.toml"
    monkeypatch.setenv("TAGWERK_CONFIG", str(missing))
    with pytest.raises(SystemExit, match=re.escape(str(missing))):
        tagwerk.main(["today"])


def test_fix_then_today_shows_booked_hours(data_dir: Path, capsys: pytest.CaptureFixture[str]) -> None:
    run(capsys, "fix", "09:00", "10:30", "assets")
    assert run(capsys, "today") == [["work/assets", "1:30"], ["work", "1:30"], ["total", "1:30"]]


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
    assert run(capsys, "today") == [
        ["work/assets", "0:30"],
        ["personal/auberge", "0:30"],
        ["work", "0:30"],
        ["total", "1:00"],
    ]


def test_personal_rows_follow_work_rows_regardless_of_minutes(
    data_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    run(capsys, "fix", "09:00", "09:30", "assets")
    run(capsys, "fix", "10:00", "12:00", "auberge", "--personal")
    assert run(capsys, "today") == [
        ["work/assets", "0:30"],
        ["personal/auberge", "2:00"],
        ["work", "0:30"],
        ["total", "2:30"],
    ]


def test_table_rows_name_the_kind_so_two_kinds_general_rows_stay_apart(
    home: Path, ledger: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    seed(
        ledger,
        mark(T0, "active"),
        *polls(T0, 5, window_class="kitty", title="~/code/work-org", cwd=f"{home}/code/work-org"),
        *polls(T0 + 5 * MINUTE, 5, window_class="kitty", title="~/code", cwd=f"{home}/code"),
        mark(T0 + 10 * MINUTE, "idle"),
    )
    assert table(capsys, "month", "2026-08") == [
        ["work/general", "0:05"],
        ["personal/general", "0:05"],
        ["work", "0:05"],
        ["total", "0:10"],
    ]


def test_off_span_removes_booked_minutes(data_dir: Path, capsys: pytest.CaptureFixture[str]) -> None:
    run(capsys, "fix", "09:00", "10:00", "assets")
    run(capsys, "fix", "09:30", "10:00", "lunch", "--off")
    assert run(capsys, "today") == [["work/assets", "0:30"], ["work", "0:30"], ["total", "0:30"]]


def test_fix_accepts_an_explicit_local_date(data_dir: Path, capsys: pytest.CaptureFixture[str]) -> None:
    today = datetime.now(UTC).astimezone().date()
    yesterday = today - timedelta(days=1)
    run(capsys, "fix", f"{today}T09:00", f"{today}T09:45", "assets")
    run(capsys, "fix", f"{yesterday}T09:00", f"{yesterday}T12:00", "assets")
    assert run(capsys, "today") == [["work/assets", "0:45"], ["work", "0:45"], ["total", "0:45"]]


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
    assert run(capsys, "today") == [["work/assets", "1:00"], ["work", "1:00"], ["total", "1:00"]]


def test_init_writes_the_template_once_and_it_books_into_the_expanded_home(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = home / ".config/tagwerk/config.toml"
    assert lines(capsys, "init") == [f"wrote {config}"]
    run(capsys, "fix", "09:00", "10:00", "assets")
    assert run(capsys, "today") == [["work/assets", "1:00"], ["work", "1:00"], ["total", "1:00"]]
    assert len(list((home / ".local/share/tagwerk").glob("*.jsonl"))) == 1
    with pytest.raises(SystemExit, match=re.escape(str(config))):
        tagwerk.main(["init"])
    assert config.read_text() == tagwerk.CONFIG_TEMPLATE


def test_month_with_an_empty_ledger_prints_zero_totals(ledger: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert table(capsys, "month", "2026-08") == [["work", "0:00"], ["total", "0:00"]]


def test_month_without_an_argument_uses_the_current_month(data_dir: Path, capsys: pytest.CaptureFixture[str]) -> None:
    run(capsys, "fix", "09:00", "10:00", "assets")
    assert table(capsys, "month") == [["work/assets", "1:00"], ["work", "1:00"], ["total", "1:00"]]


def test_month_credits_a_span_filed_in_that_month(ledger: Path, capsys: pytest.CaptureFixture[str]) -> None:
    seed(ledger, span(T0, T0 + 90 * MINUTE, "assets"))
    assert table(capsys, "month", "2026-08") == [["work/assets", "1:30"], ["work", "1:30"], ["total", "1:30"]]
    assert table(capsys, "month", "2026-07") == [["work", "0:00"], ["total", "0:00"]]


def test_month_reads_the_previous_file_for_a_span_crossing_into_its_first_local_minutes(
    ledger: Path, berlin: None, capsys: pytest.CaptureFixture[str]
) -> None:
    last_of_july_utc = datetime(2026, 7, 31, 21, 30, tzinfo=UTC)
    seed(ledger, span(last_of_july_utc, last_of_july_utc + 60 * MINUTE, "assets"))
    assert [path.name for path in ledger.glob("*.jsonl")] == ["2026-07.jsonl"]
    assert table(capsys, "month", "2026-08") == [["work/assets", "0:30"], ["work", "0:30"], ["total", "0:30"]]
    assert table(capsys, "month", "2026-07") == [["work/assets", "0:30"], ["work", "0:30"], ["total", "0:30"]]


def test_invoice_rows_are_quarter_hours_summing_to_the_rounded_total(
    ledger: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    seed(
        ledger,
        span(T0, T0 + 68 * MINUTE, "hermes"),
        span(T0 + 120 * MINUTE, T0 + 253 * MINUTE, "assets"),
        span(T0 + 300 * MINUTE, T0 + 342 * MINUTE, "nexus"),
    )
    assert lines(capsys, "invoice", "2026-08") == [
        "| Project | Hours |",
        "| --- | ---: |",
        "| assets | 2.25 |",
        "| hermes | 1.00 |",
        "| nexus | 0.75 |",
        "| **Total** | **4.00** |",
    ]


def test_invoice_excludes_personal_minutes(data_dir: Path, capsys: pytest.CaptureFixture[str]) -> None:
    run(capsys, "fix", "09:00", "10:00", "assets")
    run(capsys, "fix", "10:00", "11:00", "auberge", "--personal")
    month = datetime.now(UTC).astimezone().strftime("%Y-%m")
    assert lines(capsys, "invoice", month) == [
        "| Project | Hours |",
        "| --- | ---: |",
        "| assets | 1.00 |",
        "| **Total** | **1.00** |",
    ]


def test_fixed_minutes_are_paid_in_the_tables_and_absent_from_the_invoice(
    ledger: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    seed(
        ledger,
        hours(T0, 1),
        hours(T0 + timedelta(hours=1), 2, "auberge", "fixed"),
        hours(T0 + timedelta(hours=3), 3, "blog", "personal"),
    )
    assert table(capsys, "month", "2026-08") == [
        ["work/assets", "1:00"],
        ["fixed/auberge", "2:00"],
        ["personal/blog", "3:00"],
        ["work", "3:00"],
        ["total", "6:00"],
    ]
    assert lines(capsys, "invoice", "2026-08") == [
        "| Project | Hours |",
        "| --- | ---: |",
        "| assets | 1.00 |",
        "| **Total** | **1.00** |",
    ]


def test_invoice_for_a_month_of_only_fixed_minutes_prints_a_zero_total(
    ledger: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    seed(ledger, hours(T0, 3, "auberge", "fixed"))
    assert lines(capsys, "invoice", "2026-08") == ["| Project | Hours |", "| --- | ---: |", "| **Total** | **0.00** |"]


def test_invoice_for_an_empty_month_prints_the_header_and_a_zero_total(
    ledger: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert lines(capsys, "invoice", "2026-08") == ["| Project | Hours |", "| --- | ---: |", "| **Total** | **0.00** |"]


def test_invoice_rounds_the_true_total_not_the_per_project_minutes(
    home: Path, ledger: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    seed(ledger, *present(T0, 7), beat(T0, f"{home}/code/work-org/assets"), beat(T0, f"{home}/code/work-org/checkout"))
    assert run(capsys, "month", "2026-08")[-2:] == [["work", "0:07"], ["total", "0:07"]]
    assert lines(capsys, "invoice", "2026-08") == [
        "| Project | Hours |",
        "| --- | ---: |",
        "| assets | 0.00 |",
        "| checkout | 0.00 |",
        "| **Total** | **0.00** |",
    ]


def test_present_minutes_with_no_signal_land_on_personal_other(
    ledger: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    seed(ledger, *present(T0, 10))
    assert table(capsys, "month", "2026-08") == [["personal/other", "0:10"], ["work", "0:00"], ["total", "0:10"]]


def test_idle_stops_credit_and_active_resumes_it(ledger: Path, capsys: pytest.CaptureFixture[str]) -> None:
    seed(ledger, *present(T0, 15), mark(T0 + 5 * MINUTE, "idle"), mark(T0 + 8 * MINUTE, "active"))
    assert table(capsys, "month", "2026-08") == [["personal/other", "0:12"], ["work", "0:00"], ["total", "0:12"]]


def test_minutes_without_a_recent_poll_are_absent(ledger: Path, capsys: pytest.CaptureFixture[str]) -> None:
    seed(ledger, *polls(T0, 1), mark(T0 + 10 * MINUTE, "idle"))
    assert table(capsys, "month", "2026-08") == [["personal/other", "0:02"], ["work", "0:00"], ["total", "0:02"]]


def test_month_credits_exactly_its_first_and_last_local_minute(
    ledger: Path, berlin: None, capsys: pytest.CaptureFixture[str]
) -> None:
    before_october = datetime(2026, 9, 30, 21, 59, tzinfo=UTC)
    before_november = datetime(2026, 10, 31, 22, 59, tzinfo=UTC)
    seed(ledger, *present(before_october, 2), *present(before_november, 2))
    assert table(capsys, "month", "2026-09")[-1] == ["total", "0:01"]
    assert table(capsys, "month", "2026-10")[-1] == ["total", "0:02"]
    assert table(capsys, "month", "2026-11")[-1] == ["total", "0:01"]


def test_the_repeated_dst_hour_credits_every_present_minute_once(
    ledger: Path, berlin: None, capsys: pytest.CaptureFixture[str]
) -> None:
    seed(ledger, *present(datetime(2026, 10, 25, 0, 30, tzinfo=UTC), 61))
    assert table(capsys, "month", "2026-10") == [["personal/other", "1:01"], ["work", "0:00"], ["total", "1:01"]]


def test_one_beat_in_a_work_repo_takes_every_present_minute(
    home: Path, ledger: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    seed(ledger, *present(T0, 10), beat(T0, f"{home}/code/work-org/assets"))
    assert table(capsys, "month", "2026-08") == [["work/assets", "0:10"], ["work", "0:10"], ["total", "0:10"]]


def test_beats_in_two_repos_split_each_minute_evenly(
    home: Path, ledger: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    seed(ledger, *present(T0, 10), beat(T0, f"{home}/code/work-org/assets"), beat(T0, f"{home}/code/work-org/checkout"))
    assert table(capsys, "month", "2026-08") == [
        ["work/assets", "0:05"],
        ["work/checkout", "0:05"],
        ["work", "0:10"],
        ["total", "0:10"],
    ]


def test_a_beat_lease_expires_after_beat_lease_min(
    home: Path, ledger: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    seed(ledger, *present(T0, 15), beat(T0, f"{home}/code/work-org/assets"))
    assert table(capsys, "month", "2026-08") == [
        ["work/assets", "0:10"],
        ["personal/other", "0:05"],
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
    assert table(capsys, "month", "2026-08") == [
        ["work/assets", "0:07"],
        ["personal/other", "0:05"],
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
    assert table(capsys, "month", "2026-08") == [["work/assets", "0:12"], ["work", "0:12"], ["total", "0:12"]]


def test_a_beat_outside_every_root_leases_nothing(home: Path, ledger: Path, capsys: pytest.CaptureFixture[str]) -> None:
    seed(ledger, *present(T0, 5), beat(T0, f"{home}/Downloads"))
    assert table(capsys, "month", "2026-08") == [["personal/other", "0:05"], ["work", "0:00"], ["total", "0:05"]]


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
    assert table(capsys, "month", "2026-08") == [
        ["work/assets", "0:05"],
        ["personal/other", "0:05"],
        ["work", "0:05"],
        ["total", "0:10"],
    ]


def test_the_ambient_bucket_is_credited_only_while_no_repo_holds_a_lease(
    home: Path, ledger: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    seed(ledger, *present(T0, 15, title="#next-lending - work-org - Slack"), beat(T0, f"{home}/code/work-org/assets"))
    assert table(capsys, "month", "2026-08") == [
        ["work/assets", "0:10"],
        ["work/general", "0:05"],
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
    assert table(capsys, "month", "2026-08") == [[f"{kind}/{project}", "0:05"], ["work", work], ["total", "0:05"]]


def test_a_lease_taken_before_the_range_is_held_at_its_start(
    home: Path, ledger: Path, berlin: None, capsys: pytest.CaptureFixture[str]
) -> None:
    august = datetime(2026, 7, 31, 22, 0, tzinfo=UTC)
    seed(ledger, beat(august - 5 * MINUTE, f"{home}/code/work-org/assets"), *present(august, 5))
    assert [path.name for path in ledger.glob("*.jsonl")] == ["2026-07.jsonl"]
    assert table(capsys, "month", "2026-08") == [["work/assets", "0:05"], ["work", "0:05"], ["total", "0:05"]]


def test_a_span_overrides_beats_wholesale(home: Path, ledger: Path, capsys: pytest.CaptureFixture[str]) -> None:
    seed(
        ledger,
        *present(T0, 10),
        beat(T0, f"{home}/code/work-org/assets"),
        span(T0 + 5 * MINUTE, T0 + 10 * MINUTE, "meeting"),
    )
    assert table(capsys, "month", "2026-08") == [
        ["work/assets", "0:05"],
        ["work/meeting", "0:05"],
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
    assert table(capsys, "month", "2026-08") == [["work/assets", "0:05"], ["work", "0:05"], ["total", "0:05"]]


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
    assert table(capsys, "month", "2026-08") == [
        ["work/assets", "0:02"],
        ["work/checkout", "0:01"],
        ["personal/other", "0:02"],
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
    assert unit["Service"]["ExecStart"] == "/usr/bin/tagwerk focus"
    assert unit["Service"]["Restart"] == "on-failure"
    assert unit["Unit"]["PartOf"] == unit["Install"]["WantedBy"] == "graphical-session.target"


def test_idle_unit_runs_hypridle_on_the_packaged_config_inside_the_graphical_session() -> None:
    unit = configparser.ConfigParser(interpolation=None)
    unit.read(CONTRIB / "tagwerk-idle.service")
    assert unit["Service"]["ExecStart"] == "/usr/bin/hypridle --config /usr/share/tagwerk/hypridle.conf"
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
    assert run(capsys, "today") == [["work/x", "1:00"], ["personal/y", "2:00"], ["work", "1:00"], ["total", "3:00"]]


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


def test_beat_appends_one_line_with_src_and_cwd(home: Path, ledger: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo = f"{home}/code/work-org/assets.1"
    run(capsys, "beat", "claude", "--cwd", repo)
    [beat] = ledger_lines(ledger)
    assert {key: beat[key] for key in ("ev", "src", "cwd")} == {"ev": "beat", "src": "claude", "cwd": repo}
    assert beat["ts"].endswith("Z")


def test_a_second_beat_within_the_throttle_appends_nothing_until_the_stamp_ages(
    home: Path, ledger: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = f"{home}/code/work-org/assets.1"
    run(capsys, "beat", "claude", "--cwd", repo)
    run(capsys, "beat", "claude", "--cwd", repo)
    assert len(ledger_lines(ledger)) == 1
    [marker] = (home / "run/tagwerk").glob("claude-*")
    aged = marker.stat().st_mtime - 61
    os.utime(marker, (aged, aged))
    run(capsys, "beat", "claude", "--cwd", repo)
    assert len(ledger_lines(ledger)) == 2


def test_beats_from_two_sources_or_two_cwds_are_throttled_apart(
    home: Path, ledger: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    run(capsys, "beat", "claude", "--cwd", f"{home}/code/work-org/assets")
    run(capsys, "beat", "pi", "--cwd", f"{home}/code/work-org/assets")
    run(capsys, "beat", "claude", "--cwd", f"{home}/code/work-org/checkout")
    assert len(ledger_lines(ledger)) == 3


def test_beat_reads_the_cwd_from_a_claude_hook_payload_on_stdin(
    home: Path, ledger: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = f"{home}/code/work-org/assets"
    payload = {"session_id": "abc", "hook_event_name": "PostToolUse", "cwd": repo, "tool_name": "Bash"}
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    run(capsys, "beat", "claude")
    [beat] = ledger_lines(ledger)
    assert beat["cwd"] == repo


class Terminal(io.StringIO):
    def isatty(self) -> bool:
        return True


@pytest.mark.parametrize(
    "stdin", [io.StringIO(""), io.StringIO('{"session_id": "abc"}'), Terminal('{"cwd": "/elsewhere"}')]
)
def test_beat_without_a_cwd_on_a_piped_stdin_uses_the_process_cwd(
    home: Path, ledger: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], stdin: io.StringIO
) -> None:
    repo = home / "code/work-org/assets"
    repo.mkdir(parents=True)
    monkeypatch.chdir(repo)
    monkeypatch.setattr("sys.stdin", stdin)
    run(capsys, "beat", "pi")
    [beat] = ledger_lines(ledger)
    assert beat["cwd"] == str(repo)


def test_beat_outside_every_root_appends_nothing_and_exits_zero(
    home: Path, ledger: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    run(capsys, "beat", "claude", "--cwd", f"{home}/Downloads")
    assert not ledger.exists()


def test_beat_throttle_is_read_from_the_config(
    home: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    (home / "config.toml").write_text(
        f'data_dir = "{home}/data"\nbeat_throttle_sec = 0\n[roots]\n"{home}/code/work-org" = "work"\n'
    )
    monkeypatch.setenv("TAGWERK_CONFIG", str(home / "config.toml"))
    run(capsys, "beat", "claude", "--cwd", f"{home}/code/work-org/assets")
    run(capsys, "beat", "claude", "--cwd", f"{home}/code/work-org/assets")
    assert len(ledger_lines(home / "data")) == 2


@pytest.mark.parametrize("ev", ["idle", "active"])
def test_idle_and_active_each_append_one_bare_mark(ledger: Path, capsys: pytest.CaptureFixture[str], ev: str) -> None:
    run(capsys, ev)
    [event] = ledger_lines(ledger)
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


def test_claude_hooks_fragment_beats_on_four_events_with_a_5s_timeout() -> None:
    hooks = json.loads((CONTRIB / "claude-hooks.json").read_text())["hooks"]
    assert set(hooks) == {"SessionStart", "UserPromptSubmit", "PostToolUse", "Stop"}
    commands = [hook for groups in hooks.values() for group in groups for hook in group["hooks"]]
    assert len(commands) == 4
    assert all(hook["command"].startswith("tagwerk beat claude") for hook in commands)
    assert all(hook["timeout"] == 5 for hook in commands)
    assert hooks["PostToolUse"][0]["matcher"] == "*"


def test_pi_extension_spawns_tagwerk_by_absolute_path_on_four_events() -> None:
    text = (CONTRIB / "pi/tagwerk.ts").read_text()
    for event in ("session_start", "turn_start", "tool_execution_end", "agent_settled"):
        assert f"pi.on('{event}', beat)" in text
    assert "const TAGWERK = '/usr/bin/tagwerk';" in text
    assert "['beat', 'pi', '--cwd', ctx.cwd]" in text


WEEK = weeks_back(T0.date())
MONDAY = T0 - 2 * timedelta(days=1)


def test_week_bar_fills_its_rounded_share_and_marks_the_cap(
    ledger: Path, berlin: None, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("NO_COLOR", "1")
    seed(ledger, hours(T0, 10))
    out = lines(capsys, "week", "-n", WEEK)
    assert "\033" not in "\n".join(out)
    assert [line.split()[:2] for line in out[:7]] == [
        ["Mon", "03"],
        ["Tue", "04"],
        ["Wed", "05"],
        ["Thu", "06"],
        ["Fri", "07"],
        ["Sat", "08"],
        ["Sun", "09"],
    ]
    _, _, bar, booked = out[2].split()
    assert booked == "10:00"
    assert (len(bar), bar.count("█"), bar.count("·"), bar.index("│")) == (25, 20, 4, 16)
    _, _, empty, none = out[6].split()
    assert (empty, none) == ("·" * 16 + "│" + "·" * 8, "0:00")


def test_week_paints_labels_red_over_the_day_cap_or_on_a_busy_weekend(
    ledger: Path, berlin: None, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("FORCE_COLOR", "1")
    tuesday, wednesday, saturday = MONDAY + timedelta(days=1), T0, MONDAY + timedelta(days=5)
    seed(
        ledger,
        hours(tuesday, 5),
        hours(tuesday + 5 * timedelta(hours=1), 4, "auberge", "personal"),
        hours(wednesday, 7),
        hours(saturday, 1),
    )
    out = lines(capsys, "week", "-n", WEEK)
    red = f"{tagwerk.RED}{{}}{tagwerk.RESET}"
    assert out[1].startswith(red.format("Tue 04"))
    assert out[2].startswith("Wed 05  ")
    assert out[5].startswith(red.format("Sat 08"))
    assert out[6].startswith("Sun 09  ")


@pytest.mark.parametrize(("extra_minutes", "footer"), [(0, "work 40:00 / 40:00"), (60, "work 41:00 / 40:00")])
def test_week_footer_shows_work_against_the_cap_and_turns_red_above_it(
    ledger: Path,
    berlin: None,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    extra_minutes: int,
    footer: str,
) -> None:
    monkeypatch.setenv("FORCE_COLOR", "1")
    seed(ledger, *(hours(MONDAY + timedelta(days=offset), 8) for offset in range(5)))
    seed(ledger, span(T0 + 9 * timedelta(hours=1), T0 + 9 * timedelta(hours=1) + extra_minutes * MINUTE, "assets"))
    seed(ledger, hours(T0 + 11 * timedelta(hours=1), 1, "auberge", "personal"))
    out = lines(capsys, "week", "-n", WEEK)
    assert out[7] == (f"{tagwerk.RED}{footer}{tagwerk.RESET}" if extra_minutes else footer)


def test_the_week_footer_counts_fixed_minutes_as_paid(
    ledger: Path, berlin: None, capsys: pytest.CaptureFixture[str]
) -> None:
    seed(ledger, hours(T0, 2), hours(T0 + 2 * timedelta(hours=1), 1, "auberge", "fixed"))
    assert lines(capsys, "week", "-n", WEEK)[7] == "work 3:00 / 40:00"


def test_week_lands_a_span_over_utc_midnight_on_one_local_date(
    ledger: Path, berlin: None, capsys: pytest.CaptureFixture[str]
) -> None:
    late = datetime(2026, 8, 5, 23, 30, tzinfo=UTC)
    seed(ledger, span(late, late + 60 * MINUTE, "assets"))
    out = run(capsys, "week", "-n", WEEK)
    assert out[2][-1] == "0:00"
    assert out[3][-1] == "1:00"


def test_week_defaults_to_the_current_week(data_dir: Path, capsys: pytest.CaptureFixture[str]) -> None:
    run(capsys, "fix", "09:00", "10:00", "assets")
    today = datetime.now(UTC).astimezone().date()
    out = run(capsys, "week")
    assert out[today.weekday()][:2] == [f"{today:%a}", f"{today:%d}"]
    assert out[today.weekday()][-1] == "1:00"
    assert out[7] == ["work", "1:00", "/", "40:00"]


@pytest.mark.parametrize(
    ("env", "escaped"), [({}, False), ({"FORCE_COLOR": "1"}, True), ({"NO_COLOR": "1", "FORCE_COLOR": "1"}, True)]
)
def test_piped_output_carries_escapes_only_under_force_color(
    ledger: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    env: dict[str, str],
    escaped: bool,
) -> None:
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    seed(ledger, hours(T0, 2))
    assert ("\033[" in "\n".join(lines(capsys, "week", "-n", WEEK))) is escaped


def test_bar_colours_are_stable_per_work_project_blue_for_general_and_grey_for_personal(
    ledger: Path, berlin: None, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("FORCE_COLOR", "1")
    seed(
        ledger,
        hours(T0, 2),
        hours(T0 + 2 * timedelta(hours=1), 2, "general"),
        hours(T0 + 4 * timedelta(hours=1), 2, "auberge", "personal"),
    )
    wednesday = lines(capsys, "week", "-n", WEEK)[2]
    segments = re.findall(r"\033\[38;5;(\d+)m(█+)", wednesday)
    assert [len(cells) for _, cells in segments] == [4, 4, 4]
    assets, general, auberge = (int(index) for index, _ in segments)
    assert assets in tagwerk.PALETTE
    assert general == tagwerk.BLUE
    assert auberge in tagwerk.GREYS
    assert lines(capsys, "week", "-n", WEEK)[2] == wednesday


def test_repos_sharing_a_hue_alternate_full_and_shade_cells_so_their_segments_stay_apart(
    ledger: Path, berlin: None, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    seed(
        ledger,
        hours(T0, 3),
        hours(T0 + 3 * timedelta(hours=1), 2, "hermes"),
        hours(T0 + 5 * timedelta(hours=1), 1, "hedera"),
        hours(T0 + 6 * timedelta(hours=1), 1, "auberge", "personal"),
    )
    monkeypatch.setenv("FORCE_COLOR", "1")
    segments = re.findall(r"\033\[38;5;(\d+)m([█▓]+)", lines(capsys, "week", "-n", WEEK)[2])
    assert [cells for _, cells in segments] == ["█" * 6, "▓" * 4, "█" * 2, "█" * 2]
    (orchid,) = {int(index) for index, _ in segments[:3]}
    assert orchid in tagwerk.PALETTE
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.delenv("FORCE_COLOR")
    assert lines(capsys, "week", "-n", WEEK)[2].split()[2] == "██████▓▓▓▓████··│········"


def test_caps_come_from_the_config_and_change_colours_but_never_numbers(
    home: Path, berlin: None, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("FORCE_COLOR", "1")
    (home / "config.toml").write_text(f'data_dir = "{home}/data"\nday_cap_h = 4\nweek_cap_h = 4\n')
    monkeypatch.setenv("TAGWERK_CONFIG", str(home / "config.toml"))
    seed(home / "data", hours(T0, 3), hours(T0 + 3 * timedelta(hours=1), 2, "auberge", "personal"))
    out = lines(capsys, "week", "-n", WEEK)
    assert out[2].startswith(f"{tagwerk.RED}Wed 05{tagwerk.RESET}")
    assert out[2].endswith("5:00")
    assert out[7] == "work 3:00 / 4:00"
    _, _, bar, _ = out[2].split()
    assert re.sub(r"\033\[[0-9;]*m", "", bar).index("│") == 8
    august = lines(capsys, "month", "2026-08")
    assert august[1].startswith(f"{tagwerk.RED}W32{tagwerk.RESET}")
    assert august[1].endswith("5:00")
    assert august[2].startswith("W33  ")


def test_month_shows_one_bar_per_iso_week_that_agrees_with_the_table(
    ledger: Path, berlin: None, capsys: pytest.CaptureFixture[str]
) -> None:
    seed(
        ledger, hours(T0, 3), hours(T0 + timedelta(days=7), 2), hours(T0 + timedelta(days=26), 1, "auberge", "personal")
    )
    out = lines(capsys, "month", "2026-08")
    bars = [line.split() for line in out[:6]]
    assert [label for label, _, _ in bars] == ["W31", "W32", "W33", "W34", "W35", "W36"]
    assert [booked for _, _, booked in bars] == ["0:00", "3:00", "2:00", "0:00", "0:00", "1:00"]
    assert {bar.index("│") for _, bar, _ in bars} == {16}
    assert bars[1][1].count("█") == 1
    assert out[6] == ""
    assert [line.split() for line in out[7:]] == [
        ["work/assets", "5:00"],
        ["personal/auberge", "1:00"],
        ["work", "5:00"],
        ["total", "6:00"],
    ]
