# Preparar os binários e servir no CDN local

## 1. Máquina de preparação

Use Ubuntu 26.04 LTS amd64, inclusive WSL, com espaço livre para cerca de **6 GB** entre downloads e validação. Esta máquina só prepara arquivos; não instala os serviços do projeto.

```bash
sudo apt-get update
sudo apt-get install -y python3 ca-certificates gnupg ubuntu-keyring
cd /caminho/sdlc-infrastructure
python3 scripts/linux/artifacts.py prepare
```

O script baixa os arquivos listados em [catalog.json](../scripts/linux/catalog.json), verifica checksums oficiais ou assinatura GPG e gera os bundles APT. Os índices APT são isolados dos repositórios configurados na máquina, usam apenas `archive.ubuntu.com` e `security.ubuntu.com`, e são validados pelo keyring Ubuntu. Uma base de pacotes vazia permite resolver **todas as dependências**, mesmo que já estejam instaladas na máquina de preparação. Nenhum `apt install` é executado nesta etapa.

## 2. Conteúdo da pasta de saída

`artifacts/cdn/ubuntu26-amd64/` contém:

| Arquivo | Origem / uso |
| --- | --- |
| `jenkins-2.580.1.war` | Jenkins LTS, execução com Java 21 |
| `grafana-13.2.3.linux-amd64.tar.gz` | Grafana OSS |
| `prometheus-3.15.0.linux-amd64.tar.gz` | Prometheus e promtool |
| `keycloak-26.8.0.tar.gz` | Keycloak |
| `nexus-3.96.4-01-linux-x86_64.tar.gz` | Nexus com runtime do fornecedor |
| `sonarqube-26.9.0.129388.zip` e `.asc` | SonarQube Community Build e assinatura |
| `opensearch-3.9.0-linux-x64.tar.gz` | OpenSearch com JDK e plugins |
| `ubuntu26-base.tar.gz` | Nginx, módulo stream, UFW, TLS, utilitários e dependências |
| `ubuntu26-java21.tar.gz` | OpenJDK 21, fontconfig, fontes DejaVu e HarfBuzz oficiais Ubuntu |
| `ubuntu26-postgresql18.tar.gz` | PostgreSQL 18 e cliente oficial Ubuntu |
| `ubuntu26-redis.tar.gz` | Redis e redis-cli oficiais Ubuntu |
| `manifest.json` / `SHA256SUMS` | Origem, versão, tamanho e SHA256 dos arquivos |

As versões exatas dos pacotes Ubuntu ficam em `versions.tsv` dentro de cada bundle. PostgreSQL e Redis usam as versões de segurança publicadas pelo Ubuntu na data de preparação, sem compilar fontes nem adicionar PGDG/Redis APT de terceiros.

O conjunto baixado nesta entrega inclui PostgreSQL `18.6-0ubuntu0.26.04.1`, Redis `5:8.0.5-1ubuntu0.1` e OpenJDK 21 `21.0.12.1+1-1~26.04.4`. Ao preparar novamente, o APT pode selecionar atualizações oficiais mais recentes; o manifesto e `versions.tsv` registrarão esse novo conjunto.

Verifique o conteúdo antes da publicação:

```bash
cd artifacts/cdn/ubuntu26-amd64
sha256sum -c SHA256SUMS
```

## 3. Apache HTTPD e DNS local

O ambiente utiliza:

| Recurso | Endereço / caminho |
| --- | --- |
| DNS do CDN | `cdn.trendsoftware.brasil` → `192.168.0.200` |
| Servidor | Apache HTTPD em Linux |
| Pasta publicada | `/var/www/html/cdn/ubuntu26-amd64/` |
| URL dos artefatos | `http://cdn.trendsoftware.brasil/cdn/ubuntu26-amd64` |
| VM dedicada à homologação Codex | `192.168.0.250`, Ubuntu 26.04 LTS |

O servidor que serve arquivos pode usar outra distribuição Linux; os bundles de instalação continuam exclusivos de **Ubuntu 26.04 amd64**. A máquina de preparação também precisa dessa versão.

Publique o conteúdo de `artifacts/cdn/ubuntu26-amd64/` na pasta indicada, por SCP/SFTP/rsync. Em atualizações futuras, mantenha um prefixo por release, por exemplo `cdn/releases/2026-10-11/ubuntu26-amd64/`; não substitua arquivos de um release que já esteja em uso. A pasta atual foi conferida remotamente e contém os onze artefatos esperados. Durante a homologação de 11/10/2026, o bundle Java foi corrigido para incluir as dependências de fontes exigidas pelo Jenkins; o manifesto e `SHA256SUMS` foram atualizados junto com ele. Os três arquivos originais foram preservados em `/home/trendsoftware/sdlc-cdn-backup-20261011/` no servidor CDN.

No servidor Apache:

```bash
cd /var/www/html/cdn/ubuntu26-amd64
sha256sum -c SHA256SUMS
sudo apache2ctl configtest
```

Na VM de destino:

```bash
getent hosts cdn.trendsoftware.brasil
curl -I http://cdn.trendsoftware.brasil/cdn/ubuntu26-amd64/manifest.json
```

Os nomes DNS dos serviços instalados são separados do nome do CDN. Configure seus registros para os IPs das respectivas VMs. A homologação utiliza nomes `<serviço>.codex.trendsoftware.brasil` registrados em `/etc/hosts` da VM; outros computadores podem usar `curl --resolve` ou registros DNS equivalentes.

### HTTP privado e manifesto confiável

Em cada `.env`:

```dotenv
ARTIFACT_BASE_URL=http://cdn.trendsoftware.brasil/cdn/ubuntu26-amd64
ALLOW_HTTP_ARTIFACTS=true
MANIFEST_PATH=/home/trendsoftware/sdlc-manifest.json
```

HTTP é uma opção explícita para esta rede local. O instalador resolve o endereço e só permite HTTP se todos os IPs forem privados/loopback; também valida redirecionamentos. Um endpoint HTTP público é recusado. Os downloads oficiais na preparação continuam em HTTPS.

Copie o `manifest.json` original por **SSH/SFTP ou provisionamento confiável**. O arquivo obtido pelo mesmo HTTP não constitui uma fonte independente de confiança. Os SHA256 são conferidos antes de instalar/extrair qualquer arquivo. HTTP não oferece sigilo e pode sofrer interrupção de tráfego. Não envie credenciais, arquivos `.env` ou chaves privadas ao CDN.

Para HTTPS com uma CA interna, publique o certificado no Apache e instale a CA nas VMs. Use `ARTIFACT_BASE_URL=https://...` e `ALLOW_HTTP_ARTIFACTS=false`; mantenha a validação do certificado. As aplicações continuam com Nginx HTTPS, mesmo quando os binários chegam do CDN HTTP local.

### Configuração Apache opcional

A publicação atual funciona pelo DocumentRoot padrão. Para um virtual host dedicado, adapte as redes reais no exemplo:

```apache
<VirtualHost *:80>
    ServerName cdn.trendsoftware.brasil
    DocumentRoot /var/www/html
    <Directory /var/www/html/cdn>
        Options -Indexes
        AllowOverride None
        Require ip 127.0.0.1 ::1 192.168.0.0/24
    </Directory>
    ErrorLog ${APACHE_LOG_DIR}/sdlc-cdn.error.log
    CustomLog ${APACHE_LOG_DIR}/sdlc-cdn.access.log combined
</VirtualHost>
```

Salve em `/etc/apache2/sites-available/sdlc-cdn.conf`, execute `sudo a2ensite sdlc-cdn`, `sudo apache2ctl configtest` e `sudo systemctl reload apache2`. Esse exemplo não foi aplicado automaticamente no servidor existente. Libere 80/TCP somente para as redes das VMs no firewall do CDN, preservando o acesso SSH.

### S3/CloudFront como alternativa futura

O mesmo diretório pode ser publicado em S3/CloudFront futuramente; altere `ARTIFACT_BASE_URL` para HTTPS e desabilite a opção HTTP. Os instaladores não usam credenciais AWS nem exigem uma plataforma específica de CDN.

## 4. Dependências e atualização

Na VM de destino, o bootstrap mínimo é `python3`, `curl` e `ca-certificates`, instalados pelo APT oficial. Depois, os bundles do CDN fornecem as dependências e são instalados com `apt-get --no-download`; não há fallback que baixe silenciosamente outro binário do fornecedor.

Bundles podem atualizar bibliotecas Ubuntu compartilhadas. Use snapshots compatíveis com o nível de atualização das VMs. Downgrades não são autorizados automaticamente. Regenere e publique em outro prefixo quando atualizar pacotes; examine `versions.tsv` e teste em uma VM descartável antes de usar no ambiente principal.

Para mudar as versões dos produtos, atualize o catálogo, os metadados `service.sh` e os READMEs juntos. Upgrade de banco/aplicação exige o procedimento e as migrations oficiais; reexecutar o instalador não é um mecanismo de major upgrade.

[Voltar ao sumário](../README.md)
