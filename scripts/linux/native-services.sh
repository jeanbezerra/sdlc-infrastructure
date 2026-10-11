#!/usr/bin/env bash
# Configurações específicas; sourced pelo service.sh de cada serviço.
# Variáveis compartilhadas com common.sh e service.sh.
# shellcheck disable=SC2034,SC2153

service_preflight() {
    case "$SERVICE" in
        opensearch|sonarqube|nexus) (( $(awk '/MemTotal/ {print $2}' /proc/meminfo) >= 3900000 )) || die 'Este serviço exige ao menos 4 GB de RAM; prefira 8 GB.' ;;
    esac
    ACCESS_DESCRIPTION="https://$DOMAIN"
    [[ $PROXY_KIND == http ]] || ACCESS_DESCRIPTION="$DOMAIN:$PUBLIC_PORT (TLS)"
}

kernel_search_limit() {
    local current target=1048576
    current=$(sysctl -n vm.max_map_count)
    ((current <= target)) || target=$current
    owned_file "/etc/sysctl.d/90-sdlc-$SERVICE.conf"
    printf '# Managed by sdlc-infrastructure: %s\nvm.max_map_count=%s\n' "$SERVICE" "$target" > "/etc/sysctl.d/90-sdlc-$SERVICE.conf"
    sysctl -p "/etc/sysctl.d/90-sdlc-$SERVICE.conf"
}

service_prepare() {
    local java=/usr/lib/jvm/java-21-openjdk-amd64/bin/java
    case "$SERVICE" in
        jenkins)
            install -d -o "$SERVICE_USER" -g "$SERVICE_USER" -m 0750 "$DATA_DIR/init.groovy.d"
            cat > "$SDLC_DIR/service.env" <<EOF
JENKINS_HOME=$DATA_DIR
JENKINS_ADMIN_PASSWORD=$ADMIN_PASSWORD
JENKINS_URL=https://$DOMAIN/
EOF
            install -m 0640 -o root -g "$SERVICE_USER" "$REPO_DIR/jenkins/linux/bootstrap.groovy" "$DATA_DIR/init.groovy.d/bootstrap.groovy"
            EXEC_START="$java -Djenkins.install.runSetupWizard=false -jar $SDLC_DIR/app/jenkins.war --webroot=$DATA_DIR/war --httpListenAddress=127.0.0.1 --httpPort=$BACKEND_PORT"
            ;;
        grafana)
            cat > "$SDLC_DIR/config/grafana.ini" <<EOF
[paths]
data = $DATA_DIR
logs = $DATA_DIR/log
plugins = $DATA_DIR/plugins
provisioning = $SDLC_DIR/config/provisioning
[server]
http_addr = 127.0.0.1
http_port = $BACKEND_PORT
domain = $DOMAIN
root_url = https://$DOMAIN/
[security]
admin_user = admin
admin_password = $ADMIN_PASSWORD
cookie_secure = true
[users]
allow_sign_up = false
[auth.anonymous]
enabled = false
[log]
mode = console
EOF
            install -d -m 0755 "$SDLC_DIR/config/provisioning"
            EXEC_START="$SDLC_DIR/app/bin/grafana server --homepath=$SDLC_DIR/app --config=$SDLC_DIR/config/grafana.ini"
            ;;
        prometheus)
            if [[ ! -f $SDLC_DIR/config/prometheus.yml ]]; then
                cat > "$SDLC_DIR/config/prometheus.yml" <<EOF
global:
  scrape_interval: 30s
  evaluation_interval: 30s
scrape_configs:
  - job_name: prometheus
    static_configs:
      - targets: ['127.0.0.1:$BACKEND_PORT']
  - job_name: sdlc-services
    file_sd_configs:
      - files: ['$SDLC_DIR/config/targets/*.json']
EOF
            fi
            install -d -m 0755 "$SDLC_DIR/config/targets"
            "$SDLC_DIR/app/promtool" check config "$SDLC_DIR/config/prometheus.yml"
            EXEC_START="$SDLC_DIR/app/prometheus --config.file=$SDLC_DIR/config/prometheus.yml --storage.tsdb.path=$DATA_DIR --storage.tsdb.retention.time=15d --web.listen-address=127.0.0.1:$BACKEND_PORT --web.external-url=https://$DOMAIN/ --web.route-prefix=/"
            ;;
        keycloak)
            cat > "$SDLC_DIR/service.env" <<EOF
JAVA_HOME=/usr/lib/jvm/java-21-openjdk-amd64
KC_BOOTSTRAP_ADMIN_USERNAME=admin
KC_BOOTSTRAP_ADMIN_PASSWORD=$ADMIN_PASSWORD
KC_DB_PASSWORD=$DB_PASSWORD
EOF
            cat > "$SDLC_DIR/app/conf/keycloak.conf" <<EOF
db=postgres
cache=ispn
cache-embedded-network-bind-address=127.0.0.1
cache-embedded-network-bind-port=7800
db-url=jdbc:postgresql://$DB_HOST:$DB_PORT/$DB_NAME?sslmode=verify-full&sslrootcert=$SDLC_DIR/config/db-ca.crt
db-username=$DB_USER
hostname=https://$DOMAIN
http-enabled=true
http-host=127.0.0.1
http-port=$BACKEND_PORT
proxy-headers=xforwarded
http-management-host=127.0.0.1
http-management-port=19084
EOF
            chown -R "$SERVICE_USER:$SERVICE_USER" "$SDLC_DIR/app"
            if [[ ! -f $SDLC_DIR/app/.sdlc-built ]]; then
                runuser -u "$SERVICE_USER" -- env JAVA_HOME=/usr/lib/jvm/java-21-openjdk-amd64 "$SDLC_DIR/app/bin/kc.sh" build --db=postgres --cache=ispn --health-enabled=true --metrics-enabled=true
                touch "$SDLC_DIR/app/.sdlc-built"
            fi
            EXEC_START="$SDLC_DIR/app/bin/kc.sh start --optimized"
            ;;
        nexus)
            printf 'Login: admin\nSenha inicial real: /var/lib/sdlc/nexus/admin.password (gerada pelo Nexus).\n' > "$SDLC_DIR/credentials.txt"
            install -d -o "$SERVICE_USER" -g "$SERVICE_USER" -m 0750 "$DATA_DIR/etc" "$DATA_DIR/tmp" "$DATA_DIR/log"
            printf 'application-host=127.0.0.1\napplication-port=%s\n' "$BACKEND_PORT" > "$DATA_DIR/etc/nexus.properties"
            chgrp "$SERVICE_USER" "$DATA_DIR/etc/nexus.properties"
            chmod 0640 "$DATA_DIR/etc/nexus.properties"
            sed -i -E '/^-Dkaraf\.(data|log|home|base)=/d; /^-Djava.io.tmpdir=/d; /^-Djava.util.logging.config.file=/d; /^-XX:LogFile=/d' "$SDLC_DIR/app/bin/nexus.vmoptions"
            cat >> "$SDLC_DIR/app/bin/nexus.vmoptions" <<EOF
-Dkaraf.data=$DATA_DIR
-Dkaraf.log=$DATA_DIR/log
-Dkaraf.home=$SDLC_DIR/app
-Dkaraf.base=$SDLC_DIR/app
-Djava.util.logging.config.file=$SDLC_DIR/app/etc/spring/java.util.logging.properties
-Djava.io.tmpdir=$DATA_DIR/tmp
-XX:LogFile=$DATA_DIR/log/jvm.log
EOF
            # O launcher oficial inclui o JRE; run mantém o processo em primeiro plano.
            chown -R "$SERVICE_USER:$SERVICE_USER" "$SDLC_DIR/app"
            EXEC_START="$SDLC_DIR/app/bin/nexus run"
            ;;
        sonarqube)
            kernel_search_limit
            install -d -m 0750 -o "$SERVICE_USER" -g "$SERVICE_USER" "$DATA_DIR/data" "$DATA_DIR/temp" "$DATA_DIR/logs"
            cat > "$SDLC_DIR/app/conf/sonar.properties" <<EOF
sonar.jdbc.username=$DB_USER
sonar.jdbc.password=$DB_PASSWORD
sonar.jdbc.url=jdbc:postgresql://$DB_HOST:$DB_PORT/$DB_NAME?sslmode=verify-full&sslrootcert=$SDLC_DIR/config/db-ca.crt
sonar.web.host=127.0.0.1
sonar.web.port=$BACKEND_PORT
sonar.search.host=127.0.0.1
sonar.search.port=19001
sonar.core.serverBaseURL=https://$DOMAIN
sonar.path.data=$DATA_DIR/data
sonar.path.temp=$DATA_DIR/temp
sonar.path.logs=$DATA_DIR/logs
EOF
            printf 'JAVA_HOME=/usr/lib/jvm/java-21-openjdk-amd64\nSONAR_JAVA_PATH=%s\n' "$java" > "$SDLC_DIR/service.env"
            chown -R "$SERVICE_USER:$SERVICE_USER" "$SDLC_DIR/app"
            EXEC_START="$SDLC_DIR/app/bin/linux-x86-64/sonar.sh console"
            ;;
        opensearch)
            kernel_search_limit
            cp -a "$SDLC_DIR/app/config/." "$SDLC_DIR/config/"
            prepare_opensearch_tls
            local password_hash
            password_hash=$(printf '%s\n' "$ADMIN_PASSWORD" | htpasswd -inBC 12 '' | tr -d ':\n')
            cat > "$SDLC_DIR/config/opensearch-security/internal_users.yml" <<EOF
_meta:
  type: internalusers
  config_version: 2
admin:
  hash: '$password_hash'
  reserved: true
  backend_roles: [admin]
  description: Administrador inicial SDLC
EOF
            cat > "$SDLC_DIR/config/opensearch.yml" <<EOF
cluster.name: sdlc-opensearch
node.name: sdlc-node-1
network.host: 127.0.0.1
http.port: $BACKEND_PORT
transport.port: 19300
discovery.type: single-node
path.data: $DATA_DIR/data
path.logs: $DATA_DIR/logs
plugins.security.ssl.transport.pemcert_filepath: node.crt
plugins.security.ssl.transport.pemkey_filepath: node.key
plugins.security.ssl.transport.pemtrustedcas_filepath: root-ca.crt
plugins.security.ssl.transport.enforce_hostname_verification: false
plugins.security.ssl.http.enabled: true
plugins.security.ssl.http.pemcert_filepath: node.crt
plugins.security.ssl.http.pemkey_filepath: node.key
plugins.security.ssl.http.pemtrustedcas_filepath: root-ca.crt
plugins.security.allow_default_init_securityindex: true
plugins.security.authcz.admin_dn: ['CN=sdlc-admin']
plugins.security.nodes_dn: ['CN=sdlc-node']
plugins.security.restapi.roles_enabled: [all_access, security_rest_api_access]
EOF
            install -d -o "$SERVICE_USER" -g "$SERVICE_USER" -m 0750 "$DATA_DIR/data" "$DATA_DIR/logs"
            # O launcher muda o diretório para app/; a distribuição permanece somente leitura.
            sed -i -e "s|^-XX:HeapDumpPath=.*|-XX:HeapDumpPath=$DATA_DIR/data|" \
                -e "s|logs/|$DATA_DIR/logs/|g" "$SDLC_DIR/config/jvm.options"
            # O bootstrap criaria o keystore antes de iniciar os logs; config/ é root-owned.
            if [[ ! -f $SDLC_DIR/config/opensearch.keystore ]]; then
                OPENSEARCH_PATH_CONF="$SDLC_DIR/config" OPENSEARCH_JAVA_HOME="$SDLC_DIR/app/jdk" \
                    "$SDLC_DIR/app/bin/opensearch-keystore" create
            fi
            cat > "$SDLC_DIR/service.env" <<EOF
OPENSEARCH_PATH_CONF=$SDLC_DIR/config
OPENSEARCH_JAVA_HOME=$SDLC_DIR/app/jdk
OPENSEARCH_JAVA_OPTS=-Xms1g -Xmx1g
EOF
            UNIT_EXTRA='LimitMEMLOCK=infinity'
            EXEC_START="$SDLC_DIR/app/bin/opensearch"
            ;;
        postgresql)
            prepare_postgresql
            EXEC_START="/usr/lib/postgresql/18/bin/postgres -D $DATA_DIR/data -c config_file=$SDLC_DIR/config/postgresql.conf"
            ;;
        redis)
            local app_hash admin_hash
            app_hash=$(printf '%s' "$APP_PASSWORD" | sha256sum | cut -d' ' -f1)
            admin_hash=$(printf '%s' "$ADMIN_PASSWORD" | sha256sum | cut -d' ' -f1)
            cat > "$SDLC_DIR/config/redis.conf" <<EOF
bind 127.0.0.1
port $BACKEND_PORT
protected-mode yes
daemonize no
supervised no
dir $DATA_DIR
logfile ""
appendonly yes
appendfsync everysec
save 900 1
save 300 10
save 60 10000
aclfile $SDLC_DIR/config/users.acl
EOF
            printf 'user default off\nuser admin on #%s ~* &* +@all\nuser sdlc on #%s ~* &* +@all -@dangerous\n' "$admin_hash" "$app_hash" > "$SDLC_DIR/config/users.acl"
            EXEC_START="/usr/bin/redis-server $SDLC_DIR/config/redis.conf"
            ;;
    esac
    find "$SDLC_DIR/config" -type f -exec chmod 0640 {} +
    chgrp -R "$SERVICE_USER" "$SDLC_DIR/config"
    [[ ! -f $SDLC_DIR/service.env ]] || chmod 0600 "$SDLC_DIR/service.env"
}

prepare_opensearch_tls() {
    if [[ -f $SDLC_DIR/tls/internal-ca.key ]]; then return; fi
    openssl req -x509 -newkey rsa:3072 -nodes -sha256 -days 3650 -subj /CN=sdlc-opensearch-ca \
        -addext 'basicConstraints=critical,CA:true' -addext 'keyUsage=critical,keyCertSign,cRLSign' \
        -keyout "$SDLC_DIR/tls/internal-ca.key" -out "$SDLC_DIR/config/root-ca.crt" 2>/dev/null
    local name cn
    for name in node admin; do
        cn="sdlc-$name"
        openssl req -newkey rsa:3072 -nodes -subj "/CN=$cn" -keyout "$SDLC_DIR/config/$name.key" -out "$SDLC_DIR/tls/$name.csr" 2>/dev/null
        printf 'basicConstraints=critical,CA:false\nkeyUsage=critical,digitalSignature,keyEncipherment\nsubjectAltName=DNS:localhost,IP:127.0.0.1\nextendedKeyUsage=serverAuth,clientAuth\n' > "$SDLC_DIR/tls/internal.ext"
        openssl x509 -req -in "$SDLC_DIR/tls/$name.csr" -CA "$SDLC_DIR/config/root-ca.crt" -CAkey "$SDLC_DIR/tls/internal-ca.key" -CAcreateserial \
            -days 3650 -sha256 -extfile "$SDLC_DIR/tls/internal.ext" -out "$SDLC_DIR/config/$name.crt" 2>/dev/null
        rm -f -- "$SDLC_DIR/tls/$name.csr"
    done
}

prepare_postgresql() {
    install -d -o "$SERVICE_USER" -g "$SERVICE_USER" -m 0700 "$DATA_DIR/data"
    install -d -o "$SERVICE_USER" -g "$SERVICE_USER" -m 0750 "$DATA_DIR/run"
    if [[ ! -f $DATA_DIR/data/PG_VERSION ]]; then
        install -o "$SERVICE_USER" -g "$SERVICE_USER" -m 0600 "$SDLC_DIR/secrets/admin_password" "$DATA_DIR/init-password"
        runuser -u "$SERVICE_USER" -- /usr/lib/postgresql/18/bin/initdb -D "$DATA_DIR/data" -U sdlc_admin \
            --pwfile="$DATA_DIR/init-password" --auth-local=peer --auth-host=scram-sha-256 --encoding=UTF8 --locale=C.UTF-8
        rm -f -- "$DATA_DIR/init-password"
    fi
    [[ $(cat "$DATA_DIR/data/PG_VERSION") == 18 ]] || die 'Cluster existente não é PostgreSQL 18.'
    install -o root -g "$SERVICE_USER" -m 0640 "$SDLC_DIR/tls/server.key" "$SDLC_DIR/config/server.key"
    install -o root -g "$SERVICE_USER" -m 0644 "$SDLC_DIR/tls/server.crt" "$SDLC_DIR/config/server.crt"
    cat > "$SDLC_DIR/config/postgresql.conf" <<EOF
data_directory = '$DATA_DIR/data'
hba_file = '$SDLC_DIR/config/pg_hba.conf'
ident_file = '$SDLC_DIR/config/pg_ident.conf'
listen_addresses = '127.0.0.1'
port = $BACKEND_PORT
unix_socket_directories = '$DATA_DIR/run'
ssl = on
ssl_cert_file = '$SDLC_DIR/config/server.crt'
ssl_key_file = '$SDLC_DIR/config/server.key'
ssl_min_protocol_version = 'TLSv1.2'
password_encryption = 'scram-sha-256'
logging_collector = off
EOF
    printf 'sdlc-admin sdlc-postgresql sdlc_admin\n' > "$SDLC_DIR/config/pg_ident.conf"
    cat > "$SDLC_DIR/config/pg_hba.conf" <<'EOF'
local all sdlc_admin peer map=sdlc-admin
local all all reject
hostnossl all all 0.0.0.0/0 reject
hostnossl all all ::/0 reject
hostssl all all 127.0.0.1/32 scram-sha-256
hostssl all all ::1/128 scram-sha-256
host all all 0.0.0.0/0 reject
host all all ::/0 reject
EOF
}

pg_admin() {
    runuser -u sdlc-postgresql -- /usr/lib/postgresql/18/bin/psql -X -v ON_ERROR_STOP=1 \
        -h "$DATA_DIR/run" -p "$BACKEND_PORT" -U sdlc_admin "$@"
}

service_wait() {
    case "$SERVICE" in
        postgresql)
            local deadline=$((SECONDS + 60))
            until pg_admin -d postgres -Atqc 'SELECT 1' >/dev/null 2>&1; do
                ((SECONDS < deadline)) || die 'PostgreSQL não iniciou; consulte journalctl.'
                sleep 2
            done
            if [[ ! -f $DATA_DIR/.database-initialized ]]; then
                export SDLC_APP_PASSWORD=$APP_PASSWORD SDLC_MIGRATOR_PASSWORD=$MIGRATOR_PASSWORD
                runuser -u sdlc-postgresql --preserve-environment -- /usr/lib/postgresql/18/bin/psql -X -v ON_ERROR_STOP=1 \
                    -h "$DATA_DIR/run" -p "$BACKEND_PORT" -U sdlc_admin -d postgres < "$REPO_DIR/postgresql/linux/bootstrap.sql"
                unset SDLC_APP_PASSWORD SDLC_MIGRATOR_PASSWORD
                touch "$DATA_DIR/.database-initialized"
            fi
            ;;
        redis)
            local deadline=$((SECONDS + 60))
            until REDISCLI_AUTH="$ADMIN_PASSWORD" redis-cli --user admin -h 127.0.0.1 -p "$BACKEND_PORT" PING 2>/dev/null | grep -Fxq PONG; do
                ((SECONDS < deadline)) || die 'Redis não iniciou; consulte journalctl.'
                sleep 2
            done
            ;;
        prometheus) wait_http "$UPSTREAM/-/ready" ;;
        sonarqube)
            local deadline=$((SECONDS + 600))
            until curl --silent --max-time 5 "$UPSTREAM/api/system/status" | python3 -c 'import json,sys; sys.exit(json.load(sys.stdin).get("status") != "UP")' 2>/dev/null; do
                ((SECONDS < deadline)) || die 'SonarQube não ficou operacional em 600s.'
                sleep 5
            done
            if [[ ! -f $SDLC_DIR/.sonar-admin-ready ]]; then
                SONAR_URL="$UPSTREAM" SONAR_PASSWORD="$ADMIN_PASSWORD" python3 <<'PY'
import base64, os, urllib.parse, urllib.request
request = urllib.request.Request(os.environ['SONAR_URL'] + '/api/users/change_password',
    data=urllib.parse.urlencode(dict(login='admin', previousPassword='admin', password=os.environ['SONAR_PASSWORD'])).encode(),
    headers={'Authorization': 'Basic ' + base64.b64encode(b'admin:admin').decode()})
with urllib.request.urlopen(request, timeout=30) as response:
    assert response.status == 204
PY
                touch "$SDLC_DIR/.sonar-admin-ready"
            fi
            ;;
        *) wait_http "$UPSTREAM/" ;;
    esac
}

service_remove_preflight() {
    local data_dir="/var/lib/sdlc/$SERVICE"
    if [[ -d $data_dir ]]; then
        [[ ! -L $data_dir && $(realpath -e "$data_dir") == "$data_dir" && -f $data_dir/.managed && $(cat "$data_dir/.managed") == "sdlc-v1:$SERVICE" ]] || die 'Diretório de dados não gerenciado: remoção interrompida.'
    fi
    if id "sdlc-$SERVICE" >/dev/null 2>&1; then
        [[ $(getent passwd "sdlc-$SERVICE" | cut -d: -f5) == "SDLC managed $SERVICE" ]] || die 'Conta não gerenciada.'
    fi
}

service_remove() {
    local config="/etc/sysctl.d/90-sdlc-$SERVICE.conf"
    if [[ -f $config ]]; then owned_file "$config"; rm -f -- "$config"; fi
    # PostgreSQL externo de Keycloak/SonarQube não é removido pelo desinstalador da aplicação.
}
