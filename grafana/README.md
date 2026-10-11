# Grafana OSS

## Responsabilidade no SDLC

Apresenta dashboards dos dados operacionais. Consulta Prometheus e outras datasources; não substitui a coleta e o armazenamento.

## Requisitos e distribuição

- **Ubuntu Server 26.04 LTS amd64**, systemd e sudo.
- Arquivo oficial `grafana-13.2.3.linux-amd64.tar.gz` da versão 13.2.3.
- Dependências: `ubuntu26-base.tar.gz` do CDN.
- Capacidade inicial sugerida: 2 vCPU, 2 GB RAM e 20 GB de disco para um ambiente inicial. Dimensione conforme a carga real.


## Portas

| Porta | Uso | Acesso |
| --- | --- | --- |
| 80/TCP | Redirecionamento HTTPS | ACCESS_CIDRS |
| 443/TCP | Nginx HTTPS UI/API | ACCESS_CIDRS |
| 13000/TCP | Backend nativo | Somente 127.0.0.1 |
| SSH_PORT/TCP | Administração | SSH_CIDRS |

Libere somente as portas externas no firewall de rede/security group. Veja a [matriz completa](../docs/ports.md).

## Instalação passo a passo

### 1. Preparar a VM

Prepare os arquivos seguindo o [guia do CDN](../docs/cdn.md). Copie todo o projeto para a VM, o manifesto confiável para `/home/ubuntu/sdlc-manifest.json` e configure `grafana.suaempresa.com` para o IP dela.

```bash
sudo apt-get update
sudo apt-get install -y python3 curl ca-certificates
cd /home/ubuntu/sdlc-infrastructure
```

### 2. Editar os parâmetros

```bash
cp grafana/linux/.env.example grafana/linux/.env
nano grafana/linux/.env
chmod 600 grafana/linux/.env
```

Ajuste DOMAIN, ACCESS_CIDRS, SSH_PORT, SSH_CIDRS, ARTIFACT_BASE_URL e MANIFEST_PATH. Para produção, informe TLS_CERT_PATH/TLS_KEY_PATH de uma CA confiável. Vazio gera certificado autoassinado para laboratório. Use valores literais, sem aspas/expansões. Confirme que SSH_CIDRS inclui seu IP antes de habilitar o firewall.

### 3. Instalar e verificar

```bash
sudo bash grafana/linux/install.sh --config "$(pwd)/grafana/linux/.env"
sudo systemctl status sdlc-grafana --no-pager
sudo journalctl -u sdlc-grafana -n 100 --no-pager
sudo nginx -t
sudo ufw status verbose
sudo ss -lntp
```

Confirme o backend em loopback/13000 e o proxy na porta 443. O serviço volta no boot. Reexecutar com o mesmo arquivo retoma e reinicia o serviço, preservando dados e segredos. Veja [operação Linux](../docs/linux.md) para certificados/manutenção.

### 4. Acessar e integrar

Use admin e a senha de credentials.txt. Acesso anônimo e cadastro público ficam desativados. Em Connections → Data sources, adicione Prometheus usando HTTPS, Basic Auth e a CA confiável. Na mesma VM, pode usar http://127.0.0.1:19090. Depois crie dashboards ou configure OIDC com Keycloak.

```bash
sudo cat /opt/sdlc/grafana/credentials.txt
```

## Arquivos e operação

| Recurso | Caminho |
| --- | --- |
| Configuração, binários e segredos | `/opt/sdlc/grafana/` |
| Dados locais | `/var/lib/sdlc/grafana/` |
| Conta sem login | `sdlc-grafana` |
| Unit systemd | `/etc/systemd/system/sdlc-grafana.service` |
| Nginx | `/etc/nginx/conf.d/sdlc-grafana.conf` |
| Perfil UFW | `/etc/ufw/applications.d/sdlc-grafana` |

```bash
sudo systemctl restart sdlc-grafana
sudo systemctl stop sdlc-grafana
sudo systemctl start sdlc-grafana
```

## Backup e restauração

Pare o serviço e copie o diretório de dados, incluindo grafana.db e plugins, além da configuração/provisioning. Teste restaurar o banco SQLite e dashboards.

## Diagnóstico

Se uma datasource falhar, teste a partir da VM Grafana e confira os CIDRs do Prometheus. Instale a CA confiável sem desativar a validação TLS.

Uma falha conserva o estado para diagnóstico. Corrija a causa e retome com o mesmo .env. Não há HA nem homologação completa em VM limpa; consulte [validação](../docs/validation.md).

## Desinstalação completa

Faça backup. **Apaga dados locais e todos os recursos próprios do serviço:**

```bash
sudo bash grafana/linux/uninstall.sh --yes
```

Para remover da execução, mantendo os dados/segredos para reinstalar:

```bash
sudo bash grafana/linux/uninstall.sh --yes --keep-data
```

Pacotes APT, cache compartilhado, regras SSH e histórico de journal permanecem. Consulte [escopo da remoção](../docs/linux.md#desinstalar).

## Referências

- [Documentação oficial](https://grafana.com/docs/grafana/latest/setup-grafana/installation/debian/)
- [Guia Linux](../docs/linux.md)
- [CDN e artefatos](../docs/cdn.md)
- [Voltar ao sumário](../README.md)
