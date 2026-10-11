# Prometheus

## Responsabilidade no SDLC

Coleta métricas de endpoints/exporters e mantém séries temporais consultadas com PromQL e Grafana. Alertmanager não está incluído.

## Requisitos e distribuição

- **Ubuntu Server 26.04 LTS amd64**, systemd e sudo.
- Arquivo oficial `prometheus-3.15.0.linux-amd64.tar.gz` da versão 3.15.0.
- Dependências: `ubuntu26-base.tar.gz` do CDN.
- Capacidade inicial sugerida: 2 vCPU, 4 GB RAM e 40 GB de disco; dimensione retenção e cardinalidade. Dimensione conforme a carga real.


## Portas

| Porta | Uso | Acesso |
| --- | --- | --- |
| 80/TCP | Redirecionamento HTTPS | ACCESS_CIDRS |
| 443/TCP | Nginx HTTPS UI/API | ACCESS_CIDRS |
| 19090/TCP | Backend nativo | Somente 127.0.0.1 |
| SSH_PORT/TCP | Administração | SSH_CIDRS |

Libere somente as portas externas no firewall de rede/security group. Veja a [matriz completa](../docs/ports.md).

## Instalação passo a passo

### 1. Preparar a VM

Prepare os arquivos seguindo o [guia do CDN](../docs/cdn.md). Copie todo o projeto para a VM, o manifesto confiável para `/home/ubuntu/sdlc-manifest.json` e configure `prometheus.suaempresa.com` para o IP dela.

```bash
sudo apt-get update
sudo apt-get install -y python3 curl ca-certificates
cd /home/ubuntu/sdlc-infrastructure
```

### 2. Editar os parâmetros

```bash
cp prometheus/linux/.env.example prometheus/linux/.env
nano prometheus/linux/.env
chmod 600 prometheus/linux/.env
```

Ajuste DOMAIN, ACCESS_CIDRS, SSH_PORT, SSH_CIDRS, ARTIFACT_BASE_URL e MANIFEST_PATH. Para produção, informe TLS_CERT_PATH/TLS_KEY_PATH de uma CA confiável. Vazio gera certificado autoassinado para laboratório. Use valores literais, sem aspas/expansões. Confirme que SSH_CIDRS inclui seu IP antes de habilitar o firewall.

### 3. Instalar e verificar

```bash
sudo bash prometheus/linux/install.sh --config "$(pwd)/prometheus/linux/.env"
sudo systemctl status sdlc-prometheus --no-pager
sudo journalctl -u sdlc-prometheus -n 100 --no-pager
sudo nginx -t
sudo ufw status verbose
sudo ss -lntp
```

Confirme o backend em loopback/19090 e o proxy na porta 443. O serviço volta no boot. Reexecutar com o mesmo arquivo retoma e reinicia o serviço, preservando dados e segredos. Veja [operação Linux](../docs/linux.md) para certificados/manutenção.

### 4. Acessar e integrar

O Nginx exige Basic Auth: admin e a senha de credentials.txt. A coleta inicial monitora o próprio Prometheus. Adicione arquivos JSON em /opt/sdlc/prometheus/config/targets/ com os endpoints de exporters. A retenção inicial é 15 dias. Libere a saída da VM Prometheus para as portas desses exporters.

```bash
sudo cat /opt/sdlc/prometheus/credentials.txt
```

Exemplo de conteúdo para `config/targets/hosts.json`:

```json
[{"targets":["10.20.1.10:9100"],"labels":{"ambiente":"homologacao"}}]
```

O exporter deve ser instalado separadamente e autorizar a VM Prometheus. Preserve leitura desse arquivo pelo grupo sdlc-prometheus. File discovery atualiza targets automaticamente; mudanças em prometheus.yml exigem reinício após validação.

## Arquivos e operação

| Recurso | Caminho |
| --- | --- |
| Configuração, binários e segredos | `/opt/sdlc/prometheus/` |
| Dados locais | `/var/lib/sdlc/prometheus/` |
| Conta sem login | `sdlc-prometheus` |
| Unit systemd | `/etc/systemd/system/sdlc-prometheus.service` |
| Nginx | `/etc/nginx/conf.d/sdlc-prometheus.conf` |
| Perfil UFW | `/etc/ufw/applications.d/sdlc-prometheus` |

```bash
sudo systemctl restart sdlc-prometheus
sudo systemctl stop sdlc-prometheus
sudo systemctl start sdlc-prometheus
```

## Backup e restauração

Pare o serviço e copie a TSDB e as configurações. Não trate cópia de uma TSDB ativa como backup consistente; o endpoint de administração/snapshots não foi habilitado.

## Diagnóstico

Targets DOWN exigem conferir rede, porta, path, autenticação e CA. Valide alterações com /opt/sdlc/prometheus/app/promtool check config /opt/sdlc/prometheus/config/prometheus.yml. Alta cardinalidade aumenta consumo de disco e memória.

Uma falha conserva o estado para diagnóstico. Corrija a causa e retome com o mesmo .env. Não há HA nem homologação completa em VM limpa; consulte [validação](../docs/validation.md).

## Desinstalação completa

Faça backup. **Apaga dados locais e todos os recursos próprios do serviço:**

```bash
sudo bash prometheus/linux/uninstall.sh --yes
```

Para remover da execução, mantendo os dados/segredos para reinstalar:

```bash
sudo bash prometheus/linux/uninstall.sh --yes --keep-data
```

Pacotes APT, cache compartilhado, regras SSH e histórico de journal permanecem. Consulte [escopo da remoção](../docs/linux.md#desinstalar).

## Referências

- [Documentação oficial](https://prometheus.io/docs/prometheus/latest/installation/)
- [Guia Linux](../docs/linux.md)
- [CDN e artefatos](../docs/cdn.md)
- [Voltar ao sumário](../README.md)
