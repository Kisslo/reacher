from typer.testing import CliRunner

from reacher.cli import app

runner = CliRunner()


def test_help_lists_every_command():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for cmd in (
        "init-db",
        "load-seed",
        "ingest",
        "build-lists",
        "import-outcomes",
        "report",
    ):
        assert cmd in result.stdout


def test_unimplemented_commands_fail_loudly():
    """En tom kommandostub ska krascha, inte tyst göra ingenting."""
    # report tar bara ett argument, så anropet når stubben. Utan argument hade
    # Typer avbrutit med ett användningsfel (exit 2) och testet passerat ändå.
    result = runner.invoke(app, ["report", "2026w40"])
    assert isinstance(result.exception, NotImplementedError)
