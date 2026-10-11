#!/usr/bin/env bash
# Mantém temporariamente os pacotes sem auto-start e evita criar o cluster PostgreSQL main.
set -Eeuo pipefail
[[ $EUID == 0 && $# == 1 && -d $1 ]] || exit 1
work=$(mktemp -d)
had_policy=false; had_cluster_conf=false
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
apt-get -y --no-download -o Dpkg::Options::=--force-confold install "$1/"*.deb
