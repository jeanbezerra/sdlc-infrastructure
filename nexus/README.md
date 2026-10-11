# Nexus Repository

## Responsabilidade no SDLC

Hospeda dependências e artefatos, por exemplo Maven/npm. Repositories hosted/proxy/group controlam publicação e downloads de releases/snapshots.

## Requisitos e distribuição

- **Ubuntu Server 26.04 LTS amd64**, systemd e sudo.
- Arquivo oficial `nexus-3.96.4-01-linux-x86_64.tar.gz` da versão 3.96.4-01.
- Dependências: `ubuntu26-base.tar.gz` do CDN.
- Capacidade inicial sugerida: 4 vCPU, 8 GB RAM e 100 GB de disco inicial; blobs crescem com o uso. Dimensione conforme a carga real.


## Portas

| Porta | Uso | Acesso |
| --- | --- | --- |
| 80/TCP | Redirecionamento HTTPS | ACCESS_CIDRS |
| 443/TCP | Nginx HTTPS UI/API | ACCESS_CIDRS |
| 18082/TCP | Backend nativo | Somente 127.0.0.1 |
| SSH_PORT/TCP | Administração | SSH_CIDRS |

Libere somente as portas externas no firewall de rede/security group. Veja a [matriz completa](../docs/ports.md).

## Instalação passo a passo

### 1. Preparar a VM

Prepare os arquivos seguindo o [guia do CDN](../docs/cdn.md). Copie todo o projeto para a VM, o manifesto confiável para `/home/ubuntu/sdlc-manifest.json` e configure `nexus.suaempresa.com` para o IP dela.

```bash
sudo apt-get update
sudo apt-get install -y python3 curl ca-certificates
cd /home/ubuntu/sdlc-infrastructure
```

### 2. Editar os parâmetros

```bash
cp nexus/linux/.env.example nexus/linux/.env
nano nexus/linux/.env
chmod 600 nexus/linux/.env
```

Ajuste DOMAIN, ACCESS_CIDRS, SSH_PORT, SSH_CIDRS, ARTIFACT_BASE_URL, ALLOW_HTTP_ARTIFACTS e MANIFEST_PATH. Para produção, informe TLS_CERT_PATH/TLS_KEY_PATH de uma CA confiável. Vazio gera certificado autoassinado para laboratório. Use valores literais, sem aspas/expansões. Confirme que SSH_CIDRS inclui seu IP antes de habilitar o firewall.

### 3. Instalar e verificar

```bash
sudo bash nexus/linux/install.sh --config "$(pwd)/nexus/linux/.env"
sudo systemctl status sdlc-nexus --no-pager
sudo journalctl -u sdlc-nexus -n 100 --no-pager
sudo nginx -t
sudo ufw status verbose
sudo ss -lntp
```

Confirme o backend em loopback/18082 e o proxy na porta 443. O serviço volta no boot. Reexecutar com o mesmo arquivo retoma e reinicia o serviço, preservando dados e segredos. Veja [operação Linux](../docs/linux.md) para certificados/manutenção.

### 4. Acessar e integrar

O Nexus gera a senha inicial de admin: leia sudo cat /var/lib/sdlc/nexus/admin.password, conclua o wizard e troque a senha. O arquivo pode desaparecer após o wizard. Crie repositories e usuários de publicação. Clientes usam https://SEU_DOMAIN/repository/NOME/. Connectors adicionais de registry não são criados.

```bash
sudo cat /opt/sdlc/nexus/credentials.txt
```

## Arquivos e operação

| Recurso | Caminho |
| --- | --- |
| Configuração, binários e segredos | `/opt/sdlc/nexus/` |
| Dados locais | `/var/lib/sdlc/nexus/` |
| Conta sem login | `sdlc-nexus` |
| Unit systemd | `/etc/systemd/system/sdlc-nexus.service` |
| Nginx | `/etc/nginx/conf.d/sdlc-nexus.conf` |
| Perfil UFW | `/etc/ufw/applications.d/sdlc-nexus` |

```bash
sudo systemctl restart sdlc-nexus
sudo systemctl stop sdlc-nexus
sudo systemctl start sdlc-nexus
```

## Backup e restauração

Pare o serviço e copie todo o diretório de dados, incluindo banco interno, blobs, keystores e configurações. Banco e blobs precisam refletir o mesmo ponto no tempo.

## Diagnóstico

Confira memória, permissões e journal. O launcher oficial usa o runtime do pacote. Opções/caminhos ficam em app/bin/nexus.vmoptions. Se upload receber 413, confira também proxies anteriores ao Nginx.

Uma falha conserva o estado para diagnóstico. Corrija a causa e retome com o mesmo .env. O ciclo do instalador foi validado na VM Ubuntu 26.04.1 LTS; consulte o [relatório da homologação](../docs/vm-validation.md). Backup/restore, HA e carga precisam de validação operacional.

## Desinstalação completa

Faça backup. **Apaga dados locais e todos os recursos próprios do serviço:**

```bash
sudo bash nexus/linux/uninstall.sh --yes
```

Para remover da execução, mantendo os dados/segredos para reinstalar:

```bash
sudo bash nexus/linux/uninstall.sh --yes --keep-data
```

Pacotes APT, cache compartilhado, regras SSH e histórico de journal permanecem. Consulte [escopo da remoção](../docs/linux.md#desinstalar).

## Referências

- [Documentação oficial](https://help.sonatype.com/en/download.html)
- [Guia Linux](../docs/linux.md)
- [CDN e artefatos](../docs/cdn.md)
- [Voltar ao sumário](../README.md)
