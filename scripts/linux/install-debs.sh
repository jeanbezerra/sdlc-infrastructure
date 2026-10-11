#!/usr/bin/env bash
# Mantém temporariamente os pacotes sem auto-start e evita criar o cluster PostgreSQL main.
set -Eeuo pipefail
[[ $EUID == 0 && $# == 1 && -d $1 ]] || exit 1
work=$(mktemp -d)
had_policy=false; had_cluster_conf=false
disable_new_redis=false
# policy-rc.d bloqueia o start, mas o pacote ainda habilita sua unit para o boot.
# Preserva qualquer Redis que já estivesse instalado antes deste bundle.
for package in "$1"/redis-server_*.deb; do
    if [[ -f $package ]] && [[ $(dpkg-query -W -f='${Status}' redis-server 2>/dev/null || true) != 'install ok installed' ]]; then
        disable_new_redis=true
    fi
done
cluster_conf=/etc/postgresql-common/createcluster.conf
cleanup() {
    if [[ $had_policy == false ]]; then rm -f /usr/sbin/policy-rc.d; fi
    if [[ $had_cluster_conf == true ]]; then
        cp "$work/createcluster.conf" "$cluster_conf"
    elif [[ -f $cluster_conf.dpkg-dist ]]; then
        mv "$cluster_conf.dpkg-dist" "$cluster_conf"
    else
        printf 'create_main_cluster = true\n' > "$cluster_conf"
    fi
    if [[ $disable_new_redis == true && -f /usr/lib/systemd/system/redis-server.service ]]; then
        systemctl daemon-reload
        systemctl disable --now redis-server.service
    fi
    rm -rf -- "$work"
}
if [[ -e /usr/sbin/policy-rc.d ]]; then had_policy=true; fi
if [[ -f $cluster_conf ]]; then had_cluster_conf=true; cp "$cluster_conf" "$work/createcluster.conf"; fi
trap cleanup EXIT
if [[ $had_policy == false ]]; then
    printf '#!/bin/sh\nexit 101\n' > /usr/sbin/policy-rc.d
    chmod 0755 /usr/sbin/policy-rc.d
fi
mkdir -p /etc/postgresql-common
printf 'create_main_cluster = false\n' > "$cluster_conf"
export DEBIAN_FRONTEND=noninteractive
# --no-download também impede que APT copie file: para o cache padrão.
# Usa o diretório já verificado como cache local, sem acesso a repositórios remotos.
mkdir -p "$1/partial"
apt-get -y --no-download --no-install-recommends -o "Dir::Cache::archives=$1" \
    -o Dpkg::Options::=--force-confold install "$1/"*.deb
