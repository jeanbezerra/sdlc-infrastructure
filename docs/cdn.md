# Preparar os binários e publicar no CDN

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
| `ubuntu26-java21.tar.gz` | OpenJDK 21 e dependências oficiais Ubuntu |
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

## 3. Publicar em S3/CloudFront

Execute com as suas credenciais AWS e os nomes reais do bucket/prefixo:

```bash
aws s3 sync artifacts/cdn/ubuntu26-amd64/ \
  s3://MEU-BUCKET/sdlc/2026-10-11/ubuntu26-amd64/ \
  --exclude '.*'
```

Não foi realizado upload pelo projeto. O exemplo é para o administrador executar. Use um prefixo imutável por release. Não publique configurações de VM, senhas, certificados privados ou arquivos `.env` nessa pasta. Verifique as condições de redistribuição/licenciamento dos produtos e runtimes no seu ambiente.

Configure um endpoint HTTPS do CloudFront que leia os objetos do S3. O instalador atual consome uma URL acessível sem autenticação de aplicação e sem query string. Um bucket privado pode usar acesso de origem do CloudFront; autenticação do cliente por URL assinada/cookie não foi implementada. O instalador não usa credenciais AWS.

Em cada `.env`:

```dotenv
ARTIFACT_BASE_URL=https://cdn.suaempresa.com/sdlc/2026-10-11/ubuntu26-amd64
MANIFEST_PATH=/home/ubuntu/sdlc-manifest.json
```

Copie o `manifest.json` original para `/home/ubuntu/sdlc-manifest.json` por um canal confiável, como o seu provisionamento/SSH. Conferir o hash de um binário contra um manifesto alterável no mesmo download não estabelece confiança; por isso o manifesto é fornecido localmente. Os scripts verificam SHA256 antes de instalar ou extrair arquivos e bloqueiam caminhos inseguros em arquivos compactados.

## 4. Dependências e atualização

Na VM de destino, o bootstrap mínimo é `python3`, `curl` e `ca-certificates`, instalados pelo APT oficial. Depois, os bundles do CDN fornecem as dependências e são instalados com `apt-get --no-download`; não há fallback que baixe silenciosamente outro binário do fornecedor.

Bundles podem atualizar bibliotecas Ubuntu compartilhadas. Use snapshots compatíveis com o nível de atualização das VMs. Downgrades não são autorizados automaticamente. Regenere e publique em outro prefixo quando atualizar pacotes; examine `versions.tsv` e teste em uma VM descartável antes de usar no ambiente principal.

Para mudar as versões dos produtos, atualize o catálogo, os metadados `service.sh` e os READMEs juntos. Upgrade de banco/aplicação exige o procedimento e as migrations oficiais; reexecutar o instalador não é um mecanismo de major upgrade.

[Voltar ao sumário](../README.md)
