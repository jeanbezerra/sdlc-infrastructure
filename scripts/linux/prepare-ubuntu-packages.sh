#!/usr/bin/env bash
# Baixa a árvore de dependências completa, com índices Ubuntu isolados. Não instala pacotes.
set -Eeuo pipefail
[[ $# -ge 2 ]] || { echo 'Uso: bash prepare-ubuntu-packages.sh /saida/bundle.tar.gz pacote ...' >&2; exit 1; }
# shellcheck source=/dev/null
. /etc/os-release
[[ $ID == ubuntu && $VERSION_ID == 26.04 && $(dpkg --print-architecture) == amd64 ]] || { echo 'Exige Ubuntu 26.04 amd64.' >&2; exit 1; }
output=$(realpath -m "$1"); shift
work=$(mktemp -d)
trap 'rm -rf -- "$work"' EXIT
mkdir -p "$work/lists/partial" "$work/archives/partial"
cat > "$work/ubuntu.sources" <<'EOF'
Types: deb
URIs: https://archive.ubuntu.com/ubuntu/
Suites: resolute resolute-updates
Components: main universe
Architectures: amd64
Signed-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg

Types: deb
URIs: https://security.ubuntu.com/ubuntu/
Suites: resolute-security
Components: main universe
Architectures: amd64
Signed-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg
EOF
options=(-o "Dir::Etc::sourcelist=$work/ubuntu.sources" -o Dir::Etc::sourceparts=-
    -o "Dir::State::lists=$work/lists" -o Dir::State::status=/dev/null
    -o "Dir::State::extended_states=$work/extended_states" -o "Dir::Cache::archives=$work/archives"
    -o "Dir::Cache::pkgcache=$work/pkgcache.bin" -o "Dir::Cache::srcpkgcache=$work/srcpkgcache.bin"
    -o Debug::NoLocking=true -o APT::Sandbox::User="$(id -un)" -o Acquire::Retries=3)
apt-get "${options[@]}" update
apt-get "${options[@]}" --download-only --yes --no-install-recommends install "$@"
mkdir -p "$work/bundle"
for package in "$work/archives/"*.deb; do
    [[ -f $package ]] || { echo 'Nenhum pacote baixado.' >&2; exit 1; }
    dpkg-deb -f "$package" Package Version Architecture | paste -sd '\t' >> "$work/bundle/versions.tsv"
    mv -- "$package" "$work/bundle/"
done
mkdir -p "$(dirname "$output")"
tar -czf "$output" -C "$work/bundle" .
printf 'Bundle Ubuntu criado: %s\n' "$output"
