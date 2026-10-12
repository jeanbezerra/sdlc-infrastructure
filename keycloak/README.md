# Keycloak

## Responsabilidade no SDLC

Centraliza identidade, login e SSO via OIDC/SAML para ferramentas do SDLC e aplicações, usando realms, clientes, roles e grupos.

## Requisitos e distribuição

- **Ubuntu Server 26.04 LTS amd64**, systemd e sudo.
- Arquivo oficial `keycloak-26.8.0.tar.gz` da versão 26.8.0.
- Dependências: `ubuntu26-base.tar.gz` e `ubuntu26-java21.tar.gz` do CDN.
- Capacidade inicial sugerida: 2 vCPU, 4 GB RAM e 30 GB de disco, mais o PostgreSQL dedicado. Dimensione conforme a carga real.
- PostgreSQL dedicado acessível por TLS, com banco, usuário e CA confiável próprios.

## Portas

| Porta | Uso | Acesso |
| --- | --- | --- |
| 80/TCP | Redirecionamento HTTPS | ACCESS_CIDRS |
| 443/TCP | Nginx HTTPS UI/API | ACCESS_CIDRS |
| 18084/TCP | Backend nativo | Somente 127.0.0.1 |
| 19084/TCP | Management, health e métricas | Somente loopback |
| 7800/TCP | Transporte do cache Infinispan | Somente 127.0.0.1 |
| 57800/TCP | Detecção de falhas JGroups | Somente 127.0.0.1 |
| SSH_PORT/TCP | Administração | SSH_CIDRS |

Libere somente as portas externas no firewall de rede/security group. Veja a [matriz completa](../docs/ports.md).

## Instalação passo a passo

### 1. Preparar a VM

Prepare os arquivos seguindo o [guia do CDN](../docs/cdn.md). Copie todo o projeto para a VM, o manifesto confiável para `/home/ubuntu/sdlc-manifest.json` e configure `keycloak.suaempresa.com` para o IP dela.

```bash
sudo apt-get update
sudo apt-get install -y python3 curl ca-certificates
cd /home/ubuntu/sdlc-infrastructure
```

### 2. Criar banco e role exclusivos

Na VM PostgreSQL deste projeto, a partir da raiz do repositório, gere uma senha e execute o SQL **uma vez**, depois de conferir o cluster:

```bash
umask 077
openssl rand -hex 24 > /home/ubuntu/keycloak-db-password
export SDLC_SERVICE_DB_PASSWORD="$(cat /home/ubuntu/keycloak-db-password)"
sudo --preserve-env=SDLC_SERVICE_DB_PASSWORD -u sdlc-postgresql \
  /usr/lib/postgresql/18/bin/psql -X -v ON_ERROR_STOP=1 \
  -h /var/lib/sdlc/postgresql/run -p 15432 -U sdlc_admin -d postgres \
  < keycloak/linux/database.sql
unset SDLC_SERVICE_DB_PASSWORD
```

[database.sql](linux/database.sql) cria uma role sem privilégio de cluster e um banco próprio. O produto precisa ser owner desse banco/schema para as migrations oficiais; não recebe superuser, CREATEDB ou CREATEROLE. Se houver outro cluster, adapte a conexão administrativa. O script não reutiliza nem apaga banco existente.

Transfira a senha com segurança para a VM da aplicação e a CA do PostgreSQL para `/home/ubuntu/postgresql-ca.crt`. Se o banco usa certificado autoassinado, copie **somente** `/opt/sdlc/postgresql/tls/server.crt` como certificado confiável. Nunca distribua a chave privada. Inclua a rede da aplicação nos CIDRs do proxy PostgreSQL.

### 3. Editar os parâmetros

```bash
cp keycloak/linux/.env.example keycloak/linux/.env
nano keycloak/linux/.env
chmod 600 keycloak/linux/.env
```

Ajuste DOMAIN, ACCESS_CIDRS, SSH_PORT, SSH_CIDRS, ARTIFACT_BASE_URL, ALLOW_HTTP_ARTIFACTS e MANIFEST_PATH. Configure também DB_HOST, DB_PORT=5432, DB_NAME, DB_USER, DB_PASSWORD_FILE e DB_CA_PATH. Para produção, informe TLS_CERT_PATH/TLS_KEY_PATH de uma CA confiável. Vazio gera certificado autoassinado para laboratório. Use valores literais, sem aspas/expansões. Confirme que SSH_CIDRS inclui seu IP antes de habilitar o firewall.

### 4. Instalar e verificar

```bash
sudo bash keycloak/linux/install.sh --config "$(pwd)/keycloak/linux/.env"
sudo systemctl status sdlc-keycloak --no-pager
sudo journalctl -u sdlc-keycloak -n 100 --no-pager
sudo nginx -t
sudo ufw status verbose
sudo ss -lntp
```

Confirme o backend em loopback/18084 e o proxy na porta 443. O serviço volta no boot. Reexecutar com o mesmo arquivo retoma e reinicia o serviço, preservando dados e segredos. Veja [operação Linux](../docs/linux.md) para certificados/manutenção.

### 5. Acessar e integrar

Entre em https://SEU_DOMAIN/admin/ com admin e a senha de credentials.txt. Crie um administrador definitivo e remova os parâmetros KC_BOOTSTRAP_ADMIN_* de service.env durante manutenção. Crie um realm do ambiente e um cliente OIDC por aplicação, com redirect URIs exatas. A execução usa start --optimized e headers xforwarded do Nginx.

```bash
sudo cat /opt/sdlc/keycloak/credentials.txt
```

## Arquivos e operação

| Recurso | Caminho |
| --- | --- |
| Configuração, binários e segredos | `/opt/sdlc/keycloak/` |
| Dados locais | `/var/lib/sdlc/keycloak/` |
| Conta sem login | `sdlc-keycloak` |
| Unit systemd | `/etc/systemd/system/sdlc-keycloak.service` |
| Nginx | `/etc/nginx/conf.d/sdlc-keycloak.conf` |
| Perfil UFW | `/etc/ufw/applications.d/sdlc-keycloak` |

```bash
sudo systemctl restart sdlc-keycloak
sudo systemctl stop sdlc-keycloak
sudo systemctl start sdlc-keycloak
```

## Cache e instância

Este instalador usa `cache=ispn`, com descoberta pelo banco (`jdbc-ping`) e uma única instância. As portas JGroups 7800/57800 ficam vinculadas a `127.0.0.1` e não recebem regras públicas. Para adicionar nós, planeje endereços privados, identidade dos nós, mTLS, firewall entre VMs e afinidade de sessão conforme a [documentação de caching](https://www.keycloak.org/server/caching). A configuração atual não forma um cluster entre VMs.

## Backup e restauração

Faça backup do PostgreSQL externo keycloak e salve configuração, temas e providers em /opt/sdlc/keycloak/. Export de realm não substitui backup completo do banco.

## Diagnóstico

Falhas de login/origin sugerem DOMAIN, hostname ou redirect URI incorretos. Falhas JDBC exigem conferir CA, DNS, usuário e acesso à porta 5432. Management/health/metrics ficam em loopback/19084.

Uma falha conserva o estado para diagnóstico. Corrija a causa e retome com o mesmo .env. O ciclo do instalador foi validado na VM Ubuntu 26.04.1 LTS; consulte o [relatório da homologação](../docs/vm-validation.md). Backup/restore, HA e carga precisam de validação operacional.

## Desinstalação completa

Faça backup. **Apaga dados locais e todos os recursos próprios do serviço:**

```bash
sudo bash keycloak/linux/uninstall.sh --yes
```

Para remover da execução, mantendo os dados/segredos para reinstalar:

```bash
sudo bash keycloak/linux/uninstall.sh --yes --keep-data
```

O banco externo não é apagado pela remoção da aplicação. Sua remoção é uma operação separada do administrador. Pacotes APT, cache compartilhado, regras SSH e histórico de journal permanecem. Consulte [escopo da remoção](../docs/linux.md#desinstalar).

## Referências

- [Documentação oficial](https://www.keycloak.org/server/configuration)
- [Guia Linux](../docs/linux.md)
- [CDN e artefatos](../docs/cdn.md)
- [Voltar ao sumário](../README.md)
