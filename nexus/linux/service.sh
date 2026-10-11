#!/usr/bin/env bash
# Metadados do instalador nativo Ubuntu 26.04 LTS.
# Variáveis consumidas pela biblioteca common.sh.
# shellcheck disable=SC2034
SERVICE=nexus
SERVICE_VERSION=3.96.4-01
PROXY_KIND=http
BACKEND_PORT=18082
BACKEND_PORTS="18082"
PUBLIC_PORT=443
UPSTREAM=http://127.0.0.1:18082
ARTIFACT="nexus-3.96.4-01-linux-x86_64.tar.gz"
ROOT_PREFIX=nexus-
PACKAGE_GROUPS=""
UNIT_EXTRA=''
BASIC_AUTH=false
# shellcheck source=scripts/linux/native-services.sh
source "$REPO_DIR/scripts/linux/native-services.sh"

