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
