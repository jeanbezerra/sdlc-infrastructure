#!/usr/bin/env bash
# Biblioteca Linux; somente os wrappers de cada serviço executam main.
set -Eeuo pipefail
umask 077

log() { printf '[sdlc:%s] %s\n' "${SERVICE:-linux}" "$*"; }
die() { log "ERRO: $*" >&2; exit 1; }

usage() {
    if [[ $ACTION == install ]]; then
        printf 'Uso: sudo bash %s --config /caminho/arquivo.env\n' "$0"
    else
        printf 'Uso: sudo bash %s --yes [--keep-data]\n' "$0"
        printf 'Remove o serviço e TODOS os seus dados por padrão. --keep-data preserva dados e segredos.\n'
    fi
}

parse_args() {
    CONFIG_FILE=''; YES=false; KEEP_DATA=false
    while (($#)); do
        case "$1" in
            --help|-h) usage; exit 0 ;;
            --config) [[ $ACTION == install && $# -ge 2 ]] || die 'Argumento --config inválido'; CONFIG_FILE=$2; shift ;;
            --yes) [[ $ACTION == uninstall ]] || die '--yes somente na desinstalação'; YES=true ;;
            --keep-data) [[ $ACTION == uninstall ]] || die '--keep-data somente na desinstalação'; KEEP_DATA=true ;;
            *) die "Argumento desconhecido: $1" ;;
        esac
        shift
    done
}

check_host() {
    [[ $EUID -eq 0 ]] || die 'Execute com sudo ou como root.'
    [[ $(uname -s) == Linux ]] || die 'Instalador somente Linux.'
    # shellcheck source=/dev/null
    . /etc/os-release
    [[ $ID == ubuntu ]] || die 'Distribuição suportada: Ubuntu Server LTS.'
    [[ $VERSION_ID == 26.04 ]] || die 'Use Ubuntu Server 26.04 LTS.'
    [[ $(dpkg --print-architecture) == amd64 ]] || die 'Os binários deste conjunto são para VMs amd64.'
    [[ -d /run/systemd/system ]] || die 'É necessário Ubuntu com systemd (VM ou máquina física).'
    command -v flock >/dev/null || die 'Instale util-linux.'
    # Serializa alterações globais de Nginx/UFW e operações de serviços.
    exec 9>/run/lock/sdlc-infrastructure.lock
    flock -n 9 || die 'Outro instalador/desinstalador SDLC está em execução.'
}

# Configuração declarativa, sem source/eval nem expansão de comandos.
read_config() {
    local config=$1 line key value
    [[ -f $config && ! -L $config ]] || die "Arquivo de configuração ausente ou symlink: $config"
    DOMAIN=''; ACCESS_CIDRS=''; SSH_PORT=22; SSH_CIDRS='0.0.0.0/0,::/0'
    TLS_CERT_PATH=''; TLS_KEY_PATH=''; ARTIFACT_BASE_URL=''; MANIFEST_PATH=''; ALLOW_HTTP_ARTIFACTS=false
    DB_HOST=''; DB_PORT=5432; DB_NAME=''; DB_USER=''; DB_PASSWORD_FILE=''; DB_CA_PATH=''
    declare -A seen=()
    while IFS= read -r line || [[ -n $line ]]; do
        line=${line%$'\r'}
        [[ -z $line || $line == \#* ]] && continue
        [[ $line =~ ^([A-Z_]+)=(.*)$ ]] || die 'Use KEY=value, sem export, aspas ou espaços ao redor de =.'
        key=${BASH_REMATCH[1]}; value=${BASH_REMATCH[2]}
        case "$key" in DOMAIN|ACCESS_CIDRS|SSH_PORT|SSH_CIDRS|TLS_CERT_PATH|TLS_KEY_PATH|ARTIFACT_BASE_URL|MANIFEST_PATH|ALLOW_HTTP_ARTIFACTS|DB_HOST|DB_PORT|DB_NAME|DB_USER|DB_PASSWORD_FILE|DB_CA_PATH) ;; *) die "Chave não reconhecida: $key" ;; esac
        [[ ! ${seen[$key]+exists} ]] || die "Chave duplicada: $key"
        seen[$key]=1
        [[ $value != *[\"\'\`\$\;\\]* && $value != *$'\t'* && $value != *' '* ]] || die "Valor inválido em $key. Use valores literais sem aspas/espaços."
        printf -v "$key" '%s' "$value"
    done < "$config"
    [[ $DOMAIN =~ ^[a-z0-9]([a-z0-9.-]*[a-z0-9])?$ && $DOMAIN == *.* && $DOMAIN != *..* && ${#DOMAIN} -le 253 ]] || die 'DOMAIN deve ser um nome DNS em minúsculas, por exemplo grafana.exemplo.com.'
    if [[ ! $SSH_PORT =~ ^[0-9]+$ || ${#SSH_PORT} -gt 5 ]]; then die 'SSH_PORT inválida.'; fi
    if ((10#$SSH_PORT < 1 || 10#$SSH_PORT > 65535)); then die 'SSH_PORT inválida.'; fi
    [[ -n $ACCESS_CIDRS && -n $SSH_CIDRS ]] || die 'Defina ACCESS_CIDRS e SSH_CIDRS.'
    command -v python3 >/dev/null || die 'Instale python3 antes de executar o instalador.'
    python3 - "$ACCESS_CIDRS" "$SSH_CIDRS" "$PROXY_KIND" "$DOMAIN" <<'PY' || die 'CIDRs/domínio inválidos.'
import ipaddress, re, sys
if any(not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', label) for label in sys.argv[4].split('.')):
    sys.exit('DOMAIN contém um label DNS inválido.')
for index, values in enumerate(sys.argv[1:3]):
    for value in values.split(','):
        try:
            net = ipaddress.ip_network(value, strict=True)
        except ValueError:
            sys.exit('CIDR inválido/não normalizado: ' + value)
        if index == 0 and sys.argv[3] != 'http' and net.prefixlen == 0:
            sys.exit('PostgreSQL/Redis exigem redes restritas; /0 não é permitido.')
PY
    if [[ $ACTION == install && -n ${SSH_CONNECTION:-} ]]; then
        python3 - "${SSH_CONNECTION%% *}" "$SSH_CIDRS" <<'PY' || die 'SSH_CIDRS não inclui o IP desta sessão SSH.'
import ipaddress, sys
client = ipaddress.ip_address(sys.argv[1])
if not any(client in ipaddress.ip_network(cidr) for cidr in sys.argv[2].split(',')):
    sys.exit(1)
PY
    fi
    [[ -z $TLS_CERT_PATH && -z $TLS_KEY_PATH || -n $TLS_CERT_PATH && -n $TLS_KEY_PATH ]] || die 'Informe certificado e chave juntos.'
    if [[ $ACTION == install && -n $TLS_CERT_PATH ]]; then
        [[ $TLS_CERT_PATH == /* && $TLS_KEY_PATH == /* && -f $TLS_CERT_PATH && -f $TLS_KEY_PATH ]] || die 'TLS exige caminhos absolutos para arquivos existentes.'
    fi
    if [[ $ACTION == install ]]; then
        [[ $ALLOW_HTTP_ARTIFACTS == true || $ALLOW_HTTP_ARTIFACTS == false ]] || die 'ALLOW_HTTP_ARTIFACTS deve ser true ou false.'
        [[ $ARTIFACT_BASE_URL =~ ^https?://[a-zA-Z0-9./_-]+$ ]] || die 'ARTIFACT_BASE_URL deve ser uma URL HTTP(S) sem query string.'
        local -a url_args=()
        [[ $ALLOW_HTTP_ARTIFACTS != true ]] || url_args=(--allow-private-http)
        python3 "$REPO_DIR/scripts/linux/artifacts.py" check-url --url "$ARTIFACT_BASE_URL" "${url_args[@]}" || die 'CDN inválido ou HTTP não autorizado para rede privada.'
        [[ $MANIFEST_PATH == /* && -f $MANIFEST_PATH && ! -L $MANIFEST_PATH ]] || die 'Informe o MANIFEST_PATH absoluto para o manifest.json confiável gerado na preparação do CDN.'
    fi
    if [[ $SERVICE == keycloak || $SERVICE == sonarqube ]]; then
        [[ $DB_HOST =~ ^[a-z0-9][a-z0-9.-]+$ && $DB_NAME =~ ^[a-z][a-z0-9_]*$ && $DB_USER =~ ^[a-z][a-z0-9_]*$ ]] || die 'Defina DB_HOST (DNS/IP), DB_NAME e DB_USER sem caracteres especiais.'
        if [[ ! $DB_PORT =~ ^[0-9]+$ || ${#DB_PORT} -gt 5 ]]; then die 'DB_PORT inválida.'; fi
        if ((10#$DB_PORT < 1 || 10#$DB_PORT > 65535)); then die 'DB_PORT inválida.'; fi
        if [[ $ACTION == install ]]; then
            [[ $DB_PASSWORD_FILE == /* && -f $DB_PASSWORD_FILE && $DB_CA_PATH == /* && -f $DB_CA_PATH ]] || die 'DB_PASSWORD_FILE e DB_CA_PATH devem apontar para arquivos absolutos existentes.'
            DB_PASSWORD=$(cat "$DB_PASSWORD_FILE")
            [[ $DB_PASSWORD =~ ^[a-zA-Z0-9_!@%-]{16,128}$ ]] || die 'Senha do banco: use 16–128 caracteres alfanuméricos ou _!@%- (sem espaços).'
        fi
    fi
    export DOMAIN ACCESS_CIDRS SSH_PORT SSH_CIDRS TLS_CERT_PATH TLS_KEY_PATH ARTIFACT_BASE_URL MANIFEST_PATH
}

check_paths() {
    SDLC_DIR="/opt/sdlc/$SERVICE"
    export SDLC_DIR
    [[ ! -L /opt/sdlc && ! -L $SDLC_DIR ]] || die 'Diretório gerenciado não pode ser um symlink.'
    [[ $(realpath -m -- "$SDLC_DIR") == "/opt/sdlc/$SERVICE" ]] || die 'Caminho gerenciado fora de /opt/sdlc.'
    if [[ -d $SDLC_DIR ]]; then
        [[ -f $SDLC_DIR/.managed && ! -L $SDLC_DIR/.managed && $(cat "$SDLC_DIR/.managed") == "sdlc-v1:$SERVICE" ]] || die "Diretório existente não pertence ao instalador: $SDLC_DIR"
    fi
}

owned_file() {
    local target=$1
    [[ ! -L $target ]] || die "Arquivo gerenciado não pode ser symlink: $target"
    if [[ -e $target ]]; then
        head -n 1 "$target" | grep -Fxq "# Managed by sdlc-infrastructure: $SERVICE" || die "Arquivo existente de outro proprietário: $target"
    fi
}

check_domain() {
    local config
    for config in /opt/sdlc/*/install.env; do
        [[ -f $config && $config != "$SDLC_DIR/install.env" ]] || continue
        if grep -Fxq "DOMAIN=$DOMAIN" "$config"; then
            die "DOMAIN já está reservado por $config. Cada serviço HTTP precisa de um domínio próprio."
        fi
    done
}

prepare_dir() {
    install -d -m 0755 /opt/sdlc
    install -d -m 0750 "$SDLC_DIR"
    printf 'sdlc-v1:%s\n' "$SERVICE" > "$SDLC_DIR/.managed"
    if [[ -f $SDLC_DIR/install.env ]]; then
        cmp -s "$CONFIG_FILE" "$SDLC_DIR/install.env" || die 'Configuração difere da instalação anterior. Não fazemos upgrade/reconfiguração silenciosa; consulte docs/linux.md.'
    else
        install -m 0600 "$CONFIG_FILE" "$SDLC_DIR/install.env"
    fi
    if [[ -f $SDLC_DIR/version && $(cat "$SDLC_DIR/version") != "$SERVICE_VERSION" ]]; then
        die 'Versão diferente da instalada. Faça backup e siga o procedimento oficial de upgrade.'
    fi
    printf '%s\n' "$SERVICE_VERSION" > "$SDLC_DIR/version"
    install -d -m 0750 "$SDLC_DIR/config"
    install -d -m 0700 "$SDLC_DIR/secrets" "$SDLC_DIR/tls"
    local name value
    for name in admin_password app_password migrator_password; do
        [[ ! -L $SDLC_DIR/secrets/$name ]] || die 'Symlink em segredos.'
        if [[ ! -f $SDLC_DIR/secrets/$name ]]; then
            printf 'Aa1_%s\n' "$(openssl rand -hex 24)" > "$SDLC_DIR/secrets/$name"
        fi
        chmod 0600 "$SDLC_DIR/secrets/$name"
        value=$(cat "$SDLC_DIR/secrets/$name")
        printf -v "${name^^}" '%s' "$value"
        export "${name^^}"
    done
    printf 'Segredos de %s. Leia somente como root; não publique este arquivo.\n' "$SERVICE" > "$SDLC_DIR/credentials.txt"
    printf 'admin: %s\nsdlc_app: %s\nsdlc_migrator: %s\n' "$ADMIN_PASSWORD" "$APP_PASSWORD" "$MIGRATOR_PASSWORD" >> "$SDLC_DIR/credentials.txt"
    chmod 0600 "$SDLC_DIR/credentials.txt"
}

fetch_artifact() {
    local -a transport_args=()
    [[ $ALLOW_HTTP_ARTIFACTS != true ]] || transport_args=(--allow-private-http)
    python3 "$REPO_DIR/scripts/linux/artifacts.py" fetch --manifest "$MANIFEST_PATH" \
        --base-url "$ARTIFACT_BASE_URL" --name "$1" --output "$2" "${transport_args[@]}"
}

install_dependencies() {
    local group archive package_dir
    export DEBIAN_FRONTEND=noninteractive
    install -d -m 0700 /opt/sdlc/cache
    for group in base $PACKAGE_GROUPS; do
        archive="/opt/sdlc/cache/ubuntu26-$group.tar.gz"
        fetch_artifact "ubuntu26-$group.tar.gz" "$archive"
        package_dir=$(mktemp -d)
        python3 "$REPO_DIR/scripts/linux/artifacts.py" extract --archive "$archive" --output "$package_dir"
        # Instala pacotes Ubuntu assinados na origem, sem baixar dependências fora do bundle.
        bash "$REPO_DIR/scripts/linux/install-debs.sh" "$package_dir"
        rm -rf -- "$package_dir"
    done
    systemctl enable --now nginx
}

prepare_user() {
    SERVICE_USER="sdlc-$SERVICE"
    export SERVICE_USER
    if id "$SERVICE_USER" >/dev/null 2>&1; then
        [[ $(getent passwd "$SERVICE_USER" | cut -d: -f5) == "SDLC managed $SERVICE" ]] || die 'Conta de serviço preexistente não pertence a este projeto.'
    else
        useradd --system --user-group --home-dir "/var/lib/sdlc/$SERVICE" --shell /usr/sbin/nologin --comment "SDLC managed $SERVICE" "$SERVICE_USER"
    fi
    DATA_DIR="/var/lib/sdlc/$SERVICE"
    export DATA_DIR
    [[ ! -L /var/lib/sdlc && ! -L $DATA_DIR ]] || die 'Symlink no diretório de dados.'
    if [[ -d $DATA_DIR ]]; then
        [[ -f $DATA_DIR/.managed && ! -L $DATA_DIR/.managed && $(cat "$DATA_DIR/.managed") == "sdlc-v1:$SERVICE" ]] || die 'Dados existentes não gerenciados pelo projeto.'
    fi
    install -d -m 0755 /var/lib/sdlc
    install -d -m 0750 -o "$SERVICE_USER" -g "$SERVICE_USER" "$DATA_DIR"
    printf 'sdlc-v1:%s\n' "$SERVICE" > "$DATA_DIR/.managed"
    chgrp "$SERVICE_USER" "$SDLC_DIR" "$SDLC_DIR/config"
    if [[ $SERVICE == keycloak || $SERVICE == sonarqube ]]; then
        install -m 0644 "$DB_CA_PATH" "$SDLC_DIR/config/db-ca.crt"
    fi
}

install_binary() {
    [[ -n $ARTIFACT ]] || return 0
    local archive="$SDLC_DIR/$ARTIFACT"
    fetch_artifact "$ARTIFACT" "$archive"
    if [[ ! -f $SDLC_DIR/.app-ready ]]; then
        [[ ! -e $SDLC_DIR/app ]] || die 'Diretório app sem marcador de conclusão. Mova-o para diagnóstico antes de retomar.'
        local staging
        staging=$(mktemp -d "$SDLC_DIR/app.XXXXXX")
        if [[ $ARTIFACT == *.war ]]; then
            install -m 0644 "$archive" "$staging/jenkins.war"
        else
            local -a root_args=()
            [[ -z $ROOT_PREFIX ]] || root_args=(--root-prefix "$ROOT_PREFIX")
            python3 "$REPO_DIR/scripts/linux/artifacts.py" extract --archive "$archive" --output "$staging" --strip-root "${root_args[@]}"
        fi
        chmod 0755 "$staging"
        mv -- "$staging" "$SDLC_DIR/app"
        touch "$SDLC_DIR/.app-ready"
    fi
    # Alguns tarballs omitem entradas de diretórios; umask 077 cria pais 0700.
    # Garante leitura/travessia pela conta de serviço sem abrir o diretório base.
    chgrp -R "$SERVICE_USER" "$SDLC_DIR/app"
    chmod -R g+rX "$SDLC_DIR/app"
}

prepare_tls() {
    if [[ -n $TLS_CERT_PATH ]]; then
        install -m 0644 "$TLS_CERT_PATH" "$SDLC_DIR/tls/server.crt"
        install -m 0600 "$TLS_KEY_PATH" "$SDLC_DIR/tls/server.key"
    elif [[ ! -f $SDLC_DIR/tls/server.crt ]]; then
        log 'Gerando certificado autoassinado para laboratório; distribua a confiança ou forneça um certificado de CA.'
        openssl req -x509 -newkey rsa:3072 -sha256 -nodes -days 365 \
            -subj "/CN=$DOMAIN" -addext "subjectAltName=DNS:$DOMAIN" \
            -keyout "$SDLC_DIR/tls/server.key" -out "$SDLC_DIR/tls/server.crt" 2>/dev/null
    fi
    openssl x509 -in "$SDLC_DIR/tls/server.crt" -noout -checkhost "$DOMAIN" >/dev/null || die 'Certificado não cobre DOMAIN.'
    openssl x509 -in "$SDLC_DIR/tls/server.crt" -noout -checkend 86400 >/dev/null || die 'Certificado expirado ou com menos de 24h restantes.'
    local cert_key private_key
    cert_key=$(openssl x509 -in "$SDLC_DIR/tls/server.crt" -pubkey -noout | openssl pkey -pubin -outform DER | sha256sum)
    private_key=$(openssl pkey -in "$SDLC_DIR/tls/server.key" -pubout -outform DER | sha256sum)
    [[ $cert_key == "$private_key" ]] || die 'Certificado e chave TLS não correspondem.'
}

check_ports() {
    local port
    for port in $BACKEND_PORTS; do
        if ss -H -lnt "sport = :$port" | grep -q .; then
            [[ -f $SDLC_DIR/.configured ]] || die "Porta local $port já está ocupada."
        fi
    done
    if [[ $PROXY_KIND != http ]] && ss -H -lnt "sport = :$PUBLIC_PORT" | grep -q .; then
        [[ -f /etc/nginx/sdlc-streams/sdlc-$SERVICE.conf ]] || die "Porta pública $PUBLIC_PORT já está ocupada."
    fi
}

write_unit() {
    local unit="/etc/systemd/system/sdlc-$SERVICE.service"
    owned_file "$unit"
    render_unit > "$unit"
    systemctl daemon-reload
    touch "$SDLC_DIR/.configured"
    systemctl enable "sdlc-$SERVICE.service"
    systemctl restart "sdlc-$SERVICE.service"
}

render_unit() {
    cat <<EOF
# Managed by sdlc-infrastructure: $SERVICE
[Unit]
Description=SDLC $SERVICE (native Linux)
After=network-online.target
Wants=network-online.target
[Service]
Type=simple
User=$SERVICE_USER
Group=$SERVICE_USER
WorkingDirectory=$DATA_DIR
EnvironmentFile=-$SDLC_DIR/service.env
ExecStart=$EXEC_START
Restart=on-failure
RestartSec=10
TimeoutStopSec=120
KillSignal=SIGTERM
LimitNOFILE=65536
LimitNPROC=8192
UMask=0027
NoNewPrivileges=true
PrivateTmp=true
ProtectHome=true
$UNIT_EXTRA
[Install]
WantedBy=multi-user.target
EOF
}

nginx_allow_rules() {
    local cidr
    local -a cidrs
    IFS=, read -ra cidrs <<< "$ACCESS_CIDRS"
    for cidr in "${cidrs[@]}"; do printf '    allow %s;\n' "$cidr"; done
    printf '    deny all;\n'
}

write_nginx() {
    local target tmp previous='' added_stream=false nginx_dump
    if [[ $PROXY_KIND == http ]]; then
        target="/etc/nginx/conf.d/sdlc-$SERVICE.conf"
    else
        install -d -m 0755 /etc/nginx/sdlc-streams
        if [[ ! -f /etc/nginx/sdlc-stream.conf ]]; then
            nginx_dump=$(nginx -T 2>&1) || die 'Nginx existente inválido; corrija antes de instalar o proxy.'
            if grep -Eq '^\s*stream\s*\{' <<< "$nginx_dump"; then
                die 'Já existe um bloco stream no Nginx. Integre o include manualmente conforme docs/linux.md.'
            fi
            printf '# Managed by sdlc-infrastructure: shared\nstream { include /etc/nginx/sdlc-streams/*.conf; }\n' > /etc/nginx/sdlc-stream.conf
            printf '\ninclude /etc/nginx/sdlc-stream.conf; # sdlc-infrastructure stream\n' >> /etc/nginx/nginx.conf
            added_stream=true
        fi
        target="/etc/nginx/sdlc-streams/sdlc-$SERVICE.conf"
    fi
    owned_file "$target"
    tmp=$(mktemp)
    if [[ ${BASIC_AUTH:-false} == true ]]; then
        printf '%s\n' "$ADMIN_PASSWORD" | htpasswd -iBc "$SDLC_DIR/nginx.htpasswd" admin >/dev/null
        install -m 0644 "$SDLC_DIR/nginx.htpasswd" "/etc/nginx/sdlc-$SERVICE.htpasswd"
    fi
    render_nginx > "$tmp"
    [[ ! -f $target ]] || previous=$(cat "$target")
    install -m 0644 "$tmp" "$target"
    rm -f -- "$tmp"
    if ! nginx -t; then
        if [[ -n $previous ]]; then printf '%s\n' "$previous" > "$target"; else rm -f -- "$target"; fi
        if [[ $added_stream == true ]]; then
            sed -i '\|^include /etc/nginx/sdlc-stream.conf; # sdlc-infrastructure stream$|d' /etc/nginx/nginx.conf
            rm -f -- /etc/nginx/sdlc-stream.conf
        fi
        die 'Configuração Nginx inválida; vhost anterior restaurado. O backend permanece para diagnóstico.'
    fi
    systemctl reload nginx
}

render_nginx() {
        printf '# Managed by sdlc-infrastructure: %s\n' "$SERVICE"
        if [[ $PROXY_KIND == http ]]; then
            cat <<EOF
map \$http_upgrade \$sdlc_${SERVICE}_connection { default upgrade; '' close; }
server {
    listen 80;
    listen [::]:80;
    server_name $DOMAIN;
    return 308 https://$DOMAIN\$request_uri;
}
server {
    listen 443 ssl;
    listen [::]:443 ssl;
    server_name $DOMAIN;
    ssl_certificate $SDLC_DIR/tls/server.crt;
    ssl_certificate_key $SDLC_DIR/tls/server.key;
    ssl_protocols TLSv1.2 TLSv1.3;
    client_max_body_size 0;
    access_log /var/log/nginx/sdlc-$SERVICE.access.log;
    error_log /var/log/nginx/sdlc-$SERVICE.error.log;
EOF
            nginx_allow_rules
            if [[ ${BASIC_AUTH:-false} == true ]]; then
                printf '    auth_basic "SDLC %s";\n    auth_basic_user_file /etc/nginx/sdlc-%s.htpasswd;\n' "$SERVICE" "$SERVICE"
            fi
            cat <<EOF
    location / {
        proxy_pass $UPSTREAM;
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$remote_addr;
        proxy_set_header X-Forwarded-Proto https;
        proxy_set_header X-Forwarded-Host \$host;
        proxy_set_header X-Forwarded-Port 443;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection \$sdlc_${SERVICE}_connection;
        proxy_read_timeout 900s;
        proxy_send_timeout 900s;
        proxy_request_buffering off;
        proxy_buffering off;
EOF
            if [[ $UPSTREAM == https:* ]]; then
                printf '        proxy_ssl_verify on;\n        proxy_ssl_server_name on;\n        proxy_ssl_name localhost;\n        proxy_ssl_trusted_certificate %s/config/root-ca.crt;\n' "$SDLC_DIR"
            fi
            printf '    }\n}\n'
        else
            printf 'server {\n    listen %s%s;\n    listen [::]:%s%s;\n' "$PUBLIC_PORT" "$([[ $PROXY_KIND == redis ]] && printf ' ssl')" "$PUBLIC_PORT" "$([[ $PROXY_KIND == redis ]] && printf ' ssl')"
            if [[ $PROXY_KIND == redis ]]; then
                printf '    ssl_certificate %s/tls/server.crt;\n    ssl_certificate_key %s/tls/server.key;\n    ssl_protocols TLSv1.2 TLSv1.3;\n' "$SDLC_DIR" "$SDLC_DIR"
            fi
            nginx_allow_rules
            printf '    proxy_pass 127.0.0.1:%s;\n    proxy_connect_timeout 10s;\n    proxy_timeout 1h;\n}\n' "$BACKEND_PORT"
        fi
}

configure_firewall() {
    local profile="/etc/ufw/applications.d/sdlc-$SERVICE" cidr ports=$PUBLIC_PORT
    owned_file "$profile"
    [[ $PROXY_KIND != http ]] || ports=80,443
    cat > "$profile" <<EOF
# Managed by sdlc-infrastructure: $SERVICE
[SDLC-$SERVICE]
title=SDLC $SERVICE
description=Somente as portas do proxy Nginx para $SERVICE
ports=$ports/tcp
EOF
    local ssh_config_ports=''
    if [[ -x /usr/sbin/sshd ]]; then
        ssh_config_ports=$(/usr/sbin/sshd -T 2>/dev/null | awk '$1 == "port" {print $2}')
        [[ -z $ssh_config_ports ]] || grep -Fxq "$SSH_PORT" <<< "$ssh_config_ports" || die 'SSH_PORT não corresponde ao sshd. Corrija antes de habilitar o UFW.'
    fi
    local -a cidrs
    IFS=, read -ra cidrs <<< "$SSH_CIDRS"
    for cidr in "${cidrs[@]}"; do
        ufw allow from "$cidr" to any port "$SSH_PORT" proto tcp comment sdlc-ssh
    done
    IFS=, read -ra cidrs <<< "$ACCESS_CIDRS"
    for cidr in "${cidrs[@]}"; do
        ufw allow from "$cidr" to any app "SDLC-$SERVICE" comment "sdlc-$SERVICE"
    done
    # Não redefinimos as políticas de um firewall já ativo.
    if ! LC_ALL=C ufw status | grep -q '^Status: active'; then
        ufw default deny incoming
        ufw default allow outgoing
        ufw --force enable
    fi
    ufw status verbose
}

wait_http() {
    local url=$1 code deadline=$((SECONDS + 600))
    local -a tls_args=()
    [[ $url != https:* ]] || tls_args=(--cacert "$SDLC_DIR/config/root-ca.crt")
    while ((SECONDS < deadline)); do
        code=$(curl --silent --output /dev/null --write-out '%{http_code}' --max-time 5 "${tls_args[@]}" "$url" || true)
        case "$code" in 200|201|204|301|302|303|401|403) return 0 ;; esac
        sleep 5
    done
    die "Backend não respondeu em 600s: $url. Consulte logs; reexecute com a mesma configuração para retomar."
}

install_service() {
    [[ -n $CONFIG_FILE ]] || die 'Informe --config. Copie e edite o .env.example do serviço.'
    read_config "$CONFIG_FILE"
    check_paths
    check_domain
    check_ports
    service_preflight
    if [[ -f $SDLC_DIR/install.env ]]; then
        cmp -s "$CONFIG_FILE" "$SDLC_DIR/install.env" || die 'Configuração diferente da instalação anterior: consulte docs/linux.md.'
        [[ $(cat "$SDLC_DIR/version") == "$SERVICE_VERSION" ]] || die 'Upgrade de versão exige procedimento específico.'
    fi
    install_dependencies
    prepare_dir
    prepare_user
    prepare_tls
    install_binary
    service_prepare
    write_unit
    service_wait
    systemctl is-active --quiet "sdlc-$SERVICE.service" || die 'A unit não permaneceu ativa; consulte journalctl antes de publicar o proxy.'
    write_nginx
    configure_firewall
    touch "$SDLC_DIR/.installed"
    log "Instalado. Acesso: $ACCESS_DESCRIPTION. Segredos: sudo cat $SDLC_DIR/credentials.txt"
}

remove_firewall() {
    local profile="/etc/ufw/applications.d/sdlc-$SERVICE" cidr
    [[ -f $profile ]] || return 0
    owned_file "$profile"
    local -a cidrs
    IFS=, read -ra cidrs <<< "$ACCESS_CIDRS"
    for cidr in "${cidrs[@]}"; do
        ufw --force delete allow from "$cidr" to any app "SDLC-$SERVICE"
    done
    rm -f -- "$profile"
}

uninstall_service() {
    check_paths
    if [[ ! -d $SDLC_DIR ]]; then log 'Serviço não instalado por este projeto.'; return; fi
    log "Remoção de sdlc-$SERVICE em $SDLC_DIR e /var/lib/sdlc/$SERVICE; preservar dados: $KEEP_DATA. Pacotes Ubuntu, Nginx, UFW e acesso SSH são compartilhados e permanecem."
    [[ $YES == true ]] || die 'Para confirmar a remoção, reexecute com --yes. O padrão apaga os dados; use --keep-data se necessário.'
    CONFIG_FILE="$SDLC_DIR/install.env"
    read_config "$CONFIG_FILE"
    service_remove_preflight
    local unit="/etc/systemd/system/sdlc-$SERVICE.service" target
    [[ ! -f $unit ]] || { owned_file "$unit"; systemctl disable --now "sdlc-$SERVICE.service"; }
    service_remove
    rm -f -- "$unit"
    systemctl daemon-reload
    if [[ $PROXY_KIND == http ]]; then target="/etc/nginx/conf.d/sdlc-$SERVICE.conf"; else target="/etc/nginx/sdlc-streams/sdlc-$SERVICE.conf"; fi
    if [[ -f $target ]]; then owned_file "$target"; rm -f -- "$target"; fi
    rm -f -- "/etc/nginx/sdlc-$SERVICE.htpasswd"
    if [[ -d /etc/nginx/sdlc-streams ]] && ! find /etc/nginx/sdlc-streams -name '*.conf' -print -quit | grep -q .; then
        sed -i '\|^include /etc/nginx/sdlc-stream.conf; # sdlc-infrastructure stream$|d' /etc/nginx/nginx.conf
        rm -f -- /etc/nginx/sdlc-stream.conf
        rmdir /etc/nginx/sdlc-streams
    fi
    nginx -t
    systemctl reload nginx
    remove_firewall
    rm -f -- "/var/log/nginx/sdlc-$SERVICE."*
    if [[ $KEEP_DATA == true ]]; then
        rm -f -- "$SDLC_DIR/.installed" "$SDLC_DIR/.configured"
        log "Dados e segredos preservados em $SDLC_DIR e /var/lib/sdlc/$SERVICE."
    else
        # SDLC_DIR é um caminho fixo validado, com marcador de propriedade; nunca recebe input livre.
        [[ $(realpath -e "$SDLC_DIR") == "/opt/sdlc/$SERVICE" ]] || die 'Caminho de remoção mudou.'
        rm -rf --one-file-system -- "$SDLC_DIR"
        local data_dir="/var/lib/sdlc/$SERVICE"
        if [[ -d $data_dir ]]; then
            [[ ! -L $data_dir && $(realpath -e "$data_dir") == "$data_dir" && $(cat "$data_dir/.managed") == "sdlc-v1:$SERVICE" ]] || die 'Caminho/marcador de dados inválido.'
            rm -rf --one-file-system -- "$data_dir"
        fi
        if id "sdlc-$SERVICE" >/dev/null 2>&1; then userdel "sdlc-$SERVICE"; fi
        log 'Serviço, dados, binários próprios, configuração, certificado e regras específicas removidos.'
    fi
}

main() {
    parse_args "$@"
    check_host
    trap 'log "Falha na linha $LINENO. Estado preservado para diagnóstico; nenhuma limpeza global foi executada." >&2' ERR
    if [[ $ACTION == install ]]; then install_service; else uninstall_service; fi
}
