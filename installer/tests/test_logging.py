import io
import json
import logging
import re
from dataclasses import replace
from pathlib import Path

import pytest

from sdlc_installer.logging_config import EventFormatter, LoggingSettings, LogSession, Redactor, settings


def test_toml_relative_paths_overrides_and_validation(tmp_path):
    path = tmp_path / "logging.toml"
    path.write_text(
        '[logging]\nfile_path="logs/events.jsonl"\nconsole_level="WARNING"\nrotation="time"\nwhen="H"\ninterval=2\n',
        encoding="utf-8",
    )
    config = settings(path, state_dir=tmp_path, overrides={"console_level": "DEBUG"})
    assert Path(config.file_path) == tmp_path / "logs/events.jsonl"
    assert config.console_level == "DEBUG"
    assert config.rotation == "time" and config.interval == 2
    for content in (
        "[logging]\nwrong=true",
        "[logging]\nmax_bytes=0",
        '[logging]\nconsole_level="TRACE"',
        "[logging]\ninclude_context=1",
        '[logging]\ntext_format="{message.__class__}"',
        '[logging]\nredact_patterns=["["]',
        '[logging]\nlogger_levels={paramiko="TRACE"}',
    ):
        path.write_text(content, encoding="utf-8")
        with pytest.raises((ValueError, re.error)):
            settings(path, state_dir=tmp_path)


@pytest.mark.parametrize("format_name", ["text", "json"])
def test_all_handlers_redact_messages_context_traceback_and_terminal_codes(
    tmp_path, monkeypatch, format_name
):
    monkeypatch.setenv("SDLC_SSH_PASSWORD", "env-secret")
    config = LoggingSettings(
        console_format=format_name,
        file_format=format_name,
        file_path=str(tmp_path / "events.log"),
        redact_values=["configured-secret"],
        redact_patterns=[r"tenant-\d+"],
    )
    redactor = Redactor(config)
    console = io.StringIO()
    monkeypatch.setattr("sys.stderr", console)
    with LogSession(
        config, redactor, {"host": "tenant-123", "service": "grafana", "run": "a" * 24}
    ) as session:
        try:
            raise ValueError("configured-secret env-secret")
        except ValueError:
            session.logger.error(
                "password=literal-secret Bearer private-token \x1b[31menv-secret",
                exc_info=True,
                extra={"event": "failure"},
            )
    for text in (console.getvalue(), (tmp_path / "events.log").read_text(encoding="utf-8")):
        for secret in (
            "configured-secret",
            "env-secret",
            "literal-secret",
            "private-token",
            "tenant-123",
            "\x1b",
        ):
            assert secret not in text
        assert "[REDACTED]" in text
        if format_name == "json":
            event = json.loads(text)
            assert event["event"] == "failure" and event["service"] == "grafana"
            assert "exception" in event


@pytest.mark.parametrize("rotation", ["size", "time", "none"])
def test_rotation_valid_json_and_retention_and_logger_restore(tmp_path, rotation):
    path = tmp_path / "events.jsonl"
    config = LoggingSettings(
        console_enabled=False,
        file_path=str(path),
        rotation=rotation,
        max_bytes=450,
        backup_count=2,
        when="S",
        interval=1,
    )
    root = logging.getLogger()
    before = (list(root.handlers), root.level, logging.getLogger("paramiko").level)
    with LogSession(config, Redactor(config), {"command": "verify"}) as session:
        for number in range(15):
            session.event("test", "x" * 60, count=number)
        if rotation == "time":
            handler = session.handlers[0]
            handler.rolloverAt = 0
            session.event("test", "force time rotation")
    assert (list(root.handlers), root.level, logging.getLogger("paramiko").level) == before
    paths = list(tmp_path.glob("events.jsonl*"))
    assert len(paths) <= 3 and path.exists()
    assert (len(paths) > 1) == (rotation != "none")
    for candidate in paths:
        for line in candidate.read_text(encoding="utf-8").splitlines():
            assert json.loads(line)["event"] == "test"


def test_context_can_be_removed_and_levels_are_independent(tmp_path):
    config = LoggingSettings(
        console_enabled=False, file_level="ERROR", include_context=False, file_path=str(tmp_path / "log")
    )
    with LogSession(config, Redactor(config), {"host": "hidden"}) as session:
        session.event("ignored", "ignore")
        session.event("written", "write", level="ERROR")
    event = json.loads((tmp_path / "log").read_text())
    assert event["event"] == "written" and "host" not in event


def test_default_template_and_recursive_json_redaction():
    config = LoggingSettings()
    redactor = Redactor(config)
    record = logging.LogRecord("sdlc_installer", logging.INFO, "", 1, "safe", (), None)
    assert "safe" in EventFormatter(config, redactor, {}, "text").format(record)
    assert redactor.data({"token": "value", "nested": [{"PASSWORD": "other"}]}) == {
        "token": "[REDACTED]",
        "nested": [{"PASSWORD": "[REDACTED]"}],
    }


def test_example_configuration_is_valid():
    source = Path(__file__).resolve().parents[1] / "config/logging.example.toml"
    config = settings(source, state_dir=source.parent)
    assert config.file_format == "json" and config.backup_count == 5


def test_formatter_preserves_machine_json_for_quotes_and_multiline():
    config = replace(LoggingSettings(), redact_values=['a"b\\c'])
    record = logging.LogRecord("sdlc_installer", logging.INFO, "", 1, 'first\na"b\\c', (), None)
    event = json.loads(EventFormatter(config, Redactor(config), {}, "json").format(record))
    assert event["message"] == "first\n[REDACTED]"


@pytest.mark.parametrize("utc", [True, False])
def test_timestamps_include_timezone(utc):
    config = replace(LoggingSettings(), utc=utc)
    record = logging.LogRecord("sdlc_installer", logging.INFO, "", 1, "safe", (), None)
    event = json.loads(EventFormatter(config, Redactor(config), {}, "json").format(record))
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{4}", event["timestamp"])
