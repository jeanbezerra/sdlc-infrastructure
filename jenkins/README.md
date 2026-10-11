# Jenkins

## Responsabilidade no SDLC

Executa pipelines de CI: compila, testa, consulta quality gates e publica artefatos. O controller tem zero executores; os builds rodam em agentes.

## Requisitos e distribuição

- **Ubuntu Server 26.04 LTS amd64**, systemd e sudo.
- Arquivo oficial `jenkins-2.580.1.war` da versão 2.580.1.
- Dependências: `ubuntu26-base.tar.gz` e `ubuntu26-java21.tar.gz` do CDN.
- Capacidade inicial sugerida: 2 vCPU, 4 GB RAM, 40 GB de disco, além da capacidade dos agentes. Dimensione conforme a carga real.


## Portas

| Porta | Uso | Acesso |
| --- | --- | --- |
| 80/TCP | Redirecionamento HTTPS | ACCESS_CIDRS |
| 443/TCP | Nginx HTTPS UI/API | ACCESS_CIDRS |
| 18081/TCP | Backend nativo | Somente 127.0.0.1 |
| 50000/TCP | Agentes TCP | Desativada; use WebSocket/443 |
| SSH_PORT/TCP | Administração | SSH_CIDRS |

Libere somente as portas externas no firewall de rede/security group. Veja a [matriz completa](../docs/ports.md).

## Instalação passo a passo

### 1. Preparar a VM

Prepare os arquivos seguindo o [guia do CDN](../docs/cdn.md). Copie todo o projeto para a VM, o manifesto confiável para `/home/ubuntu/sdlc-manifest.json` e configure `jenkins.suaempresa.com` para o IP dela.

```bash
sudo apt-get update
sudo apt-get install -y python3 curl ca-certificates
cd /home/ubuntu/sdlc-infrastructure
```

### 2. Editar os parâmetros

```bash
cp jenkins/linux/.env.example jenkins/linux/.env
nano jenkins/linux/.env
chmod 600 jenkins/linux/.env
```

Ajuste DOMAIN, ACCESS_CIDRS, SSH_PORT, SSH_CIDRS, ARTIFACT_BASE_URL e MANIFEST_PATH. Para produção, informe TLS_CERT_PATH/TLS_KEY_PATH de uma CA confiável. Vazio gera certificado autoassinado para laboratório. Use valores literais, sem aspas/expansões. Confirme que SSH_CIDRS inclui seu IP antes de habilitar o firewall.

### 3. Instalar e verificar

```bash
sudo bash jenkins/linux/install.sh --config "$(pwd)/jenkins/linux/.env"
sudo systemctl status sdlc-jenkins --no-pager
sudo journalctl -u sdlc-jenkins -n 100 --no-pager
sudo nginx -t
sudo ufw status verbose
sudo ss -lntp
```

Confirme o backend em loopback/18081 e o proxy na porta 443. O serviço volta no boot. Reexecutar com o mesmo arquivo retoma e reinicia o serviço, preservando dados e segredos. Veja [operação Linux](../docs/linux.md) para certificados/manutenção.

### 4. Acessar e integrar

Use admin e a senha de credentials.txt. O bootstrap bloqueia acesso anônimo e substitui o wizard. Instale os plugins necessários em Manage Jenkins → Plugins. Cadastre credenciais e um agente WebSocket/443; não execute builds no controller.

```bash
sudo cat /opt/sdlc/jenkins/credentials.txt
```

## Arquivos e operação

| Recurso | Caminho |
| --- | --- |
| Configuração, binários e segredos | `/opt/sdlc/jenkins/` |
| Dados locais | `/var/lib/sdlc/jenkins/` |
| Conta sem login | `sdlc-jenkins` |
| Unit systemd | `/etc/systemd/system/sdlc-jenkins.service` |
| Nginx | `/etc/nginx/conf.d/sdlc-jenkins.conf` |
| Perfil UFW | `/etc/ufw/applications.d/sdlc-jenkins` |

```bash
sudo systemctl restart sdlc-jenkins
sudo systemctl stop sdlc-jenkins
sudo systemctl start sdlc-jenkins
```

## Backup e restauração

Pare o serviço e salve todo o diretório de dados, incluindo jobs, plugins, credentials e secrets. Proteja as chaves que permitem decifrar credenciais. Teste restauração com a mesma versão.

## Diagnóstico

Se um agente falhar, confira URL Jenkins, CA confiável, CIDRs e WebSocket. Builds na fila precisam de agentes: o controller tem zero executores.

Uma falha conserva o estado para diagnóstico. Corrija a causa e retome com o mesmo .env. Não há HA nem homologação completa em VM limpa; consulte [validação](../docs/validation.md).

## Desinstalação completa

Faça backup. **Apaga dados locais e todos os recursos próprios do serviço:**

```bash
sudo bash jenkins/linux/uninstall.sh --yes
```

Para remover da execução, mantendo os dados/segredos para reinstalar:

```bash
sudo bash jenkins/linux/uninstall.sh --yes --keep-data
```

Pacotes APT, cache compartilhado, regras SSH e histórico de journal permanecem. Consulte [escopo da remoção](../docs/linux.md#desinstalar).

## Referências

- [Documentação oficial](https://www.jenkins.io/doc/book/installing/war-file/)
- [Guia Linux](../docs/linux.md)
- [CDN e artefatos](../docs/cdn.md)
- [Voltar ao sumário](../README.md)
