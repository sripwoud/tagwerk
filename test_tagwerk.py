import subprocess
from pathlib import Path

import pytest

import tagwerk

SCRIPT = Path(tagwerk.__file__)


def test_help_exits_zero_and_prints_usage(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as raised:
        tagwerk.main(["--help"])
    assert raised.value.code == 0
    assert capsys.readouterr().out.startswith("usage: tagwerk")


def test_script_runs_through_shebang() -> None:
    result = subprocess.run([str(SCRIPT), "--help"], capture_output=True, text=True, check=False)
    assert result.returncode == 0
    assert result.stdout.startswith("usage: tagwerk")
