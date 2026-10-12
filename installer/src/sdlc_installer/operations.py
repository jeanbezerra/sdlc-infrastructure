"""Acompanhamento e recibos locais recuperáveis pelo ID remoto."""

import json
import os
import tempfile
import time
from pathlib import Path

from .core import RUN_ID, require

TERMINAL = {"succeeded", "failed", "interrupted"}


def save_report(args, run, result):
    require(RUN_ID.fullmatch(run), "ID de execução inválido.")
    directory = Path(args.state_dir) / "runs" / run
    directory.mkdir(parents=True, mode=0o700, exist_ok=True)
    os.chmod(directory, 0o700)
    report = directory / "result.json"
    session = getattr(args, "session", None)
    data = {**result, "run": run, "deployment": getattr(args, "deployment", None)}
    if session:
        data = session.redactor.data({**session.context, **data})
    # Publicação atômica: history nunca lê um JSON parcialmente escrito.
    fd, name = tempfile.mkstemp(prefix=".result-", suffix=".tmp", dir=directory)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        os.chmod(temporary, 0o600)
        os.replace(temporary, report)
    finally:
        temporary.unlink(missing_ok=True)
    return directory


def follow(remote, service, run, args):
    directory = save_report(args, run, {"state": "observing", "service": service})
    log = directory / "operation.log"
    fd = os.open(log, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.chmod(log, 0o600)
    session = getattr(args, "session", None)
    output = getattr(args, "output", None)
    offset, deadline, pending, previous = 0, time.monotonic() + args.timeout, "", None

    def write(text, stream):
        text = remote.redact(text)
        if session:
            text = session.redactor.text(text)
        stream.write(text)
        stream.flush()
        if session:
            for line in text.splitlines():
                session.event("remote_output", line, level=session.config.remote_level)
        else:
            print(text, end="", flush=True)

    from contextlib import nullcontext

    with (
        os.fdopen(fd, "w", encoding="utf-8") as stream,
        output.progress(f"Acompanhando {service} • {run}") if output else nullcontext() as progress,
    ):
        while True:
            result = remote.status(service, run, offset)
            log_complete = result.pop("log_complete")
            pending += result.pop("output")
            offset = result.pop("offset")
            state = (result["state"], result.get("phase", ""))
            if session and state != previous:
                session.context["phase"] = state[1]
                session.event("run_state", f"Estado: {state[0]}; fase: {state[1] or '-'}.")
                previous = state
            if progress:
                progress.update(f"{service} • {run} • {state[0]} / {state[1]}")
            # Preserva linhas entre chunks para ocultar também segredos divididos pelo transporte.
            split = pending.rfind("\n") + 1
            if split:
                write(pending[:split], stream)
                pending = pending[split:]
            terminal = result["state"] in TERMINAL and log_complete
            if terminal and pending:
                write(pending, stream)
                pending = ""
            save_report(args, run, result)
            args.last_result = result
            if terminal:
                if session:
                    session.event(
                        "run_complete",
                        "Operação remota concluída.",
                        level="INFO" if result["state"] == "succeeded" else "ERROR",
                    )
                if not output:
                    print(json.dumps(result, ensure_ascii=False, indent=2))
                return 0 if result["state"] == "succeeded" else 1
            require(
                time.monotonic() < deadline,
                f"Acompanhamento expirou; operação remota continua. Use status com --run {run}.",
            )
            if log_complete:
                time.sleep(getattr(args, "poll_interval", 2))


def history(state_dir, *, deployment=None, state=None, limit=20):
    base = Path(state_dir) / "runs"
    records = []
    for report in base.glob("*/result.json"):
        if report.is_symlink() or report.stat().st_size > 1024 * 1024:
            continue
        try:
            data = json.loads(report.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("Recibo inválido")
            if deployment and data.get("deployment") != deployment or state and data.get("state") != state:
                continue
            records.append((report.stat().st_mtime_ns, {**data, "report": str(report.absolute())}))
        except (OSError, ValueError):
            if not deployment and state in {None, "corrupt"}:
                records.append(
                    (
                        report.stat().st_mtime_ns,
                        {"run": report.parent.name, "state": "corrupt", "report": str(report.absolute())},
                    )
                )
    records.sort(key=lambda item: (item[0], item[1].get("run", "")), reverse=True)
    return [record for _, record in records[:limit]]
