import subprocess
import sys

from duration.cli import main


def test_formats_bare_seconds(capsys):
    assert main(["75"]) == 0
    assert capsys.readouterr().out.strip() == "1m15s"


def test_parses_text(capsys):
    assert main(["--text", "5m"]) == 0
    assert capsys.readouterr().out.strip() == "5m0s"


def test_no_arguments_prints_help(capsys):
    assert main([]) == 2
    assert "usage" in capsys.readouterr().out


def test_module_entrypoint_works():
    done = subprocess.run(
        [sys.executable, "-m", "duration", "119"],
        capture_output=True, text=True, check=True, timeout=30,
    )
    assert done.stdout.strip() == "1m59s"
