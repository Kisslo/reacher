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
        "check-sources",
        "fetch-financials",
        "build-lists",
        "import-outcomes",
        "report",
    ):
        assert cmd in result.stdout


def test_unimplemented_commands_fail_loudly(tmp_path):
    """En tom kommandostub ska krascha, inte tyst göra ingenting."""
    # SCB-källan är stubbad tills T1-06 (#22). Byt till nästa stubb när den finns.
    result = runner.invoke(app, ["ingest", "--source", "scb", "--db", str(tmp_path / "t.db")])
    assert isinstance(result.exception, NotImplementedError)
