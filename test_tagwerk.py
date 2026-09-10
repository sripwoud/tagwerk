import json
import re
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

import tagwerk

SCRIPT = Path(tagwerk.__file__)


@pytest.fixture
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    data_dir = tmp_path / "data"
    config = tmp_path / "config.toml"
    config.write_text(f'data_dir = "{data_dir}"\n')
    monkeypatch.setenv("TAGWERK_CONFIG", str(config))
    monkeypatch.delenv("TAGWERK_DATA_DIR", raising=False)
    return data_dir


def run(capsys: pytest.CaptureFixture[str], *argv: str) -> list[list[str]]:
    assert tagwerk.main(list(argv)) == 0
    return [line.split() for line in capsys.readouterr().out.splitlines()]


@pytest.mark.parametrize("command", [[], ["fix"], ["today"]])
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
    assert ledger.name == f"{datetime.now(UTC):%Y-%m}.jsonl"
    [line] = ledger.read_text().splitlines()
    span = json.loads(line)
    assert {k: span[k] for k in ("ev", "kind", "project", "src")} == {
        "ev": "span",
        "kind": "work",
        "project": "assets",
        "src": "fix",
    }
    assert all(span[k].endswith("Z") for k in ("ts", "start", "end"))
    start, end = datetime.fromisoformat(span["start"]), datetime.fromisoformat(span["end"])
    assert end - start == timedelta(minutes=90)
    assert start.astimezone().strftime("%H:%M") == "09:00"


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
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    config = tmp_path / "config.toml"
    config.write_text('[roots]\n"~/code" = "personal"\n')
    monkeypatch.setenv("TAGWERK_CONFIG", str(config))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    run(capsys, "fix", "09:00", "10:00", "assets")
    assert len(list((tmp_path / ".local/share/tagwerk").glob("*.jsonl"))) == 1


def test_example_config_books_into_the_expanded_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("TAGWERK_CONFIG", str(SCRIPT.with_name("config.example.toml")))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    run(capsys, "fix", "09:00", "10:00", "assets")
    assert run(capsys, "today") == [["assets", "1:00"], ["work", "1:00"], ["total", "1:00"]]
    assert len(list((tmp_path / ".local/share/tagwerk").glob("*.jsonl"))) == 1
