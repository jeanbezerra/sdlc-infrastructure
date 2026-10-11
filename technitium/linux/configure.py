#!/usr/bin/env python3
"""Bootstrap e registros DNS usando a API oficial local do Technitium 15."""
import argparse
from collections import defaultdict
import ipaddress
import json
from pathlib import Path
import re
import socket
import stat
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

API_URL = 'http://127.0.0.1:5380'


def dns_name(value):
    if not isinstance(value, str):
        raise ValueError('Nome DNS deve ser texto.')
    value = value.lower().rstrip('.')
    if len(value) > 253 or not value or any(not re.fullmatch(r'[a-z0-9_](?:[a-z0-9_-]{0,61}[a-z0-9_])?', label) for label in value.split('.')):
        raise ValueError('Nome DNS inválido: ' + value)
    return value


def validate_network(listen, cidrs, forwarders):
    address = ipaddress.IPv4Address(listen)
    private = [ipaddress.ip_network(net) for net in ['10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16']]
    if not any(address in net for net in private):
        raise ValueError('DNS_LISTEN_IPV4 deve ser um IP privado RFC1918 da VM.')
    networks = [ipaddress.ip_network(cidr, strict=True) for cidr in cidrs.split(',')]
    if any(net.version != 4 or net.prefixlen == 0 for net in networks):
        raise ValueError('DNS_CIDRS exige redes IPv4 restritas; /0 não é permitido.')
    if not any(address in net for net in networks):
        raise ValueError('DNS_CIDRS deve incluir o IP da VM para as verificações locais.')
    for value in forwarders.split(',') if forwarders else []:
        upstream = ipaddress.IPv4Address(value)
        if upstream == address or upstream.is_loopback or upstream.is_unspecified or upstream.is_multicast:
            raise ValueError('Forwarder inválido ou apontando para este DNS.')


def read_records(path, domain=None, listen=None):
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise ValueError('Registros exigem um arquivo JSON existente sem symlink.')
    data = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(data, dict) or set(data) != {'zone', 'records'}:
        raise ValueError('JSON exige exatamente zone e records.')
    zone = dns_name(data['zone'])
    if '.' not in zone or not isinstance(data['records'], list):
        raise ValueError('Zone deve ser um domínio completo; records deve ser uma lista.')
    records, seen, ttls = [], set(), {}
    for item in data['records']:
        if not isinstance(item, dict) or set(item) - {'name', 'type', 'value', 'ttl'} or not {'name', 'type', 'value'} <= set(item):
            raise ValueError('Cada registro exige name, type e value; ttl é opcional.')
        name = item['name']
        if name == '@':
            name = zone
        else:
            absolute = isinstance(name, str) and name.endswith('.')
            name = dns_name(name)
            if name != zone and not name.endswith('.' + zone):
                if absolute:
                    raise ValueError('Nome DNS absoluto deve pertencer à zona: ' + name)
                name = dns_name(name + '.' + zone)
        kind = item['type']
        if kind not in ('A', 'AAAA', 'CNAME', 'TXT'):
            raise ValueError('Tipo suportado pelo JSON: A, AAAA, CNAME ou TXT. Outros tipos: use o painel.')
        ttl = item.get('ttl', 300)
        if type(ttl) is not int or not 30 <= ttl <= 604800:
            raise ValueError('TTL deve ser inteiro entre 30 e 604800 segundos.')
        value = item['value']
        if not isinstance(value, str):
            raise ValueError('Value deve ser texto.')
        if kind == 'A':
            value = str(ipaddress.IPv4Address(value))
        elif kind == 'AAAA':
            value = str(ipaddress.IPv6Address(value))
        elif kind == 'CNAME':
            value = dns_name(value)
            if name == zone:
                raise ValueError('CNAME não pode ocupar o apex da zona.')
        elif not value or len(value.encode('utf-8')) > 255 or '\n' in value or '\r' in value:
            raise ValueError('TXT exige uma string de 1–255 bytes, sem quebras de linha.')
        key = (name, kind)
        if (name, kind, value) in seen or key in ttls and ttls[key] != ttl:
            raise ValueError('Registro duplicado ou TTLs diferentes no mesmo conjunto.')
        seen.add((name, kind, value)); ttls[key] = ttl
        records.append(dict(name=name, type=kind, value=value, ttl=ttl))
    for name in {r['name'] for r in records}:
        local = [r for r in records if r['name'] == name]
        if any(r['type'] == 'CNAME' for r in local) and len(local) != 1:
            raise ValueError('CNAME deve ser o único registro desse nome.')
    if domain:
        panel = dns_name(domain)
        if panel != zone and not panel.endswith('.' + zone):
            raise ValueError('DOMAIN do painel deve pertencer à zona inicial.')
        existing = [r for r in records if r['name'] == panel]
        if existing and (len(existing) != 1 or existing[0]['type'] != 'A' or existing[0]['value'] != listen):
            raise ValueError('O registro do painel precisa ser A com o IP da VM.')
        if not existing:
            records.append(dict(name=panel, type='A', value=listen, ttl=300))
    return zone, records


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError('Redirecionamento inesperado na API local.')


class Api:
    def __init__(self):
        self.token = None
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())

    def call(self, path, **values):
        headers = {'Content-Type': 'application/x-www-form-urlencoded'}
        if self.token:
            headers['Authorization'] = 'Bearer ' + self.token
        request = urllib.request.Request(API_URL + path, data=urllib.parse.urlencode(values).encode(), headers=headers)
        with self.opener.open(request, timeout=30) as response:
            result = json.load(response)
        if result.get('status') != 'ok':
            # Não imprime resposta bruta: ela pode conter dados de autenticação.
            raise ValueError('API recusou ' + path + '; confira permissões, senha/2FA e logs locais.')
        return result

    def login(self, password_file, otp=None):
        path = Path(password_file)
        if path.is_symlink() or not path.is_file() or stat.S_IMODE(path.stat().st_mode) & 0o077:
            raise ValueError('Senha deve estar em arquivo protegido com chmod 600, sem symlink.')
        values = dict(user='admin', **{'pass': path.read_text().strip()})
        if otp:
            if not re.fullmatch(r'[0-9]{6}', otp):
                raise ValueError('OTP deve ter seis dígitos.')
            values['totp'] = otp
        deadline = time.monotonic() + 60
        while True:
            try:
                self.token = self.call('/api/user/login', **values)['token']
                return
            except urllib.error.HTTPError as error:
                raise ValueError('API de login retornou HTTP ' + str(error.code) + '; consulte os logs locais.') from None
            except urllib.error.URLError:
                if time.monotonic() >= deadline:
                    raise ValueError('Painel local não respondeu em 60 segundos.') from None
                time.sleep(2)

    def logout(self):
        if self.token:
            try:
                self.call('/api/user/logout')
            except (ValueError, OSError):
                pass
            self.token = None


def existing_value(record):
    data, kind = record['rData'], record['type']
    if kind in ('A', 'AAAA'):
        return str(ipaddress.ip_address(data['ipAddress']))
    if kind == 'CNAME':
        return dns_name(data['cname'])
    if kind == 'TXT':
        return data['text']
    return None


def apply_records(api, zone, records, replace=False, bootstrap=False, cidrs=None):
    zones = api.call('/api/zones/list')['response']['zones']
    existing = next((item for item in zones if item['name'].lower().rstrip('.') == zone), None)
    created = existing is None
    if created:
        api.call('/api/zones/create', zone=zone, type='Primary', useSoaSerialDateScheme='true')
    elif existing['type'] != 'Primary' or existing.get('internal'):
        raise ValueError('O JSON só configura zonas primárias não internas.')
    if created or bootstrap:
        options = dict(zone=zone, zoneTransfer='Deny', notify='None', update='Deny')
        if cidrs:
            options.update(queryAccess='UseSpecifiedNetworkACL', queryAccessNetworkACL=cidrs)
        api.call('/api/zones/options/set', **options)
    current = api.call('/api/zones/records/get', domain=zone, zone=zone, listZone='true')['response']['records']
    groups = defaultdict(list)
    for record in records:
        groups[(record['name'], record['type'])].append(record)
    plan = []
    for (name, kind), items in groups.items():
        other = [r for r in current if r['name'].lower().rstrip('.') == name and r['type'] != kind and r['type'] not in ('RRSIG', 'NSEC')]
        if kind == 'CNAME' and other or kind != 'CNAME' and any(r['type'] == 'CNAME' for r in other):
            raise ValueError('CNAME conflita com outro tipo em ' + name + '; resolva pelo painel.')
        found = [r for r in current if r['name'].lower().rstrip('.') == name and r['type'] == kind]
        matching = {existing_value(r) for r in found if not r.get('disabled') and r['ttl'] == items[0]['ttl']}
        wanted = {r['value'] for r in items}
        if matching == wanted and len(found) == len(wanted):
            continue
        if found and not replace:
            raise ValueError('Conflito em ' + name + '/' + kind + '; use o painel ou --replace para substituir esse conjunto.')
        # Planeja todos os conjuntos antes de alterar qualquer registro existente.
        plan.append((items, bool(found)))
    for items, overwrite in plan:
        for index, item in enumerate(items):
            fields = {'A': 'ipAddress', 'AAAA': 'ipAddress', 'CNAME': 'cname', 'TXT': 'text'}
            api.call('/api/zones/records/add', zone=zone, domain=item['name'], type=item['type'], ttl=item['ttl'],
                     overwrite='true' if overwrite and index == 0 else 'false', **{fields[item['type']]: item['value']})
    print(f'Zona {zone}: {len(records)} registros conferidos; {len(plan)} conjuntos aplicados.')


def bootstrap(api, args):
    zone, records = read_records(args.records, args.domain, args.listen)
    apply_records(api, zone, records, bootstrap=True, cidrs=args.cidrs)
    api.call('/api/settings/set', dnsServerDomain=args.domain, dnsServerLocalEndPoints=args.listen + ':53',
             webServiceLocalAddresses='127.0.0.1', webServiceHttpPort=5380, webServiceEnableTls='false',
             webServiceReverseProxyAddresses='127.0.0.1/32', webServiceRealIpHeader='X-Real-IP',
             recursion='UseSpecifiedNetworkACL', recursionNetworkACL=args.cidrs,
             forwarders=args.forwarders or 'false', forwarderProtocol='Udp',
             dnssecValidation='true', cacheMaximumEntries=10000, logQueries='false',
             maxLogFileDays=7, maxStatFileDays=7, enableBlocking='false',
             dnsServerEnableCheckForUpdate='false', dnsAppsEnableAutomaticUpdate='false',
             enableDnsOverHttp='false', enableDnsOverHttps='false', enableDnsOverTls='false', enableDnsOverQuic='false')


def check_host(args):
    interfaces = json.loads(subprocess.check_output(['ip', '-j', '-4', 'address', 'show'], text=True))
    if args.listen not in {addr['local'] for interface in interfaces for addr in interface.get('addr_info', [])}:
        raise ValueError('DNS_LISTEN_IPV4 não pertence a uma interface desta VM.')
    for kind in (socket.SOCK_STREAM, socket.SOCK_DGRAM):
        with socket.socket(socket.AF_INET, kind) as probe:
            try:
                probe.bind((args.listen, 53))
            except OSError:
                if not Path(args.managed).is_file():
                    raise ValueError('Porta 53 TCP/UDP já ocupada no IP escolhido; preserve o DNS existente.') from None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    for action in ('validate-network', 'bootstrap'):
        cmd = sub.add_parser(action)
        for name in ('listen', 'cidrs', 'forwarders'):
            cmd.add_argument('--' + name, required=True)
        if action == 'bootstrap':
            cmd.add_argument('--records', required=True)
            cmd.add_argument('--domain', required=True)
            cmd.add_argument('--password-file', required=True)
    cmd = sub.add_parser('validate-records')
    cmd.add_argument('--records', required=True)
    cmd.add_argument('--domain')
    cmd.add_argument('--listen')
    cmd = sub.add_parser('check-host')
    cmd.add_argument('--listen', required=True)
    cmd.add_argument('--managed', required=True)
    cmd = sub.add_parser('apply')
    cmd.add_argument('--records', required=True)
    cmd.add_argument('--password-file', required=True)
    cmd.add_argument('--cidrs', required=True)
    cmd.add_argument('--otp')
    cmd.add_argument('--replace', action='store_true')
    args = parser.parse_args()
    if args.command == 'validate-network':
        validate_network(args.listen, args.cidrs, args.forwarders)
    elif args.command == 'validate-records':
        read_records(args.records, args.domain, args.listen)
    elif args.command == 'check-host':
        check_host(args)
    else:
        # Valida antes de autenticar ou alterar o servidor.
        read_records(args.records, getattr(args, 'domain', None), getattr(args, 'listen', None))
        api = Api()
        try:
            api.login(args.password_file, getattr(args, 'otp', None))
            if args.command == 'bootstrap':
                validate_network(args.listen, args.cidrs, args.forwarders)
                bootstrap(api, args)
            else:
                zone, records = read_records(args.records)
                networks = [ipaddress.ip_network(cidr, strict=True) for cidr in args.cidrs.split(',')]
                if any(net.version != 4 or net.prefixlen == 0 for net in networks):
                    raise ValueError('Zonas criadas exigem redes IPv4 restritas.')
                apply_records(api, zone, records, args.replace, cidrs=args.cidrs)
        finally:
            api.logout()


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, KeyError, subprocess.CalledProcessError) as error:
        sys.exit('ERRO: ' + str(error))
