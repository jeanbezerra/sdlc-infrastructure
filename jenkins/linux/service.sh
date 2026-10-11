#!/usr/bin/env bash
# Metadados do instalador nativo Ubuntu 26.04 LTS.
# Variáveis consumidas pela biblioteca common.sh.
# shellcheck disable=SC2034
SERVICE=jenkins
SERVICE_VERSION=2.580.1
PROXY_KIND=http
BACKEND_PORT=18081
BACKEND_PORTS="18081"
PUBLIC_PORT=443
UPSTREAM=http://127.0.0.1:18081
ARTIFACT="jenkins-2.580.1.war"
ROOT_PREFIX=''
PACKAGE_GROUPS="java21"
UNIT_EXTRA=''
BASIC_AUTH=false
# shellcheck source=scripts/linux/native-services.sh
source "$REPO_DIR/scripts/linux/native-services.sh"

