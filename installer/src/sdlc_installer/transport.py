"""Transporte SSH/SFTP com confiança explícita da chave do servidor."""

import base64
import getpass
import hashlib
import json
import os
import shlex
import sys
import time
import uuid
from importlib.resources import files
from pathlib import Path

from .core import RUN_ID, InstallerError, require


def fingerprint(key):
    return "SHA256:" + base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode().rstrip("=")


def ssh_module():
    try:
        import paramiko

        return paramiko
    except ImportError:
        raise InstallerError("Execute uv sync --project installer --locked.") from None


class Remote:
    def __init__(self, host, args):
        paramiko = ssh_module()
        self.host, self.args, self.secrets = host, args, []
        password_only = getattr(args, "password_only", False)
        validation_only = getattr(args, "validation_only", False)
        self.password = getattr(args, "ssh_password", None)
        if self.password is None:
            self.password = os.environ.pop("SDLC_SSH_PASSWORD", None)
        self.sudo_password = None if validation_only else os.environ.pop("SDLC_SUDO_PASSWORD", None)
        passphrase = None if password_only else os.environ.pop("SDLC_KEY_PASSPHRASE", None)
        if args.ask_pass:
            self.password = getpass.getpass("Senha SSH: ")
        if args.ask_sudo_pass:
            self.sudo_password = getpass.getpass("Senha sudo: ")
        if getattr(args, "ask_key_passphrase", False):
            passphrase = getpass.getpass("Senha da chave privada: ")
        self.secrets = [value for value in (self.password, self.sudo_password, passphrase) if value]
        self.session = getattr(args, "session", None)
        if self.session:
            self.session.redactor.add(*self.secrets)
            self.session.event("ssh_connect", "Conectando por SSH.", level="DEBUG")
        self.client = paramiko.SSHClient()
        pin = host.get("host_key_sha256")
        if not pin:
            known = Path(host["known_hosts"]) if host.get("known_hosts") else Path.home() / ".ssh/known_hosts"
            if known.is_file():
                self.client.load_host_keys(str(known))
        if pin:
            # O pin é a fonte de confiança e é conferido antes de autenticar.
            class Pinned(paramiko.MissingHostKeyPolicy):
                def missing_host_key(self, client, hostname, key):
                    require(fingerprint(key) == pin, "Fingerprint SSH diverge do inventário.")

            self.client.set_missing_host_key_policy(Pinned())
        else:
            self.client.set_missing_host_key_policy(paramiko.RejectPolicy())
        try:
            self.client.connect(
                host["address"],
                port=host.get("port", 22),
                username=host["user"],
                key_filename=None if password_only else host.get("identity_file"),
                password=self.password,
                passphrase=passphrase,
                allow_agent=self.password is None,
                look_for_keys=self.password is None,
                timeout=getattr(args, "connect_timeout", 15),
                auth_timeout=getattr(args, "connect_timeout", 15),
                banner_timeout=getattr(args, "connect_timeout", 15),
            )
            connected = self.client.get_transport()
            require(connected and connected.is_authenticated(), "Autenticação SSH não foi concluída.")
            if pin:
                require(
                    fingerprint(connected.get_remote_server_key()) == pin,
                    "Fingerprint SSH diverge do inventário.",
                )
            connected.set_keepalive(20)
        except BaseException:
            self.client.close()
            raise

    def redact(self, value):
        for secret in self.secrets:
            value = value.replace(secret, "[REDACTED]")
        return value

    def command(self, command, root=False):
        timeout = getattr(self.args, "command_timeout", 60)
        started = time.monotonic()
        if root and self.host["user"] != "root":
            command = ("sudo -S -p '' " if self.sudo_password else "sudo -n ") + command
        stdin, stdout, stderr = self.client.exec_command(command, timeout=timeout)
        if root and self.sudo_password and self.host["user"] != "root":
            stdin.write(self.sudo_password + "\n")
            stdin.flush()
        stdin.channel.shutdown_write()
        channel = stdout.channel
        out, err, deadline = bytearray(), bytearray(), time.monotonic() + timeout
        while True:
            if channel.recv_ready():
                out.extend(channel.recv(65536))
            if channel.recv_stderr_ready():
                err.extend(channel.recv_stderr(65536))
            if channel.exit_status_ready() and not channel.recv_ready() and not channel.recv_stderr_ready():
                break
            require(
                time.monotonic() < deadline,
                "Timeout do comando SSH; consulte status para operações já iniciadas.",
            )
            time.sleep(0.02)
        code = channel.recv_exit_status()
        result = self.redact(out.decode("utf-8", errors="replace"))
        require(
            code == 0,
            self.redact(err.decode("utf-8", errors="replace") or result or f"Comando remoto falhou: {code}"),
        )
        if self.session:
            self.session.event(
                "ssh_command_complete",
                "Comando SSH concluído.",
                level="DEBUG",
                duration_ms=round((time.monotonic() - started) * 1000),
            )
        return result

    def check_sudo(self):
        self.command("/usr/bin/true", root=True)

    def upload(self, request, payload):
        run = uuid.uuid4().hex[:24]
        if self.session:
            self.session.context["run"] = run
            self.session.event(
                "run_prepared", f"ID preparado: {run}. Recuperação: status {self.args.deployment} --run {run}"
            )
        else:
            print(f"ID preparado: {run}", file=sys.stderr, flush=True)
        sftp = self.client.open_sftp()
        stage = None
        try:
            home = sftp.normalize(".")
            stage = home + "/.sdlc-remote-" + run
            sftp.mkdir(stage, mode=0o700)
            for name, content in (
                ("payload.tar.gz", payload),
                (
                    "remote.py",
                    files("sdlc_installer")
                    .joinpath("resources/remote.py")
                    .read_bytes()
                    .replace(b"\r\n", b"\n"),
                ),
            ):
                with sftp.file(stage + "/" + name, "wb") as stream:
                    stream.write(content)
                sftp.chmod(stage + "/" + name, 0o600)
            command = '/usr/bin/env "SSH_CONNECTION=$SSH_CONNECTION" /usr/bin/python3 ' + shlex.quote(
                stage + "/remote.py"
            )
            command += (
                " launch --stage " + shlex.quote(stage) + " --service " + request["service"] + " --run " + run
            )
            result = json.loads(self.command(command, root=True))
            return result
        finally:
            # Somente os dois arquivos e o diretório exclusivo desta execução.
            if stage:
                for name in ("payload.tar.gz", "remote.py"):
                    try:
                        sftp.remove(stage + "/" + name)
                    except OSError:
                        pass
                try:
                    sftp.rmdir(stage)
                except OSError:
                    pass
            sftp.close()

    def status(self, service, run, offset=0):
        require(RUN_ID.fullmatch(run), "ID de execução inválido.")
        path = f"/opt/sdlc-remote/{service}/jobs/{run}/remote.py"
        command = (
            f"/usr/bin/python3 {shlex.quote(path)} status --service {service} --run {run} --offset {offset}"
        )
        return json.loads(self.command(command, root=True))

    def close(self):
        self.client.close()
