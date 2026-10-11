# Instalação nativa no Ubuntu Server 26.04 LTS

## Preparar a VM

1. Crie uma VM Ubuntu Server 26.04 LTS amd64 com IP estável, systemd e acesso sudo. Ajuste CPU/RAM/disco conforme o README do serviço.
2. Copie **todo o projeto**, preservando `scripts/linux/` e a pasta do serviço. Os wrappers usam a biblioteca comum; um `install.sh` isolado não é suficiente.
3. Instale o bootstrap pelo repositório oficial Ubuntu:

```bash
sudo apt-get update
sudo apt-get install -y python3 curl ca-certificates
```

4. Configure um nome DNS que resolva para a VM. Em um laboratório sem DNS, adicione no computador cliente uma entrada `/etc/hosts` correspondente.
5. Copie para a VM o manifesto confiável do CDN e, quando aplicável, certificado e chave TLS. Leia [docs/cdn.md](cdn.md).
6. Libere no security group/firewall de rede as mesmas portas e redes da [matriz de portas](ports.md). O UFW só controla o firewall local da VM.

## Configuração

Copie `<serviço>/linux/.env.example` para `<serviço>/linux/.env` e edite:

| Variável | Significado |
| --- | --- |
| `DOMAIN` | Nome DNS em minúsculas, exclusivo do serviço |
| `ACCESS_CIDRS` | Redes permitidas no proxy, separadas por vírgula, sem espaços |
| `SSH_PORT` | Porta real do sshd; padrão 22 |
| `SSH_CIDRS` | Redes dos administradores SSH; confirme que incluem o seu IP antes da ativação do UFW |
| `ARTIFACT_BASE_URL` | URL do prefixo do CDN local ou HTTPS remoto |
| `ALLOW_HTTP_ARTIFACTS` | `true` autoriza HTTP somente para endereços privados/loopback, com SHA256 confiável; padrão `false` |
| `MANIFEST_PATH` | Caminho absoluto para o manifesto confiável |
| `TLS_CERT_PATH` / `TLS_KEY_PATH` | Caminhos absolutos para fullchain PEM e chave não criptografada; vazios geram certificado autoassinado |
| `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER` | Banco PostgreSQL dedicado de Keycloak/SonarQube |
| `DB_PASSWORD_FILE`, `DB_CA_PATH` | Arquivo com senha do banco e CA/certificado confiável para `sslmode=verify-full` |

Os arquivos são declarativos `CHAVE=valor`: não use `export`, aspas, substituição `$()`, variáveis de shell, espaços ou comentários na mesma linha. A configuração nunca é executada com `source`/`eval`. Use os caminhos absolutos indicados nos exemplos.

Para HTTP, `0.0.0.0/0,::/0` permite qualquer cliente e só deve ser usado deliberadamente. PostgreSQL e Redis rejeitam `/0` e exigem redes específicas. CIDRs precisam estar normalizados; use `10.20.0.0/16`, não `10.20.1.2/16`.

## Instalar

Para [Technitium DNS](../technitium/README.md), consultas DNS chegam diretamente ao IP privado em 53/TCP e UDP, com redes próprias em `DNS_CIDRS`. O painel usa o mesmo proxy HTTPS das demais aplicações. Esse instalador preserva o resolvedor e os arquivos de rede do Ubuntu; consulte seu README para configuração inicial e distribuição do DNS aos clientes.

```bash
sudo bash SERVICO/linux/install.sh --config "$(pwd)/SERVICO/linux/.env"
```

O instalador valida o ambiente, reserva os caminhos e portas, baixa e verifica artefatos, instala dependências oficiais Ubuntu, cria uma conta sem login, grava a unit systemd, inicia e aguarda o backend, valida o Nginx com `nginx -t`, aplica o proxy e configura o UFW. Uma trava global impede dois instaladores de alterar Nginx/UFW simultaneamente.

Os backends escutam em loopback. Nginx aplica HTTPS e os CIDRs também no nível da aplicação/proxy, mesmo que o UFW já tenha regras mais amplas. Um firewall já ativo mantém suas políticas; o script não usa `ufw reset` nem remove regras externas. Regras por perfis `SDLC-<serviço>` permitem que instalações compartilhem 80/443 sem apagar a liberação de outro serviço.

A instalação de pacotes evita o auto-start quando não existe `policy-rc.d` externo e impede a criação automática de um PostgreSQL `main`. O cluster deste projeto é independente, com diretório próprio. Políticas de pacote preexistentes são preservadas. Quando o pacote Redis é instalado pela primeira vez, sua unit padrão `redis-server.service` é desabilitada, inclusive para o boot; o projeto executa somente `sdlc-redis.service`. Um Redis já instalado antes do bundle é preservado. Verifique se outras aplicações já escutam nas portas da matriz antes de instalar.

## Acessar e diagnosticar

```bash
sudo systemctl status sdlc-SERVICO --no-pager
sudo journalctl -u sdlc-SERVICO -n 100 --no-pager
sudo nginx -t
sudo ufw status verbose
sudo ss -lntp
sudo cat /opt/sdlc/SERVICO/credentials.txt
```

Os logs de proxy ficam em `/var/log/nginx/sdlc-<serviço>.*.log`. Os demais logs ficam no journal ou no diretório do serviço. Arquivos com senha são legíveis somente por root ou pela conta que precisa consumi-los. Não copie segredos para o CDN/Git nem publique logs que contenham tokens.

O certificado autoassinado permite iniciar um laboratório, mas precisa ser instalado como confiável nos clientes para validar TLS. Para produção, forneça um certificado da CA da organização ou pública. Certificados são copiados, não renovados automaticamente; após renovação, reexecute com o mesmo `.env` para atualizar a cópia e reiniciar os serviços. A chave privada nunca deve ser enviada aos clientes.

## Retomar, reiniciar e atualizar

Uma falha preserva o estado para diagnóstico. Corrija a causa e reexecute com o **mesmo arquivo**. Segredos e dados não são regenerados. A reexecução reinicia o serviço, portanto pode causar indisponibilidade breve. Uma extração só vira `app/` depois de concluída.

```bash
sudo systemctl restart sdlc-SERVICO
sudo systemctl stop sdlc-SERVICO
sudo systemctl start sdlc-SERVICO
```

O instalador rejeita mudanças no arquivo e na versão instalada. Para trocar domínio, rede, URL do CDN ou versão, faça backup e manutenção explícita: revise as configurações salvas, units, proxy e regras, seguindo o procedimento do fornecedor. Não apague o marcador `version` para forçar upgrade. `--keep-data` permite reinstalar a **mesma configuração e versão**, não migra dados entre versões.

Se já houver um bloco Nginx `stream`, o script recusa criar um segundo. O administrador deve incluir `include /etc/nginx/sdlc-streams/*.conf;` dentro do bloco existente e criar `/etc/nginx/sdlc-stream.conf` como arquivo vazio para sinalizar a integração manual. Não crie o include no contexto `http`. Nesse cenário, cuide manualmente da configuração stream compartilhada na remoção.

## Desinstalar

**Remoção completa dos recursos próprios e de todos os dados do serviço:**

```bash
sudo bash SERVICO/linux/uninstall.sh --yes
```

O script para e desabilita a unit, remove binários próprios, conta de serviço, configurações, certificado, diretório de dados, logs Nginx e regras/perfil UFW específicos. Valida marcadores e caminhos antes de remover dados. Sem `--yes`, mostra o escopo e termina sem alteração.

**Remover da execução, preservando dados e segredos para reinstalar:**

```bash
sudo bash SERVICO/linux/uninstall.sh --yes --keep-data
```

A conta de serviço, `/opt/sdlc/<serviço>/` e `/var/lib/sdlc/<serviço>/` são preservados. Proxy, unit e regras do serviço são removidos. Reinstale usando o arquivo original salvo em `/opt/sdlc/<serviço>/install.env`.

Pacotes APT, Nginx/UFW/JDK compartilhados, `/opt/sdlc/cache`, acesso SSH e logs do journal permanecem. As alterações de `vm.max_map_count` de SonarQube/OpenSearch perdem o arquivo persistente na remoção, mas o valor atual do kernel permanece até reinicialização/manutenção. Não faça `apt autoremove`, limpeza global de journal ou exclusão de diretórios de outras aplicações como parte de uma desinstalação individual.

Keycloak e SonarQube usam bancos externos: desinstalar a aplicação não apaga esses bancos. Remova-os separadamente só depois de backup e confirmação do responsável. Desinstalar PostgreSQL com `--yes` remove **todos os bancos do cluster SDLC**, incluindo os usados por outras aplicações.

## Backup e limites

Uma VM por serviço simplifica recursos, upgrades e recuperação. Esta implementação é de uma instância: não provisiona HA, replicação, PITR, backup automático ou monitoramento externo. Inclua backup cifrado, retenção, cópia fora da VM e testes de restauração no seu plano operacional. O README de cada serviço explica o que salvar.

[Voltar ao sumário](../README.md)
