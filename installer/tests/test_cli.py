import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from sdlc_installer import cli, core, operations
from sdlc_installer.logging_config import LogSession, Redactor, settings
from sdlc_installer.paths import repository

ROOT = Path(__file__).resolve().parents[2]


def call(capsys, tmp_path, *argv):
    code = cli.main(["--repo", str(ROOT), "--json", "--state-dir", str(tmp_path), *argv])
    output = capsys.readouterr()
    data = json.loads(output.out)
    # Qualquer evento em stderr também deve ser um documento JSON independente.
    for line in output.err.splitlines():
        json.loads(line)
    return code, data


def test_global_options_before_and_after_subcommand(capsys, tmp_path):
    for argv in (["--json", "services"], ["services", "--json"], ["--verbose", "services", "--no-color"]):
        args = cli.parser().parse_args(argv)
        assert args.command == "services"
    code, data = call(capsys, tmp_path, "services")
    assert code == 0 and len(data["native"]) == 10
    assert set(data["pending"]) == {"argocd", "harbor", "kubernetes"}


def test_invalid_arguments_and_config_fail_before_connect(capsys, tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "Remote", lambda *args: pytest.fail("Não deve conectar"))
    for arguments, expected in [
        (["status", "name", "--run", "../invalid"], 1),
        (["install", "name", "--timeout", "0"], 2),
        (["install", "name", "--poll-interval", "nan"], 2),
        (["uninstall", "name"], 1),
        (["unknown"], 2),
    ]:
        code, data = call(capsys, tmp_path, *arguments)
        assert code == expected and data["exit_code"] == expected
    path = tmp_path / "invalid.toml"
    path.write_text('[logging]\nrotation="invalid"')
    assert call(capsys, tmp_path, "services", "--log-config", str(path))[0] == 1


def test_repository_discovery_and_installed_package_override(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SDLC_REPO", str(ROOT))
    assert repository() == ROOT
    with pytest.raises(ValueError, match="inválido"):
        repository(str(tmp_path))
    monkeypatch.delenv("SDLC_REPO")
    monkeypatch.chdir(ROOT / "grafana/linux")
    assert repository() == ROOT


def test_deployments_and_doctor_do_not_connect(capsys, tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "Remote", lambda *args: pytest.fail("Não deve conectar"))
    path = ROOT / "installer/inventory.example.json"
    code, data = call(capsys, tmp_path, "deployments", "-i", str(path))
    assert code == 0 and len(data["deployments"]) == 2
    # Doctor selecionado inválido deve registrar a falha no diagnóstico.
    code, data = call(capsys, tmp_path, "doctor", "missing", "-i", str(path))
    assert code == 1 and data["checks"][-1]["state"] == "failed"


def test_history_corrupt_receipts_and_filters(capsys, tmp_path):
    args = SimpleNamespace(state_dir=tmp_path, deployment="grafana-lan")
    operations.save_report(args, "a" * 24, {"state": "succeeded", "service": "grafana"})
    operations.save_report(args, "b" * 24, {"state": "failed", "service": "grafana"})
    directory = tmp_path / "runs" / ("c" * 24)
    directory.mkdir()
    (directory / "result.json").write_text("{broken")
    code, data = call(capsys, tmp_path, "history", "--state", "failed")
    assert code == 0 and len(data["runs"]) == 1 and data["runs"][0]["run"] == "b" * 24
    assert len(operations.history(tmp_path, limit=2)) == 2
    assert operations.history(tmp_path)[0]["state"] == "corrupt"


def test_follow_redacts_secret_across_chunks_and_drains_terminal_log(tmp_path):
    config = settings(
        state_dir=tmp_path, overrides={"console_enabled": False, "redact_values": ["split-secret"]}
    )
    redactor = Redactor(config)
    args = SimpleNamespace(
        state_dir=tmp_path,
        deployment="vm",
        timeout=30,
        poll_interval=0.001,
        output=cli.Output(json_output=True, redactor=redactor),
    )
    replies = [
        dict(state="running", phase="install", output="split-", offset=6, log_complete=False),
        dict(state="failed", phase="verify", output="secret\nlast", offset=17, log_complete=False),
        dict(state="failed", phase="verify", output=" line\n", offset=23, log_complete=True),
    ]
    remote = SimpleNamespace(status=lambda *args: replies.pop(0), redact=lambda text: text)
    with LogSession(config, redactor, {"run": "a" * 24}) as args.session:
        assert operations.follow(remote, "grafana", "a" * 24, args) == 1
    log = (tmp_path / "runs" / ("a" * 24) / "operation.log").read_text()
    assert log == "[REDACTED]\nlast line\n"
    assert "split-secret" not in (tmp_path / "logs/installer.jsonl").read_text()


def test_detached_launch_and_status_return_exactly_one_json(capsys, tmp_path, monkeypatch):
    run = "d" * 24
    fake = SimpleNamespace(
        check_sudo=lambda: None,
        close=lambda: None,
        upload=lambda *args: {"run": run, "state": "queued"},
        status=lambda *args: {
            "run": run,
            "state": "succeeded",
            "output": "",
            "offset": 0,
            "log_complete": True,
        },
    )
    monkeypatch.setattr(
        core, "deployment", lambda *args, **kwargs: ({"address": "vm"}, {"service": "grafana"}, {})
    )
    monkeypatch.setattr(core, "bundle", lambda *args: b"payload")
    monkeypatch.setattr(cli, "Remote", lambda *args: fake)
    code, data = call(capsys, tmp_path, "install", "vm", "--no-follow")
    assert code == 0 and data["run"] == run and data["state"] == "queued"
    assert (tmp_path / "runs" / run / "result.json").is_file()
    code, data = call(capsys, tmp_path, "status", "vm", "--run", run)
    assert code == 0 and data["state"] == "succeeded"


def test_connection_failure_masks_secrets_even_before_constructor_returns(capsys, tmp_path, monkeypatch):
    monkeypatch.setenv("SDLC_SSH_PASSWORD", "ephemeral-secret")
    monkeypatch.setattr(
        core, "deployment", lambda *args, **kwargs: ({"address": "vm"}, {"service": "grafana"}, {})
    )
    monkeypatch.setattr(core, "bundle", lambda *args: b"payload")

    def failure(*args):
        raise RuntimeError("Connection failed ephemeral-secret")

    monkeypatch.setattr(cli, "Remote", failure)
    code, data = call(capsys, tmp_path, "verify", "vm")
    assert code == 1 and "ephemeral-secret" not in json.dumps(data)
    assert "ephemeral-secret" not in (tmp_path / "logs/installer.jsonl").read_text()


def test_quiet_and_no_log_file(capsys, tmp_path):
    assert call(capsys, tmp_path, "services", "--quiet", "--no-log-file")[0] == 0
    assert not (tmp_path / "logs").exists()
