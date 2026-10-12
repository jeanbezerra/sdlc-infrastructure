"""Logging determinístico e redigido, sem executar objetos declarados no TOML."""

import contextlib
import json
import logging
import os
import re
import string
import time
import tomllib
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler, TimedRotatingFileHandler
from pathlib import Path

LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
FIELDS = {
    "timestamp",
    "level",
    "logger",
    "event",
    "message",
    "command",
    "deployment",
    "service",
    "host",
    "run",
    "phase",
    "duration_ms",
}


@dataclass
class LoggingSettings:
    console_enabled: bool = True
    console_level: str = "INFO"
    console_format: str = "rich"
    console_color: str = "auto"
    file_enabled: bool = True
    file_level: str = "DEBUG"
    file_format: str = "json"
    file_path: str = ""
    rotation: str = "size"
    max_bytes: int = 10 * 1024 * 1024
    backup_count: int = 5
    when: str = "midnight"
    interval: int = 1
    utc: bool = True
    timestamp_format: str = "%Y-%m-%dT%H:%M:%S%z"
    text_format: str = "{timestamp} {level:<8} {event} [{service}/{run}] {message}"
    include_context: bool = True
    remote_level: str = "INFO"
    logger_levels: dict = field(default_factory=lambda: {"paramiko": "WARNING"})
    redact_values: list = field(default_factory=list)
    redact_env: list = field(
        default_factory=lambda: ["SDLC_SSH_PASSWORD", "SDLC_SUDO_PASSWORD", "SDLC_KEY_PASSPHRASE"]
    )
    redact_patterns: list = field(default_factory=list)


def settings(path=None, *, state_dir, overrides=None):
    config = LoggingSettings(file_path=str(Path(state_dir) / "logs/installer.jsonl"))
    if path:
        source = Path(path).expanduser().absolute()
        with source.open("rb") as stream:
            data = tomllib.load(stream)
        if set(data) != {"logging"} or not isinstance(data["logging"], dict):
            raise ValueError("TOML exige somente a seção [logging].")
        values = data["logging"]
        if set(values) - set(asdict(config)):
            raise ValueError(
                "Opção de logging desconhecida: " + ", ".join(sorted(set(values) - set(asdict(config))))
            )
        for key, value in values.items():
            if key == "file_path":
                if not isinstance(value, str) or not value:
                    raise ValueError("file_path deve ser um caminho não vazio.")
                value = str(source.parent / Path(value).expanduser())
            setattr(config, key, value)
    for key, value in (overrides or {}).items():
        if value is not None:
            setattr(config, key, value)
    for key, default in asdict(LoggingSettings()).items():
        value = getattr(config, key)
        if type(value) is not type(default):
            raise ValueError(f"Tipo inválido em logging.{key}.")
    for key in ("console_level", "file_level", "remote_level"):
        if getattr(config, key) not in LEVELS:
            raise ValueError(f"Nível inválido em logging.{key}.")
    if config.console_format not in {"rich", "text", "json"} or config.file_format not in {"text", "json"}:
        raise ValueError("Formato de logging inválido.")
    if config.console_color not in {"auto", "always", "never"} or config.rotation not in {
        "size",
        "time",
        "none",
    }:
        raise ValueError("Cor/rotação de logging inválida.")
    if config.max_bytes <= 0 or config.backup_count < 1 or config.interval <= 0:
        raise ValueError("max_bytes/interval devem ser positivos e backup_count >= 1.")
    if not re.fullmatch(r"S|M|H|D|midnight|W[0-6]", config.when, re.IGNORECASE):
        raise ValueError("when inválido para rotação temporal.")
    if config.file_enabled and not config.file_path:
        raise ValueError("file_path é obrigatório com file_enabled=true.")
    for key in ("redact_values", "redact_env", "redact_patterns"):
        if any(not isinstance(value, str) or not value for value in getattr(config, key)):
            raise ValueError(f"logging.{key} exige uma lista de textos não vazios.")
    if any(
        not isinstance(key, str) or not key or value not in LEVELS
        for key, value in config.logger_levels.items()
    ):
        raise ValueError("logger_levels exige nomes e níveis válidos.")
    for _, name, _, conversion in string.Formatter().parse(config.text_format):
        if name is not None and (name not in FIELDS or conversion):
            raise ValueError("Campo inválido em text_format: " + str(name))
    try:
        config.text_format.format_map(dict.fromkeys(FIELDS, ""))
    except (ValueError, KeyError) as error:
        raise ValueError("text_format inválido.") from error
    for pattern in config.redact_patterns:
        re.compile(pattern)
    return config


class Redactor:
    def __init__(self, config=None):
        config = config or LoggingSettings()
        self.values = set(config.redact_values)
        self.add(*(os.environ.get(name) for name in config.redact_env))
        self.patterns = [re.compile(pattern) for pattern in config.redact_patterns]

    def add(self, *values):
        self.values.update(value for value in values if isinstance(value, str) and value)

    def text(self, value):
        text = str(value)
        for value in sorted(self.values, key=len, reverse=True):
            text = text.replace(value, "[REDACTED]")
        text = re.sub(
            r"(?i)(\b(?:password|passwd|token|secret|authorization|api[_-]?key)\b\s*[:=]\s*)([^\s,;]+)",
            r"\1[REDACTED]",
            text,
        )
        text = re.sub(r"(?i)\b(Bearer|Basic)\s+[A-Za-z0-9+/=._-]+", r"\1 [REDACTED]", text)
        text = re.sub(r"(https?://)[^\s/@]+:[^\s/@]+@", r"\1[REDACTED]@", text)
        # Impede controle do terminal por conteúdo retornado pela VM.
        text = re.sub(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))", "", text)
        text = re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", "", text)
        for pattern in self.patterns:
            text = pattern.sub("[REDACTED]", text)
        return text

    def data(self, value):
        if isinstance(value, dict):
            return {
                key: "[REDACTED]"
                if re.fullmatch(r"(?i)password|passwd|token|secret|authorization|api[_-]?key", str(key))
                else self.data(item)
                for key, item in value.items()
            }
        if isinstance(value, (tuple, list)):
            return [self.data(item) for item in value]
        return self.text(value) if isinstance(value, str) else value


class EventFormatter(logging.Formatter):
    def __init__(self, config, redactor, context, format_name):
        super().__init__()
        self.config, self.redactor, self.context, self.format_name = config, redactor, context, format_name

    def format(self, record):
        stamp = datetime.fromtimestamp(record.created, timezone.utc)
        if not self.config.utc:
            stamp = stamp.astimezone()
        data = {
            "timestamp": stamp.strftime(self.config.timestamp_format),
            "level": record.levelname,
            "logger": record.name,
            "event": getattr(record, "event", record.name),
            "message": record.getMessage(),
        }
        if self.config.include_context:
            data.update(self.context)
            data.update({key: getattr(record, key) for key in FIELDS - set(data) if hasattr(record, key)})
        if record.exc_info:
            data["exception"] = self.formatException(record.exc_info)
        data = self.redactor.data(data)
        if self.format_name == "json":
            return json.dumps(data, ensure_ascii=False)
        text = self.config.text_format.format_map(
            {
                **dict.fromkeys(FIELDS, "-"),
                **{key: value if value is not None else "-" for key, value in data.items()},
            }
        )
        if "exception" in data:
            text += "\n" + data["exception"]
        return text


class PrivateFile:
    def _open(self):
        fd = os.open(self.baseFilename, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        os.chmod(self.baseFilename, 0o600)
        return os.fdopen(fd, self.mode, encoding=self.encoding, errors=self.errors)


class SizeHandler(PrivateFile, RotatingFileHandler):
    pass


class TimeHandler(PrivateFile, TimedRotatingFileHandler):
    pass


class FileHandler(PrivateFile, logging.FileHandler):
    pass


class LogSession:
    def __init__(self, config, redactor, context):
        self.config, self.redactor, self.context = config, redactor, context
        self.logger = logging.getLogger("sdlc_installer")
        self.handlers = []
        self.start = time.monotonic()

    def __enter__(self):
        root = logging.getLogger()
        self.previous = (list(root.handlers), root.level)
        self.levels = {}
        try:
            if self.config.console_enabled:
                if self.config.console_format == "rich":
                    from rich.console import Console
                    from rich.logging import RichHandler

                    color = self.config.console_color
                    handler = RichHandler(
                        console=Console(
                            stderr=True,
                            force_terminal=True if color == "always" else False if color == "never" else None,
                        ),
                        show_time=False,
                        show_level=False,
                        show_path=False,
                        markup=False,
                        highlighter=None,
                    )
                else:
                    handler = logging.StreamHandler()
                self._handler(handler, self.config.console_level, self.config.console_format)
            if self.config.file_enabled:
                path = Path(self.config.file_path).expanduser().absolute()
                path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
                if path.is_symlink():
                    raise ValueError("Arquivo de log não pode ser um symlink.")
                if self.config.rotation == "size":
                    handler = SizeHandler(
                        path,
                        maxBytes=self.config.max_bytes,
                        backupCount=self.config.backup_count,
                        encoding="utf-8",
                    )
                elif self.config.rotation == "time":
                    handler = TimeHandler(
                        path,
                        when=self.config.when,
                        interval=self.config.interval,
                        backupCount=self.config.backup_count,
                        encoding="utf-8",
                        utc=self.config.utc,
                    )
                else:
                    handler = FileHandler(path, encoding="utf-8")
                self._handler(handler, self.config.file_level, self.config.file_format)
            root.handlers = self.handlers
            root.setLevel(logging.WARNING)
            for name, level in {"sdlc_installer": "DEBUG", **self.config.logger_levels}.items():
                logger = logging.getLogger(name)
                self.levels[name] = logger.level
                logger.setLevel(level)
            return self
        except Exception:
            self.__exit__(None, None, None)
            raise

    def _handler(self, handler, level, format_name):
        self.handlers.append(handler)
        handler.setLevel(level)
        handler.setFormatter(EventFormatter(self.config, self.redactor, self.context, format_name))

    def event(self, name, message, level="INFO", **fields):
        self.logger.log(getattr(logging, level), message, extra={"event": name, **fields})

    def __exit__(self, *exc):
        root = logging.getLogger()
        root.handlers, level = self.previous
        root.setLevel(level)
        for name, value in self.levels.items():
            logging.getLogger(name).setLevel(value)
        for handler in self.handlers:
            with contextlib.suppress(Exception):
                handler.close()
