"""Instale e acompanhe serviços SDLC em Ubuntu 26.04 LTS por SSH."""

import argparse
import hashlib
import json
import os
import socket
import sys
import time
from pathlib import Path
from types import SimpleNamespace

from . import __version__, core
from .logging_config import LogSession, Redactor, settings
from .operations import follow, history, save_report
from .paths import repository
from .presentation import Output
from .transport import Remote, fingerprint, ssh_module

DEFAULTS = dict(
    repo=None,
    inventory=None,
    state_dir=None,
    json_output=False,
    quiet=False,
    verbose=0,
    no_color=False,
    log_config=None,
    log_level=None,
    log_file=None,
    log_format=None,
    no_log_file=False,
    no_console_log=False,
    connect_timeout=15,
    command_timeout=60,
    timeout=3600,
    poll_interval=2,
    remote_log_level=None,
)


class UsageError(ValueError):
    pass


class ArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        raise UsageError(message)


def positive(value):
    number = float(value)
    if not 0 < number < float("inf"):
        raise argparse.ArgumentTypeError("informe um número positivo e finito")
    return number


def positive_int(value):
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("informe um inteiro positivo")
    return number


def common(parser):
    group = parser.add_argument_group("Projeto, saída e logging (aceitos antes ou depois do comando)")
    options = [
        (("--repo",), dict(help="Raiz do projeto SDLC; alternativa: SDLC_REPO.")),
        (("--inventory", "-i"), dict(help="Inventário JSON; padrão: <repo>/installer/inventory.json.")),
        (("--state-dir",), dict(help="Recibos e logs; padrão: <repo>/installer/.state.")),
        (
            ("--json",),
            dict(dest="json_output", action="store_true", help="Um JSON em stdout; logs em stderr."),
        ),
        (
            ("--quiet", "-q"),
            dict(action="store_true", help="Oculta logs do console e progresso; mantém o resultado."),
        ),
        (
            ("--verbose", "-v"),
            dict(action="count", help="DEBUG no console; -vv também habilita DEBUG do Paramiko."),
        ),
        (("--no-color",), dict(action="store_true", help="Desabilita cores; respeita também NO_COLOR.")),
        (("--log-config",), dict(help="TOML de logging; alternativa: SDLC_LOG_CONFIG.")),
        (
            ("--log-level",),
            dict(
                choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"], help="Nível do console e arquivo."
            ),
        ),
        (
            ("--remote-log-level",),
            dict(choices=["DEBUG", "INFO", "WARNING", "ERROR"], help="Nível atribuído às linhas da VM."),
        ),
        (("--log-file",), dict(help="Arquivo de eventos; caminho relativo ao diretório atual.")),
        (("--log-format",), dict(choices=["text", "json"], help="Formato do arquivo de eventos.")),
        (
            ("--no-log-file",),
            dict(action="store_true", help="Desabilita arquivo de eventos; recibos permanecem."),
        ),
        (("--no-console-log",), dict(action="store_true", help="Desabilita somente eventos em stderr.")),
    ]
    for flags, kwargs in options:
        group.add_argument(*flags, default=argparse.SUPPRESS, **kwargs)


def parser():
    command = ArgumentParser(
        prog="sdlc-installer",
        description=__doc__,
        epilog="Exemplo: sdlc-installer install grafana-codex --ask-pass --ask-sudo-pass\nUse COMANDO --help para as opções específicas.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    common(command)
    command.set_defaults(**DEFAULTS)
    command.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = command.add_subparsers(dest="command", required=True, title="Comandos")
    descriptions = {
        "services": "Lista serviços, versões, portas e pendências.",
        "deployments": "Lista os destinos configurados, sem SSH.",
        "doctor": "Diagnostica repositório, inventário e entradas locais, sem SSH.",
        "history": "Lista recibos locais; filtros por deployment/estado.",
        "fingerprint": "Consulta a chave SSH sem autenticação para conferência externa.",
        "plan": "Valida configuração e mostra o plano, sem SSH.",
        "install": "Envia scripts, instala e verifica o serviço.",
        "verify": "Verifica uma instalação existente na VM.",
        "uninstall": "Remove o serviço; exige --yes.",
        "status": "Consulta ou acompanha uma execução pelo ID.",
    }
    for action, description in descriptions.items():
        cmd = sub.add_parser(action, help=description, description=description)
        common(cmd)
        if action in {"services", "deployments"}:
            continue
        if action == "history":
            cmd.add_argument("--deployment")
            cmd.add_argument(
                "--state",
                choices=["queued", "observing", "running", "succeeded", "failed", "interrupted", "corrupt"],
            )
            cmd.add_argument("--limit", type=positive_int, default=20)
            continue
        if action == "doctor":
            cmd.add_argument("deployment", nargs="?", help="Sem argumento, diagnostica todos os deployments.")
            continue
        cmd.add_argument("host" if action == "fingerprint" else "deployment")
        if action == "plan":
            cmd.add_argument("--action", choices=["install", "verify", "uninstall"], default="install")
        elif action != "fingerprint":
            cmd.set_defaults(action=action)
        if action == "uninstall":
            cmd.add_argument("--yes", action="store_true", help="Autoriza remoção; o padrão apaga dados.")
            cmd.add_argument(
                "--keep-data", action="store_true", help="Preserva dados e segredos para reinstalação."
            )
        if action == "status":
            cmd.add_argument("--run", required=True, help="ID de 24 caracteres hexadecimais.")
            cmd.add_argument("--follow", action="store_true", help="Acompanha até obter resultado terminal.")
        if action in {"install", "verify", "uninstall", "status"}:
            cmd.add_argument("--ask-pass", action="store_true", help="Solicita a senha SSH sem exibir.")
            cmd.add_argument("--ask-sudo-pass", action="store_true", help="Solicita a senha sudo sem exibir.")
            cmd.add_argument(
                "--timeout", type=positive, default=3600, help="Limite do acompanhamento em segundos (3600)."
            )
            cmd.add_argument(
                "--poll-interval", type=positive, default=2, help="Intervalo entre consultas em segundos (2)."
            )
            cmd.add_argument(
                "--command-timeout",
                type=positive,
                default=60,
                help="Timeout de cada comando SSH em segundos (60).",
            )
            if action != "status":
                cmd.add_argument(
                    "--no-follow",
                    action="store_true",
                    help="Retorna após iniciar o job; use status para acompanhar.",
                )
        if action in {"fingerprint", "install", "verify", "uninstall", "status"}:
            cmd.add_argument(
                "--connect-timeout",
                type=positive,
                default=15,
                help="Timeout de conexão/autenticação SSH em segundos (15).",
            )
    return command


def logging_overrides(args):
    values = {}
    level = args.log_level or os.environ.get("SDLC_LOG_LEVEL")
    if level:
        values.update(console_level=level, file_level=level)
    if args.verbose:
        values["console_level"] = "DEBUG"
    if args.verbose > 1:
        values["logger_levels"] = {"paramiko": "DEBUG"}
    if args.quiet or args.no_console_log:
        values["console_enabled"] = False
    if args.no_log_file:
        values["file_enabled"] = False
    if args.no_color or "NO_COLOR" in os.environ or args.json_output:
        values["console_color"] = "never"
    if args.json_output:
        values["console_format"] = "json"
    if args.log_file:
        values["file_path"] = str(Path(args.log_file).expanduser().absolute())
    if args.log_format:
        values["file_format"] = args.log_format
    if args.remote_log_level:
        values["remote_level"] = args.remote_log_level
    return values


def doctor(args):
    checks = [dict(check="repository", state="ok", path=str(core.ROOT))]
    data = core.inventory(args.inventory)
    names = [args.deployment] if args.deployment else sorted(data["deployments"])
    if not names:
        checks.append(dict(check="deployments", state="failed", error="Inventário sem deployments."))
    for name in names:
        try:
            host, request, inputs = core.deployment(
                SimpleNamespace(inventory=args.inventory, deployment=name, action="install"), prepare=True
            )
            payload = core.bundle(request, inputs)
            if not host.get("host_key_sha256"):
                known = (
                    Path(host["known_hosts"]) if host.get("known_hosts") else Path.home() / ".ssh/known_hosts"
                )
                core.require(
                    known.is_file(), "Sem pin e sem known_hosts. Configure a confiança SSH antes de conectar."
                )
            checks.append(
                dict(
                    check=name,
                    state="ok",
                    service=request["service"],
                    host=host["address"],
                    payload_bytes=len(payload),
                    ssh_trust="pin" if host.get("host_key_sha256") else "known_hosts",
                )
            )
        except Exception as error:
            checks.append(dict(check=name, state="failed", error=str(error)))
    ok = all(check["state"] == "ok" for check in checks)
    return {
        "state": "succeeded" if ok else "failed",
        "checks": checks,
        "scope": "local; nenhuma conexão SSH/CDN foi feita",
    }, 0 if ok else 1


def execute(args):
    output, session = args.output, args.session
    if args.command == "services":
        native = [core.metadata(service) for service in core.CATALOG["native"]]
        rows = [
            (
                meta["service"],
                "pronto",
                meta["service_version"],
                meta["proxy_kind"],
                meta["public_port"],
                meta["backend_ports"],
            )
            for meta in native
        ]
        rows += [
            (service, "pendente", "—", reason, "—", "—")
            for service, reason in core.CATALOG["pending"].items()
        ]
        output.table(
            rows,
            ["Serviço", "Estado", "Versão", "Proxy / observação", "Porta pública", "Backend"],
            title="Serviços SDLC",
            data={"native": native, "pending": core.CATALOG["pending"]},
        )
        return 0
    if args.command == "deployments":
        data = core.inventory(args.inventory)
        records = []
        for name in sorted(data["deployments"]):
            host, request, _ = core.deployment(
                SimpleNamespace(inventory=args.inventory, deployment=name, action="verify")
            )
            records.append(
                dict(
                    deployment=name,
                    service=request["service"],
                    host=host["address"],
                    user=host["user"],
                    port=host.get("port", 22),
                )
            )
        output.table(
            [
                (row["deployment"], row["service"], row["host"], row["user"], str(row["port"]))
                for row in records
            ],
            ["Deployment", "Serviço", "Host", "Usuário", "SSH"],
            title="Destinos configurados",
            data={"deployments": records},
        )
        return 0
    if args.command == "doctor":
        data, code = doctor(args)
        output.result(data, "Diagnóstico local")
        return code
    if args.command == "history":
        records = history(args.state_dir, deployment=args.deployment, state=args.state, limit=args.limit)
        output.table(
            [
                (
                    row.get("run", "—"),
                    row.get("deployment") or "—",
                    row.get("service", "—"),
                    row.get("state", "—"),
                    row.get("phase", "—"),
                )
                for row in records
            ],
            ["Execução", "Deployment", "Serviço", "Estado", "Fase"],
            title="Histórico local",
            data={"runs": records},
        )
        return 0
    if args.command == "fingerprint":
        host = core.inventory(args.inventory)["hosts"].get(args.host)
        core.require(host, "Host ausente no inventário.")
        session.context["host"] = host["address"]
        paramiko = ssh_module()
        with socket.create_connection(
            (host["address"], host.get("port", 22)), timeout=args.connect_timeout
        ) as connection:
            transport = paramiko.Transport(connection)
            try:
                transport.start_client(timeout=args.connect_timeout)
                output.result(
                    dict(
                        host=args.host,
                        address=host["address"],
                        host_key_sha256=fingerprint(transport.get_remote_server_key()),
                        trust="Chave observada sem autenticação. Confira pela console da VM antes de fixar no inventário.",
                    )
                )
            finally:
                transport.close()
        return 0
    if args.command == "uninstall":
        core.require(args.yes, "Desinstalação exige --yes; --keep-data preserva dados e segredos.")
    if args.command == "status":
        core.require(core.RUN_ID.fullmatch(args.run), "ID de execução inválido.")
    host, request, inputs = core.deployment(args, prepare=args.command in {"plan", "install"})
    session.context.update(service=request["service"], host=host["address"])
    if "inputs/db_password_file" in inputs:
        session.redactor.add(inputs["inputs/db_password_file"].decode("utf-8").strip())
    if args.command == "plan":
        output.result(
            dict(
                deployment=args.deployment,
                host=host["address"],
                user=host["user"],
                port=host.get("port", 22),
                request=request,
                uploads={name: hashlib.sha256(value).hexdigest() for name, value in inputs.items()},
            ),
            "Plano local",
        )
        return 0
    payload = core.bundle(request, inputs) if args.command != "status" else None
    remote = None
    try:
        remote = Remote(host, args)
        remote.check_sudo()
        session.event("sudo_ready", "Privilégios sudo verificados.", level="DEBUG")
        if args.command == "status":
            session.context["run"] = args.run
            if args.follow:
                code = follow(remote, request["service"], args.run, args)
                output.result(args.last_result)
                return code
            result = remote.status(request["service"], args.run)
            result = session.redactor.data(result)
            # Consulta simples preserva o recibo local sem truncar operation.log.
            save_report(
                args,
                args.run,
                {
                    key: value
                    for key, value in result.items()
                    if key not in {"output", "offset", "log_complete"}
                },
            )
            output.result(result)
            return 1 if result["state"] in {"failed", "interrupted"} else 0
        result = remote.upload(request, payload)
        run = result["run"]
        session.context["run"] = run
        save_report(args, run, result)
        session.event(
            "run_launched",
            f"Execução {run} iniciada. Use status {args.deployment} --run {run} para reconectar.",
        )
        if args.no_follow:
            output.result(
                {**result, "resume": f"sdlc-installer status {args.deployment} --run {run} --follow"}
            )
            return 0
        code = follow(remote, request["service"], run, args)
        output.result(args.last_result)
        return code
    finally:
        if remote:
            remote.close()


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    # Captura antes de o transporte consumir as variáveis de ambiente.
    redactor = Redactor()
    session = None
    output = None
    try:
        args = parser().parse_args(argv)
        core.ROOT = repository(args.repo)
        args.inventory = (
            str(Path(args.inventory).expanduser().absolute())
            if args.inventory
            else str(core.ROOT / "installer/inventory.json")
        )
        args.state_dir = (
            str(Path(args.state_dir).expanduser().absolute())
            if args.state_dir
            else str(core.ROOT / "installer/.state")
        )
        config = settings(
            args.log_config or os.environ.get("SDLC_LOG_CONFIG"),
            state_dir=args.state_dir,
            overrides=logging_overrides(args),
        )
        redactor = Redactor(config)
        output = Output(
            json_output=args.json_output, quiet=args.quiet, color=config.console_color, redactor=redactor
        )
        context = dict(
            command=args.command,
            deployment=getattr(args, "deployment", None),
            service=None,
            host=None,
            run=getattr(args, "run", None),
            phase="local",
        )
        session = LogSession(config, redactor, context)
        args.output, args.session = output, session
        with session:
            session.event("command_start", "Comando iniciado.", level="DEBUG")
            try:
                code = execute(args)
            except KeyboardInterrupt:
                code = 130
                message = (
                    "Acompanhamento interrompido; operações já iniciadas continuam na VM. Consulte status."
                )
                session.event("command_interrupted", message, level="WARNING")
                output.result(
                    {"state": "interrupted", "error": message, "run": context.get("run"), "exit_code": code}
                )
            except Exception as error:
                code = 1
                message = redactor.text(error)
                session.event("command_error", message, level="ERROR")
                # Stack trace em DEBUG, sem variáveis locais e com a mesma redação.
                session.logger.debug("Detalhes do erro", exc_info=True, extra={"event": "exception"})
                output.result(
                    {"state": "failed", "error": message, "run": context.get("run"), "exit_code": code}
                )
            session.event(
                "command_complete",
                f"Comando terminou com código {code}.",
                level="DEBUG",
                duration_ms=round((time.monotonic() - session.start) * 1000),
            )
            return code
    except SystemExit as error:
        return error.code
    except Exception as error:
        code = 2 if isinstance(error, UsageError) else 1
        message = redactor.text(error)
        if "--json" in argv:
            print(json.dumps({"state": "failed", "error": message, "exit_code": code}, ensure_ascii=False))
        else:
            print("ERRO: " + message + "\nConsulte sdlc-installer --help.", file=sys.stderr)
        return code


def entrypoint():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
