#!/usr/bin/env python3
"""Verificação explícita na VM de homologação; --seed cria registros de teste."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('technitium_config', ROOT / 'technitium/linux/configure.py')
dns = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dns)


def run(*args):
    return subprocess.check_output(args, text=True).strip()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seed', action='store_true')
    args = parser.parse_args()
    assert os.geteuid() == 0, 'Execute como root na VM de homologação.'
    state = Path('/opt/sdlc/technitium')
    config = dict(line.split('=', 1) for line in (state / 'install.env').read_text().splitlines() if line and not line.startswith('#'))
    zone = json.loads((state / 'config/initial-records.json').read_text())['zone']
    address, domain = config['DNS_LISTEN_IPV4'], config['DOMAIN']
    assert run('systemctl', 'is-active', 'sdlc-technitium') == 'active'
    assert run('systemctl', 'is-enabled', 'sdlc-technitium') == 'enabled'
    assert run('systemctl', 'is-active', 'systemd-resolved') == 'active'
    for protocol in ('-lntp', '-lnup'):
        sockets = run('ss', '-H', protocol)
        listeners = [line for line in sockets.splitlines() if 'dotnet' in line]
        assert any(address + ':53' in line for line in listeners), listeners
        assert not any(line.split()[3].startswith(('0.0.0.0:', '[::]:')) for line in listeners), listeners
    assert '127.0.0.1:5380' in run('ss', '-H', '-lntp')
    for mode in ([], ['+tcp']):
        assert run('dig', *mode, '+time=3', '+tries=1', '+short', '@' + address, domain, 'A') == address
        assert run('dig', *mode, '+time=3', '+tries=1', '+short', '@' + address, 'cdn.' + zone, 'A') == '192.168.0.200'
    assert run('curl', '--noproxy', '*', '--silent', '--show-error', '--fail', '--cacert', str(state / 'tls/server.crt'),
               '--resolve', domain + ':443:' + address, '--output', '/dev/null', '--write-out', '%{http_code}', 'https://' + domain + '/') == '200'
    assert run('curl', '--noproxy', '*', '--silent', '--resolve', domain + ':80:' + address,
               '--output', '/dev/null', '--write-out', '%{http_code}', 'http://' + domain + '/') == '308'
    records_path = Path('/var/lib/sdlc/technitium/codex-probe.json')
    if args.seed:
        assert not records_path.exists(), 'Fixtures já existem; use o verificador sem --seed.'
        records_path.write_text(json.dumps({'zone': 'codex-test.' + zone, 'records': [
            {'name': 'retained', 'type': 'A', 'value': '192.168.0.201'},
            {'name': 'retained', 'type': 'AAAA', 'value': 'fd00::201'},
            {'name': 'alias', 'type': 'CNAME', 'value': 'retained.codex-test.' + zone},
            {'name': 'txt', 'type': 'TXT', 'value': 'codex-retained'}]}))
        records_path.chmod(0o600)
        run('bash', str(ROOT / 'technitium/linux/configure.sh'), '--records', str(records_path))
        # Exercita TXT/AAAA/CNAME repetidos e a detecção de conjuntos iguais na API real.
        run('bash', str(ROOT / 'technitium/linux/configure.sh'), '--records', str(records_path))
    assert records_path.is_file(), 'Fixture não preservada.'
    api = dns.Api()
    try:
        api.login(state / 'secrets/admin_password')
        if args.seed:
            # A senha inicial do fornecedor não pode autenticar na implantação.
            try:
                dns.Api().call('/api/user/login', user='admin', **{'pass': 'admin'})
            except ValueError:
                pass
            else:
                raise AssertionError('Senha administrativa padrão aceita.')
            api.call('/api/settings/set', cacheMaximumEntries=8000)
        settings = api.call('/api/settings/get')['response']
        assert settings['cacheMaximumEntries'] == 8000, 'Mudança posterior ao bootstrap foi perdida.'
        assert settings['recursion'] == 'UseSpecifiedNetworkACL'
        assert settings['recursionNetworkACL'] == config['DNS_CIDRS'].split(',')
        assert settings['dnsServerLocalEndPoints'] == [address + ':53']
        assert settings['webServiceLocalAddresses'] == ['127.0.0.1']
        assert not settings['dnsAppsEnableAutomaticUpdate']
        options = api.call('/api/zones/options/get', zone='codex-test.' + zone)['response']
        assert options['queryAccess'] == 'UseSpecifiedNetworkACL'
        assert options['queryAccessNetworkACL'] == config['DNS_CIDRS'].split(',')
    finally:
        api.logout()
    for kind, expected in [('A', '192.168.0.201'), ('AAAA', 'fd00::201')]:
        assert run('dig', '+short', '@' + address, 'retained.codex-test.' + zone, kind) == expected
    assert run('dig', '+short', '@' + address, 'txt.codex-test.' + zone, 'TXT') == '"codex-retained"'
    assert '192.168.0.201' in run('dig', '+short', '@' + address, 'alias.codex-test.' + zone, 'A')
    assert run('systemctl', 'show', 'sdlc-technitium', '-p', 'MemoryMax', '--value') == '1610612736'
    print('DNS TCP/UDP, quatro tipos de registro, API autenticada, HTTPS/TLS e persistência: OK')
    print(run('systemctl', 'show', 'sdlc-technitium', '-p', 'MemoryCurrent', '-p', 'MemoryPeak', '-p', 'MemoryMax'))


if __name__ == '__main__':
    main()
