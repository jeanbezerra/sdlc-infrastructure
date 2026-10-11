#!/usr/bin/env bash
# shellcheck disable=SC2034
SERVICE=technitium
SERVICE_VERSION=15.6.1
PROXY_KIND=http
BACKEND_PORT=5380
BACKEND_PORTS=5380
PUBLIC_PORT=443
UPSTREAM=http://127.0.0.1:5380
ARTIFACT=technitium-15.6.1.tar.gz
# O portátil oficial tem arquivos na raiz, sem diretório envolvente.
ROOT_PREFIX=''
PACKAGE_GROUPS=technitium
UNIT_EXTRA='AmbientCapabilities=CAP_NET_BIND_SERVICE
CapabilityBoundingSet=CAP_NET_BIND_SERVICE
ProtectSystem=strict
ReadWritePaths=/var/lib/sdlc/technitium
MemoryHigh=1G
MemoryMax=1536M
MemorySwapMax=256M'
# Reutiliza somente os checks de ownership e a remoção comuns aos serviços nativos.
# shellcheck source=scripts/linux/native-services.sh
source "$REPO_DIR/scripts/linux/native-services.sh"

service_config_defaults() { DNS_LISTEN_IPV4=''; DNS_CIDRS=''; DNS_FORWARDERS=''; DNS_RECORDS_PATH=''; }
service_config_key() {
    case "$1" in DNS_LISTEN_IPV4|DNS_CIDRS|DNS_FORWARDERS|DNS_RECORDS_PATH) return 0 ;; *) return 1 ;; esac
}
service_validate_config() {
    python3 "$REPO_DIR/technitium/linux/configure.py" validate-network \
        --listen "$DNS_LISTEN_IPV4" --cidrs "$DNS_CIDRS" --forwarders "$DNS_FORWARDERS" || die 'Configuração DNS inválida; instalação interrompida.'
    export DNS_LISTEN_IPV4 DNS_CIDRS DNS_FORWARDERS DNS_RECORDS_PATH
}
service_preflight() {
    ACCESS_DESCRIPTION="https://$DOMAIN; DNS $DNS_LISTEN_IPV4:53 TCP/UDP"
    python3 "$REPO_DIR/technitium/linux/configure.py" check-host --listen "$DNS_LISTEN_IPV4" \
        --managed "$SDLC_DIR/.configured" || die 'IP/portas DNS indisponíveis; instalação interrompida.'
    if [[ ! -f $SDLC_DIR/config/initial-records.json ]]; then
        [[ $DNS_RECORDS_PATH == /* && -f $DNS_RECORDS_PATH && ! -L $DNS_RECORDS_PATH ]] || die 'DNS_RECORDS_PATH exige um JSON local existente, absoluto e sem symlink.'
        python3 "$REPO_DIR/technitium/linux/configure.py" validate-records --records "$DNS_RECORDS_PATH" --domain "$DOMAIN" --listen "$DNS_LISTEN_IPV4" || die 'Registros DNS inválidos; instalação interrompida.'
    fi
}

# common.sh usa strip-root; o portátil do Technitium já possui arquivos na raiz.
install_binary() {
    local archive="$SDLC_DIR/$ARTIFACT" staging
    fetch_artifact "$ARTIFACT" "$archive"
    if [[ ! -f $SDLC_DIR/.app-ready ]]; then
        [[ ! -e $SDLC_DIR/app ]] || die 'app sem marcador de conclusão; preserve para diagnóstico antes de retomar.'
        staging=$(mktemp -d "$SDLC_DIR/app.XXXXXX")
        python3 "$REPO_DIR/scripts/linux/artifacts.py" extract --archive "$archive" --output "$staging"
        [[ -f $staging/DnsServerApp.dll ]] || die 'Binário portátil não contém DnsServerApp.dll.'
        chmod 0755 "$staging"
        mv -- "$staging" "$SDLC_DIR/app"
        touch "$SDLC_DIR/.app-ready"
    fi
    chgrp -R "$SERVICE_USER" "$SDLC_DIR/app"
    chmod -R g+rX "$SDLC_DIR/app"
}

service_prepare() {
    [[ -f $SDLC_DIR/config/initial-records.json ]] || install -m 0600 "$DNS_RECORDS_PATH" "$SDLC_DIR/config/initial-records.json"
    install -d -m 0750 -o "$SERVICE_USER" -g "$SERVICE_USER" "$DATA_DIR/config" "$DATA_DIR/logs"
    install -m 0640 -o root -g "$SERVICE_USER" "$SDLC_DIR/secrets/admin_password" "$SDLC_DIR/config/bootstrap-password"
    cat > "$SDLC_DIR/service.env" <<EOF
DNS_SERVER_DOMAIN=$DOMAIN
DNS_SERVER_ADMIN_PASSWORD_FILE=$SDLC_DIR/config/bootstrap-password
DNS_SERVER_WEB_SERVICE_LOCAL_ADDRESSES=127.0.0.1
DNS_SERVER_WEB_SERVICE_HTTP_PORT=$BACKEND_PORT
DNS_SERVER_WEB_SERVICE_REVERSE_PROXY_ADDRESSES=127.0.0.1/32
DNS_SERVER_RECURSION=UseSpecifiedNetworkACL
DNS_SERVER_RECURSION_NETWORK_ACL=$DNS_CIDRS
DNS_SERVER_FORWARDERS=$DNS_FORWARDERS
DNS_SERVER_FORWARDER_PROTOCOL=Udp
DNS_SERVER_ENABLE_BLOCKING=false
DNS_SERVER_LOG_FOLDER_PATH=$DATA_DIR/logs
DNS_SERVER_LOG_MAX_LOG_FILE_DAYS=7
DNS_SERVER_STATS_MAX_STAT_FILE_DAYS=7
DOTNET_CLI_TELEMETRY_OPTOUT=1
EOF
    EXEC_START="/usr/bin/dotnet $SDLC_DIR/app/DnsServerApp.dll $DATA_DIR/config --portable-app --stop-if-bind-fails"
    # Não publica os listeners padrão nem interfere no systemd-resolved durante o primeiro boot.
    if [[ ! -f $DATA_DIR/.dns-initialized ]]; then
        systemctl is-active --quiet sdlc-technitium-bootstrap && die 'Bootstrap já está ativo; verifique antes de retomar.'
        systemd-run --quiet --collect --service-type=exec --unit=sdlc-technitium-bootstrap \
            --property="User=$SERVICE_USER" --property="Group=$SERVICE_USER" \
            --property="WorkingDirectory=$SDLC_DIR/app" --property="EnvironmentFile=$SDLC_DIR/service.env" \
            --property=PrivateNetwork=true --property=NoNewPrivileges=true \
            --property=AmbientCapabilities=CAP_NET_BIND_SERVICE --property=CapabilityBoundingSet=CAP_NET_BIND_SERVICE \
            --property=MemoryMax=1536M /usr/bin/dotnet "$SDLC_DIR/app/DnsServerApp.dll" "$DATA_DIR/config" --portable-app
        local pid status=0
        pid=$(systemctl show -p MainPID --value sdlc-technitium-bootstrap)
        if [[ $pid == 0 ]]; then
            systemctl stop sdlc-technitium-bootstrap || true
            die 'Bootstrap não iniciou; consulte journalctl -u sdlc-technitium-bootstrap.'
        fi
        nsenter -t "$pid" -n -- python3 "$REPO_DIR/technitium/linux/configure.py" bootstrap \
            --password-file "$SDLC_DIR/secrets/admin_password" --records "$SDLC_DIR/config/initial-records.json" \
            --domain "$DOMAIN" --listen "$DNS_LISTEN_IPV4" --cidrs "$DNS_CIDRS" --forwarders "$DNS_FORWARDERS" || status=$?
        systemctl stop sdlc-technitium-bootstrap
        ((status == 0)) || die 'Bootstrap falhou; dados preservados para diagnóstico.'
        touch "$DATA_DIR/.dns-initialized"
    fi
    printf 'Painel: https://%s\nUsuário: admin\nSenha inicial: %s\n' "$DOMAIN" "$ADMIN_PASSWORD" > "$SDLC_DIR/credentials.txt"
}
service_wait() {
    wait_http "$UPSTREAM/"
    dig +time=3 +tries=1 +short "@$DNS_LISTEN_IPV4" "$DOMAIN" A | grep -Fxq "$DNS_LISTEN_IPV4" || die 'Registro do painel não respondeu por UDP.'
    dig +tcp +time=3 +tries=1 +short "@$DNS_LISTEN_IPV4" "$DOMAIN" A | grep -Fxq "$DNS_LISTEN_IPV4" || die 'Registro do painel não respondeu por TCP.'
}
service_firewall() {
    local profile=/etc/ufw/applications.d/sdlc-technitium-dns cidr
    owned_file "$profile"
    cat > "$profile" <<'EOF'
# Managed by sdlc-infrastructure: technitium
[SDLC-technitium-dns]
title=Technitium DNS TCP e UDP
description=Consultas DNS das redes autorizadas
ports=53/tcp|53/udp
EOF
    local -a cidrs
    IFS=, read -ra cidrs <<< "$DNS_CIDRS"
    for cidr in "${cidrs[@]}"; do
        ufw allow from "$cidr" to "$DNS_LISTEN_IPV4" app SDLC-technitium-dns comment sdlc-technitium-dns
    done
}
service_remove_firewall() {
    local profile=/etc/ufw/applications.d/sdlc-technitium-dns cidr
    [[ -f $profile ]] || return 0
    owned_file "$profile"
    local -a cidrs
    IFS=, read -ra cidrs <<< "$DNS_CIDRS"
    for cidr in "${cidrs[@]}"; do ufw --force delete allow from "$cidr" to "$DNS_LISTEN_IPV4" app SDLC-technitium-dns; done
    rm -f -- "$profile"
}
