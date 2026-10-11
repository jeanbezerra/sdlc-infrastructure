# SonarQube Community Build

## Responsabilidade no SDLC

Analisa código e apresenta bugs, vulnerabilidades, hotspots e dívida técnica. A pipeline pode aguardar o quality gate antes de entregar um artefato.

## Requisitos e distribuição

- **Ubuntu Server 26.04 LTS amd64**, systemd e sudo.
- Arquivo oficial `sonarqube-26.9.0.129388.zip` da versão 26.9.0.129388.
- Dependências: `ubuntu26-base.tar.gz` e `ubuntu26-java21.tar.gz` do CDN.
- Capacidade inicial sugerida: 4 vCPU, 8 GB RAM e 60 GB de disco inicial, com PostgreSQL separado. Dimensione conforme a carga real.
- PostgreSQL dedicado acessível por TLS, com banco, usuário e CA confiável próprios.

## Portas

| Porta | Uso | Acesso |
| --- | --- | --- |
| 80/TCP | Redirecionamento HTTPS | ACCESS_CIDRS |
| 443/TCP | Nginx HTTPS UI/API | ACCESS_CIDRS |
| 19000/TCP | Backend nativo | Somente 127.0.0.1 |
| 19001/TCP | Elasticsearch interno | Somente 127.0.0.1; não liberada |
| SSH_PORT/TCP | Administração | SSH_CIDRS |

Libere somente as portas externas no firewall de rede/security group. Veja a [matriz completa](../docs/ports.md).

## Instalação passo a passo

### 1. Preparar a VM

Prepare os arquivos seguindo o [guia do CDN](../docs/cdn.md). Copie todo o projeto para a VM, o manifesto confiável para `/home/ubuntu/sdlc-manifest.json` e configure `sonarqube.suaempresa.com` para o IP dela.

```bash
sudo apt-get update
sudo apt-get install -y python3 curl ca-certificates
cd /home/ubuntu/sdlc-infrastructure
```

### 2. Criar banco e role exclusivos

Na VM PostgreSQL deste projeto, a partir da raiz do repositório, gere uma senha e execute o SQL **uma vez**, depois de conferir o cluster:

```bash
umask 077
openssl rand -hex 24 > /home/ubuntu/sonarqube-db-password
export SDLC_SERVICE_DB_PASSWORD="$(cat /home/ubuntu/sonarqube-db-password)"
sudo --preserve-env=SDLC_SERVICE_DB_PASSWORD -u sdlc-postgresql \
  /usr/lib/postgresql/18/bin/psql -X -v ON_ERROR_STOP=1 \
  -h /var/lib/sdlc/postgresql/run -p 15432 -U sdlc_admin -d postgres \
  < sonarqube/linux/database.sql
unset SDLC_SERVICE_DB_PASSWORD
```

[database.sql](linux/database.sql) cria uma role sem privilégio de cluster e um banco próprio. O produto precisa ser owner desse banco/schema para as migrations oficiais; não recebe superuser, CREATEDB ou CREATEROLE. Se houver outro cluster, adapte a conexão administrativa. O script não reutiliza nem apaga banco existente.

Transfira a senha com segurança para a VM da aplicação e a CA do PostgreSQL para `/home/ubuntu/postgresql-ca.crt`. Se o banco usa certificado autoassinado, copie **somente** `/opt/sdlc/postgresql/tls/server.crt` como certificado confiável. Nunca distribua a chave privada. Inclua a rede da aplicação nos CIDRs do proxy PostgreSQL.

### 3. Editar os parâmetros

```bash
cp sonarqube/linux/.env.example sonarqube/linux/.env
nano sonarqube/linux/.env
chmod 600 sonarqube/linux/.env
```

Ajuste DOMAIN, ACCESS_CIDRS, SSH_PORT, SSH_CIDRS, ARTIFACT_BASE_URL, ALLOW_HTTP_ARTIFACTS e MANIFEST_PATH. Configure também DB_HOST, DB_PORT=5432, DB_NAME, DB_USER, DB_PASSWORD_FILE e DB_CA_PATH. Para produção, informe TLS_CERT_PATH/TLS_KEY_PATH de uma CA confiável. Vazio gera certificado autoassinado para laboratório. Use valores literais, sem aspas/expansões. Confirme que SSH_CIDRS inclui seu IP antes de habilitar o firewall.

### 4. Instalar e verificar

```bash
sudo bash sonarqube/linux/install.sh --config "$(pwd)/sonarqube/linux/.env"
sudo systemctl status sdlc-sonarqube --no-pager
sudo journalctl -u sdlc-sonarqube -n 100 --no-pager
sudo nginx -t
sudo ufw status verbose
sudo ss -lntp
```

Confirme o backend em loopback/19000 e o proxy na porta 443. O serviço volta no boot. Reexecutar com o mesmo arquivo retoma e reinicia o serviço, preservando dados e segredos. Veja [operação Linux](../docs/linux.md) para certificados/manutenção.

### 5. Acessar e integrar

Use admin e a senha de credentials.txt. Na primeira inicialização o script troca admin/admin por essa senha usando a API local, antes de publicar o proxy. Crie projetos e tokens de análise com privilégio mínimo; configure SonarScanner/Maven/Gradle e quality gates no Jenkins. A instalação usa PostgreSQL, não H2.

```bash
sudo cat /opt/sdlc/sonarqube/credentials.txt
```

## Arquivos e operação

| Recurso | Caminho |
| --- | --- |
| Configuração, binários e segredos | `/opt/sdlc/sonarqube/` |
| Dados locais | `/var/lib/sdlc/sonarqube/` |
| Conta sem login | `sdlc-sonarqube` |
| Unit systemd | `/etc/systemd/system/sdlc-sonarqube.service` |
| Nginx | `/etc/nginx/conf.d/sdlc-sonarqube.conf` |
| Perfil UFW | `/etc/ufw/applications.d/sdlc-sonarqube` |

```bash
sudo systemctl restart sdlc-sonarqube
sudo systemctl stop sdlc-sonarqube
sudo systemctl start sdlc-sonarqube
```

## Backup e restauração

Faça backup do banco sonarqube e de configuração/plugins. Índices locais são regeneráveis a partir do banco; teste recuperação com a mesma versão. Upgrade exige migrations do fornecedor.

## Diagnóstico

Confira /var/lib/sdlc/sonarqube/logs/, RAM, disco e journal. O instalador ajusta vm.max_map_count e limites de arquivos/processos. Falhas JDBC exigem conferir CA, DNS, role e porta 5432.

Uma falha conserva o estado para diagnóstico. Corrija a causa e retome com o mesmo .env. O ciclo do instalador foi validado na VM Ubuntu 26.04.1 LTS; consulte o [relatório da homologação](../docs/vm-validation.md). Backup/restore, HA e carga precisam de validação operacional.

## Desinstalação completa

Faça backup. **Apaga dados locais e todos os recursos próprios do serviço:**

```bash
sudo bash sonarqube/linux/uninstall.sh --yes
```

Para remover da execução, mantendo os dados/segredos para reinstalar:

```bash
sudo bash sonarqube/linux/uninstall.sh --yes --keep-data
```

O banco externo não é apagado pela remoção da aplicação. Sua remoção é uma operação separada do administrador. Pacotes APT, cache compartilhado, regras SSH e histórico de journal permanecem. Consulte [escopo da remoção](../docs/linux.md#desinstalar).

## Referências

- [Documentação oficial](https://docs.sonarsource.com/sonarqube-community-build/server-installation/from-zip-file/basic-installation)
- [Guia Linux](../docs/linux.md)
- [CDN e artefatos](../docs/cdn.md)
- [Voltar ao sumário](../README.md)
