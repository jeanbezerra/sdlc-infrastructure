# PostgreSQL 18

## Responsabilidade no SDLC

Mantém dados relacionais transacionais. Keycloak/SonarQube usam bancos exclusivos; o banco sdlc separa owner, migrations e aplicação.

## Requisitos e distribuição

- **Ubuntu Server 26.04 LTS amd64**, systemd e sudo.
- Pacote APT oficial Ubuntu: 18 / pacote Ubuntu; versão exata em `versions.tsv` no bundle.
- Dependências: `ubuntu26-base.tar.gz` e `ubuntu26-postgresql18.tar.gz` do CDN.
- Capacidade inicial sugerida: 2 vCPU, 4 GB RAM e 60 GB SSD inicial; dimensione IOPS, WAL e backups. Dimensione conforme a carga real.


## Portas

| Porta | Uso | Acesso |
| --- | --- | --- |
| 5432/TCP | Nginx TCP/TLS | CIDRs específicos, sem /0 |
| 15432/TCP | Backend nativo | Somente 127.0.0.1 |
| SSH_PORT/TCP | Administração | SSH_CIDRS |

Libere somente as portas externas no firewall de rede/security group. Veja a [matriz completa](../docs/ports.md).

## Instalação passo a passo

### 1. Preparar a VM

Prepare os arquivos seguindo o [guia do CDN](../docs/cdn.md). Copie todo o projeto para a VM, o manifesto confiável para `/home/ubuntu/sdlc-manifest.json` e configure `postgresql.suaempresa.com` para o IP dela.

```bash
sudo apt-get update
sudo apt-get install -y python3 curl ca-certificates
cd /home/ubuntu/sdlc-infrastructure
```

### 2. Editar os parâmetros

```bash
cp postgresql/linux/.env.example postgresql/linux/.env
nano postgresql/linux/.env
chmod 600 postgresql/linux/.env
```

Ajuste DOMAIN, ACCESS_CIDRS, SSH_PORT, SSH_CIDRS, ARTIFACT_BASE_URL, ALLOW_HTTP_ARTIFACTS e MANIFEST_PATH. Para produção, informe TLS_CERT_PATH/TLS_KEY_PATH de uma CA confiável. Vazio gera certificado autoassinado para laboratório. Use valores literais, sem aspas/expansões. Confirme que SSH_CIDRS inclui seu IP antes de habilitar o firewall.

### 3. Instalar e verificar

```bash
sudo bash postgresql/linux/install.sh --config "$(pwd)/postgresql/linux/.env"
sudo systemctl status sdlc-postgresql --no-pager
sudo journalctl -u sdlc-postgresql -n 100 --no-pager
sudo nginx -t
sudo ufw status verbose
sudo ss -lntp
```

Confirme o backend em loopback/15432 e o proxy na porta 5432. O serviço volta no boot. Reexecutar com o mesmo arquivo retoma e reinicia o serviço, preservando dados e segredos. Veja [operação Linux](../docs/linux.md) para certificados/manutenção.

### 4. Acessar e integrar

Administração: sdlc_admin, reservada ao administrador. Aplicação: sdlc_app. Migrations: sdlc_migrator, usando SET ROLE sdlc_owner. Senhas em credentials.txt. O schema app possui grants DML/sequences para a aplicação e default privileges de sdlc_owner; sdlc_app não cria objetos. Crie bancos exclusivos para os demais produtos conforme seus guias.

```bash
sudo cat /opt/sdlc/postgresql/credentials.txt
```

Cliente autorizado, com certificado público/CA confiável:

```bash
psql "host=postgresql.suaempresa.com port=5432 dbname=sdlc user=sdlc_app sslmode=verify-full sslrootcert=/caminho/postgresql-ca.crt"
```

Administração local:

```bash
sudo -u sdlc-postgresql /usr/lib/postgresql/18/bin/psql -X -v ON_ERROR_STOP=1 \
  -h /var/lib/sdlc/postgresql/run -p 15432 -U sdlc_admin -d postgres
```

Valide com `SELECT version(), current_database(), current_user;`. Migrations devem conectar como sdlc_migrator e usar `SET ROLE sdlc_owner;` antes de criar objetos no schema app.

## Arquivos e operação

| Recurso | Caminho |
| --- | --- |
| Configuração, binários e segredos | `/opt/sdlc/postgresql/` |
| Dados locais | `/var/lib/sdlc/postgresql/` |
| Conta sem login | `sdlc-postgresql` |
| Unit systemd | `/etc/systemd/system/sdlc-postgresql.service` |
| Nginx | `/etc/nginx/sdlc-streams/sdlc-postgresql.conf` |
| Perfil UFW | `/etc/ufw/applications.d/sdlc-postgresql` |

```bash
sudo systemctl restart sdlc-postgresql
sudo systemctl stop sdlc-postgresql
sudo systemctl start sdlc-postgresql
```

## Backup e restauração

Use pg_dump -Fc por banco e pg_dumpall --globals-only para roles, protegendo os arquivos. Para PITR, planeje base backup e arquivamento WAL. Teste restore em outro PostgreSQL 18. Não há backup automático, HA ou replicação neste instalador.

## Diagnóstico

Administração local usa peer mapping, socket /var/lib/sdlc/postgresql/run e porta 15432. TCP sem TLS é rejeitado. Nginx aplica CIDRs; PostgreSQL vê o proxy local. sslmode=verify-full exige DNS/certificado correspondentes. Não use sdlc_admin nas aplicações.

Uma falha conserva o estado para diagnóstico. Corrija a causa e retome com o mesmo .env. O ciclo do instalador foi validado na VM Ubuntu 26.04.1 LTS; consulte o [relatório da homologação](../docs/vm-validation.md). Backup/restore, HA e carga precisam de validação operacional.

## Desinstalação completa

Faça backup. **Apaga dados locais e todos os recursos próprios do serviço:**

```bash
sudo bash postgresql/linux/uninstall.sh --yes
```

Para remover da execução, mantendo os dados/segredos para reinstalar:

```bash
sudo bash postgresql/linux/uninstall.sh --yes --keep-data
```

A remoção apaga TODOS os bancos deste cluster SDLC, inclusive bancos de outras aplicações. Pacotes APT, cache compartilhado, regras SSH e histórico de journal permanecem. Consulte [escopo da remoção](../docs/linux.md#desinstalar).

## Referências

- [Documentação oficial](https://www.postgresql.org/docs/18/)
- [Guia Linux](../docs/linux.md)
- [CDN e artefatos](../docs/cdn.md)
- [Voltar ao sumário](../README.md)
