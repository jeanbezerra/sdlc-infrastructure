#!/usr/bin/env python3
"""CLI controladora Windows/Linux; operações de destino somente Ubuntu 26.04."""
import argparse
import base64
import getpass
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shlex
import socket
import sys
import tarfile
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
HERE = Path(__file__).resolve().parent
CATALOG = json.loads((HERE / 'catalog.json').read_text(encoding='utf-8'))
FILE_KEYS = {'TLS_CERT_PATH', 'TLS_KEY_PATH', 'DB_PASSWORD_FILE', 'DB_CA_PATH', 'DNS_RECORDS_PATH'}
RUN_ID = re.compile(r'[a-f0-9]{24}')


class InstallerError(Exception):
    pass


def require(condition, message):
    if not condition:
        raise InstallerError(message)


def regular_file(path):
    path = Path(path)
    require(path.is_file() and not path.is_symlink(), f'Arquivo ausente ou symlink: {path}')
    require(path.stat().st_size <= 8 * 1024 * 1024, f'Arquivo maior que 8 MiB: {path}')
    return path


def metadata(service):
    require(service in CATALOG['native'], CATALOG['pending'].get(service, 'Serviço desconhecido: ' + service))
    folder = service
    if service == 'keycloak' and not (ROOT / folder).is_dir():
        folder = 'keycloack'
    source = regular_file(ROOT / folder / 'linux/service.sh').read_text(encoding='utf-8')
    result = dict(service=service, folder=folder)
    for key in ('SERVICE_VERSION', 'PROXY_KIND', 'BACKEND_PORTS', 'PUBLIC_PORT', 'ARTIFACT', 'PACKAGE_GROUPS'):
        match = re.search(r'^' + key + r'=(.*)$', source, re.MULTILINE)
        require(match is not None, 'Metadado ausente: ' + key)
        result[key.lower()] = match[1].strip().strip('\"\'')
    return result


def local_path(base, value):
    require(isinstance(value, str) and value, 'Caminho local deve ser texto não vazio.')
    path = Path(value).expanduser()
    return path if path.is_absolute() else base / path


def inventory(path):
    data = json.loads(regular_file(path).read_text(encoding='utf-8'))
    require(isinstance(data, dict) and set(data) == {'schema', 'hosts', 'deployments'} and data['schema'] == 1,
            'Inventário exige schema=1, hosts e deployments.')
    require(isinstance(data['hosts'], dict) and isinstance(data['deployments'], dict), 'Hosts/deployments devem ser objetos.')
    for name, host in data['hosts'].items():
        require(isinstance(host, dict) and set(host) <= {'address', 'port', 'user', 'identity_file', 'known_hosts', 'host_key_sha256'},
                'Host inválido; senhas não pertencem ao inventário: ' + name)
        require(isinstance(host.get('address'), str) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9.:-]*', host['address']), 'Address inválido.')
        require(isinstance(host.get('user'), str) and re.fullmatch(r'[a-z_][a-z0-9_-]*', host['user']), 'User inválido.')
        require(type(host.get('port', 22)) is int and 1 <= host.get('port', 22) <= 65535, 'Porta SSH inválida.')
        if host.get('host_key_sha256'):
            require(re.fullmatch(r'SHA256:[A-Za-z0-9+/]{43}', host['host_key_sha256']), 'Fingerprint SHA256 inválido.')
    return data


def env_file(path):
    values = {}
    for line in regular_file(path).read_text(encoding='utf-8').splitlines():
        if not line or line.startswith('#'):
            continue
        match = re.fullmatch(r'([A-Z][A-Z0-9_]+)=(.*)', line)
        require(match and match[1] not in values, 'Configuração exige KEY=value sem duplicatas.')
        values[match[1]] = match[2]
    return values


def deployment(args, prepare=False):
    path = Path(args.inventory).absolute()
    data = inventory(path)
    require(args.deployment in data['deployments'], 'Deployment ausente: ' + args.deployment)
    item = data['deployments'][args.deployment]
    require(isinstance(item, dict) and set(item) <= {'host', 'service', 'config', 'manifest', 'overrides', 'files'}, 'Deployment inválido.')
    require(item.get('host') in data['hosts'], 'Host ausente no inventário.')
    meta = metadata(item.get('service', ''))
    host = dict(data['hosts'][item['host']])
    for key in ('identity_file', 'known_hosts'):
        if host.get(key):
            host[key] = str(regular_file(local_path(path.parent, host[key])).absolute())
    request = dict(action=args.action, service=meta['service'], metadata=meta,
                   keep_data=getattr(args, 'keep_data', False), ssh_connection='')
    files = {}
    if prepare and args.action == 'install':
        config = env_file(local_path(path.parent, item.get('config')))
        known_keys = set(env_file(ROOT / meta['folder'] / 'linux/.env.example'))
        require(set(config) <= known_keys, 'Chave não suportada no .env; use o modelo do serviço e arquivos para segredos.')
        overrides, inputs = item.get('overrides', {}), item.get('files', {})
        require(isinstance(overrides, dict) and isinstance(inputs, dict), 'Overrides/files devem ser objetos.')
        require(set(overrides) <= set(config), 'Override exige uma chave já presente no .env.')
        config.update(overrides)
        require(str(host.get('port', 22)) == config.get('SSH_PORT', '22'), 'SSH_PORT do serviço difere da conexão SSH.')
        target = '/opt/sdlc-remote/' + meta['service']
        files['inputs/manifest.json'] = regular_file(local_path(path.parent, item.get('manifest'))).read_bytes()
        manifest = json.loads(files['inputs/manifest.json'])
        require(manifest.get('schema') == 1 and manifest.get('platform') == 'ubuntu26.04-amd64', 'Manifesto/plataforma inválidos.')
        names = {entry['name'] for entry in manifest['artifacts']}
        required = {'ubuntu26-base.tar.gz'} | {'ubuntu26-' + group + '.tar.gz' for group in meta['package_groups'].split()}
        if meta['artifact']:
            required.add(meta['artifact'])
        require(required <= names, 'Manifesto não contém todos os artefatos do serviço.')
        config['MANIFEST_PATH'] = target + '/inputs/manifest.json'
        require(set(inputs) <= FILE_KEYS and set(inputs) <= set(config), 'Chave de files não suportada pelo serviço.')
        for key in FILE_KEYS & set(config):
            require(not config[key] or key in inputs, f'Informe o arquivo local em files.{key}; caminhos remotos livres não são aceitos.')
        for key, source in inputs.items():
            relative = 'inputs/' + key.lower()
            files[relative] = regular_file(local_path(path.parent, source)).read_bytes()
            config[key] = target + '/' + relative
        for key, value in config.items():
            require(isinstance(value, str) and not re.search(r'[\s\"\'`$;\\]', value), 'Valor literal inválido em ' + key)
        files['inputs/service.env'] = ''.join(f'{key}={value}\n' for key, value in config.items()).encode()
        request['config'] = config
        if meta['service'] == 'technitium':
            import importlib.util
            spec = importlib.util.spec_from_file_location('dns_config', ROOT / 'technitium/linux/configure.py')
            dns = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(dns)
            dns.read_records(local_path(path.parent, inputs.get('DNS_RECORDS_PATH')), config['DOMAIN'], config['DNS_LISTEN_IPV4'])
    return host, request, files


def bundle(request, inputs):
    files = dict(inputs)
    folder = request['metadata']['folder']
    for directory in (ROOT / 'scripts/linux', ROOT / folder / 'linux'):
        for path in directory.rglob('*'):
            require(not path.is_symlink(), 'Symlink no código: ' + str(path))
            if path.is_file() and '__pycache__' not in path.parts and path.name not in ('.env', '.env.example'):
                require(path.suffix in ('.sh', '.py', '.json', '.sql', '.groovy'), 'Arquivo inesperado no código: ' + str(path))
                content = regular_file(path).read_bytes()
                if path.suffix in ('.sh', '.py', '.groovy', '.sql'):
                    content = content.replace(b'\r\n', b'\n')
                files['project/' + path.relative_to(ROOT).as_posix()] = content
    files['request.json'] = json.dumps(request, ensure_ascii=False).encode()
    checksums = {name: hashlib.sha256(value).hexdigest() for name, value in files.items()}
    files['checksums.json'] = json.dumps(checksums).encode()
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode='w:gz') as archive:
        for name, content in sorted(files.items()):
            entry = tarfile.TarInfo(name)
            entry.size, entry.mode = len(content), 0o600
            archive.addfile(entry, io.BytesIO(content))
    require(output.tell() < 20 * 1024 * 1024, 'Payload maior que 20 MiB.')
    return output.getvalue()


def fingerprint(key):
    return 'SHA256:' + base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode().rstrip('=')


def ssh_module():
    try:
        import paramiko
        return paramiko
    except ImportError:
        raise InstallerError('Instale installer/requirements.txt no ambiente Python.') from None


class Remote:
    def __init__(self, host, args):
        paramiko = ssh_module()
        self.host, self.args, self.secrets = host, args, []
        self.password = os.environ.pop('SDLC_SSH_PASSWORD', None)
        self.sudo_password = os.environ.pop('SDLC_SUDO_PASSWORD', None)
        passphrase = os.environ.pop('SDLC_KEY_PASSPHRASE', None)
        if args.ask_pass:
            self.password = getpass.getpass('Senha SSH: ')
        if args.ask_sudo_pass:
            self.sudo_password = getpass.getpass('Senha sudo: ')
        self.secrets = [value for value in (self.password, self.sudo_password, passphrase) if value]
        self.client = paramiko.SSHClient()
        pin = host.get('host_key_sha256')
        known = Path(host.get('known_hosts', Path.home() / '.ssh/known_hosts'))
        if not pin and known.is_file():
            self.client.load_host_keys(str(known))
        if pin:
            # O pin é a fonte de confiança e é conferido antes de autenticar.
            class Pinned(paramiko.MissingHostKeyPolicy):
                def missing_host_key(self, client, hostname, key):
                    require(fingerprint(key) == pin, 'Fingerprint SSH diverge do inventário.')
            self.client.set_missing_host_key_policy(Pinned())
        else:
            self.client.set_missing_host_key_policy(paramiko.RejectPolicy())
        try:
            self.client.connect(host['address'], port=host.get('port', 22), username=host['user'],
                                key_filename=host.get('identity_file'), password=self.password,
                                passphrase=passphrase, allow_agent=self.password is None,
                                look_for_keys=self.password is None, timeout=15, auth_timeout=20, banner_timeout=20)
            if pin:
                require(fingerprint(self.client.get_transport().get_remote_server_key()) == pin, 'Fingerprint SSH diverge do inventário.')
            self.client.get_transport().set_keepalive(20)
        except Exception:
            self.client.close()
            raise

    def redact(self, value):
        for secret in self.secrets:
            value = value.replace(secret, '[REDACTED]')
        return value

    def command(self, command, root=False):
        if root and self.host['user'] != 'root':
            command = ('sudo -S -p \'\' ' if self.sudo_password else 'sudo -n ') + command
        stdin, stdout, stderr = self.client.exec_command(command, timeout=60)
        if root and self.sudo_password and self.host['user'] != 'root':
            stdin.write(self.sudo_password + '\n')
            stdin.flush()
        stdin.channel.shutdown_write()
        channel = stdout.channel
        out, err, deadline = bytearray(), bytearray(), time.monotonic() + 60
        while True:
            if channel.recv_ready():
                out.extend(channel.recv(65536))
            if channel.recv_stderr_ready():
                err.extend(channel.recv_stderr(65536))
            if channel.exit_status_ready() and not channel.recv_ready() and not channel.recv_stderr_ready():
                break
            require(time.monotonic() < deadline, 'Timeout do comando SSH; consulte status para operações já iniciadas.')
            time.sleep(.02)
        code = channel.recv_exit_status()
        result = self.redact(out.decode('utf-8', errors='replace'))
        require(code == 0, self.redact(err.decode('utf-8', errors='replace') or result or f'Comando remoto falhou: {code}'))
        return result

    def check_sudo(self):
        self.command('/usr/bin/true', root=True)

    def upload(self, request, payload):
        run = uuid.uuid4().hex[:24]
        print(f"ID preparado: {run}. Recuperação: status {self.args.deployment} --run {run}", flush=True)
        sftp = self.client.open_sftp()
        stage = None
        try:
            home = sftp.normalize('.')
            stage = home + '/.sdlc-remote-' + run
            sftp.mkdir(stage, mode=0o700)
            for name, content in (('payload.tar.gz', payload), ('remote.py', (HERE / 'remote.py').read_bytes().replace(b'\r\n', b'\n'))):
                with sftp.file(stage + '/' + name, 'wb') as stream:
                    stream.write(content)
                sftp.chmod(stage + '/' + name, 0o600)
            command = '/usr/bin/env "SSH_CONNECTION=$SSH_CONNECTION" /usr/bin/python3 ' + shlex.quote(stage + '/remote.py')
            command += ' launch --stage ' + shlex.quote(stage) + ' --service ' + request['service'] + ' --run ' + run
            result = json.loads(self.command(command, root=True))
            return result
        finally:
            # Somente os dois arquivos e o diretório exclusivo desta execução.
            if stage:
                for name in ('payload.tar.gz', 'remote.py'):
                    try:
                        sftp.remove(stage + '/' + name)
                    except OSError:
                        pass
                try:
                    sftp.rmdir(stage)
                except OSError:
                    pass
            sftp.close()

    def status(self, service, run, offset=0):
        require(RUN_ID.fullmatch(run), 'ID de execução inválido.')
        path = f'/opt/sdlc-remote/{service}/jobs/{run}/remote.py'
        command = f'/usr/bin/python3 {shlex.quote(path)} status --service {service} --run {run} --offset {offset}'
        return json.loads(self.command(command, root=True))

    def close(self):
        self.client.close()


def follow(remote, service, run, args):
    directory = Path(args.state_dir) / 'runs' / run
    directory.mkdir(parents=True, mode=0o700, exist_ok=True)
    os.chmod(directory, 0o700)
    log = directory / 'operation.log'
    log.touch(mode=0o600, exist_ok=True)
    os.chmod(log, 0o600)
    log.write_text('', encoding='utf-8')
    offset, deadline = 0, time.monotonic() + args.timeout
    while True:
        result = remote.status(service, run, offset)
        log_complete = result.pop('log_complete')
        text = remote.redact(result.pop('output'))
        if text:
            print(text, end='', flush=True)
            with log.open('a', encoding='utf-8') as stream:
                stream.write(text)
        offset = result.pop('offset')
        report = directory / 'result.json'
        report.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        os.chmod(report, 0o600)
        if result['state'] in ('succeeded', 'failed', 'interrupted') and log_complete:
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0 if result['state'] == 'succeeded' else 1
        require(time.monotonic() < deadline, f'Acompanhamento expirou; operação remota continua. Use status com --run {run}.')
        if log_complete:
            time.sleep(2)


def parser():
    command = argparse.ArgumentParser(description=__doc__)
    sub = command.add_subparsers(dest='command', required=True)
    sub.add_parser('services', help='Lista serviços suportados e pendentes, sem SSH.')
    for action in ('fingerprint', 'plan', 'install', 'verify', 'uninstall', 'status'):
        cmd = sub.add_parser(action)
        cmd.add_argument('deployment' if action != 'fingerprint' else 'host')
        cmd.add_argument('--inventory', default=str(HERE / 'inventory.json'))
        if action == 'plan':
            cmd.add_argument('--action', choices=['install', 'verify', 'uninstall'], default='install')
        elif action != 'fingerprint':
            cmd.set_defaults(action=action)
        if action == 'uninstall':
            cmd.add_argument('--yes', action='store_true', help='Autoriza remoção; o padrão apaga dados.')
            cmd.add_argument('--keep-data', action='store_true')
        if action == 'status':
            cmd.add_argument('--run', required=True)
            cmd.add_argument('--follow', action='store_true')
        if action in ('install', 'verify', 'uninstall', 'status'):
            cmd.add_argument('--ask-pass', action='store_true')
            cmd.add_argument('--ask-sudo-pass', action='store_true')
            cmd.add_argument('--state-dir', default=str(HERE / '.state'))
            cmd.add_argument('--timeout', type=int, default=3600)
    return command


def main(argv=None):
    args = parser().parse_args(argv)
    remote = None
    try:
        if args.command == 'services':
            for service in CATALOG['native']:
                meta = metadata(service)
                print(f"{service:12} pronto    {meta['service_version']}    {meta['proxy_kind']}:{meta['public_port']}")
            for service, reason in CATALOG['pending'].items():
                print(f'{service:12} pendente  {reason}')
            return 0
        if args.command == 'fingerprint':
            host = inventory(args.inventory)['hosts'].get(args.host)
            require(host, 'Host ausente no inventário.')
            paramiko = ssh_module()
            with socket.create_connection((host['address'], host.get('port', 22)), timeout=15) as connection:
                transport = paramiko.Transport(connection)
                try:
                    transport.start_client(timeout=15)
                    print(fingerprint(transport.get_remote_server_key()))
                    print('Chave observada sem autenticação. Confira pela console da VM antes de fixá-la no inventário.')
                finally:
                    transport.close()
            return 0
        if args.command == 'uninstall':
            require(args.yes, 'Desinstalação exige --yes; --keep-data preserva dados e segredos.')
        host, request, files = deployment(args, prepare=args.command in ('plan', 'install'))
        if args.command == 'plan':
            print(json.dumps(dict(deployment=args.deployment, host=host['address'], user=host['user'],
                                  port=host.get('port', 22), request=request,
                                  uploads={name: hashlib.sha256(value).hexdigest() for name, value in files.items()}),
                             ensure_ascii=False, indent=2))
            return 0
        require(args.timeout > 0, 'Timeout deve ser positivo.')
        payload = bundle(request, files) if args.command != 'status' else None
        remote = Remote(host, args)
        remote.check_sudo()
        if args.command == 'status':
            if args.follow:
                return follow(remote, request['service'], args.run, args)
            result = remote.status(request['service'], args.run)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 1 if result['state'] in ('failed', 'interrupted') else 0
        result = remote.upload(request, payload)
        run = result['run']
        print(f"Execução {run}: {args.command} {request['service']} em {host['address']}. Use status --run {run} para reconectar.", flush=True)
        return follow(remote, request['service'], run, args)
    except KeyboardInterrupt:
        print('Acompanhamento interrompido; operações já iniciadas continuam na VM. Consulte status.', file=sys.stderr)
        return 130
    except Exception as error:
        message = str(error)
        if remote:
            message = remote.redact(message)
        print('ERRO: ' + message, file=sys.stderr)
        return 1
    finally:
        if remote:
            remote.close()


if __name__ == '__main__':
    # Logs das VMs são UTF-8, inclusive quando o controlador é Windows.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8', errors='replace')
    raise SystemExit(main())
