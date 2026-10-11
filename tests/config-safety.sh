#!/usr/bin/env bash
set -Eeuo pipefail
REPO_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
# shellcheck source=scripts/linux/common.sh
source "$REPO_DIR/scripts/linux/common.sh"
ACTION=install; SERVICE=grafana; PROXY_KIND=http
work=$(mktemp -d)
trap 'rm -rf -- "$work"' EXIT
printf '{"schema":1,"platform":"ubuntu26.04-amd64","artifacts":[]}\n' > "$work/manifest.json"
cat > "$work/good.env" <<EOF
DOMAIN=grafana.example.com
ACCESS_CIDRS=127.0.0.1/32,::1/128
SSH_CIDRS=127.0.0.1/32
ARTIFACT_BASE_URL=https://cdn.example.com/release
MANIFEST_PATH=$work/manifest.json
EOF
read_config "$work/good.env"
expect_rejection() {
    if (read_config "$work/bad.env") > /dev/null 2>&1; then
        echo "Falhou: aceitou $1" >&2; exit 1
    fi
}
cp "$work/good.env" "$work/bad.env"
printf 'UNKNOWN=yes\n' >> "$work/bad.env"
expect_rejection 'chave desconhecida'
cp "$work/good.env" "$work/bad.env"
printf 'DOMAIN=other.example.com\n' >> "$work/bad.env"
expect_rejection 'chave duplicada'
# Ataque permanece literal; o parser jamais executa o conteúdo.
# shellcheck disable=SC2016
sed 's|DOMAIN=.*|DOMAIN=$(touch /tmp/sdlc-must-not-execute)|' "$work/good.env" > "$work/bad.env"
expect_rejection 'substituição de shell'
sed 's|SSH_PORT=.*|SSH_PORT=65536|' "$work/good.env" > "$work/bad.env"
printf 'SSH_PORT=65536\n' >> "$work/bad.env"
expect_rejection 'porta inválida'
sed 's|ACCESS_CIDRS=.*|ACCESS_CIDRS=10.20.1.2/16|' "$work/good.env" > "$work/bad.env"
expect_rejection 'CIDR não normalizado'
PROXY_KIND=postgresql
sed 's|ACCESS_CIDRS=.*|ACCESS_CIDRS=0.0.0.0/0|' "$work/good.env" > "$work/bad.env"
expect_rejection 'banco público'
PROXY_KIND=http
ln -s "$work/good.env" "$work/bad-link.env"
if (read_config "$work/bad-link.env") >/dev/null 2>&1; then exit 1; fi
printf 'arquivo de outra aplicação\n' > "$work/foreign.conf"
if (owned_file "$work/foreign.conf") >/dev/null 2>&1; then exit 1; fi
ln -s "$work/foreign.conf" "$work/link.conf"
if (owned_file "$work/link.conf") >/dev/null 2>&1; then exit 1; fi
SERVICE='../../tmp'
if (check_paths) >/dev/null 2>&1; then exit 1; fi
SERVICE=grafana; ACTION=uninstall; YES=false; KEEP_DATA=false
mkdir "$work/state"
printf 'sentinela\n' > "$work/state/keep"
# Exercita o gate de remoção em fixture; não usa diretório de serviço do host.
check_paths() { SDLC_DIR="$work/state"; }
if (uninstall_service) >/dev/null 2>&1; then exit 1; fi
[[ $(cat "$work/state/keep") == sentinela ]]
for service in grafana redis postgresql; do
    bash "$REPO_DIR/$service/linux/uninstall.sh" --help >/dev/null
done
for service in argocd harbor; do
    if bash "$REPO_DIR/$service/linux/install.sh" >/dev/null 2>&1; then exit 1; else [[ $? == 2 ]]; fi
done
printf 'Configuração, ownership e wrappers pendentes: OK\n'
