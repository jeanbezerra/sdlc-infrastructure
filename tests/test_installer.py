import argparse
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tarfile
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


sys.path.insert(0, str(ROOT / 'installer/src'))
from sdlc_installer import core as cli
from sdlc_installer import cli as command
from sdlc_installer import operations, transport

remote = load('installer_remote', ROOT / 'installer/src/sdlc_installer/resources/remote.py') if os.name == 'posix' else None


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.work = Path(self.directory.name)
        manifest = dict(schema=1, platform='ubuntu26.04-amd64', artifacts=[
            dict(name=name, sha256='0' * 64, size=1) for name in ('ubuntu26-base.tar.gz', cli.metadata('grafana')['artifact'])])
        (self.work / 'manifest.json').write_text(json.dumps(manifest))
        self.data = dict(schema=1, hosts={'vm': dict(address='192.168.0.250', user='trendsoftware', port=22)},
                         deployments={'grafana': dict(host='vm', service='grafana', config=str(ROOT / 'grafana/linux/.env.example'),
                                                     manifest='manifest.json', overrides=dict(ACCESS_CIDRS='192.168.0.0/24', SSH_CIDRS='192.168.0.0/24'), files={})})
        self.path = self.work / 'inventory.json'

    def args(self, action='install'):
        self.path.write_text(json.dumps(self.data))
        return argparse.Namespace(inventory=str(self.path), deployment='grafana', action=action, keep_data=False)

    def test_catalog_matches_all_native_scripts(self):
        for service in cli.CATALOG['native']:
            meta = cli.metadata(service)
            self.assertTrue((ROOT / meta['folder'] / 'linux/install.sh').is_file())
            self.assertTrue((ROOT / meta['folder'] / 'linux/uninstall.sh').is_file())
            self.assertTrue(meta['backend_ports'])
        for service in cli.CATALOG['pending']:
            with self.assertRaises(cli.InstallerError):
                cli.metadata(service)

    def test_inventory_rejects_plaintext_password_and_invalid_ssh_port(self):
        for key, value in [('password', 'secret'), ('port', 0), ('port', True), ('host_key_sha256', 'untrusted')]:
            with self.subTest(key=key):
                original = dict(self.data['hosts']['vm'])
                self.data['hosts']['vm'][key] = value
                with self.assertRaises(cli.InstallerError):
                    cli.deployment(self.args())
                self.data['hosts']['vm'] = original

    def test_install_paths_are_stable_and_bundle_excludes_private_project_files(self):
        _, request, files = cli.deployment(self.args(), prepare=True)
        self.assertEqual(request['config']['MANIFEST_PATH'], '/opt/sdlc-remote/grafana/inputs/manifest.json')
        payload = cli.bundle(request, files)
        with tarfile.open(fileobj=io.BytesIO(payload), mode='r:gz') as archive:
            names = archive.getnames()
            self.assertIn('project/grafana/linux/install.sh', names)
            self.assertIn('project/scripts/linux/common.sh', names)
            self.assertFalse(any('/.env' in name or name.startswith(('artifacts/', '.validation/', '.git/')) for name in names))
            checksums = json.load(archive.extractfile('checksums.json'))
            for name, checksum in checksums.items():
                self.assertEqual(hashlib.sha256(archive.extractfile(name).read()).hexdigest(), checksum)

    def test_shell_expansion_and_unknown_override_are_rejected(self):
        for overrides in [dict(DOMAIN='$(touch /tmp/injected)'), dict(UNKNOWN='value')]:
            self.data['deployments']['grafana']['overrides'] = overrides
            with self.assertRaises(cli.InstallerError):
                cli.deployment(self.args(), prepare=True)

    def test_unsupported_inline_secret_key_is_rejected_before_plan(self):
        source = (ROOT / 'grafana/linux/.env.example').read_text()
        (self.work / 'private.env').write_text(source + '\nDB_PASSWORD=do-not-print-this-secret\n')
        self.data['deployments']['grafana']['config'] = 'private.env'
        with self.assertRaises(cli.InstallerError) as error:
            cli.deployment(self.args(), prepare=True)
        self.assertNotIn('do-not-print-this-secret', str(error.exception))

    def test_manifest_missing_required_artifact_is_rejected(self):
        manifest = json.loads((self.work / 'manifest.json').read_text())
        manifest['artifacts'].pop()
        (self.work / 'manifest.json').write_text(json.dumps(manifest))
        with self.assertRaises(cli.InstallerError):
            cli.deployment(self.args(), prepare=True)

    def test_verify_and_uninstall_do_not_require_local_install_inputs(self):
        self.data['deployments']['grafana']['config'] = 'missing.env'
        self.data['deployments']['grafana']['manifest'] = 'missing.json'
        for action in ('verify', 'uninstall'):
            _, request, files = cli.deployment(self.args(action), prepare=False)
            self.assertEqual(request['action'], action)
            self.assertEqual(files, {})

    def test_uninstall_without_yes_never_connects(self):
        with patch.object(command, 'Remote') as connect, patch('sys.stderr', io.StringIO()), patch('sys.stdout', io.StringIO()):
            self.assertEqual(command.main(['uninstall', 'grafana', '--inventory', str(self.path), '--state-dir', str(self.work)]), 1)
            connect.assert_not_called()

    def test_inputs_are_uploaded_and_remote_file_paths_are_rewritten(self):
        self.data['deployments']['grafana']['files'] = dict(TLS_CERT_PATH='server.crt', TLS_KEY_PATH='server.key')
        (self.work / 'server.crt').write_text('certificate')
        (self.work / 'server.key').write_text('private-key')
        _, request, files = cli.deployment(self.args(), prepare=True)
        self.assertEqual(files['inputs/tls_key_path'], b'private-key')
        self.assertEqual(request['config']['TLS_KEY_PATH'], '/opt/sdlc-remote/grafana/inputs/tls_key_path')

    def test_follow_drains_large_terminal_log_and_returns_failure(self):
        responses = [dict(state='failed', output='first\n', offset=10, log_complete=False),
                     dict(state='failed', output='last\n', offset=20, log_complete=True)]
        class FakeRemote:
            def status(self, *args):
                return responses.pop(0)
            def redact(self, value):
                return value
        args = argparse.Namespace(state_dir=str(self.work), timeout=30)
        with patch('sys.stdout', io.StringIO()):
            self.assertEqual(operations.follow(FakeRemote(), 'grafana', 'a' * 24, args), 1)
        self.assertEqual((self.work / 'runs' / ('a' * 24) / 'operation.log').read_text(), 'first\nlast\n')

    def test_wrong_pin_is_rejected_before_auth_even_with_known_hosts(self):
        class FakeClient:
            authenticated = False
            loaded_known_hosts = False
            def load_host_keys(self, path):
                self.loaded_known_hosts = True
            def set_missing_host_key_policy(self, policy):
                self.policy = policy
            def connect(self, hostname, **options):
                self.policy.missing_host_key(self, hostname, SimpleNamespace(asbytes=lambda: b'actual-server-key'))
                self.authenticated = True
            def close(self):
                pass
        client = FakeClient()
        fake = SimpleNamespace(SSHClient=lambda: client, MissingHostKeyPolicy=object)
        known = self.work / 'known_hosts'
        known.touch()
        host = dict(address='192.168.0.250', user='trendsoftware', known_hosts=str(known), host_key_sha256='SHA256:' + 'A' * 43)
        with patch.object(transport, 'ssh_module', return_value=fake), patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(cli.InstallerError, 'Fingerprint'):
                transport.Remote(host, SimpleNamespace(ask_pass=False, ask_sudo_pass=False))
        self.assertFalse(client.authenticated)
        self.assertFalse(client.loaded_known_hosts)


@unittest.skipUnless(os.name == 'posix', 'Executor requer Linux/POSIX.')
class RemotePayloadTests(unittest.TestCase):
    def test_remote_logs_redact_generated_and_uploaded_passwords(self):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            (work / 'grafana/secrets').mkdir(parents=True)
            (work / 'grafana/secrets/admin_password').write_text('generated-password\n')
            (work / 'inputs').mkdir()
            (work / 'inputs/db_password_file').write_text('uploaded-password\n')
            with patch.object(remote, 'SERVICE_BASE', work):
                result = remote.redact('generated-password uploaded-password', work, 'grafana')
            self.assertEqual(result, '[REDACTED] [REDACTED]')

    def test_verify_accepts_marker_written_by_native_installer(self):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            base = work / 'grafana'
            base.mkdir()
            (base / '.managed').write_text('sdlc-v1:grafana\n')
            (base / '.installed').touch()
            (base / 'install.env').write_text('DOMAIN=grafana.example.com\n')
            def healthy(command, env=None):
                return 'Status: active' if command[0] == 'ufw' else '200' if command[0] == 'curl' else ''
            with patch.object(remote, 'SERVICE_BASE', work), patch.object(remote, 'checked', healthy), \
                    patch.object(remote, 'http_probe', return_value=(200, b'{"database":"ok"}')):
                result = remote.verify_service(dict(service='grafana', metadata=cli.metadata('grafana')))
            self.assertEqual(result['backend'], 'HTTP 200')

    def payload(self, entries):
        output = io.BytesIO()
        with tarfile.open(fileobj=output, mode='w:gz') as archive:
            for name, kind, data in entries:
                entry = tarfile.TarInfo(name)
                entry.type = kind
                entry.size = len(data) if kind == tarfile.REGTYPE else 0
                entry.linkname = '/etc/passwd' if kind == tarfile.SYMTYPE else ''
                archive.addfile(entry, io.BytesIO(data) if kind == tarfile.REGTYPE else None)
        output.seek(0)
        return output

    def test_payload_rejects_traversal_symlink_and_duplicates(self):
        cases = [[('../escape', tarfile.REGTYPE, b'x')], [('inputs/key', tarfile.SYMTYPE, b'')],
                 [('request.json', tarfile.REGTYPE, b'{}'), ('request.json', tarfile.REGTYPE, b'{}')]]
        for entries in cases:
            with self.subTest(entries=entries), tempfile.TemporaryDirectory() as directory:
                work = Path(directory)
                package = work / 'payload.tar.gz'
                package.write_bytes(self.payload(entries).getvalue())
                with patch.object(remote, 'safe_directory', lambda path: path.mkdir(parents=True, exist_ok=True)):
                    with self.assertRaises(ValueError):
                        remote.unpack(package, work / 'out')
                self.assertFalse((work / 'escape').exists())

    def test_payload_rejects_modified_content(self):
        entries = [('inputs/service.env', tarfile.REGTYPE, b'changed'),
                   ('checksums.json', tarfile.REGTYPE, json.dumps({'inputs/service.env': hashlib.sha256(b'original').hexdigest()}).encode())]
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            package = work / 'payload.tar.gz'
            package.write_bytes(self.payload(entries).getvalue())
            with patch.object(remote, 'safe_directory', lambda path: path.mkdir(parents=True, exist_ok=True)):
                with self.assertRaisesRegex(ValueError, 'SHA256'):
                    remote.unpack(package, work / 'out')


if __name__ == '__main__':
    unittest.main()
