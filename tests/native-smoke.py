"""Smoke nativo sem root, instalação APT, systemd real ou mudanças no firewall do host."""
import base64
import getpass
import importlib.util
import json
import os
from pathlib import Path
import resource
import shlex
import socket
import ssl
import subprocess
import tempfile
import time
import urllib.error
import urllib.request

REPO = Path(__file__).resolve().parents[1]
CDN = REPO / 'artifacts/cdn/ubuntu26-amd64'
spec = importlib.util.spec_from_file_location('artifacts', REPO / 'scripts/linux/artifacts.py')
artifacts = importlib.util.module_from_spec(spec)
spec.loader.exec_module(artifacts)
SERVICES = ['postgresql', 'redis', 'prometheus', 'grafana', 'jenkins', 'keycloak', 'sonarqube', 'opensearch', 'nexus']
PASSWORD = 'Aa1_native_smoke_0123456789abcdef'


def run(args, env=None, **options):
    return subprocess.run(args, env=env, check=True, text=True, capture_output=True, **options)


def free_port():
    with socket.socket() as stream:
        stream.bind(('127.0.0.1', 0))
        return stream.getsockname()[1]


def stop(process):
    if process.poll() is None:
        # Todos os processos são filhos em uma sessão própria criada por este teste.
        import signal
        os.killpg(process.pid, signal.SIGTERM)
        try:
            process.wait(timeout=30)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=10)


def check_loopback(process):
    import ipaddress
    pids = set()
    for file in Path('/proc').glob('[0-9]*/stat'):
        try:
            # Campos após comm: state, ppid, pgrp, session.
            fields = file.read_text().rsplit(')', 1)[1].split()
            if int(fields[3]) == process.pid:
                pids.add(int(file.parent.name))
        except (OSError, ValueError):
            pass
    import re
    for line in run(['ss', '-H', '-lntp']).stdout.splitlines():
        owners = {int(pid) for pid in re.findall(r'pid=(\d+)', line)}
        if owners & pids:
            address = ipaddress.ip_address(line.split()[3].rsplit(':', 1)[0].strip('[]'))
            local = address.is_loopback or (isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None and address.ipv4_mapped.is_loopback)
            assert local, 'Listener não local: ' + line


def http(url, password=None, ca=None, data=None):
    request = urllib.request.Request(url, data=data)
    if password:
        request.add_header('Authorization', 'Basic ' + base64.b64encode(('admin:' + password).encode()).decode())
    context = ssl.create_default_context(cafile=str(ca)) if ca else None
    try:
        with urllib.request.urlopen(request, timeout=5, context=context) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()


def ready(url, password=None, ca=None, expected=200, process=None, limit=150):
    deadline = time.monotonic() + limit
    while time.monotonic() < deadline:
        if process is not None and process.poll() is not None:
            raise RuntimeError('Processo terminou antes da prontidão: ' + url)
        try:
            status, body = http(url, password, ca)
            if status == expected:
                return body
        except (OSError, TimeoutError) as error:
            if isinstance(error, urllib.error.URLError) and isinstance(error.reason, ssl.SSLCertVerificationError):
                raise
        time.sleep(1)
    raise RuntimeError('Timeout de prontidão: ' + url)


def main():
    if os.geteuid() == 0:
        raise RuntimeError('Smoke deve executar sem root; usa somente diretórios temporários.')
    # /tmp pode ser tmpfs pequeno: distribuições Java exigem armazenamento em disco.
    work = Path(tempfile.mkdtemp(prefix='sdlc-native-smoke-', dir='/var/tmp'))
    print('Smoke isolado: ' + str(work), flush=True)
    deps = work / 'deps'
    deps.mkdir()
    # Descompacta pacotes, sem executar scripts de manutenção ou instalar no host.
    for group in ['base', 'java21', 'postgresql18', 'redis']:
        bundle = work / ('bundle-' + group)
        artifacts.extract(CDN / ('ubuntu26-' + group + '.tar.gz'), bundle)
        for package in bundle.glob('*.deb'):
            run(['dpkg-deb', '-x', str(package), str(deps)])
    # Pacotes Debian usam links absolutos para /etc; redireciona somente os links
    # cuja contraparte foi extraída, sem alterar /etc do host.
    for link in deps.rglob('*'):
        if link.is_symlink() and os.readlink(link).startswith('/'):
            target = deps / os.readlink(link).lstrip('/')
            if target.exists():
                link.unlink()
                link.symlink_to(os.path.relpath(target, link.parent))
    import builtins, sys
    builtins._ = lambda text: text
    sys.path.insert(0, str(deps / 'usr/lib/python3/dist-packages'))
    from ufw.common import UFWRule
    first = UFWRule('allow', 'tcp', '80,443', src='10.20.0.0/16')
    second = UFWRule('allow', 'tcp', '80,443', src='10.20.0.0/16')
    first.dapp = 'SDLC-grafana'; second.dapp = 'SDLC-jenkins'
    assert first.match(second) == 1
    assert 'dapp_SDLC-grafana' in str(first) and 'dapp_SDLC-jenkins' in str(second)
    print('UFW: identidade dos perfis compartilhando portas: OK', flush=True)
    env = dict(os.environ)
    env['LD_LIBRARY_PATH'] = str(deps / 'usr/lib/x86_64-linux-gnu')
    env['PATH'] = str(deps / 'usr/bin') + ':' + env['PATH']
    java_home = deps / 'usr/lib/jvm/java-21-openjdk-amd64'
    env['JAVA_HOME'] = str(java_home)
    run([str(java_home / 'bin/java'), '-version'], env)
    states = {}
    processes = []

    def launch(command, service_env, name):
        log = (work / (name + '.log')).open('w')
        def limits():
            resource.setrlimit(resource.RLIMIT_NOFILE, (65536, resource.getrlimit(resource.RLIMIT_NOFILE)[1]))
        process = subprocess.Popen(command, env=service_env, stdout=log, stderr=subprocess.STDOUT,
                                   start_new_session=True, preexec_fn=limits)
        log.close()
        processes.append(process)
        return process

    try:
        for folder in SERVICES:
            service = 'keycloak' if folder == 'keycloack' else folder
            base = work / service
            data = work / (service + '-data')
            for path in [base / 'app', base / 'config', base / 'secrets', base / 'tls', data]:
                path.mkdir(parents=True, exist_ok=True)
            for name in ['admin_password', 'app_password', 'migrator_password']:
                (base / 'secrets' / name).write_text(PASSWORD + '\n')
            run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '2',
                 '-subj', '/CN=localhost', '-addext', 'subjectAltName=DNS:localhost,IP:127.0.0.1',
                 '-keyout', str(base / 'tls/server.key'), '-out', str(base / 'tls/server.crt')])
            metadata = run(['bash', '-c', f'REPO_DIR={shlex.quote(str(REPO))}; source {shlex.quote(str(REPO / folder / "linux/service.sh"))}; printf "%s" "$ARTIFACT"']).stdout
            if metadata:
                if metadata.endswith('.war'):
                    import shutil
                    shutil.copyfile(CDN / metadata, base / 'app/jenkins.war')
                else:
                    artifacts.extract(CDN / metadata, base / 'app', True, 'nexus-' if service == 'nexus' else None)
            port = free_port()
            public = free_port()
            service_env = dict(env, SDLC_DIR=str(base), DATA_DIR=str(data), TEST_PORT=str(port), TEST_PUBLIC=str(public),
                               SERVICE_USER=getpass.getuser(), DOMAIN=service + '.example.com',
                               ADMIN_PASSWORD=PASSWORD, APP_PASSWORD=PASSWORD, MIGRATOR_PASSWORD=PASSWORD,
                               DB_PASSWORD=PASSWORD, DB_HOST='localhost', DB_NAME=service, DB_USER=service,
                               DB_PORT=str(states.get('postgresql', {}).get('public', 5432)))
            if service in ['keycloak', 'sonarqube']:
                import shutil
                shutil.copyfile(states['postgresql']['base'] / 'tls/server.crt', base / 'config/db-ca.crt')
                sql_env = dict(env, SDLC_SERVICE_DB_PASSWORD=PASSWORD)
                run([str(deps / 'usr/lib/postgresql/18/bin/psql'), '-X', '-v', 'ON_ERROR_STOP=1', '-h',
                     str(states['postgresql']['data'] / 'run'), '-p', str(states['postgresql']['port']),
                     '-U', 'sdlc_admin', '-d', 'postgres', '-f', str(REPO / folder / 'linux/database.sql')], sql_env)
            script = r'''
source "$REPO_DIR/scripts/linux/common.sh"
source "$REPO_DIR/$FOLDER/linux/service.sh"
BACKEND_PORT=$TEST_PORT; PUBLIC_PORT=$TEST_PUBLIC
ACCESS_CIDRS=127.0.0.1/32,::1/128
UPSTREAM=http://127.0.0.1:$BACKEND_PORT
[[ $SERVICE != opensearch ]] || UPSTREAM=https://127.0.0.1:$BACKEND_PORT
# Mocks SOMENTE das operações privilegiadas; configuração e binários são os reais.
install() {
    local -a args=()
    while (($#)); do
        case "$1" in -o|-g) shift 2 ;; *) args+=("$1"); shift ;; esac
    done
    command install "${args[@]}"
}
chown() { :; }
chgrp() { :; }
kernel_search_limit() { :; }
runuser() {
    while (($#)); do
        case "$1" in -u) shift 2 ;; --preserve-environment) shift ;; --) shift; break ;; *) break ;; esac
    done
    local -a args=() value
    for value in "$@"; do
        value=${value/\/usr\/lib\/postgresql\//${DEPS}/usr/lib/postgresql/}
        value=${value/JAVA_HOME=\/usr\/lib\/jvm\/java-21-openjdk-amd64/JAVA_HOME=${DEPS}/usr/lib/jvm/java-21-openjdk-amd64}
        args+=("$value")
    done
    "${args[@]}"
}
service_prepare
render_nginx > "$SDLC_DIR/nginx.conf"
render_unit > "$SDLC_DIR/unit.service"
printf '%s' "$EXEC_START" > "$SDLC_DIR/command"
'''
            script_env = dict(service_env, REPO_DIR=str(REPO), FOLDER=folder, DEPS=str(deps))
            result = run(['bash', '-c', script], script_env)
            (work / (service + '-prepare.log')).write_text(result.stdout + result.stderr)
            # Ajusta caminhos do ambiente de teste e identifica o usuário real no peer map.
            if service == 'postgresql':
                # No teste sem root, o DB user é o owner da chave; o PostgreSQL exige 0600.
                # Na instalação real a chave é root:grupo-do-serviço 0640, também aceito pelo servidor.
                (base / 'config/server.key').chmod(0o600)
                ident = base / 'config/pg_ident.conf'
                ident.write_text(ident.read_text().replace('sdlc-postgresql', getpass.getuser()))
            if (base / 'service.env').exists():
                for line in (base / 'service.env').read_text().splitlines():
                    key, value = line.split('=', 1)
                    service_env[key] = value.replace('/usr/lib/jvm/', str(deps / 'usr/lib/jvm') + '/')
            command = (base / 'command').read_text().replace('/usr/lib/postgresql/', str(deps / 'usr/lib/postgresql') + '/')
            command = command.replace('/usr/lib/jvm/', str(deps / 'usr/lib/jvm') + '/')
            if service == 'redis':
                command = command.replace('/usr/bin/redis-server', str(deps / 'usr/bin/redis-server'))
            process = launch(shlex.split(command), service_env, service)
            states[service] = dict(base=base, data=data, port=port, public=public, process=process)
            if service == 'postgresql':
                psql = [str(deps / 'usr/lib/postgresql/18/bin/psql'), '-X', '-v', 'ON_ERROR_STOP=1', '-h', str(data / 'run'), '-p', str(port), '-U', 'sdlc_admin']
                deadline = time.monotonic() + 30
                while subprocess.run(psql + ['-d', 'postgres', '-c', 'SELECT 1'], env=env, capture_output=True).returncode:
                    if time.monotonic() > deadline: raise RuntimeError('PostgreSQL não iniciou')
                    time.sleep(.5)
                sql_env = dict(env, SDLC_APP_PASSWORD=PASSWORD, SDLC_MIGRATOR_PASSWORD=PASSWORD)
                run(psql + ['-d', 'postgres', '-f', str(REPO / 'postgresql/linux/bootstrap.sql')], sql_env)
                run(psql + ['-d', 'postgres', '-f', str(REPO / 'postgresql/linux/bootstrap.sql')], sql_env)
                app = [str(deps / 'usr/lib/postgresql/18/bin/psql'), '-X', '-v', 'ON_ERROR_STOP=1', '-h', '127.0.0.1', '-p', str(port), '-U', 'sdlc_app', '-d', 'sdlc']
                app_env = dict(env, PGPASSWORD=PASSWORD, PGSSLMODE='require')
                assert subprocess.run(app + ['-c', 'CREATE TABLE app.forbidden(id int)'], env=app_env, capture_output=True).returncode != 0
                migrator = list(app)
                migrator[migrator.index('sdlc_app')] = 'sdlc_migrator'
                run(migrator + ['-c', 'SET ROLE sdlc_owner; CREATE TABLE app.smoke(id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY, value text NOT NULL)'], app_env)
                run(app + ['-c', "INSERT INTO app.smoke(value) VALUES ('persisted'); SELECT * FROM app.smoke"], app_env)
                assert subprocess.run(app + ['-c', 'SELECT 1'], env=dict(app_env, PGSSLMODE='disable'), capture_output=True).returncode != 0
                assert subprocess.run(app + ['-c', 'SELECT 1'], env=dict(app_env, PGPASSWORD='wrong'), capture_output=True).returncode != 0
                # Nginx TCP é iniciado antes dos serviços que dependem do banco.
                fragment = base / 'nginx.conf'
                nginx_conf = work / 'nginx-tcp.conf'
                nginx_conf.write_text(f'load_module {deps}/usr/lib/nginx/modules/ngx_stream_module.so;\npid {work}/nginx.pid;\nerror_log {work}/nginx.log;\nevents {{}}\nstream {{ include {fragment}; }}\n')
                nginx = launch([str(deps / 'usr/sbin/nginx'), '-p', str(work), '-c', str(nginx_conf), '-g', 'daemon off;'], env, 'nginx')
                time.sleep(1)
                tls_conn = f'host=localhost hostaddr=127.0.0.1 port={public} dbname=sdlc user=sdlc_app sslmode=verify-full sslrootcert={base}/tls/server.crt'
                run([str(deps / 'usr/lib/postgresql/18/bin/psql'), '-X', '-v', 'ON_ERROR_STOP=1', tls_conn, '-c', "SELECT ssl FROM pg_stat_ssl WHERE pid=pg_backend_pid()"], app_env)
                states['nginx'] = dict(process=nginx)
                print('PostgreSQL: TLS via Nginx, SCRAM, bootstrap repetível, DML e bloqueio DDL/sem TLS: OK', flush=True)
            elif service == 'redis':
                redis = [str(deps / 'usr/bin/redis-cli'), '-h', '127.0.0.1', '-p', str(port), '--user', 'sdlc']
                redis_env = dict(env, REDISCLI_AUTH=PASSWORD)
                deadline = time.monotonic() + 30
                while 'PONG' not in subprocess.run(redis + ['PING'], env=redis_env, capture_output=True, text=True).stdout:
                    if time.monotonic() > deadline: raise RuntimeError('Redis não iniciou')
                    time.sleep(.5)
                run(redis + ['SET', 'smoke', 'persisted'], redis_env)
                assert 'NOPERM' in run(redis + ['CONFIG', 'GET', '*'], redis_env).stdout
                assert 'NOAUTH' in run(redis[:-2] + ['PING'], env).stdout
                nginx_conf = work / 'nginx-tcp.conf'
                nginx_conf.write_text(nginx_conf.read_text().replace('stream {', 'stream { include ' + str(base / 'nginx.conf') + ';'))
                run([str(deps / 'usr/sbin/nginx'), '-p', str(work), '-c', str(nginx_conf), '-s', 'reload'], env)
                time.sleep(1)
                tls_redis = [str(deps / 'usr/bin/redis-cli'), '--tls', '--cacert', str(base / 'tls/server.crt'), '--sni', 'localhost',
                             '-h', '127.0.0.1', '-p', str(public), '--user', 'sdlc']
                assert 'PONG' in run(tls_redis + ['PING'], redis_env).stdout
                stop(process)
                process = launch(shlex.split(command), service_env, service)
                states[service]['process'] = process
                time.sleep(1)
                assert run(redis + ['GET', 'smoke'], redis_env).stdout.strip() == 'persisted'
                print('Redis: TLS via Nginx, ACL, AOF após reinício e bloqueio administrativo: OK', flush=True)
            else:
                url = ('https://localhost:' if service == 'opensearch' else 'http://127.0.0.1:') + str(port)
                path = '/-/ready' if service == 'prometheus' else '/api/health' if service == 'grafana' else '/api/system/status' if service == 'sonarqube' else '/api/json' if service == 'jenkins' else '/realms/master' if service == 'keycloak' else '/service/rest/v1/status' if service == 'nexus' else '/'
                body = ready(url + path, PASSWORD if service in ['jenkins', 'opensearch'] else None,
                             base / 'config/root-ca.crt' if service == 'opensearch' else None, process=process)
                if service == 'sonarqube':
                    deadline = time.monotonic() + 150
                    while json.loads(body).get('status') != 'UP':
                        if time.monotonic() > deadline: raise RuntimeError('SonarQube não ficou UP')
                        time.sleep(1)
                        body = ready(url + path, process=process)
                    wait_script = 'source "$REPO_DIR/scripts/linux/common.sh"; source "$REPO_DIR/sonarqube/linux/service.sh"; UPSTREAM="$TEST_URL"; service_wait'
                    run(['bash', '-c', wait_script], dict(service_env, REPO_DIR=str(REPO), TEST_URL=url))
                    assert json.loads(http(url + '/api/authentication/validate', PASSWORD)[1]).get('valid')
                if service == 'jenkins': assert http(url + path)[0] in [401, 403]
                if service == 'opensearch': assert http(url + path, ca=base / 'config/root-ca.crt')[0] == 401
                if service == 'nexus': assert (data / 'admin.password').is_file()
                check_loopback(process)
                print(service + ': inicialização nativa e API/health: OK', flush=True)
                stop(process)
        # Sintaxe dos nove proxies juntos, sem usar caminhos/logs de sistema.
        stop(states['nginx']['process'])
        http_port, https_port = free_port(), free_port()
        http_fragments = []; stream_fragments = []
        for name, state in states.items():
            if name == 'nginx': continue
            fragment = state['base'] / 'nginx.conf'
            text = fragment.read_text().replace('/var/log/nginx/', str(work) + '/')
            # Nginx moderno também tenta bind em -t; o smoke não usa portas privilegiadas.
            text = text.replace('listen 80;', f'listen {http_port};').replace('listen [::]:80;', f'listen [::]:{http_port};')
            text = text.replace('listen 443 ssl;', f'listen {https_port} ssl;').replace('listen [::]:443 ssl;', f'listen [::]:{https_port} ssl;')
            text = text.replace('/etc/nginx/sdlc-prometheus.htpasswd', str(work / 'test.htpasswd'))
            fragment.write_text(text)
            (stream_fragments if name in ['postgresql', 'redis'] else http_fragments).append(fragment)
        (work / 'test.htpasswd').write_text('admin:unused\n')
        config = work / 'nginx-all.conf'
        temporary_paths = ''.join(f'{directive}_temp_path {work}/{directive}; ' for directive in ['client_body', 'proxy', 'fastcgi', 'scgi', 'uwsgi'])
        config.write_text(f'load_module {deps}/usr/lib/nginx/modules/ngx_stream_module.so;\npid {work}/all.pid;\nerror_log {work}/all.log;\nevents {{}}\nhttp {{ access_log off; ' + temporary_paths + ''.join(f'include {f}; ' for f in http_fragments) + '}\nstream { ' + ''.join(f'include {f}; ' for f in stream_fragments) + '}\n')
        run([str(deps / 'usr/sbin/nginx'), '-p', str(work), '-c', str(config), '-t'], env)
        print('Nginx: sintaxe dos nove proxies HTTP/stream: OK', flush=True)
    except Exception:
        print('Logs preservados para diagnóstico: ' + str(work), flush=True)
        raise
    finally:
        for process in reversed(processes):
            stop(process)
    print('Smoke nativo concluído. Logs: ' + str(work), flush=True)


if __name__ == '__main__':
    main()
