#!/usr/bin/env python3
"""Executor Ubuntu, somente stdlib; arquivos e relatórios privados de root."""
import argparse
import base64
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import ipaddress
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import socket
import ssl
import struct
import subprocess
import sys
import tarfile
import urllib.error
import urllib.request

SERVICES = {'grafana', 'jenkins', 'keycloak', 'nexus', 'opensearch', 'postgresql', 'prometheus', 'redis', 'sonarqube', 'technitium'}
BASE = Path('/opt/sdlc-remote')
SERVICE_BASE = Path('/opt/sdlc')
MARKER = 'sdlc-remote-v1'


def require(condition, message):
    if not condition:
        raise ValueError(message)


def now():
    return datetime.now(timezone.utc).isoformat()


def write_json(path, value):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    temporary.chmod(0o600)
    os.replace(temporary, path)


def safe_directory(path):
    require(not any(parent.is_symlink() for parent in (path, *path.parents)), 'Symlink em diretório remoto.')
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    require(path.is_dir() and path.stat().st_uid == 0, 'Diretório remoto precisa pertencer a root.')
    path.chmod(0o700)


def workspace(service, create=False):
    require(service in SERVICES, 'Serviço não suportado.')
    for path in (BASE, BASE / service):
        if not path.exists() and create:
            safe_directory(path)
            (path / '.managed').write_text(MARKER, encoding='utf-8')
        require(not path.is_symlink() and path.is_dir() and (path / '.managed').read_text() == MARKER,
                'Diretório remoto sem marcador de propriedade.')
        safe_directory(path)
    return BASE / service


def job_path(service, run):
    require(re.fullmatch(r'[a-f0-9]{24}', run), 'ID de execução inválido.')
    root = workspace(service)
    path = root / 'jobs' / run
    require(path.is_dir() and not path.is_symlink(), 'Execução inexistente.')
    return path


def unpack(archive, destination):
    """Extrai somente arquivos regulares, sem traversal, links ou duplicatas."""
    names, total = set(), 0
    with tarfile.open(archive, 'r:gz') as stream:
        for entry in stream:
            name = PurePosixPath(entry.name)
            require(entry.isfile() and not name.is_absolute() and '\\' not in entry.name and
                    all(part not in ('', '.', '..') for part in entry.name.split('/')),
                    'Entrada inválida no payload.')
            require(entry.name not in names and len(names) < 256, 'Payload duplicado ou com arquivos demais.')
            total += entry.size
            require(0 <= entry.size <= 8 * 1024 * 1024 and total <= 24 * 1024 * 1024, 'Payload excede limite de tamanho.')
            names.add(entry.name)
            target = destination / name
            safe_directory(target.parent)
            with target.open('xb') as output, stream.extractfile(entry) as content:
                shutil.copyfileobj(content, output)
            target.chmod(0o600)
    checksums = json.loads((destination / 'checksums.json').read_text())
    require(set(checksums) == names - {'checksums.json'}, 'Índice do payload difere dos arquivos.')
    for name, expected in checksums.items():
        require(hashlib.sha256((destination / name).read_bytes()).hexdigest() == expected, 'SHA256 incorreto no payload.')
    request = json.loads((destination / 'request.json').read_text())
    folder = request['metadata']['folder']
    require(folder == request['service'] or request['service'] == 'keycloak' and folder == 'keycloack', 'Pasta de serviço inválida.')
    allowed_inputs = {'service.env', 'manifest.json', 'tls_cert_path', 'tls_key_path', 'db_password_file', 'db_ca_path', 'dns_records_path'}
    for name in names:
        require(name in ('request.json', 'checksums.json') or
                name.startswith(('project/scripts/linux/', f'project/{folder}/linux/')) or
                name.startswith('inputs/') and name.split('/', 1)[1] in allowed_inputs, 'Arquivo fora dos caminhos permitidos.')
    return request


def host_check():
    require(os.geteuid() == 0, 'Execute com sudo/root.')
    release = dict(line.split('=', 1) for line in Path('/etc/os-release').read_text().splitlines() if '=' in line)
    require(release.get('ID', '').strip('\"') == 'ubuntu' and release.get('VERSION_ID', '').strip('\"') == '26.04',
            'Destino suportado: Ubuntu 26.04 LTS.')
    require(subprocess.check_output(['dpkg', '--print-architecture'], text=True).strip() == 'amd64', 'Destino exige amd64.')
    require(Path('/run/systemd/system').is_dir(), 'Destino exige systemd.')
    for command in ('bash', 'flock', 'curl', 'systemd-run'):
        require(shutil.which(command), 'Pré-requisito ausente: ' + command)


def launch(args):
    host_check()
    require(re.fullmatch(r'[a-f0-9]{24}', args.run), 'ID de execução inválido.')
    stage = Path(args.stage)
    require(stage.is_absolute() and stage.name == '.sdlc-remote-' + args.run and not stage.is_symlink(), 'Stage inválido.')
    require(not any(parent.is_symlink() for parent in stage.parents), 'Symlink nos pais de stage.')
    archive = stage / 'payload.tar.gz'
    require(archive.is_file() and not archive.is_symlink() and archive.stat().st_size <= 20 * 1024 * 1024, 'Payload inválido.')
    root = workspace(args.service, create=True)
    safe_directory(root / 'jobs')
    job = root / 'jobs' / args.run
    require(not job.exists(), 'ID já utilizado.')
    safe_directory(job)
    # Copia os bytes antes de extrair; a execução systemd nunca usa código do usuário.
    frozen = job / 'payload.tar.gz'
    shutil.copyfile(archive, frozen)
    frozen.chmod(0o600)
    request = unpack(frozen, job)
    require(request['service'] == args.service and request['action'] in ('install', 'verify', 'uninstall'), 'Operação inválida.')
    require(type(request.get('keep_data')) is bool, 'Keep_data inválido.')
    connection = os.environ.get('SSH_CONNECTION', '')
    if connection:
        parts = connection.split()
        require(len(parts) == 4, 'SSH_CONNECTION inválido.')
        ipaddress.ip_address(parts[0]); ipaddress.ip_address(parts[2])
        require(all(part.isdigit() for part in (parts[1], parts[3])), 'Portas de SSH_CONNECTION inválidas.')
    request['ssh_connection'] = connection
    write_json(job / 'request.json', request)
    shutil.copyfile(__file__, job / 'remote.py')
    (job / 'remote.py').chmod(0o600)
    frozen.unlink()
    result = dict(run=args.run, service=args.service, action=request['action'], state='queued', phase='queued',
                  started_at=now(), payload_sha256=hashlib.sha256(archive.read_bytes()).hexdigest())
    write_json(job / 'result.json', result)
    (job / 'operation.log').touch(mode=0o600)
    unit = f'sdlc-remote-{args.service}-{args.run}'
    command = ['systemd-run', '--quiet', '--unit=' + unit, '--property=Type=exec', '--property=UMask=0077',
               '--property=RuntimeMaxSec=7200', '/usr/bin/python3', str(job / 'remote.py'),
               'execute', '--service', args.service, '--run', args.run]
    start = subprocess.run(command, text=True, capture_output=True)
    if start.returncode:
        result.update(state='failed', phase='launch', exit_code=start.returncode, error='systemd-run recusou a execução.', finished_at=now())
        write_json(job / 'result.json', result)
        raise ValueError(result['error'])
    print(json.dumps(result))


@contextmanager
def operation_lock(service):
    with open('/run/lock/sdlc-remote-' + service + '.lock', 'a') as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError('Outra operação remota do mesmo serviço está em execução.') from None
        yield


def env_values(path):
    result = {}
    for line in path.read_text().splitlines():
        if line and not line.startswith('#'):
            key, value = line.split('=', 1)
            result[key] = value
    return result


def redact(text, root, service):
    paths = list((SERVICE_BASE / service / 'secrets').glob('*'))
    paths += [root / 'inputs/db_password_file']
    for path in paths:
        if path.is_file() and not path.is_symlink() and path.stat().st_size < 4096:
            secret = path.read_text(errors='replace').strip()
            if len(secret) >= 8:
                text = text.replace(secret, '[REDACTED]')
    return text


def script(command, env, emit):
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               text=True, encoding='utf-8', errors='replace', env=env)
    with process.stdout:
        for line in process.stdout:
            emit(line)
    require(process.wait() == 0, 'Script retornou falha; veja operation.log. Estado parcial preservado.')


def promote(job, root, service):
    current = SERVICE_BASE / service / 'install.env'
    incoming = job / 'inputs/service.env'
    if current.exists():
        require(current.read_bytes() == incoming.read_bytes(), 'Configuração difere da instalação existente; manutenção explícita necessária.')
        for source in (job / 'inputs').iterdir():
            previous = root / 'inputs' / source.name
            if source.name != 'manifest.json' and previous.exists():
                require(source.read_bytes() == previous.read_bytes(), 'Arquivo de configuração/segredo alterado; manutenção explícita necessária.')
    for name in ('project', 'inputs'):
        safe_directory(root / name)
        for source in (job / name).rglob('*'):
            target = root / name / source.relative_to(job / name)
            require(not target.is_symlink(), 'Symlink na área de implantação.')
            if source.is_dir():
                safe_directory(target)
            else:
                safe_directory(target.parent)
                shutil.copyfile(source, target)
                target.chmod(0o600)


def checked(command, env=None):
    result = subprocess.run(command, env=env, text=True, capture_output=True, timeout=30)
    require(result.returncode == 0, 'Verificação falhou: ' + command[0])
    return result.stdout.strip()


def http_probe(url, ca=None, password=None):
    handlers = [urllib.request.ProxyHandler({})]
    if ca:
        handlers.append(urllib.request.HTTPSHandler(context=ssl.create_default_context(cafile=str(ca))))
    request = urllib.request.Request(url)
    if password:
        request.add_header('Authorization', 'Basic ' + base64.b64encode(('admin:' + password).encode()).decode())
    try:
        with urllib.request.build_opener(*handlers).open(request, timeout=10) as response:
            return response.status, response.read(65536)
    except urllib.error.HTTPError as error:
        return error.code, error.read(65536)


def verify_service(request):
    service, meta = request['service'], request['metadata']
    base = SERVICE_BASE / service
    require((base / '.installed').is_file() and (base / '.managed').read_text().strip() == 'sdlc-v1:' + service, 'Serviço não instalado/concluído por este projeto.')
    config = env_values(base / 'install.env')
    checked(['systemctl', 'is-active', 'sdlc-' + service + '.service'])
    checked(['systemctl', 'is-active', 'nginx.service'])
    checked(['nginx', '-t'])
    require('Status: active' in checked(['ufw', 'status'], dict(os.environ, LC_ALL='C')), 'UFW não está ativo.')
    details = dict(unit='active', nginx='active/config-valid', firewall='active')
    port = meta['backend_ports'].split()[0]
    if service == 'postgresql':
        result = checked(['runuser', '-u', 'sdlc-postgresql', '--', '/usr/lib/postgresql/18/bin/psql',
                          '-X', '-h', '/var/lib/sdlc/postgresql/run', '-p', port, '-U', 'sdlc_admin', '-d', 'postgres', '-Atqc', 'SELECT 1'])
        require(result == '1', 'PostgreSQL não respondeu SELECT 1.')
        details['backend'] = 'SELECT 1'
    elif service == 'redis':
        env = dict(os.environ, REDISCLI_AUTH=(base / 'secrets/admin_password').read_text().strip())
        result = checked(['redis-cli', '--user', 'admin', '-h', '127.0.0.1', '-p', port, 'PING'], env)
        require(result == 'PONG', 'Redis não respondeu PONG.')
        details['backend'] = 'PONG autenticado'
    elif service == 'technitium':
        for tcp in (False, True):
            command = ['dig', '@' + config['DNS_LISTEN_IPV4'], config['DOMAIN'], 'A', '+short', '+time=3', '+tries=1']
            if tcp:
                command.append('+tcp')
            require(config['DNS_LISTEN_IPV4'] in checked(command).splitlines(), 'DNS não respondeu o registro do painel.')
        code, _ = http_probe('http://127.0.0.1:' + port + '/')
        require(code == 200, 'Painel Technitium não respondeu.')
        details['backend'] = 'DNS UDP/TCP e painel HTTP'
    else:
        paths = {'grafana': '/api/health', 'prometheus': '/-/ready', 'jenkins': '/api/json',
                 'sonarqube': '/api/system/status', 'keycloak': '/realms/master', 'nexus': '/service/rest/v1/status'}
        secure = service == 'opensearch'
        password = (base / 'secrets/admin_password').read_text().strip() if secure or service == 'jenkins' else None
        url = ('https://localhost:' if secure else 'http://127.0.0.1:') + port + paths.get(service, '/')
        code, body = http_probe(url, base / 'config/root-ca.crt' if secure else None, password)
        require(code == 200, 'Health do backend retornou HTTP ' + str(code))
        if service == 'sonarqube':
            require(json.loads(body)['status'] == 'UP', 'SonarQube ainda não está UP.')
        if service == 'grafana':
            require(json.loads(body)['database'] == 'ok', 'Banco interno Grafana não está operacional.')
        details['backend'] = 'HTTP 200'
    if meta['proxy_kind'] == 'http':
        code = checked(['curl', '--silent', '--show-error', '--noproxy', '*', '--max-time', '10', '--output', '/dev/null',
                        '--write-out', '%{http_code}', '--cacert', str(base / 'tls/server.crt'),
                        '--resolve', config['DOMAIN'] + ':443:127.0.0.1', 'https://' + config['DOMAIN'] + '/'])
        require(code in ('200', '201', '204', '301', '302', '303', '401', '403'), 'Proxy retornou HTTP ' + code)
        details['proxy'] = 'Certificado/hostname válidos; HTTP ' + code + (' (ACL/autenticação)' if code in ('401', '403') else '')
    else:
        connection = request.get('ssh_connection', '').split()
        address = connection[2] if len(connection) == 4 else '127.0.0.1'
        if any(ipaddress.ip_address(address) in ipaddress.ip_network(cidr) for cidr in config['ACCESS_CIDRS'].split(',')):
            context = ssl.create_default_context(cafile=str(base / 'tls/server.crt'))
            with socket.create_connection((address, int(meta['public_port'])), timeout=10) as raw:
                if service == 'postgresql':
                    raw.sendall(struct.pack('!II', 8, 80877103))
                    require(raw.recv(1) == b'S', 'Proxy PostgreSQL recusou TLS.')
                with context.wrap_socket(raw, server_hostname=config['DOMAIN']):
                    details['proxy'] = 'TLS e hostname válidos'
        else:
            listeners = checked(['ss', '-H', '-lnt', 'sport = :' + meta['public_port']])
            require(listeners, 'Porta pública ausente.')
            details['proxy'] = 'Listener presente; TLS não sondado porque ACCESS_CIDRS exclui a própria VM.'
    return details


def verify(request):
    with open('/run/lock/sdlc-infrastructure.lock', 'a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError('Instalador/desinstalador SDLC em execução; tente verificar depois.') from None
        return verify_service(request)


def execute(args):
    job = job_path(args.service, args.run)
    root = workspace(args.service)
    request = json.loads((job / 'request.json').read_text())
    result = json.loads((job / 'result.json').read_text())

    def emit(text):
        with (job / 'operation.log').open('a', encoding='utf-8') as stream:
            stream.write(redact(text, root, args.service))

    def phase(value):
        result.update(state='running', phase=value)
        write_json(job / 'result.json', result)
        emit('[remote] ' + value + '\n')

    try:
        with operation_lock(args.service):
            phase('preflight')
            host_check()
            env = dict(os.environ, SSH_CONNECTION=request['ssh_connection'])
            folder = request['metadata']['folder']
            if request['action'] == 'install':
                promote(job, root, args.service)
                project = root / 'project'
                # Mesmas validações dos scripts, sem instalar pacotes nem publicar o proxy.
                preflight = 'REPO_DIR=$1; ACTION=install; source "$1/scripts/linux/common.sh"; source "$1/$2/linux/service.sh"; CONFIG_FILE=$3; check_host; read_config "$3"; check_paths; check_domain; check_ports; service_preflight'
                script(['bash', '-c', preflight, 'preflight', str(project), folder, str(root / 'inputs/service.env')], env, emit)
                if request['metadata']['proxy_kind'] == 'http':
                    for public_port in ('80', '443'):
                        listeners = checked(['ss', '-H', '-lntp', 'sport = :' + public_port])
                        require(all('"nginx"' in line for line in listeners.splitlines()),
                                'Porta HTTP pública ocupada por outro processo: ' + public_port)
                config = request['config']
                probe = urllib.request.Request(config['ARTIFACT_BASE_URL'].rstrip('/') + '/ubuntu26-base.tar.gz', method='HEAD')
                with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(probe, timeout=15) as response:
                    require(response.status == 200, 'CDN não respondeu HTTP 200.')
                result['preflight'] = dict(platform='ubuntu26.04-amd64', cdn='reachable',
                                           memory_kib=int(re.search(r'^MemTotal:\s+(\d+)', Path('/proc/meminfo').read_text(), re.MULTILINE)[1]),
                                           disk_free_bytes=shutil.disk_usage(root).free)
                phase('install')
                script(['bash', str(project / folder / 'linux/install.sh'), '--config', str(root / 'inputs/service.env')], env, emit)
                phase('verify')
                result['checks'] = verify(request)
            elif request['action'] == 'verify':
                phase('verify')
                result['checks'] = verify(request)
            else:
                phase('uninstall')
                command = ['bash', str(job / 'project' / folder / 'linux/uninstall.sh'), '--yes']
                if request['keep_data']:
                    command.append('--keep-data')
                script(command, env, emit)
                installed = SERVICE_BASE / args.service
                require(not (installed / '.installed').exists(), 'Marcador de instalação permaneceu após a remoção.')
                state = subprocess.run(['systemctl', 'is-active', '--quiet', 'sdlc-' + args.service + '.service']).returncode
                require(state != 0, 'Unit permaneceu ativa após remoção.')
                result['checks'] = dict(unit='inactive', data='preserved' if request['keep_data'] else 'removed')
                if not request['keep_data']:
                    require(not installed.exists() and not (Path('/var/lib/sdlc') / args.service).exists(), 'Dados permaneceram após remoção completa.')
                    for name in ('inputs', 'project'):
                        path = root / name
                        if path.exists():
                            require(not path.is_symlink(), 'Symlink na área de implantação.')
                            shutil.rmtree(path)
            result.update(state='succeeded', phase='completed', exit_code=0)
    except Exception as error:
        result.update(state='failed', error=redact(str(error), root, args.service), exit_code=1)
        emit('[remote] ERRO: ' + result['error'] + '\n')
    finally:
        result['finished_at'] = now()
        write_json(job / 'result.json', result)
        # Preserva auditoria, não cópias redundantes de segredos ou código por job.
        for name in ('inputs', 'project'):
            path = job / name
            if path.is_dir() and not path.is_symlink():
                shutil.rmtree(path)
    return result['exit_code']


def status(args):
    job = job_path(args.service, args.run)
    result = json.loads((job / 'result.json').read_text())
    if result['state'] in ('queued', 'running'):
        unit = f'sdlc-remote-{args.service}-{args.run}'
        state = subprocess.run(['systemctl', 'show', unit, '--property=ActiveState', '--value'], text=True, capture_output=True).stdout.strip()
        if state not in ('active', 'activating', 'deactivating'):
            # Releitura: o worker pode ter terminado entre a leitura e systemctl show.
            result = json.loads((job / 'result.json').read_text())
            if result['state'] in ('queued', 'running'):
                result.update(state='interrupted', error='Worker inativo sem resultado final (reboot/limite/encerramento). Estado preservado; verifique antes de retomar.')
    require(args.offset >= 0, 'Offset inválido.')
    with (job / 'operation.log').open('rb') as stream:
        stream.seek(args.offset)
        content = stream.read(65536)
        result.update(output=content.decode('utf-8', errors='replace'), offset=stream.tell(),
                      log_complete=stream.tell() >= (job / 'operation.log').stat().st_size)
    print(json.dumps(result, ensure_ascii=False))


def main():
    command = argparse.ArgumentParser(description=__doc__)
    command.add_argument('action', choices=['launch', 'execute', 'status'])
    command.add_argument('--service', required=True)
    command.add_argument('--run', required=True)
    command.add_argument('--stage')
    command.add_argument('--offset', type=int, default=0)
    args = command.parse_args()
    try:
        require(os.geteuid() == 0, 'Execute com sudo/root.')
        if args.action == 'launch':
            launch(args)
        elif args.action == 'execute':
            return execute(args)
        else:
            status(args)
        return 0
    except Exception as error:
        print('ERRO: ' + str(error), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
