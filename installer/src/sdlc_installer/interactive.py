"""Terminal guiado para operar os mesmos serviços e contratos da CLI."""

import getpass
import ipaddress
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from rich.panel import Panel
from rich.text import Text

from . import core, discovery, operations, registration, transport
from .presentation import Output

STATES = {
    "created": "Cadastrado",
    "updated": "Atualizado",
    "unchanged": "Cadastro mantido",
    "registered": "Cadastrado",
    "reachable": "SSH acessível",
    "unreachable": "Sem conexão",
    "tcp_only": "SSH não respondeu",
    "key_mismatch": "Chave divergente",
    "verified": "Conferida",
    "valid": "Válido",
    "pending": "Pendente",
    "unknown": "A conferir",
    "queued": "Na fila",
    "observing": "Em acompanhamento",
    "running": "Em execução",
    "succeeded": "Concluído",
    "failed": "Falhou",
    "interrupted": "Interrompido",
    "ok": "OK",
    "corrupt": "Recibo inválido",
    "active": "Ativo",
    "inactive": "Parado",
    "preserved": "Preservados",
    "removed": "Removidos",
}
PHASES = {
    "completed": "Finalizada",
    "preflight": "Conferindo requisitos",
    "install": "Instalando serviço",
    "verify": "Verificando serviço",
    "uninstall": "Removendo serviço",
    "queued": "Aguardando",
}
LABELS = {
    "unit": "Serviço",
    "backend": "Aplicação",
    "nginx": "Proxy Nginx",
    "ufw": "Firewall",
    "data": "Dados",
    "address": "Endereço",
    "user": "Usuário SSH",
    "port": "Porta SSH",
    "identity_file": "Chave privada",
    "known_hosts": "Arquivo de chaves confiáveis",
    "host_key_sha256": "Chave do servidor",
    "DOMAIN": "Domínio do serviço",
    "ACCESS_CIDRS": "Redes de acesso",
    "SSH_CIDRS": "Redes de acesso SSH",
    "ARTIFACT_BASE_URL": "Endereço do CDN",
    "ALLOW_HTTP_ARTIFACTS": "Permitir CDN por HTTP (true/false)",
    "TLS_CERT_PATH": "Certificado TLS",
    "TLS_KEY_PATH": "Chave privada TLS",
    "DB_HOST": "Servidor PostgreSQL",
    "DB_PORT": "Porta PostgreSQL",
    "DB_NAME": "Nome do banco",
    "DB_USER": "Usuário do banco",
    "DB_PASSWORD_FILE": "Arquivo com a senha do banco",
    "DB_CA_PATH": "Certificado CA do PostgreSQL",
    "DNS_RECORDS_PATH": "Arquivo dos registros DNS",
    "DNS_LISTEN_IPV4": "IP fixo do servidor DNS",
    "DNS_CIDRS": "Redes dos clientes DNS",
    "DNS_FORWARDERS": "Resolvedores externos (IPs separados por vírgula)",
}


def label(key):
    return LABELS.get(key, key.replace("_", " ").capitalize())


def literal(value):
    core.require(
        not re.search(r"[\s\"\'`$;\\]", value),
        "Use um valor literal, sem espaços, aspas ou expansão de shell.",
    )


def domain(value):
    core.require(
        len(value) <= 253
        and "." in value
        and all(re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", part) for part in value.split(".")),
        "Use um domínio em minúsculas, por exemplo grafana.suaempresa.com.",
    )


def cidrs(value):
    for item in value.split(","):
        ipaddress.ip_network(item, strict=True)


def port(value):
    core.require(value.isdecimal() and 1 <= int(value) <= 65535, "A porta precisa estar entre 1 e 65535.")


def username(value):
    core.require(re.fullmatch(r"[a-z_][a-z0-9_-]*", value), "Use um usuário SSH válido, por exemplo ubuntu.")


def local_file(value):
    return core.regular_file(Path(value).expanduser()).absolute()


class GuidedOutput(Output):
    def __init__(self, source):
        self.console, self.redactor = source.console, source.redactor
        self.json_output, self.quiet, self.last_result = False, source.quiet, None

    def message(self, value):
        self.console.print(Text(self.redactor.text(value) if self.redactor else str(value)))

    def summary(self, rows, title):
        self.table(rows, ["Informação", "Valor"], title=title, data={})

    def table(self, rows, columns, *, title, data):
        self.last_result = self.redactor.data(data) if self.redactor else data
        if title == "Hosts do inventário":
            columns = ["VM", "Usuário", "Estado", "Chave SSH", "Último login"]
            rows = [
                (
                    f"{host['host']}\n{host['address']}:{host['port']}",
                    host["user"],
                    host["state"],
                    host.get("ssh_trust", "—"),
                    host["ssh_login"],
                )
                for host in data["hosts"]
            ]
        rows = [
            tuple(
                STATES.get(str(item), str(item))
                if columns[index] in {"Estado", "Chave SSH", "Último login"}
                else item
                for index, item in enumerate(row)
            )
            for row in rows
        ]
        super().table(rows, columns, title=title, data=data)

    def result(self, data, title="Resultado"):
        data = self.redactor.data(data) if self.redactor else data
        self.last_result = data
        if "request" in data:
            request = data["request"]
            config = request.get("config", {})
            self.summary(
                [
                    ("Serviço", request["service"]),
                    ("Versão", request["metadata"]["service_version"]),
                    ("VM", f"{data['host']}:{data['port']}"),
                    ("Usuário SSH", data["user"]),
                    *[
                        (label(key), config[key])
                        for key in ("DOMAIN", "ACCESS_CIDRS", "SSH_CIDRS", "ARTIFACT_BASE_URL")
                        if key in config
                    ],
                    ("Arquivos validados", str(len(data.get("uploads", {})))),
                ],
                "Revisão da instalação",
            )
        elif isinstance(data.get("checks"), list):
            self.table(
                [
                    (row["check"], STATES.get(row["state"], row["state"]), row.get("error", "Validado"))
                    for row in data["checks"]
                ],
                ["Verificação", "Estado", "Detalhes"],
                title=title,
                data=data,
            )
        else:
            rows = [("Resultado", STATES.get(data.get("state"), data.get("state", "Consultado")))]
            rows += [
                (name, PHASES.get(data[key], data[key]) if key == "phase" else data[key])
                for key, name in (
                    ("host", "VM"),
                    ("address", "Endereço"),
                    ("run", "Execução"),
                    ("phase", "Etapa"),
                )
                if data.get(key)
            ]
            if data.get("deployments"):
                rows.append(("Instalação preparada", ", ".join(data["deployments"])))
            self.summary(rows, title)
        # summary/table também capturam dados; preserve o resultado da operação.
        self.last_result = data
        if data.get("error"):
            self.message("Não foi possível concluir: " + str(data["error"]))
        for pending in data.get("pending", []):
            self.message("Ainda falta: " + pending)
        if isinstance(data.get("checks"), dict):
            self.summary(
                [(label(key), STATES.get(str(value), str(value))) for key, value in data["checks"].items()],
                "Verificações do serviço",
            )
            self.last_result = data


class Wizard:
    def __init__(self, args):
        self.args = args
        self.output = GuidedOutput(args.output)
        self.ssh_passwords = {}

    def password_key(self, name, host):
        return (str(Path(self.args.inventory).absolute()), name, *core.ssh_context(host).values())

    def saved_password(self, deployment):
        data = self.data()
        name = data["deployments"][deployment]["host"]
        host = data["hosts"][name]
        return self.ssh_passwords.get(self.password_key(name, host))

    def heading(self, title, detail=""):
        self.output.console.print(
            Panel(
                Text(self.args.session.redactor.text(title + ("\n" + detail if detail else ""))),
                title="Instalador SDLC",
            )
        )

    def ask(self, text, *, default="", required=True, validate=None):
        while True:
            answer = input(f"{text}" + (f" [{default}]" if default != "" else "") + ": ").strip()
            answer = answer or str(default)
            if not answer and required:
                self.output.message("Preencha este campo ou use Ctrl+C para voltar.")
                continue
            try:
                if answer and validate:
                    validate(answer)
                return answer
            except (ValueError, core.InstallerError) as error:
                self.output.message(str(error))

    def confirm(self, text, *, default=False):
        while True:
            answer = input(text + (" [S/n]: " if default else " [s/N]: ")).strip().lower()
            if not answer:
                return default
            if answer in {"s", "sim", "y", "yes"}:
                return True
            if answer in {"n", "não", "nao", "no"}:
                return False
            self.output.message("Responda s para sim ou n para não.")

    def choose(self, title, options, *, back="Voltar"):
        self.output.message(title)
        for index, (_, description) in enumerate(options, 1):
            self.output.message(f"  {index}. {description}")
        self.output.message("  0. " + back)
        while True:
            answer = input("Escolha: ").strip()
            if answer.isdecimal() and 0 <= int(answer) <= len(options):
                return options[int(answer) - 1][0] if int(answer) else None
            self.output.message(f"Digite um número entre 0 e {len(options)}.")

    def data(self):
        return registration.read_inventory(self.args.inventory)

    def command(self, *argv):
        from . import cli

        operation = cli.parser().parse_args(list(argv))
        # Reuse sessão, destino dos recibos e opções de saída da sessão aberta.
        for name in cli.DEFAULTS:
            if name not in {"connect_timeout", "command_timeout", "timeout", "poll_interval"}:
                setattr(operation, name, getattr(self.args, name))
        operation.output, operation.session = self.output, self.args.session
        if operation.command in {"install", "verify", "uninstall", "status"}:
            password = self.saved_password(operation.deployment)
            if password is not None:
                operation.ssh_password, operation.password_only, operation.ask_pass = password, True, False
        context = self.args.session.context
        previous = dict(context)
        context.update(
            command=operation.command,
            deployment=getattr(operation, "deployment", None),
            service=None,
            host=None,
            run=getattr(operation, "run", None),
            phase="local",
        )
        self.output.last_result = None
        started = time.monotonic()
        try:
            self.args.session.event("command_start", "Etapa iniciada.", level="DEBUG")
            code = cli.execute(operation)
            return code, self.output.last_result
        except KeyboardInterrupt:
            if context.get("run"):
                self.output.message(
                    "Acompanhamento interrompido. A execução já iniciada continua na VM; retome pelo histórico."
                )
            else:
                self.output.message("Etapa interrompida.")
            return 130, None
        except Exception as error:
            self.args.session.event("command_error", str(error), level="ERROR")
            self.output.message(str(error))
            if context.get("run"):
                self.output.message("Consulte esta execução no histórico antes de iniciar outra operação.")
            return 1, None
        finally:
            self.args.session.event(
                "command_complete",
                "Etapa encerrada.",
                level="DEBUG",
                duration_ms=round((time.monotonic() - started) * 1000),
            )
            context.clear()
            context.update(previous)

    def select_host(self):
        data = self.data()
        if not data["hosts"]:
            self.output.message("Primeiro, cadastre as VMs.")
            self.add_hosts()
            data = self.data()
        if not data["hosts"]:
            return None
        return self.choose(
            "Selecione a VM",
            [
                (name, f"{name} — {host['address']}:{host.get('port', 22)} ({host['user']})")
                for name, host in sorted(data["hosts"].items())
            ],
        )

    def select_deployment(self):
        data = self.data()
        if not data["deployments"]:
            self.output.message("Prepare um serviço antes de operar a instalação.")
            return self.prepare_service()
        return self.choose(
            "Selecione a instalação",
            [
                (name, f"{item['service']} em {item['host']} — {name}")
                for name, item in sorted(data["deployments"].items())
            ],
        )

    def add_hosts(self):
        self.heading(
            "Etapa 1 de 4 · Cadastrar VMs",
            "Informe os IPs ou nomes DNS. Enter aceita os valores sugeridos; Ctrl+C volta ao menu.",
        )

        def addresses(value):
            values = re.split(r"[\s,]+", value)
            core.require(
                all("/" not in item for item in values), "Informe IPs ou nomes DNS, sem faixa de rede."
            )
            discovery.targets(values)

        values = discovery.targets(
            re.split(
                r"[\s,]+", self.ask("IPs ou nomes DNS, separados por espaço ou vírgula", validate=addresses)
            )
        )
        original = registration.snapshot(self.args.inventory)
        data = self.data()
        users = {host["user"] for host in data["hosts"].values()}
        ports = {host.get("port", 22) for host in data["hosts"].values()}
        user = self.ask(
            "Usuário SSH dessas VMs",
            default=next(iter(users)) if len(users) == 1 else "ubuntu",
            validate=username,
        )
        ssh_port = int(
            self.ask("Porta SSH", default=next(iter(ports)) if len(ports) == 1 else 22, validate=port)
        )
        rows = []
        for address in values:
            names = [
                name
                for name, host in data["hosts"].items()
                if host["address"] == address and host.get("port", 22) == ssh_port
            ]
            name = (
                names[0]
                if names
                else "host-" + address.replace(":", "-") + (f"-ssh{ssh_port}" if ssh_port != 22 else "")
            )
            host = dict(data["hosts"].get(name, {}))
            host.update(address=address, user=user, port=ssh_port)
            registration.put(data["hosts"], name, host, True)
            rows.append((name, address, user, str(ssh_port)))
        core.validate_inventory(data)
        self.output.table(
            rows,
            ["Nome", "Endereço", "Usuário SSH", "Porta"],
            title="VMs para cadastrar ou atualizar",
            data={},
        )
        if self.confirm("Salvar o cadastro e testar as conexões?", default=True):
            registration.save_inventory(self.args.inventory, data, original)
            self.args.session.event("inventory_registered", "VMs cadastradas pelo menu.")
            _, result = self.command("hosts", *(row[0] for row in rows), "--check")
            self.output.message("Cadastro salvo. VMs sem conexão podem ser testadas novamente pelo menu.")
            available = [row for row in (result or {}).get("hosts", []) if row.get("fingerprint")]
            if available and self.confirm(
                "Validar login e registrar as chaves das VMs acessíveis agora?", default=True
            ):
                for row in available:
                    self.server_key(row["host"], row)
                self.command("hosts", *(row[0] for row in rows), "--check")

    def effective_config(self, item):
        base = Path(self.args.inventory).parent
        return {**core.env_file(core.local_path(base, item["config"])), **item.get("overrides", {})}

    def network_default(self):
        values = set()
        for item in self.data()["deployments"].values():
            try:
                values.add(self.effective_config(item)["ACCESS_CIDRS"])
            except (core.InstallerError, KeyError):
                continue
        return next(iter(values)) if len(values) == 1 else ""

    def advanced(self, config, overrides, inputs, manifest):
        while True:
            fields = [key for key in config if key not in {"MANIFEST_PATH", "SSH_PORT"}]
            selected = self.choose(
                "Ajustes adicionais",
                [
                    ("manifest", "Manifesto dos artefatos — " + str(manifest)),
                    *[
                        (
                            key,
                            label(key)
                            + " — "
                            + (
                                str(inputs.get(key, "não informado"))
                                if key in core.FILE_KEYS
                                else str(overrides.get(key, config[key]))
                            ),
                        )
                        for key in fields
                    ],
                ],
                back="Concluir ajustes",
            )
            if selected is None:
                return manifest
            if selected == "manifest":
                manifest = local_file(
                    self.ask("Arquivo de manifesto", default=str(manifest), validate=local_file)
                )
            elif selected in core.FILE_KEYS:
                if (
                    selected in {"TLS_CERT_PATH", "TLS_KEY_PATH"}
                    and selected in inputs
                    and self.confirm("Remover os certificados TLS e usar certificado autoassinado?")
                ):
                    for key in ("TLS_CERT_PATH", "TLS_KEY_PATH"):
                        inputs.pop(key, None)
                        overrides[key] = ""
                else:
                    inputs[selected] = local_file(
                        self.ask(label(selected), default=str(inputs.get(selected, "")), validate=local_file)
                    )
            else:
                validator = (
                    domain if selected == "DOMAIN" else cidrs if selected.endswith("CIDRS") else literal
                )
                overrides[selected] = self.ask(
                    label(selected),
                    default=overrides.get(selected, config[selected]),
                    required=False,
                    validate=validator,
                )

    def prepare_service(self):
        self.heading(
            "Etapa 2 de 4 · Preparar serviço",
            "Escolha VM e serviço; os caminhos e a porta SSH são preenchidos automaticamente.",
        )
        host_name = self.select_host()
        if not host_name:
            return None
        service = self.choose(
            "Serviço",
            [
                (name, f"{name} · {core.metadata(name)['service_version']}")
                for name in sorted(core.CATALOG["native"])
            ],
        )
        if not service:
            return None
        data = self.data()
        host = data["hosts"][host_name]
        matches = [
            (name, item)
            for name, item in data["deployments"].items()
            if item["host"] == host_name and item["service"] == service
        ]
        selected = (
            self.choose("Cadastro existente", [(name, name) for name, _ in matches])
            if len(matches) > 1
            else matches[0][0]
            if matches
            else f"{service}-{host_name}"
        )
        if not selected:
            return None
        item = data["deployments"].get(selected, {})
        base = Path(self.args.inventory).parent
        config_path = (
            core.local_path(base, item["config"]).absolute()
            if item
            else core.ROOT / core.metadata(service)["folder"] / "linux/.env.example"
        )
        config = core.env_file(config_path)
        core.require(
            set(config)
            <= set(core.env_file(core.ROOT / core.metadata(service)["folder"] / "linux/.env.example")),
            "Chave não suportada no .env; use arquivos para os segredos do serviço.",
        )
        overrides = dict(item.get("overrides", {}))
        core.require(
            set(overrides) <= set(config),
            "Parâmetro não suportado no cadastro; segredos devem ser informados por arquivo.",
        )
        effective = {**config, **overrides}
        inputs = {
            key: core.local_path(base, value).absolute() for key, value in item.get("files", {}).items()
        }
        overrides["DOMAIN"] = self.ask(
            "Domínio do serviço", default=effective["DOMAIN"] if item else "", validate=domain
        )
        overrides["ACCESS_CIDRS"] = self.ask(
            "Redes autorizadas, separadas por vírgula",
            default=effective["ACCESS_CIDRS"] if item else self.network_default(),
            validate=cidrs,
        )
        overrides["SSH_CIDRS"] = effective["SSH_CIDRS"] if item else overrides["ACCESS_CIDRS"]
        if service in {"keycloak", "sonarqube"}:
            self.output.message("Informe o banco PostgreSQL dedicado a este serviço.")
            for key in ("DB_HOST", "DB_PORT", "DB_NAME", "DB_USER"):
                overrides[key] = self.ask(
                    label(key),
                    default=effective[key] if item or key != "DB_HOST" else "",
                    validate=port if key == "DB_PORT" else literal,
                )
        if service == "technitium":

            def ipv4(value):
                ipaddress.IPv4Address(value)

            overrides["DNS_LISTEN_IPV4"] = self.ask(
                "IP fixo do servidor DNS",
                default=effective["DNS_LISTEN_IPV4"]
                if item
                else host["address"]
                if re.fullmatch(r"[0-9.]+", host["address"])
                else "",
                validate=ipv4,
            )
            overrides.setdefault("DNS_CIDRS", overrides["ACCESS_CIDRS"])
        for key in sorted(core.FILE_KEYS & set(config)):
            if effective[key] or key in inputs:
                inputs[key] = local_file(
                    self.ask(label(key), default=str(inputs.get(key, "")), validate=local_file)
                )
        manifest = (
            core.local_path(base, item["manifest"]).absolute()
            if item
            else core.ROOT / "artifacts/cdn/ubuntu26-amd64/manifest.json"
        )
        if not manifest.is_file():
            self.output.message(
                "O manifesto dos artefatos ainda não foi encontrado. Você pode indicar o arquivo nos ajustes ou completar depois."
            )
        if self.confirm("Ajustar CDN, certificados ou outros parâmetros?"):
            manifest = self.advanced(config, overrides, inputs, manifest)
        self.output.summary(
            [
                ("VM", f"{host_name} · {host['address']}:{host.get('port', 22)}"),
                ("Serviço", service),
                ("Nome da instalação", selected),
                *[(label(key), value) for key, value in overrides.items() if key != "SSH_PORT"],
                *[(label(key), str(value)) for key, value in inputs.items()],
                ("Manifesto", str(manifest)),
            ],
            "Revisão do cadastro",
        )
        if not self.confirm("Salvar esta preparação?", default=True):
            return None
        argv = [
            "register",
            host_name,
            "--address",
            host["address"],
            "--user",
            host["user"],
            "--service",
            service,
            "--deployment",
            selected,
            "--config",
            str(config_path),
            "--manifest",
            str(manifest),
            "--replace",
        ]
        for key, value in overrides.items():
            argv += ["--set", f"{key}={value}"]
        for key, value in inputs.items():
            argv += ["--file", f"{key}={value}"]
        code, _ = self.command(*argv)
        if code == 0:
            self.output.message("Próximo passo: escolha Revisar e instalar no menu principal.")
            return selected
        return None

    def save_host(self, name, host, *, original=None):
        original = original if original is not None else registration.snapshot(self.args.inventory)
        data = self.data()
        if host.get("port", 22) != data["hosts"][name].get("port", 22):
            for item in data["deployments"].values():
                if item["host"] == name:
                    item.setdefault("overrides", {})["SSH_PORT"] = str(host.get("port", 22))
        if core.ssh_validation_status(host) == "pending":
            host.pop("ssh_validation", None)
        self.ssh_passwords = {key: value for key, value in self.ssh_passwords.items() if key[1] != name}
        data["hosts"][name] = host
        core.validate_inventory(data)
        registration.save_inventory(self.args.inventory, data, original)
        self.args.session.event("ssh_configuration_saved", "Acesso SSH atualizado pelo menu.")
        self.output.message("Configuração SSH salva.")

    def server_key(self, name, observation=None):
        original = registration.snapshot(self.args.inventory)
        source = dict(self.data()["hosts"][name])
        host = core.inventory_host(self.args.inventory, name)
        observation = observation or discovery.probe(host, 3)
        if not observation.get("fingerprint"):
            self.output.message(
                "Não foi possível consultar a chave: " + observation.get("error", "SSH indisponível.")
            )
            return False
        core.require(
            observation.get("address", host["address"]) == host["address"]
            and observation.get("port", host.get("port", 22)) == host.get("port", 22),
            "O destino mudou; teste a conexão novamente.",
        )
        value = observation["fingerprint"]
        self.output.summary(
            [
                ("VM", name),
                ("Endereço", f"{host['address']}:{host.get('port', 22)}"),
                ("Usuário SSH", host["user"]),
                ("Chave apresentada", value),
            ],
            "Validar acesso SSH",
        )
        if observation.get("ssh_trust") == "mismatch" or source.get("host_key_sha256") not in {None, value}:
            self.output.message(
                "A chave mudou em relação à registrada: " + str(source.get("host_key_sha256", "known_hosts"))
            )
            if not self.confirm("Aceitar a chave apresentada e substituir a anterior após validar a senha?"):
                return False
        elif observation.get("ssh_trust") != "verified":
            if not self.confirm("Aceitar esta chave e registrar após validar a senha?", default=True):
                return False
        password = getpass.getpass(f"Senha SSH de {host['user']}@{host['address']} (sem eco): ")
        self.args.session.redactor.add(password)
        if not password:
            self.output.message("Senha vazia. Validação cancelada; a chave não foi alterada.")
            return False
        candidate = dict(host, host_key_sha256=value)
        args = SimpleNamespace(
            ask_pass=False,
            ask_sudo_pass=False,
            ask_key_passphrase=False,
            ssh_password=password,
            password_only=True,
            validation_only=True,
            connect_timeout=self.args.connect_timeout,
            session=self.args.session,
        )
        try:
            self.output.message("Testando autenticação SSH…")
            remote = transport.Remote(candidate, args)
            remote.close()
        except Exception as error:
            source["ssh_validation"] = dict(
                status="failed",
                checked_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                **core.ssh_context(source),
            )
            self.save_host(name, source, original=original)
            self.args.session.event("ssh_login_failed", str(error), level="ERROR", host=host["address"])
            self.output.message("Login falhou; a chave anterior foi mantida. " + str(error))
            return False
        source["host_key_sha256"] = value
        source["ssh_validation"] = dict(
            status="valid",
            checked_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            **core.ssh_context(source),
        )
        self.save_host(name, source, original=original)
        self.ssh_passwords[self.password_key(name, source)] = password
        self.args.session.event(
            "ssh_login_validated", "Login SSH validado e chave registrada.", host=host["address"]
        )
        self.output.message(
            "Válido: login confirmado e chave registrada. Senha disponível apenas nesta sessão."
        )
        return True

    def test_hosts(self):
        if not self.data()["hosts"]:
            self.add_hosts()
            return
        self.heading(
            "VMs e acesso SSH",
            "A lista testa DNS/SSH e mostra o último login registrado. Selecione uma VM para validar com senha.",
        )
        self.command("hosts", "--check")
        name = self.select_host()
        if not name:
            return
        while True:
            option = self.choose(
                "Acesso de " + name,
                [
                    ("key", "Validar login com senha e adicionar/substituir a chave do servidor"),
                    ("identity", "Selecionar chave privada ou agente SSH"),
                    ("user", "Alterar usuário e porta SSH"),
                    ("known", "Selecionar arquivo known_hosts confiável"),
                ],
            )
            if option is None:
                return
            if option == "key":
                if self.server_key(name):
                    self.command("hosts", name, "--check")
                continue
            source = dict(self.data()["hosts"][name])
            if option == "identity":
                method = self.choose(
                    "Autenticação por chave",
                    [
                        ("file", "Usar arquivo de chave privada"),
                        ("agent", "Usar agente/chaves padrão do SSH"),
                    ],
                )
                if method is None:
                    continue
                if method == "file":
                    path = local_file(self.ask("Arquivo de chave privada", validate=local_file))
                    source["identity_file"] = registration.relative_path(
                        Path(self.args.inventory).parent, path
                    )
                else:
                    source.pop("identity_file", None)
            elif option == "known":
                path = local_file(self.ask("Arquivo known_hosts confiável", validate=local_file))
                source["known_hosts"] = registration.relative_path(Path(self.args.inventory).parent, path)
                source.pop("host_key_sha256", None)
            else:
                source["user"] = self.ask("Usuário SSH", default=source["user"], validate=username)
                source["port"] = int(self.ask("Porta SSH", default=source.get("port", 22), validate=port))
            self.output.summary(
                [(label(key), str(value)) for key, value in source.items() if key != "ssh_validation"],
                "Acesso SSH",
            )
            if self.confirm("Salvar o acesso SSH?", default=True):
                self.save_host(name, source)
                self.command("hosts", name, "--check")

    def ensure_trust(self, deployment):
        name = self.data()["deployments"][deployment]["host"]
        self.output.message("Conferindo conexão e identidade da VM…")
        record = discovery.probe(core.inventory_host(self.args.inventory, name), 5)
        if record.get("ssh_trust") == "verified" and record["state"] == "reachable":
            return True
        if not record.get("fingerprint"):
            self.output.message("VM indisponível: " + record.get("error", record["state"]))
            return False
        self.output.message("Vamos aceitar a chave apresentada e validar o login com senha.")
        return self.server_key(name, record)

    def authentication(self, deployment):
        if self.saved_password(deployment) is not None:
            self.output.message("Usando o login com senha validado nesta sessão.")
            method = "password"
        else:
            method = self.choose(
                "Como entrar na VM?",
                [("key", "Chave privada ou agente SSH"), ("password", "Senha SSH (entrada sem eco)")],
            )
        if method is None:
            return None
        flags = ["--ask-pass"] if method == "password" else []
        if method == "key" and self.confirm("A chave privada possui senha?"):
            flags.append("--ask-key-passphrase")
        if self.confirm("O sudo exige senha?"):
            flags.append("--ask-sudo-pass")
        return flags

    def operate(self, action):
        self.heading(
            {
                "install": "Etapa 3 de 4 · Revisar e instalar",
                "verify": "Etapa 4 de 4 · Verificar instalação",
                "uninstall": "Remover serviço",
            }[action]
        )
        selected = self.select_deployment()
        if not selected:
            return
        if action == "install":
            if self.command("plan", selected)[0]:
                self.output.message(
                    "Complete os itens pendentes em Preparar serviço ou no acesso SSH e tente novamente."
                )
                return
            if not self.confirm("Iniciar esta instalação na VM indicada?"):
                return
        else:
            item = self.data()["deployments"][selected]
            host = self.data()["hosts"][item["host"]]
            self.output.summary(
                [("Serviço", item["service"]), ("VM", host["address"]), ("Instalação", selected)],
                "Destino da operação",
            )
        flags = []
        if action == "uninstall":
            mode = self.choose(
                "Dados do serviço",
                [
                    ("keep", "Remover serviço e preservar dados/segredos"),
                    ("delete", "Remover serviço, dados e segredos"),
                ],
            )
            if mode is None:
                return
            if self.ask(f"Digite {selected} para confirmar a remoção", required=False) != selected:
                self.output.message("Remoção cancelada.")
                return
            flags = ["--yes"] + (["--keep-data"] if mode == "keep" else [])
        if not self.ensure_trust(selected):
            return
        if action == "install" and self.command("doctor", selected)[0]:
            self.output.message("Resolva os itens do diagnóstico antes de instalar.")
            return
        auth = self.authentication(selected)
        if auth is not None:
            self.command(action, selected, *flags, *auth)

    def show_history(self):
        self.heading(
            "Histórico e acompanhamento", "Selecione uma execução para consultar ou continuar acompanhando."
        )
        self.command("history")
        records = operations.history(self.args.state_dir, limit=20)
        data = self.data()

        def compatible(item):
            deployment = data["deployments"].get(item.get("deployment"))
            if not deployment or not core.RUN_ID.fullmatch(item.get("run", "")):
                return False
            return not item.get("host") or item["host"] == data["hosts"][deployment["host"]]["address"]

        available = [item for item in records if compatible(item)]
        if not available:
            self.output.message("Nenhuma execução recuperável para este inventário.")
            return
        record = self.choose(
            "Execução",
            [
                (
                    item,
                    f"{item.get('deployment')} · {STATES.get(item['state'], item['state'])} · {item['run']}",
                )
                for item in available
            ],
        )
        if record is None or not self.ensure_trust(record["deployment"]):
            return
        auth = self.authentication(record["deployment"])
        if auth is None:
            return
        flags = (
            ["--follow"]
            if self.confirm("Acompanhar até terminar?", default=record["state"] not in operations.TERMINAL)
            else []
        )
        self.command("status", record["deployment"], "--run", record["run"], *flags, *auth)

    def change_inventory(self):
        path = Path(self.ask("Arquivo de inventário", default=self.args.inventory)).expanduser().absolute()
        registration.read_inventory(path)
        self.args.inventory = str(path)
        self.ssh_passwords.clear()
        self.output.message("Inventário selecionado: " + str(path))

    def run(self):
        actions = [
            (self.add_hosts, "1/4 · Cadastrar VMs por IP ou nome DNS"),
            (self.prepare_service, "2/4 · Preparar serviço"),
            (lambda: self.operate("install"), "3/4 · Revisar e instalar"),
            (lambda: self.operate("verify"), "4/4 · Verificar instalação"),
            (self.test_hosts, "Ver VMs, testar conexão e configurar SSH"),
            (self.show_history, "Histórico e acompanhar execuções"),
            (lambda: self.operate("uninstall"), "Remover serviço"),
            (self.change_inventory, "Trocar inventário"),
        ]
        while True:
            try:
                data = self.data()
                host_count, service_count = len(data["hosts"]), len(data["deployments"])
                detail = f"{host_count} {'VM' if host_count == 1 else 'VMs'} · {service_count} {'serviço preparado' if service_count == 1 else 'serviços preparados'}"
            except Exception as error:
                detail = "Inventário indisponível: " + str(error)
            self.heading(
                "Escolha o próximo passo",
                f"{detail}\nInventário: {Path(self.args.inventory).name}\nEnter aceita sugestões. Ctrl+C cancela um formulário.",
            )
            try:
                action = self.choose("Menu principal", actions, back="Sair")
            except (KeyboardInterrupt, EOFError):
                self.output.message("Sessão encerrada.")
                return 0
            if action is None:
                self.output.message("Sessão encerrada.")
                return 0
            try:
                action()
            except KeyboardInterrupt:
                self.output.message("Formulário cancelado. Alterações ainda não salvas foram descartadas.")
            except EOFError:
                self.output.message("Entrada encerrada. Voltando ao terminal.")
                return 0
            except Exception as error:
                self.args.session.event("menu_step_error", str(error), level="ERROR")
                self.output.message("Não foi possível concluir esta etapa: " + str(error))


def run(args):
    wizard = Wizard(args)
    try:
        return wizard.run()
    finally:
        wizard.ssh_passwords.clear()
