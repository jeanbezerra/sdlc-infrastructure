# OpenSearch

## Responsabilidade no SDLC

Indexa e pesquisa documentos, logs e eventos de operação/CI. Esta instalação fornece servidor/API; Dashboards e shippers de ingestão são separados.

## Requisitos e distribuição

- **Ubuntu Server 26.04 LTS amd64**, systemd e sudo.
- Arquivo oficial `opensearch-3.9.0-linux-x64.tar.gz` da versão 3.9.0.
- Dependências: `ubuntu26-base.tar.gz` do CDN.
- Capacidade inicial sugerida: 4 vCPU, 8 GB RAM e 100 GB SSD inicial; heap inicial de 1 GB. Dimensione conforme a carga real.


## Portas

| Porta | Uso | Acesso |
| --- | --- | --- |
| 80/TCP | Redirecionamento HTTPS | ACCESS_CIDRS |
| 443/TCP | Nginx HTTPS UI/API | ACCESS_CIDRS |
| 19200/TCP | Backend nativo | Somente 127.0.0.1 |
| 19300/TCP | Transporte do cluster | Somente loopback |
| SSH_PORT/TCP | Administração | SSH_CIDRS |

Libere somente as portas externas no firewall de rede/security group. Veja a [matriz completa](../docs/ports.md).

## Instalação passo a passo

### 1. Preparar a VM

Prepare os arquivos seguindo o [guia do CDN](../docs/cdn.md). Copie todo o projeto para a VM, o manifesto confiável para `/home/ubuntu/sdlc-manifest.json` e configure `opensearch.suaempresa.com` para o IP dela.

```bash
sudo apt-get update
sudo apt-get install -y python3 curl ca-certificates
cd /home/ubuntu/sdlc-infrastructure
```

### 2. Editar os parâmetros

```bash
cp opensearch/linux/.env.example opensearch/linux/.env
nano opensearch/linux/.env
chmod 600 opensearch/linux/.env
```

Ajuste DOMAIN, ACCESS_CIDRS, SSH_PORT, SSH_CIDRS, ARTIFACT_BASE_URL, ALLOW_HTTP_ARTIFACTS e MANIFEST_PATH. Para produção, informe TLS_CERT_PATH/TLS_KEY_PATH de uma CA confiável. Vazio gera certificado autoassinado para laboratório. Use valores literais, sem aspas/expansões. Confirme que SSH_CIDRS inclui seu IP antes de habilitar o firewall.

### 3. Instalar e verificar

```bash
sudo bash opensearch/linux/install.sh --config "$(pwd)/opensearch/linux/.env"
sudo systemctl status sdlc-opensearch --no-pager
sudo journalctl -u sdlc-opensearch -n 100 --no-pager
sudo nginx -t
sudo ufw status verbose
sudo ss -lntp
```

Confirme o backend em loopback/19200 e o proxy na porta 443. O serviço volta no boot. Reexecutar com o mesmo arquivo retoma e reinicia o serviço, preservando dados e segredos. Veja [operação Linux](../docs/linux.md) para certificados/manutenção.

### 4. Acessar e integrar

A API usa autenticação nativa: admin e a senha de credentials.txt. Security plugin ativo, certificados internos próprios e nenhum usuário demo. Crie roles de ingestão/leitura, templates e retenção; envie eventos por um shipper compatível com TLS/autenticação. É um único nó, sem HA.

```bash
sudo cat /opt/sdlc/opensearch/credentials.txt
```

```bash
curl --cacert /caminho/proxy-ca.crt --user admin https://opensearch.suaempresa.com/
```

O curl pede a senha. Use a CA/certificado público do proxy.

## Arquivos e operação

| Recurso | Caminho |
| --- | --- |
| Configuração, binários e segredos | `/opt/sdlc/opensearch/` |
| Dados locais | `/var/lib/sdlc/opensearch/` |
| Conta sem login | `sdlc-opensearch` |
| Unit systemd | `/etc/systemd/system/sdlc-opensearch.service` |
| Nginx | `/etc/nginx/conf.d/sdlc-opensearch.conf` |
| Perfil UFW | `/etc/ufw/applications.d/sdlc-opensearch` |

```bash
sudo systemctl restart sdlc-opensearch
sudo systemctl stop sdlc-opensearch
sudo systemctl start sdlc-opensearch
```

## Backup e restauração

Configure repositório externo de snapshots e use a API oficial, com testes de restore. Salve configuração e material de recuperação protegido. Cópia do diretório de dados ativo não substitui snapshot consistente.

## Diagnóstico

O instalador inicializa o keystore antes de iniciar o servidor e grava logs da JVM e da aplicação em `/var/lib/sdlc/opensearch/logs/`. Confira vm.max_map_count, RAM, permissões e journal. API/19200 e transporte/19300 são locais. O proxy usa HTTPS e valida o certificado do backend pela CA interna em `/opt/sdlc/opensearch/config/root-ca.crt`, com nome TLS `localhost`. Os clientes validam separadamente o certificado HTTPS do proxy.

Uma falha conserva o estado para diagnóstico. Corrija a causa e retome com o mesmo .env. O ciclo do instalador foi validado na VM Ubuntu 26.04.1 LTS; consulte o [relatório da homologação](../docs/vm-validation.md). Backup/restore, HA e carga precisam de validação operacional.

## Desinstalação completa

Faça backup. **Apaga dados locais e todos os recursos próprios do serviço:**

```bash
sudo bash opensearch/linux/uninstall.sh --yes
```

Para remover da execução, mantendo os dados/segredos para reinstalar:

```bash
sudo bash opensearch/linux/uninstall.sh --yes --keep-data
```

Pacotes APT, cache compartilhado, regras SSH e histórico de journal permanecem. Consulte [escopo da remoção](../docs/linux.md#desinstalar).

## Referências

- [Documentação oficial](https://docs.opensearch.org/latest/install-and-configure/install-opensearch/tar/)
- [Guia Linux](../docs/linux.md)
- [CDN e artefatos](../docs/cdn.md)
- [Voltar ao sumário](../README.md)
