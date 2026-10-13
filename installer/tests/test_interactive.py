import json
import socket
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from sdlc_installer import cli, core, discovery, interactive, operations, transport
from sdlc_installer.logging_config import Redactor
from sdlc_installer.presentation import Output

ROOT = Path(__file__).resolve().parents[2]
PIN = "SHA256:" + "A" * 43


def invoke(capsys, tmp_path, monkeypatch, answers, *argv):
    iterator = iter(answers)

    def answer(prompt):
        print(prompt, end="")
        value = next(iterator)
        if isinstance(value, BaseException):
            raise value
        return value

    monkeypatch.setattr("builtins.input", answer)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    code = cli.main(
        [
            "--repo",
            str(ROOT),
            "--inventory",
            str(tmp_path / "inventory.json"),
            "--state-dir",
            str(tmp_path / "state"),
            "--no-color",
            "--no-console-log",
            *argv,
        ]
    )
    output = capsys.readouterr()
    return code, output.out + output.err


def inventory(tmp_path, *, pin=True, service=False):
    host = dict(address="vm.example.com", user="ubuntu", port=22)
    if pin:
        host["host_key_sha256"] = PIN
    item = dict(
        host="vm",
        service="grafana",
        config=str(ROOT / "grafana/linux/.env.example"),
        manifest="manifest.json",
        overrides=dict(DOMAIN="grafana.example.com", ACCESS_CIDRS="192.0.2.0/24", SSH_CIDRS="192.0.2.0/24"),
        files={},
    )
    data = dict(schema=1, hosts={"vm": host}, deployments={"grafana-vm": item} if service else {})
    (tmp_path / "inventory.json").write_text(json.dumps(data))
    (tmp_path / "manifest.json").write_text(
        json.dumps(
            dict(
                schema=1,
                platform="ubuntu26.04-amd64",
                artifacts=[
                    dict(name=name, sha256="0" * 64, size=1)
                    for name in ("ubuntu26-base.tar.gz", core.metadata("grafana")["artifact"])
                ],
            )
        )
    )
    return data


def probe(host, timeout):
    return dict(
        address=host["address"],
        port=host.get("port", 22),
        state="reachable",
        resolved_addresses=["192.0.2.4"],
        fingerprint=PIN,
        key_type="ssh-ed25519",
        ssh_trust="verified" if host.get("host_key_sha256") else "unknown",
    )


def test_no_subcommand_opens_menu_and_exit_does_not_create_inventory(capsys, tmp_path, monkeypatch):
    monkeypatch.setattr(discovery, "probe", lambda *args: pytest.fail("Não deve conectar"))
    code, text = invoke(capsys, tmp_path, monkeypatch, ["0"])
    assert code == 0 and "Menu principal" in text and "1/4" in text and "Sessão encerrada" in text
    assert not (tmp_path / "inventory.json").exists()


@pytest.mark.parametrize("argv", [[], ["--json"], ["menu"], ["menu", "--json"]])
def test_menu_requires_tty_and_preserves_automation_errors(capsys, tmp_path, monkeypatch, argv):
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    code = cli.main(["--repo", str(ROOT), *argv])
    result = capsys.readouterr()
    assert code == 2 and "terminal interativo" in result.out + result.err
    if "--json" in argv:
        assert json.loads(result.out)["exit_code"] == 2


def test_add_hosts_saves_offline_vm_without_trusting_observed_key(capsys, tmp_path, monkeypatch):
    def observe(host, timeout):
        return (
            probe(host, timeout)
            if host["address"] == "vm.example.com"
            else dict(state="unreachable", error="timeout")
        )

    monkeypatch.setattr(discovery, "probe", observe)
    code, text = invoke(
        capsys, tmp_path, monkeypatch, ["1", "vm.example.com,192.0.2.5", "ubuntu", "", "", "n", "0"]
    )
    assert code == 0 and "SSH acessível" in text and "Sem conexão" in text
    data = core.inventory(tmp_path / "inventory.json")
    assert len(data["hosts"]) == 2 and all("host_key_sha256" not in host for host in data["hosts"].values())


def test_form_retries_invalid_input_and_cancel_does_not_save(capsys, tmp_path, monkeypatch):
    code, text = invoke(
        capsys,
        tmp_path,
        monkeypatch,
        ["1", "192.0.2.0/24", "192.0.2.3", "UPPER", "ubuntu", "0", "22", "n", "0"],
    )
    assert (
        code == 0
        and "sem faixa de rede" in text
        and "usuário SSH válido" in text
        and "entre 1 e 65535" in text
    )
    assert not (tmp_path / "inventory.json").exists()


def test_ctrl_c_cancels_form_and_returns_to_menu(capsys, tmp_path, monkeypatch):
    code, text = invoke(capsys, tmp_path, monkeypatch, ["1", KeyboardInterrupt(), "0"])
    assert code == 0 and "Formulário cancelado" in text and text.count("Menu principal") == 2
    assert not (tmp_path / "inventory.json").exists()


def test_eof_exits_cleanly(capsys, tmp_path, monkeypatch):
    code, text = invoke(capsys, tmp_path, monkeypatch, [EOFError()])
    assert code == 0 and "Sessão encerrada" in text


def test_prepare_service_saves_defaults_and_preserves_host_trust(capsys, tmp_path, monkeypatch):
    inventory(tmp_path)
    code, text = invoke(
        capsys, tmp_path, monkeypatch, ["2", "1", "1", "grafana.example.com", "192.0.2.0/24", "n", "", "0"]
    )
    assert code == 0 and "Revisão do cadastro" in text and "Próximo passo" in text
    data = core.inventory(tmp_path / "inventory.json")
    item = data["deployments"]["grafana-vm"]
    assert item["overrides"]["DOMAIN"] == "grafana.example.com"
    assert item["overrides"]["ACCESS_CIDRS"] == item["overrides"]["SSH_CIDRS"] == "192.0.2.0/24"
    assert data["hosts"]["vm"]["host_key_sha256"] == PIN
    assert core.local_path(tmp_path, item["config"]).resolve() == ROOT / "grafana/linux/.env.example"


def test_prepare_service_cancel_keeps_existing_inventory(capsys, tmp_path, monkeypatch):
    inventory(tmp_path)
    path = tmp_path / "inventory.json"
    original = path.read_bytes()
    code, _ = invoke(
        capsys, tmp_path, monkeypatch, ["2", "1", "1", "grafana.example.com", "192.0.2.0/24", "n", "n", "0"]
    )
    assert code == 0 and path.read_bytes() == original


def test_unsupported_inline_secret_is_rejected_before_form_display(capsys, tmp_path, monkeypatch):
    data = inventory(tmp_path, service=True)
    config = tmp_path / "private.env"
    config.write_text(
        (ROOT / "grafana/linux/.env.example").read_text() + "\nDB_PASSWORD=never-show-this-secret\n"
    )
    data["deployments"]["grafana-vm"]["config"] = str(config)
    (tmp_path / "inventory.json").write_text(json.dumps(data))
    code, text = invoke(capsys, tmp_path, monkeypatch, ["2", "1", "1", "0"])
    assert code == 0 and "Chave não suportada" in text and "never-show-this-secret" not in text


def test_unsupported_override_secret_is_rejected_before_form_display(capsys, tmp_path, monkeypatch):
    data = inventory(tmp_path, service=True)
    data["deployments"]["grafana-vm"]["overrides"]["DB_PASSWORD"] = "never-show-this-secret"
    (tmp_path / "inventory.json").write_text(json.dumps(data))
    code, text = invoke(capsys, tmp_path, monkeypatch, ["2", "1", "1", "0"])
    assert code == 0 and "Parâmetro não suportado" in text and "never-show-this-secret" not in text


def test_history_does_not_offer_runs_from_another_host(capsys, tmp_path, monkeypatch):
    inventory(tmp_path, service=True)
    run = "d" * 24
    operations.save_report(
        SimpleNamespace(state_dir=tmp_path / "state", deployment="grafana-vm"),
        run,
        dict(state="succeeded", host="another-vm.example.com"),
    )
    monkeypatch.setattr(discovery, "probe", lambda *args: pytest.fail("Não deve sondar"))
    code, text = invoke(capsys, tmp_path, monkeypatch, ["6", "0"])
    assert code == 0 and "Nenhuma execução recuperável" in text


def test_install_cancel_never_connects(capsys, tmp_path, monkeypatch):
    inventory(tmp_path, service=True)
    monkeypatch.setattr(cli, "Remote", lambda *args: pytest.fail("Não deve autenticar"))
    monkeypatch.setattr(discovery, "probe", lambda *args: pytest.fail("Não deve sondar"))
    code, text = invoke(capsys, tmp_path, monkeypatch, ["3", "1", "n", "0"])
    assert code == 0 and "Revisão da instalação" in text and "Iniciar esta instalação" in text


def test_install_follows_existing_protocol_and_shows_native_checks(capsys, tmp_path, monkeypatch):
    inventory(tmp_path, service=True)
    monkeypatch.setattr(discovery, "probe", probe)
    authenticated = []
    run = "a" * 24
    fake = SimpleNamespace(
        check_sudo=lambda: None,
        close=lambda: None,
        redact=lambda value: value,
        upload=lambda *args: dict(run=run, state="queued"),
        status=lambda *args: dict(
            run=run,
            state="succeeded",
            phase="completed",
            checks=dict(unit="active", backend="HTTP 200"),
            output="",
            offset=0,
            log_complete=True,
        ),
    )

    def connect(host, args):
        authenticated.append((args.ask_pass, args.ask_sudo_pass, args.deployment))
        return fake

    monkeypatch.setattr(cli, "Remote", connect)
    code, text = invoke(capsys, tmp_path, monkeypatch, ["3", "1", "s", "2", "s", "0"])
    assert code == 0 and authenticated == [(True, True, "grafana-vm")]
    assert "Concluído" in text and "HTTP 200" in text and "Verificações do serviço" in text
    result = json.loads((tmp_path / "state/runs" / run / "result.json").read_text())
    assert result["state"] == "succeeded" and result["deployment"] == "grafana-vm"
    events = [json.loads(line) for line in (tmp_path / "state/logs/installer.jsonl").read_text().splitlines()]
    assert any(event["command"] == "install" and event["run"] == run for event in events)


def test_ssh_key_is_saved_after_password_login_without_manual_fingerprint(capsys, tmp_path, monkeypatch):
    inventory(tmp_path, pin=False)
    monkeypatch.setattr(discovery, "probe", probe)
    closed = []
    monkeypatch.setattr(interactive.getpass, "getpass", lambda prompt: "login-secret")

    def connect(host, args):
        assert host["host_key_sha256"] == PIN
        assert args.ssh_password == "login-secret" and args.password_only and args.validation_only
        assert not args.ask_sudo_pass
        assert "host_key_sha256" not in core.inventory(tmp_path / "inventory.json")["hosts"]["vm"]
        return SimpleNamespace(close=lambda: closed.append(True))

    monkeypatch.setattr(transport, "Remote", connect)
    code, text = invoke(capsys, tmp_path, monkeypatch, ["5", "1", "1", "s", "0", "0"])
    assert code == 0 and "Válido" in text and "Fingerprint conferido" not in text and closed == [True]
    host = core.inventory(tmp_path / "inventory.json")["hosts"]["vm"]
    assert host["host_key_sha256"] == PIN and core.ssh_validation_status(host) == "valid"
    assert datetime.fromisoformat(host["ssh_validation"]["checked_at"]).tzinfo is not None
    for path in (tmp_path / "inventory.json", tmp_path / "state/logs/installer.jsonl"):
        assert "login-secret" not in path.read_text()


def test_unknown_key_deferred_does_not_authenticate(capsys, tmp_path, monkeypatch):
    inventory(tmp_path, pin=False, service=True)
    monkeypatch.setattr(discovery, "probe", probe)
    monkeypatch.setattr(cli, "Remote", lambda *args: pytest.fail("Não deve autenticar"))
    monkeypatch.setattr(interactive.getpass, "getpass", lambda prompt: pytest.fail("Não deve pedir senha"))
    code, text = invoke(capsys, tmp_path, monkeypatch, ["3", "1", "s", "n", "0"])
    assert code == 0 and "validar o login" in text
    assert "host_key_sha256" not in core.inventory(tmp_path / "inventory.json")["hosts"]["vm"]


@pytest.mark.parametrize("accepted", [False, True])
def test_changed_key_needs_acceptance_and_is_replaced_only_after_login(
    capsys, tmp_path, monkeypatch, accepted
):
    data = inventory(tmp_path)
    previous = "SHA256:" + "B" * 43
    data["hosts"]["vm"]["host_key_sha256"] = previous
    (tmp_path / "inventory.json").write_text(json.dumps(data))
    monkeypatch.setattr(discovery, "probe", probe)
    connected = []

    def connect(host, args):
        assert core.inventory(tmp_path / "inventory.json")["hosts"]["vm"]["host_key_sha256"] == previous
        assert host["host_key_sha256"] == PIN
        connected.append(True)
        return SimpleNamespace(close=lambda: None)

    monkeypatch.setattr(transport, "Remote", connect)
    monkeypatch.setattr(
        interactive.getpass,
        "getpass",
        lambda prompt: "login-secret" if accepted else pytest.fail("Não deve pedir senha"),
    )
    code, text = invoke(capsys, tmp_path, monkeypatch, ["5", "1", "1", "s" if accepted else "", "0", "0"])
    host = core.inventory(tmp_path / "inventory.json")["hosts"]["vm"]
    assert code == 0 and "A chave mudou" in text
    assert host["host_key_sha256"] == (PIN if accepted else previous)
    assert connected == ([True] if accepted else [])
    assert core.ssh_validation_status(host) == ("valid" if accepted else "pending")


@pytest.mark.parametrize("pin", [False, True])
def test_failed_password_login_keeps_key_and_redacts_error(capsys, tmp_path, monkeypatch, pin):
    before = inventory(tmp_path, pin=pin)["hosts"]["vm"]
    monkeypatch.setattr(discovery, "probe", probe)
    monkeypatch.setattr(interactive.getpass, "getpass", lambda prompt: "wrong-login-secret")

    def connect(host, args):
        raise RuntimeError("Senha recusada: " + args.ssh_password)

    monkeypatch.setattr(transport, "Remote", connect)
    answers = ["5", "1", "1", *([] if pin else ["s"]), "0", "0"]
    code, text = invoke(capsys, tmp_path, monkeypatch, answers)
    host = core.inventory(tmp_path / "inventory.json")["hosts"]["vm"]
    assert code == 0 and "Login falhou" in text and "[REDACTED]" in text
    assert host.get("host_key_sha256") == before.get("host_key_sha256")
    assert core.ssh_validation_status(host) == "failed"
    assert "wrong-login-secret" not in text
    for path in (tmp_path / "inventory.json", tmp_path / "state/logs/installer.jsonl"):
        assert "wrong-login-secret" not in path.read_text()


def test_login_save_refuses_inventory_change_during_authentication(capsys, tmp_path, monkeypatch):
    inventory(tmp_path, pin=False)
    monkeypatch.setattr(discovery, "probe", probe)
    monkeypatch.setattr(interactive.getpass, "getpass", lambda prompt: "login-secret")
    path = tmp_path / "inventory.json"

    def connect(host, args):
        data = core.inventory(path)
        data["hosts"]["other"] = dict(address="192.0.2.9", user="ubuntu")
        path.write_text(json.dumps(data))
        return SimpleNamespace(close=lambda: None)

    monkeypatch.setattr(transport, "Remote", connect)
    code, text = invoke(capsys, tmp_path, monkeypatch, ["5", "1", "1", "s", "0", "0"])
    data = core.inventory(path)
    assert code == 0 and "Inventário mudou" in text and "other" in data["hosts"]
    assert "host_key_sha256" not in data["hosts"]["vm"] and "ssh_validation" not in data["hosts"]["vm"]


def test_empty_password_does_not_save_or_authenticate(capsys, tmp_path, monkeypatch):
    inventory(tmp_path, pin=False)
    path = tmp_path / "inventory.json"
    original = path.read_bytes()
    monkeypatch.setattr(discovery, "probe", probe)
    monkeypatch.setattr(interactive.getpass, "getpass", lambda prompt: "")
    monkeypatch.setattr(transport, "Remote", lambda *args: pytest.fail("Não deve autenticar"))
    code, text = invoke(capsys, tmp_path, monkeypatch, ["5", "1", "1", "s", "0", "0"])
    assert code == 0 and "Senha vazia" in text and path.read_bytes() == original


def test_onboarding_validates_reachable_host_and_leaves_offline_host_pending(capsys, tmp_path, monkeypatch):
    monkeypatch.setattr(
        discovery,
        "probe",
        lambda host, timeout: (
            probe(host, timeout)
            if host["address"] == "vm.example.com"
            else dict(state="unreachable", error="timeout")
        ),
    )
    monkeypatch.setattr(interactive.getpass, "getpass", lambda prompt: "login-secret")
    monkeypatch.setattr(transport, "Remote", lambda *args: SimpleNamespace(close=lambda: None))
    code, text = invoke(
        capsys, tmp_path, monkeypatch, ["1", "vm.example.com,192.0.2.5", "ubuntu", "", "", "", "", "0"]
    )
    hosts = core.inventory(tmp_path / "inventory.json")["hosts"]
    assert code == 0 and "Válido" in text and "Sem conexão" in text
    assert core.ssh_validation_status(hosts["host-vm.example.com"]) == "valid"
    assert core.ssh_validation_status(hosts["host-192.0.2.5"]) == "pending"


def test_validated_password_is_reused_for_operations_only_in_same_session(capsys, tmp_path, monkeypatch):
    inventory(tmp_path, pin=False, service=True)
    monkeypatch.setattr(discovery, "probe", probe)
    prompts = []
    monkeypatch.setattr(
        interactive.getpass, "getpass", lambda prompt: prompts.append(prompt) or "login-secret"
    )
    monkeypatch.setattr(transport, "Remote", lambda *args: SimpleNamespace(close=lambda: None))
    connected = []
    fake = SimpleNamespace(
        check_sudo=lambda: None,
        close=lambda: None,
        upload=lambda *args: dict(run="e" * 24, state="queued"),
        status=lambda *args: dict(
            run="e" * 24, state="succeeded", phase="completed", output="", offset=0, log_complete=True
        ),
    )

    def connect(host, args):
        connected.append((args.ssh_password, args.ask_pass, args.password_only))
        return fake

    monkeypatch.setattr(cli, "Remote", connect)
    code, text = invoke(capsys, tmp_path, monkeypatch, ["5", "1", "1", "", "0", "4", "1", "n", "0"])
    assert code == 0 and "validado nesta sessão" in text and len(prompts) == 1
    assert connected == [("login-secret", False, True)]
    code, text = invoke(capsys, tmp_path, monkeypatch, ["4", "1", "0", "0"])
    assert code == 0 and "Como entrar na VM?" in text and "validado nesta sessão" not in text


@pytest.mark.parametrize(
    "field,value",
    [
        ("address", "other.example.com"),
        ("port", 2222),
        ("user", "other"),
        ("host_key_sha256", "SHA256:" + "B" * 43),
    ],
)
def test_saved_login_validation_is_bound_to_destination_and_key(tmp_path, field, value):
    host = inventory(tmp_path)["hosts"]["vm"]
    host["ssh_validation"] = dict(
        status="valid", checked_at=datetime.now(timezone.utc).isoformat(), **core.ssh_context(host)
    )
    assert core.ssh_validation_status(host) == "valid"
    assert core.ssh_validation_status(host, "SHA256:" + "B" * 43) == "pending"
    host[field] = value
    assert core.ssh_validation_status(host) == "pending"


@pytest.fixture
def ssh_server(request):
    """Servidor local real: senha válida, recusada, autenticação parcial ou troca de chave."""
    paramiko = transport.ssh_module()
    first = paramiko.RSAKey.generate(2048)
    second = paramiko.RSAKey.generate(2048) if request.param == "rotate" else first
    calls, commands = [], []
    stopped = threading.Event()
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    listener.settimeout(0.05)

    class Server(paramiko.ServerInterface):
        def check_auth_password(self, username, password):
            calls.append((username, password))
            if request.param == "partial":
                return paramiko.AUTH_PARTIALLY_SUCCESSFUL
            return (
                paramiko.AUTH_SUCCESSFUL
                if request.param == "success" and password == "login-secret"
                else paramiko.AUTH_FAILED
            )

        def get_allowed_auths(self, username):
            return "password,publickey"

        def check_channel_request(self, kind, channel_id):
            commands.append(kind)
            return paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED

    def serve():
        connections = 0
        while not stopped.is_set():
            try:
                connection, _ = listener.accept()
            except socket.timeout:
                continue
            channel = paramiko.Transport(connection)
            connections += 1
            channel.add_server_key(second if connections > 2 else first)
            try:
                channel.start_server(server=Server())
                while channel.is_active() and not stopped.wait(0.01):
                    pass
            except (EOFError, OSError, paramiko.SSHException):
                pass
            finally:
                channel.close()
                connection.close()

    worker = threading.Thread(target=serve, daemon=True)
    worker.start()
    try:
        yield listener.getsockname()[1], transport.fingerprint(first), calls, commands
    finally:
        stopped.set()
        worker.join(timeout=5)
        listener.close()
        assert not worker.is_alive(), "Servidor SSH de teste não encerrou."


@pytest.mark.parametrize("ssh_server", ["success", "reject", "partial", "rotate"], indirect=True)
def test_password_validation_with_real_local_ssh(capsys, tmp_path, monkeypatch, ssh_server, request):
    port, pin, calls, commands = ssh_server
    data = inventory(tmp_path, pin=False)
    data["hosts"]["vm"].update(address="127.0.0.1", port=port, identity_file="missing-private-key")
    path = tmp_path / "inventory.json"
    path.write_text(json.dumps(data))
    monkeypatch.setattr(interactive.getpass, "getpass", lambda prompt: "login-secret")
    monkeypatch.setenv("SDLC_SUDO_PASSWORD", "unused-sudo-secret")
    monkeypatch.setenv("SDLC_KEY_PASSPHRASE", "unused-key-secret")
    code, text = invoke(capsys, tmp_path, monkeypatch, ["5", "1", "1", "s", "0", "0"])
    import os

    assert os.environ["SDLC_SUDO_PASSWORD"] == "unused-sudo-secret"
    assert os.environ["SDLC_KEY_PASSPHRASE"] == "unused-key-secret"
    host = core.inventory(path)["hosts"]["vm"]
    success = request.node.callspec.params["ssh_server"] == "success"
    assert code == 0 and core.ssh_validation_status(host) == ("valid" if success else "failed")
    assert host.get("host_key_sha256") == (pin if success else None)
    if request.node.callspec.params["ssh_server"] == "rotate":
        assert calls == [] and "Fingerprint SSH diverge" in text
    else:
        assert calls == [("ubuntu", "login-secret")]
    assert commands == []
    for file in (path, tmp_path / "state/logs/installer.jsonl"):
        content = file.read_text()
        assert all(
            secret not in content for secret in ("login-secret", "unused-sudo-secret", "unused-key-secret")
        )


def test_editing_ssh_port_updates_associated_deployments(capsys, tmp_path, monkeypatch):
    inventory(tmp_path, service=True)
    monkeypatch.setattr(discovery, "probe", probe)
    code, _ = invoke(capsys, tmp_path, monkeypatch, ["5", "1", "3", "", "2222", "s", "0", "0"])
    data = core.inventory(tmp_path / "inventory.json")
    assert code == 0 and data["hosts"]["vm"]["port"] == 2222
    assert data["deployments"]["grafana-vm"]["overrides"]["SSH_PORT"] == "2222"


def test_uninstall_requires_typed_confirmation(capsys, tmp_path, monkeypatch):
    inventory(tmp_path, service=True)
    monkeypatch.setattr(cli, "Remote", lambda *args: pytest.fail("Não deve remover"))
    monkeypatch.setattr(discovery, "probe", lambda *args: pytest.fail("Não deve sondar"))
    code, text = invoke(capsys, tmp_path, monkeypatch, ["7", "1", "2", "wrong", "0"])
    assert code == 0 and "Remoção cancelada" in text


def test_history_selects_run_without_typing_id(capsys, tmp_path, monkeypatch):
    inventory(tmp_path, service=True)
    monkeypatch.setattr(discovery, "probe", probe)
    run = "b" * 24
    operations.save_report(
        SimpleNamespace(state_dir=tmp_path / "state", deployment="grafana-vm"),
        run,
        dict(state="succeeded", service="grafana"),
    )
    selected = []
    fake = SimpleNamespace(
        check_sudo=lambda: None,
        close=lambda: None,
        status=lambda service, run_id: (
            selected.append(run_id)
            or dict(run=run_id, state="succeeded", output="", offset=0, log_complete=True)
        ),
    )
    monkeypatch.setattr(cli, "Remote", lambda *args: fake)
    code, text = invoke(capsys, tmp_path, monkeypatch, ["6", "1", "1", "n", "n", "n", "0"])
    assert code == 0 and selected == [run] and "Concluído" in text


def test_guided_output_redacts_errors_and_handles_checks_dictionary(capsys):
    redactor = Redactor()
    redactor.add("private-secret")
    output = interactive.GuidedOutput(Output(color="never", redactor=redactor))
    output.result(dict(state="failed", error="private-secret", checks=dict(unit="inactive")))
    text = capsys.readouterr().out
    assert "private-secret" not in text and "[REDACTED]" in text and "Parado" in text


def test_advanced_cdn_setting_is_saved_from_menu(capsys, tmp_path, monkeypatch):
    inventory(tmp_path)
    code, text = invoke(
        capsys,
        tmp_path,
        monkeypatch,
        [
            "2",
            "1",
            "1",
            "grafana.example.com",
            "192.0.2.0/24",
            "s",
            "5",
            "https://cdn.example.com/cdn",
            "0",
            "s",
            "0",
        ],
    )
    assert code == 0 and "Ajustes adicionais" in text
    assert (
        core.inventory(tmp_path / "inventory.json")["deployments"]["grafana-vm"]["overrides"][
            "ARTIFACT_BASE_URL"
        ]
        == "https://cdn.example.com/cdn"
    )


def test_encrypted_key_prompt_is_consumed_and_redacted(monkeypatch):
    class FakeClient:
        def load_host_keys(self, path):
            pass

        def set_missing_host_key_policy(self, policy):
            pass

        def connect(self, address, **options):
            assert options["passphrase"] == "encrypted-key-secret"
            raise RuntimeError("authentication stopped")

        def close(self):
            pass

    paramiko = SimpleNamespace(SSHClient=FakeClient, MissingHostKeyPolicy=object)
    monkeypatch.setattr(transport, "ssh_module", lambda: paramiko)
    monkeypatch.setattr(transport.getpass, "getpass", lambda text: "encrypted-key-secret")
    monkeypatch.delenv("SDLC_SSH_PASSWORD", raising=False)
    redactor = Redactor()
    session = SimpleNamespace(redactor=redactor, event=lambda *args, **kwargs: None)
    args = SimpleNamespace(ask_pass=False, ask_sudo_pass=False, ask_key_passphrase=True, session=session)
    with pytest.raises(RuntimeError):
        transport.Remote(dict(address="vm", user="ubuntu", host_key_sha256=PIN), args)
    assert redactor.text("encrypted-key-secret") == "[REDACTED]"
