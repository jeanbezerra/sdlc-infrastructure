# Redis

## Responsabilidade no SDLC

Fornece cache, sessões e estruturas de dados em memória. AOF/RDB permitem recuperação local, sem substituir um banco transacional ou backups.

## Requisitos e distribuição

- **Ubuntu Server 26.04 LTS amd64**, systemd e sudo.
- Pacote APT oficial Ubuntu: 8 / pacote Ubuntu; versão exata em `versions.tsv` no bundle.
- Dependências: `ubuntu26-base.tar.gz` e `ubuntu26-redis.tar.gz` do CDN.
- Capacidade inicial sugerida: 2 vCPU, 2 GB RAM e 20 GB de disco inicial; dimensione a memória para os dados. Dimensione conforme a carga real.


## Portas

| Porta | Uso | Acesso |
| --- | --- | --- |
| 6380/TCP | Nginx TCP/TLS | CIDRs específicos, sem /0 |
| 16379/TCP | Backend nativo | Somente 127.0.0.1 |
| SSH_PORT/TCP | Administração | SSH_CIDRS |

Libere somente as portas externas no firewall de rede/security group. Veja a [matriz completa](../docs/ports.md).

## Instalação passo a passo

### 1. Preparar a VM

Prepare os arquivos seguindo o [guia do CDN](../docs/cdn.md). Copie todo o projeto para a VM, o manifesto confiável para `/home/ubuntu/sdlc-manifest.json` e configure `redis.suaempresa.com` para o IP dela.

```bash
sudo apt-get update
sudo apt-get install -y python3 curl ca-certificates
cd /home/ubuntu/sdlc-infrastructure
```

### 2. Editar os parâmetros

```bash
cp redis/linux/.env.example redis/linux/.env
nano redis/linux/.env
chmod 600 redis/linux/.env
```

Ajuste DOMAIN, ACCESS_CIDRS, SSH_PORT, SSH_CIDRS, ARTIFACT_BASE_URL e MANIFEST_PATH. Para produção, informe TLS_CERT_PATH/TLS_KEY_PATH de uma CA confiável. Vazio gera certificado autoassinado para laboratório. Use valores literais, sem aspas/expansões. Confirme que SSH_CIDRS inclui seu IP antes de habilitar o firewall.

### 3. Instalar e verificar

```bash
sudo bash redis/linux/install.sh --config "$(pwd)/redis/linux/.env"
sudo systemctl status sdlc-redis --no-pager
sudo journalctl -u sdlc-redis -n 100 --no-pager
sudo nginx -t
sudo ufw status verbose
sudo ss -lntp
```

Confirme o backend em loopback/16379 e o proxy na porta 6380. O serviço volta no boot. Reexecutar com o mesmo arquivo retoma e reinicia o serviço, preservando dados e segredos. Veja [operação Linux](../docs/linux.md) para certificados/manutenção.

### 4. Acessar e integrar

Usuários: admin e sdlc. credentials.txt contém a senha admin e a linha sdlc_app com a senha do usuário Redis sdlc. Default fica desativado; sdlc tem comandos de aplicação, com @dangerous bloqueada. AOF usa fsync a cada segundo e RDB snapshots periódicos. Ajuste maxmemory e eviction em config/redis.conf conforme o uso.

```bash
sudo cat /opt/sdlc/redis/credentials.txt
```

Teste no cliente autorizado, sem colocar senha nos argumentos:

```bash
read -rsp 'Senha Redis: ' REDISCLI_AUTH; echo
export REDISCLI_AUTH
redis-cli --tls --cacert /caminho/redis-ca.crt --user sdlc \
  -h redis.suaempresa.com -p 6380 PING
unset REDISCLI_AUTH
```

Resultado esperado: `PONG`. Confie no certificado público do proxy; não distribua sua chave privada.

## Arquivos e operação

| Recurso | Caminho |
| --- | --- |
| Configuração, binários e segredos | `/opt/sdlc/redis/` |
| Dados locais | `/var/lib/sdlc/redis/` |
| Conta sem login | `sdlc-redis` |
| Unit systemd | `/etc/systemd/system/sdlc-redis.service` |
| Nginx | `/etc/nginx/sdlc-streams/sdlc-redis.conf` |
| Perfil UFW | `/etc/ufw/applications.d/sdlc-redis` |

```bash
sudo systemctl restart sdlc-redis
sudo systemctl stop sdlc-redis
sudo systemctl start sdlc-redis
```

## Backup e restauração

Pare o serviço e copie os dados, incluindo diretório AOF multipart, seu manifest, RDB e configuração/ACL. Não salve somente um arquivo AOF isolado. Teste recuperação com a mesma versão.

## Diagnóstico

Clientes usam TLS/6380, com CA e usuário sdlc. NOAUTH indica credencial/usuário; erro TLS indica CA/DNS. Não libere 6379/16379 publicamente.

Uma falha conserva o estado para diagnóstico. Corrija a causa e retome com o mesmo .env. Não há HA nem homologação completa em VM limpa; consulte [validação](../docs/validation.md).

## Desinstalação completa

Faça backup. **Apaga dados locais e todos os recursos próprios do serviço:**

```bash
sudo bash redis/linux/uninstall.sh --yes
```

Para remover da execução, mantendo os dados/segredos para reinstalar:

```bash
sudo bash redis/linux/uninstall.sh --yes --keep-data
```

Pacotes APT, cache compartilhado, regras SSH e histórico de journal permanecem. Consulte [escopo da remoção](../docs/linux.md#desinstalar).

## Referências

- [Documentação oficial](https://redis.io/docs/latest/operate/oss_and_stack/install/install-redis/install-redis-on-linux/)
- [Guia Linux](../docs/linux.md)
- [CDN e artefatos](../docs/cdn.md)
- [Voltar ao sumário](../README.md)
