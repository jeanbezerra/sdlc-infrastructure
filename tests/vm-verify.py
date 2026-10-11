"""Verificação real após instalação/reboot em uma VM dedicada, executada como root."""
import argparse
import base64
import ipaddress
import json
import os
from pathlib import Path
import re
import ssl
import subprocess
import urllib.error
import urllib.parse
import urllib.request


def run(command, env=None, check=True):
    result = subprocess.run(command, env=env, capture_output=True, text=True)
    if check and result.returncode:
        raise RuntimeError('Comando de verificação falhou: ' + command[0])
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--service', required=True, choices=['postgresql', 'redis', 'prometheus', 'grafana', 'jenkins', 'keycloak', 'nexus', 'sonarqube', 'opensearch'])
    parser.add_argument('--config', required=True)
    parser.add_argument('--expect-existing', action='store_true', help='Exige os dados criados na primeira verificação, sem recriá-los.')
    args = parser.parse_args()
    assert os.geteuid() == 0, 'Execute como root na VM de homologação.'
    service = args.service
    config = dict(line.split('=', 1) for line in Path(args.config).read_text().splitlines() if line and not line.startswith('#'))
    domain = config['DOMAIN']
    base = Path('/opt/sdlc') / service
    data = Path('/var/lib/sdlc') / service
    assert run(['systemctl', 'is-active', 'sdlc-' + service]).stdout.strip() == 'active'
    assert run(['systemctl', 'is-enabled', 'sdlc-' + service]).stdout.strip() == 'enabled'
    assert run(['getent', 'passwd', 'sdlc-' + service]).stdout.strip().endswith('/usr/sbin/nologin')
    assert 'Status: active' in run(['ufw', 'status'], env=dict(os.environ, LC_ALL='C')).stdout
    run(['nginx', '-t'])
    sentinel = data / '.codex-vm-preserve'
    if not args.expect_existing and not sentinel.exists(): sentinel.write_text('codex-vm-preserve\n')
    assert sentinel.read_text() == 'codex-vm-preserve\n'
    # Checa os listeners pertencentes ao cgroup inteiro, incluindo JVMs filhas.
    cgroup = run(['systemctl', 'show', 'sdlc-' + service, '--property=ControlGroup', '--value']).stdout.strip()
    pids = set()
    for file in Path('/proc').glob('[0-9]*/cgroup'):
        try:
            if any(line.split(':', 2)[-1] == cgroup for line in file.read_text().splitlines()):
                pids.add(int(file.parent.name))
        except OSError: pass
    assert pids, 'Cgroup sem processos.'
    for line in run(['ss', '-H', '-lntp']).stdout.splitlines():
        if {int(pid) for pid in re.findall(r'pid=(\d+)', line)} & pids:
            address = ipaddress.ip_address(line.split()[3].rsplit(':', 1)[0].strip('[]'))
            assert address.is_loopback or (isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped and address.ipv4_mapped.is_loopback), 'Backend exposto fora de loopback.'
    context = ssl.create_default_context(cafile=str(base / 'tls/server.crt'))

    def http(path, password=None, user='admin', payload=None):
        request = urllib.request.Request('https://' + domain + path, data=payload)
        if password:
            request.add_header('Authorization', 'Basic ' + base64.b64encode((user + ':' + password).encode()).decode())
        try:
            with urllib.request.urlopen(request, context=context, timeout=30) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as error:
            return error.code, error.read()

    password = (base / 'secrets/admin_password').read_text().strip()
    if service == 'postgresql':
        psql = ['/usr/lib/postgresql/18/bin/psql', '-X', '-v', 'ON_ERROR_STOP=1', '-h', domain, '-p', '5432', '-d', 'sdlc', '-tA']
        env = dict(os.environ, PGSSLMODE='verify-full', PGSSLROOTCERT=str(base / 'tls/server.crt'))
        migrator = dict(env, PGPASSWORD=(base / 'secrets/migrator_password').read_text().strip())
        if not args.expect_existing:
            run(psql + ['-U', 'sdlc_migrator', '-c', 'SET ROLE sdlc_owner; CREATE TABLE IF NOT EXISTS app.codex_vm_probe(id int PRIMARY KEY, value text NOT NULL);'], migrator)
        app = dict(env, PGPASSWORD=(base / 'secrets/app_password').read_text().strip())
        if not args.expect_existing:
            run(psql + ['-U', 'sdlc_app', '-c', "INSERT INTO app.codex_vm_probe VALUES(1, 'preserved') ON CONFLICT DO NOTHING;"], app)
        assert run(psql + ['-U', 'sdlc_app', '-c', 'SELECT value FROM app.codex_vm_probe WHERE id=1;'], app).stdout.strip() == 'preserved'
        assert run(psql + ['-U', 'sdlc_app', '-c', 'SELECT ssl FROM pg_stat_ssl WHERE pid=pg_backend_pid();'], app).stdout.strip() == 't'
        assert run(psql + ['-U', 'sdlc_app', '-c', 'CREATE TABLE app.must_not_exist(id int);'], app, False).returncode != 0
        assert run(psql + ['-U', 'sdlc_app', '-c', 'SELECT 1;'], dict(app, PGSSLMODE='disable'), False).returncode != 0
    elif service == 'redis':
        cli = ['redis-cli', '--tls', '--cacert', str(base / 'tls/server.crt'), '--sni', domain, '-h', domain, '-p', '6380', '--user', 'sdlc']
        env = dict(os.environ, REDISCLI_AUTH=(base / 'secrets/app_password').read_text().strip())
        assert run(cli + ['PING'], env).stdout.strip() == 'PONG'
        if not args.expect_existing:
            run(cli + ['SET', 'sdlc:codex-vm-validation', 'preserved', 'NX'], env)
        assert run(cli + ['GET', 'sdlc:codex-vm-validation'], env).stdout.strip() == 'preserved'
        assert 'NOPERM' in run(cli + ['CONFIG', 'GET', '*'], env).stdout
    elif service == 'prometheus':
        assert http('/-/ready')[0] == 401
        assert http('/-/ready', password)[0] == 200
        assert http('/api/v1/status/buildinfo', password)[0] == 200
    elif service == 'grafana':
        status, body = http('/api/health')
        assert status == 200 and json.loads(body)['database'] == 'ok'
        assert http('/api/user', password)[0] == 200
    elif service == 'jenkins':
        assert http('/api/json')[0] in [401, 403]
        status, body = http('/api/json', password)
        assert status == 200 and json.loads(body)['numExecutors'] == 0
    elif service == 'keycloak':
        assert http('/realms/master')[0] == 200
        body = urllib.parse.urlencode(dict(grant_type='password', client_id='admin-cli', username='admin', password=password)).encode()
        status, result = http('/realms/master/protocol/openid-connect/token', payload=body)
        assert status == 200 and 'access_token' in json.loads(result)
    elif service == 'sonarqube':
        status, body = http('/api/system/status')
        assert status == 200 and json.loads(body)['status'] == 'UP'
        assert json.loads(http('/api/authentication/validate', password)[1])['valid']
    elif service == 'opensearch':
        assert http('/')[0] == 401
        status, body = http('/', password)
        assert status == 200 and json.loads(body)['cluster_name'] == 'sdlc-opensearch'
    elif service == 'nexus':
        assert http('/service/rest/v1/status')[0] == 200
        initial = (data / 'admin.password').read_text().strip()
        assert http('/service/rest/v1/security/users', initial)[0] == 200
    print(service + ': systemd, TLS via Nginx, autenticação, dados e listeners loopback: OK')


if __name__ == '__main__':
    main()
