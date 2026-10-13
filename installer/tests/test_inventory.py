import json
import os
import socket
from pathlib import Path
from types import SimpleNamespace

import pytest

from sdlc_installer import cli, core, discovery, registration
from sdlc_installer.transport import fingerprint

ROOT = Path(__file__).resolve().parents[2]


def call(capsys, tmp_path, *argv):
    code = cli.main(
        [
            "--repo",
            str(ROOT),
            "--json",
            "--no-log-file",
            "--state-dir",
            str(tmp_path / "state"),
            "--inventory",
            str(tmp_path / "inventory.json"),
            *argv,
        ]
    )
    output = capsys.readouterr()
    for line in output.err.splitlines():
        json.loads(line)
    return code, json.loads(output.out)


def host_args(address="vm.example.com"):
    return ["register", "vm", "--address", address, "--user", "ubuntu"]


def test_register_creates_inventory_and_repeat_preserves_bytes(capsys, tmp_path):
    code, result = call(capsys, tmp_path, *host_args())
    assert code == 0 and result["state"] == "created"
    path = tmp_path / "inventory.json"
    data = core.inventory(path)
    assert data["hosts"]["vm"] == dict(address="vm.example.com", user="ubuntu", port=22)
    assert data["deployments"] == {}
    previous = path.read_bytes()
    code, result = call(capsys, tmp_path, *host_args())
    assert code == 0 and result["state"] == "unchanged"
    assert path.read_bytes() == previous
    if os.name == "posix":
        assert path.stat().st_mode & 0o777 == 0o600


def test_register_refuses_collision_and_replace_preserves_other_entries(capsys, tmp_path):
    assert call(capsys, tmp_path, *host_args())[0] == 0
    assert call(capsys, tmp_path, "register", "other", "--address", "192.0.2.2", "--user", "root")[0] == 0
    path = tmp_path / "inventory.json"
    previous = path.read_bytes()
    code, result = call(capsys, tmp_path, *host_args("192.0.2.3"))
    assert code == 1 and "--replace" in result["error"]
    assert path.read_bytes() == previous
    assert call(capsys, tmp_path, *host_args("192.0.2.3"), "--replace")[0] == 0
    assert core.inventory(path)["hosts"]["other"]["user"] == "root"


@pytest.mark.parametrize(
    "arguments",
    [
        ["--port", "0"],
        ["--host-key-sha256", "invalid"],
        ["--service", "grafana"],
        ["--domain", "grafana.example.com"],
        ["--service", "grafana", "--domain", "BAD.EXAMPLE.COM", "--cidrs", "192.0.2.0/24"],
        ["--service", "grafana", "--domain", "grafana.example.com", "--cidrs", "192.0.2.5/24"],
        [
            "--service",
            "grafana",
            "--domain",
            "grafana.example.com",
            "--cidrs",
            "192.0.2.0/24",
            "--set",
            "DB_PASSWORD=secret",
        ],
        [
            "--service",
            "grafana",
            "--domain",
            "grafana.example.com",
            "--cidrs",
            "192.0.2.0/24",
            "--set",
            "ARTIFACT_BASE_URL=$(bad)",
        ],
    ],
)
def test_invalid_registration_never_writes_inventory(capsys, tmp_path, arguments):
    assert call(capsys, tmp_path, *host_args(), *arguments)[0] in {1, 2}
    assert not (tmp_path / "inventory.json").exists()


def test_register_generates_deployment_paths_and_port(capsys, tmp_path):
    code, result = call(
        capsys,
        tmp_path,
        *host_args(),
        "--port",
        "2222",
        "--service",
        "grafana",
        "--domain",
        "grafana.example.com",
        "--cidrs",
        "192.0.2.0/24",
        "--ssh-cidrs",
        "198.51.100.0/24",
        "--set",
        "TLS_CERT_PATH=",
    )
    assert code == 0 and result["deployments"] == ["grafana-vm"]
    path = tmp_path / "inventory.json"
    data = core.inventory(path)
    item = data["deployments"]["grafana-vm"]
    assert (path.parent / item["config"]).resolve() == ROOT / "grafana/linux/.env.example"
    assert (path.parent / item["manifest"]).resolve() == ROOT / "artifacts/cdn/ubuntu26-amd64/manifest.json"
    assert item["overrides"]["SSH_PORT"] == "2222"
    assert item["overrides"]["SSH_CIDRS"] == "198.51.100.0/24"
    assert item["overrides"]["TLS_CERT_PATH"] == ""


def test_registration_reports_missing_install_inputs_and_maps_files(capsys, tmp_path):
    password = tmp_path / "password.txt"
    password.write_text("never-print-this-password")
    code, result = call(
        capsys,
        tmp_path,
        *host_args(),
        "--service",
        "keycloak",
        "--domain",
        "sso.example.com",
        "--cidrs",
        "192.0.2.0/24",
        "--manifest",
        str(tmp_path / "missing.json"),
        "--file",
        "DB_PASSWORD_FILE=" + str(password),
    )
    assert code == 0 and any("missing.json" in item for item in result["pending"])
    assert "never-print-this-password" not in json.dumps(result)
    data = core.inventory(tmp_path / "inventory.json")
    assert data["deployments"]["keycloak-vm"]["files"] == {"DB_PASSWORD_FILE": "password.txt"}


def test_dns_registration_uses_supplied_fixed_ip_and_network(capsys, tmp_path):
    code, result = call(
        capsys,
        tmp_path,
        *host_args("192.0.2.4"),
        "--service",
        "technitium",
        "--domain",
        "dns.example.com",
        "--cidrs",
        "192.0.2.0/24",
    )
    assert code == 0
    overrides = core.inventory(tmp_path / "inventory.json")["deployments"]["technitium-vm"]["overrides"]
    assert overrides["DNS_LISTEN_IPV4"] == "192.0.2.4" and overrides["DNS_CIDRS"] == "192.0.2.0/24"
    assert result["pending"]  # Os registros DNS próprios ainda precisam ser associados.


def test_register_saves_offline_host_and_check_returns_failure(capsys, tmp_path, monkeypatch):
    monkeypatch.setattr(discovery, "probe", lambda *args: dict(state="unreachable", error="timeout"))
    code, result = call(capsys, tmp_path, *host_args(), "--check")
    assert code == 1 and result["check"]["state"] == "unreachable"
    assert core.inventory(tmp_path / "inventory.json")["hosts"]["vm"]["address"] == "vm.example.com"


def test_atomic_save_failure_leaves_original_and_cleans_temporary(tmp_path, monkeypatch):
    path = tmp_path / "inventory.json"
    previous = b'{"schema":1,"hosts":{},"deployments":{}}'
    path.write_bytes(previous)
    monkeypatch.setattr(registration.os, "replace", lambda *args: (_ for _ in ()).throw(OSError("failure")))
    with pytest.raises(OSError):
        registration.save_inventory(path, dict(schema=1, hosts={}, deployments={}), previous)
    assert path.read_bytes() == previous
    assert not list(tmp_path.glob(".inventory-*"))


def test_atomic_save_refuses_observed_concurrent_change(tmp_path):
    path = tmp_path / "inventory.json"
    path.write_text('{"schema":1,"hosts":{},"deployments":{}}')
    with pytest.raises(core.InstallerError, match="mudou"):
        registration.save_inventory(path, dict(schema=1, hosts={}, deployments={}), b"previous")
    assert not list(tmp_path.glob(".inventory-*"))


def test_hosts_lists_hosts_without_deployments_and_only_checks_when_requested(capsys, tmp_path, monkeypatch):
    assert call(capsys, tmp_path, *host_args())[0] == 0
    checked = []

    def probe(host, timeout):
        checked.append(host["address"])
        return dict(state="reachable", resolved_addresses=["192.0.2.10"], ssh_trust="unknown")

    monkeypatch.setattr(discovery, "probe", probe)
    code, result = call(capsys, tmp_path, "hosts")
    assert code == 0 and len(result["hosts"]) == 1 and checked == []
    assert result["hosts"][0]["deployments"] == []
    code, result = call(capsys, tmp_path, "hosts", "vm", "--check")
    assert code == 0 and checked == ["vm.example.com"]
    assert result["hosts"][0]["resolved_addresses"] == ["192.0.2.10"]


def test_hosts_check_continues_after_a_failed_host(capsys, tmp_path, monkeypatch):
    assert call(capsys, tmp_path, *host_args())[0] == 0
    assert call(capsys, tmp_path, "register", "other", "--address", "192.0.2.2", "--user", "root")[0] == 0
    monkeypatch.setattr(
        discovery,
        "probe",
        lambda host, timeout: dict(state="unreachable" if host["user"] == "root" else "reachable"),
    )
    code, result = call(capsys, tmp_path, "hosts", "--check")
    assert code == 1 and [row["state"] for row in result["hosts"]] == ["unreachable", "reachable"]


def test_unauthenticated_hosts_check_does_not_require_private_key(capsys, tmp_path, monkeypatch):
    path = tmp_path / "inventory.json"
    path.write_text(
        json.dumps(
            dict(
                schema=1,
                hosts={
                    "vm": dict(address="vm.example.com", user="ubuntu", identity_file="missing-private-key")
                },
                deployments={},
            )
        )
    )
    monkeypatch.setattr(
        discovery, "probe", lambda host, timeout: dict(state="reachable", ssh_trust="unknown")
    )
    code, result = call(capsys, tmp_path, "hosts", "--check")
    assert code == 0 and result["hosts"][0]["state"] == "reachable"


@pytest.mark.parametrize("value", ["10.0.0.0/8", "::/64", "bad/24"])
def test_discovery_refuses_large_or_invalid_networks_before_connect(value):
    with pytest.raises(core.InstallerError):
        discovery.targets([value])


def test_targets_deduplicates_and_handles_small_ipv4_ipv6_subnets():
    assert discovery.targets(["192.0.2.0/30", "192.0.2.1", "vm.example.com"]) == [
        "192.0.2.1",
        "192.0.2.2",
        "vm.example.com",
    ]
    assert discovery.targets(["192.0.2.0/31"]) == ["192.0.2.0", "192.0.2.1"]
    assert discovery.targets(["2001:db8::/127"]) == ["2001:db8::", "2001:db8::1"]
    with pytest.raises(core.InstallerError):
        discovery.targets(["192.0.2.0/30", "vm.example.com"], limit=2)


def test_discover_registers_only_ssh_hosts_without_trusting_observed_keys(capsys, tmp_path, monkeypatch):
    def scan(hosts, timeout, workers):
        return [
            dict(
                address=host["address"],
                port=host["port"],
                state="reachable" if index == 0 else "tcp_only",
                fingerprint="SHA256:" + "A" * 43,
                resolved_addresses=[host["address"]],
                ssh_trust="unknown",
            )
            for index, host in enumerate(hosts)
        ]

    monkeypatch.setattr(discovery, "scan", scan)
    code, result = call(capsys, tmp_path, "discover", "192.0.2.0/30", "--register", "--user", "ubuntu")
    assert code == 0 and result["scanned"] == 2 and len(result["hosts"]) == 1 and len(result["failures"]) == 1
    data = core.inventory(tmp_path / "inventory.json")
    assert len(data["hosts"]) == 1
    assert data["hosts"]["host-192.0.2.1"] == dict(address="192.0.2.1", port=22, user="ubuntu")
    assert result["hosts"][0]["registered"]
    previous = (tmp_path / "inventory.json").read_bytes()
    assert call(capsys, tmp_path, "discover", "192.0.2.0/30", "--register", "--user", "ubuntu")[0] == 0
    assert (tmp_path / "inventory.json").read_bytes() == previous


def test_discovery_validation_occurs_before_scan(capsys, tmp_path, monkeypatch):
    monkeypatch.setattr(discovery, "scan", lambda *args: pytest.fail("Não deve sondar"))
    assert call(capsys, tmp_path, "discover", "192.0.2.1", "--register")[0] == 1
    assert call(capsys, tmp_path, "discover", "192.0.2.1", "--register", "--user", "INVALID")[0] == 1
    assert call(capsys, tmp_path, "discover", "10.0.0.0/8")[0] == 1


def test_probe_resolves_dns_compares_key_and_closes_transport_before_socket(monkeypatch):
    closed = []
    key = SimpleNamespace(asbytes=lambda: b"public-key", get_name=lambda: "ssh-ed25519")

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            closed.append("socket")

    fake = SimpleNamespace(
        start_client=lambda **kwargs: None,
        get_remote_server_key=lambda: key,
        close=lambda: closed.append("transport"),
    )
    monkeypatch.setattr(
        discovery.socket, "getaddrinfo", lambda *args, **kwargs: [(None, None, None, None, ("192.0.2.1", 22))]
    )
    monkeypatch.setattr(discovery.socket, "create_connection", lambda *args, **kwargs: Connection())
    monkeypatch.setattr(discovery, "ssh_module", lambda: SimpleNamespace(Transport=lambda connection: fake))
    host = dict(address="vm.example.com", host_key_sha256=fingerprint(key))
    record = discovery.probe(host, 3)
    assert record["state"] == "reachable" and record["ssh_trust"] == "verified"
    assert record["resolved_addresses"] == ["192.0.2.1"] and closed == ["transport", "socket"]
    host["host_key_sha256"] = "SHA256:" + "A" * 43
    assert discovery.probe(host, 3)["state"] == "key_mismatch"


def test_probe_reports_dns_failure_without_opening_socket(monkeypatch):
    def failure(*args, **kwargs):
        raise socket.gaierror("DNS failed")

    monkeypatch.setattr(discovery.socket, "getaddrinfo", failure)
    monkeypatch.setattr(
        discovery.socket, "create_connection", lambda *args, **kwargs: pytest.fail("Não deve conectar")
    )
    record = discovery.probe(dict(address="missing.example.com"), 3)
    assert record["state"] == "unreachable" and record["error"] == "DNS failed"


def test_known_hosts_checks_nonstandard_port_and_pin_has_precedence(tmp_path, monkeypatch):
    key = SimpleNamespace(asbytes=lambda: b"observed-key", get_name=lambda: "ssh-ed25519")
    known = tmp_path / "known_hosts"
    known.touch()
    looked_up = []

    def lookup(host):
        looked_up.append(host)
        return {"ssh-ed25519": key}

    monkeypatch.setattr(
        discovery, "ssh_module", lambda: SimpleNamespace(HostKeys=lambda path: SimpleNamespace(lookup=lookup))
    )
    host = dict(address="vm.example.com", port=2222, known_hosts=str(known))
    assert discovery.trust(host, key) == "verified" and looked_up == ["[vm.example.com]:2222"]
    host["host_key_sha256"] = "SHA256:" + "A" * 43
    assert discovery.trust(host, key) == "mismatch" and len(looked_up) == 1


@pytest.mark.parametrize(
    "change",
    [
        dict(password="secret"),
        dict(status=["valid"]),
        dict(checked_at="invalid-date"),
        dict(checked_at="2026-10-12T12:00:00"),
        dict(host_key_sha256=None),
    ],
)
def test_inventory_rejects_invalid_or_secret_login_validation(change):
    host = dict(address="vm.example.com", user="ubuntu", port=22, host_key_sha256="SHA256:" + "A" * 43)
    host["ssh_validation"] = dict(
        status="valid", checked_at="2026-10-12T12:00:00+00:00", **core.ssh_context(host)
    )
    host["ssh_validation"].update(change)
    with pytest.raises(core.InstallerError):
        core.validate_inventory(dict(schema=1, hosts={"vm": host}, deployments={}))
