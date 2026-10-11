# Technitium DNS Server

O Technitium mantém os nomes da infraestrutura: ferramentas de CI, bancos, dashboards e aplicações acessam serviços por nomes estáveis. Ele também resolve nomes externos e mantém um cache para a rede. Zonas, registros e permissões são gerenciados pelo navegador.

Instalação nativa de **Technitium 15.6.1** em **Ubuntu Server 26.04 LTS amd64**, com ASP.NET Core 10 do APT oficial Ubuntu, systemd, Nginx HTTPS e UFW. O portátil oficial e as dependências chegam pelo CDN local, verificados por SHA256.

## 1. Planejar a VM

Use **2 GiB de RAM**, 1–2 vCPUs, 16 GiB de disco e IP privado fixo. A unit tem `MemoryHigh=1G` e `MemoryMax=1536M`, reservando parte da memória para Ubuntu/Nginx. O limite pode encerrar o processo em caso de excesso: monitore antes de aumentar cache, listas de bloqueio ou carga. Cache inicial: 10 mil entradas; logs/estatísticas: 7 dias; registro de cada consulta e bloqueios: desativados.

| Acesso | Porta | Clientes permitidos |
| --- | --- | --- |
| Consultas DNS diretas | **53/TCP e 53/UDP**, em `DNS_LISTEN_IPV4` | `DNS_CIDRS` |
| Painel Nginx HTTPS | **443/TCP** | `ACCESS_CIDRS` |
| Redirecionamento HTTPS | **80/TCP** | `ACCESS_CIDRS` |
| Painel interno/API | `127.0.0.1:5380/TCP` | Somente a VM/Nginx |
| SSH | `SSH_PORT`, normalmente 22/TCP | `SSH_CIDRS` |

Nginx publica apenas o painel. Consultas DNS usam IPv4; DHCP, DoH/DoT/DoQ e clustering não são provisionados. A recursão é restrita a `DNS_CIDRS`, que não aceita `/0`.

O instalador preserva `systemd-resolved`, `/etc/resolv.conf`, Netplan e DHCP. Escutar somente no IP privado permite coexistir com o stub Ubuntu em `127.0.0.53`. Se outro DNS ocupar 53/TCP ou UDP nesse IP, a instalação é recusada.

## 2. Preparar os artefatos

Na máquina de preparação Ubuntu 26.04, na raiz do projeto:

```bash
sudo apt-get update
sudo apt-get install -y python3 ca-certificates gnupg ubuntu-keyring
python3 scripts/linux/artifacts.py prepare --only \
  technitium-15.6.1.tar.gz ubuntu26-technitium.tar.gz
```

O comando prepara os dois arquivos novos e preserva as entradas anteriores do manifesto depois de conferir seus hashes. A preparação completa `python3 scripts/linux/artifacts.py prepare` também inclui o DNS.

Use `--only` quando o conjunto anterior, incluindo o bundle base, já existir localmente. Em uma máquina de preparação nova, prepare o conjunto completo primeiro; o manifesto usado na instalação precisa incluir também `ubuntu26-base.tar.gz`.

Publique `technitium-15.6.1.tar.gz`, `ubuntu26-technitium.tar.gz`, `manifest.json` e `SHA256SUMS`, gerados em `artifacts/cdn/ubuntu26-amd64/`. O bundle compartilhado `ubuntu26-base.tar.gz` também precisa estar publicado. Siga o [guia do CDN](../docs/cdn.md) para publicação no Apache em `/var/www/html/cdn/ubuntu26-amd64/` e entrega do manifesto à VM por SSH/SFTP.

Para baixar os artefatos do próprio DNS, use um resolvedor existente ou adicione temporariamente ao arquivo hosts da VM:

```text
192.168.0.200 cdn.trendsoftware.brasil
```

## 3. Configurar e instalar

Copie **todo o projeto** para a VM, preservando `scripts/linux/`. Na raiz:

```bash
sudo apt-get update
sudo apt-get install -y python3 curl ca-certificates
cp technitium/linux/.env.example technitium/linux/.env
cp technitium/config/records.example.json technitium/config/records.json
nano technitium/linux/.env
nano technitium/config/records.json
```

| Parâmetro | O que definir |
| --- | --- |
| `DOMAIN` | Nome do painel, por exemplo `dns.trendsoftware.brasil`; deve pertencer à zona do JSON |
| `DNS_LISTEN_IPV4` | IP privado fixo **desta VM**, existente em sua interface |
| `DNS_CIDRS` | Redes IPv4 dos clientes e da recursão; inclua a rede do próprio servidor |
| `DNS_FORWARDERS` | IPs IPv4 dos resolvedores externos separados por vírgula; vazio usa recursão direta |
| `DNS_RECORDS_PATH` | Caminho absoluto da cópia editada de `records.json` |
| `ACCESS_CIDRS` | Redes dos administradores do painel, independentes dos clientes DNS |
| `SSH_CIDRS` / `SSH_PORT` | Rede e porta reais do SSH |
| `ARTIFACT_BASE_URL` / `ALLOW_HTTP_ARTIFACTS` | CDN e autorização explícita para HTTP privado |
| `MANIFEST_PATH` | Manifesto entregue por canal confiável |
| `TLS_CERT_PATH` / `TLS_KEY_PATH` | Certificado e chave PEM; vazios geram certificado autoassinado |

O exemplo usa `192.168.0.250` por ser a VM de homologação Codex. Troque pelo IP definitivo quando instalar o DNS da rede. Ajuste `DNS_RECORDS_PATH` ao diretório real do projeto.

Em `config/records.example.json`, os IPs já definidos foram preservados. Os endereços de Grafana (`192.168.0.203`), Prometheus (`192.168.0.204`), Redis (`192.168.0.205`), Keycloak (`192.168.0.206`), Argo CD (`192.168.0.209`), Nexus (`192.168.0.213`) e OpenSearch (`192.168.0.214`) são sugestões de planejamento, sem repetição no arquivo. Antes de importar, confirme que estão livres na rede e configure IP fixo ou reserva DHCP para os destinos. Para aplicações no Kubernetes, use o IP estável do Ingress ou LoadBalancer que atende cada nome. Os registros de Harbor e Argo CD antecipam serviços cujos instaladores continuam pendentes.

Todos os nomes do arquivo são relativos à zona `trendsoftware.brasil`: `cdn` resulta em `cdn.trendsoftware.brasil`, e `postgresql.server1` resulta em `postgresql.server1.trendsoftware.brasil`. Os nomes `kubernetes.cluster1`, `kubernetes.cluster2` e `kubernetes.cluster3` seguem o mesmo padrão. O TTL é de 300 segundos. O JSON cria uma zona primária local; este é um exemplo mínimo:

```json
{
  "zone": "trendsoftware.brasil",
  "records": [
    {"name": "cdn", "type": "A", "value": "192.168.0.200", "ttl": 300}
  ]
}
```

O registro A do painel é acrescentado com `DOMAIN → DNS_LISTEN_IPV4`. Acrescente outros serviços usando seus IPs reais. A zona local responde por **todo** `trendsoftware.brasil`: nomes ausentes nela não são procurados automaticamente no DNS público. Se também existir uma zona pública, planeje os registros ou use um subdomínio exclusivo para a rede.

```bash
sudo bash technitium/linux/install.sh --config "$(pwd)/technitium/linux/.env"
sudo systemctl status sdlc-technitium --no-pager
sudo nginx -t
sudo ufw status verbose
sudo ss -lntup
```

A primeira configuração é gerada em uma rede isolada temporária antes de publicar o DNS. A conta `sdlc-technitium` não tem login e recebe a capability necessária para a porta 53. A senha administrativa é aleatória.

## 4. Validar e distribuir aos clientes

Substitua o IP abaixo caso necessário:

```bash
dig @192.168.0.250 cdn.trendsoftware.brasil A
dig +tcp @192.168.0.250 cdn.trendsoftware.brasil A
dig @192.168.0.250 dns.trendsoftware.brasil A
dig @192.168.0.250 ubuntu.com A
sudo cat /opt/sdlc/technitium/credentials.txt
```

O CDN deve retornar `192.168.0.200`; o último `dig` verifica recursão/encaminhamento e depende do acesso aos resolvedores externos. Abra `https://dns.trendsoftware.brasil` com usuário `admin` e a senha gerada. Confie no certificado autoassinado no cliente ou forneça certificado da sua CA. Enquanto o cliente não usar esse DNS, adicione o nome do painel ao seu arquivo hosts.

Depois de validar, configure no **DHCP/roteador** o IP da VM como DNS e renove as concessões. O instalador não altera automaticamente o DNS de toda a rede.

## 5. Gerenciar zonas e registros

No painel, use **Zones → zona → Add Record**. Para o CDN: tipo `A`, nome `cdn`, endereço `192.168.0.200`, TTL `300`. MX, PTR, SRV e outros tipos também podem ser gerenciados pelo painel. Em **Settings**, ajuste encaminhadores/cache/logs; em **Administration**, usuários e 2FA. Preserve o painel em loopback e a recursão restrita às redes autorizadas.

O script de configuração aceita registros **A, AAAA, CNAME e TXT** no formato JSON do exemplo. `name` pode ser `@` (a própria zona), um nome relativo simples ou composto (`cdn`, `postgresql.server1`) ou um FQDN dentro da zona. O domínio é acrescentado aos nomes relativos uma única vez. Um nome terminado em ponto é absoluto e precisa pertencer à zona; um nome sem ponto final que ainda não termina com o domínio da zona é relativo. Para CNAME, `value` deve conter o nome completo do destino. TTL padrão: 300. Cada arquivo declara uma zona.

```bash
sudo bash technitium/linux/configure.sh --records /caminho/novos-registros.json
# Substituição deliberada dos conjuntos nome/tipo declarados:
sudo bash technitium/linux/configure.sh --records /caminho/novos-registros.json --replace
```

Registros iguais são preservados. Um conjunto existente com conteúdo/TTL diferente gera erro antes de alterar os registros. `--replace` substitui somente os conjuntos declarados; não apaga outros nomes. A API usa POST e token Bearer em memória, encerrando a sessão ao final.

Se mudou a senha no painel, forneça um arquivo root protegido com `chmod 600` usando `--password-file /caminho/senha`. Se ativou 2FA, acrescente `--otp 123456` com o código atual. Não coloque senhas na linha de comando.

Reexecutar o instalador com o mesmo `.env` preserva zonas, senha atual e mudanças do painel: não reaplica o JSON inicial nem redefine settings. Use `configure.sh` ou o painel para configurações posteriores. IP/domínio/CIDRs da instalação exigem manutenção explícita, conforme o [guia Linux](../docs/linux.md).

## 6. Diagnóstico, backup e desinstalação

```bash
sudo journalctl -u sdlc-technitium -n 100 --no-pager
sudo systemctl show sdlc-technitium -p MemoryCurrent -p MemoryPeak -p MemoryMax
sudo ls -l /var/lib/sdlc/technitium/logs
```

Dados: `/var/lib/sdlc/technitium/config/`. Binários/certificado/configuração da instalação: `/opt/sdlc/technitium/`. Faça backup protegido dos dois diretórios com o serviço parado, ou exporte em **Settings → Backup Settings**. Teste restauração em VM isolada com a mesma versão e preserve permissões/propriedade `sdlc-technitium`.

Antes de remover um DNS usado pelos clientes, direcione o DHCP/roteador para outro resolvedor.

```bash
# Remover da execução preservando zonas, usuários, settings e segredos:
sudo bash technitium/linux/uninstall.sh --yes --keep-data
# Reinstalar com a configuração original:
sudo bash technitium/linux/install.sh --config /opt/sdlc/technitium/install.env
# Remoção completa de TODOS os dados e recursos próprios:
sudo bash technitium/linux/uninstall.sh --yes
```

A remoção completa elimina unit, binários, zonas/usuários, certificado, conta, proxy e regras específicas DNS/painel. Pacotes compartilhados, Nginx/UFW, cache, regras SSH e journal permanecem. Não altera a rede do Ubuntu nem concessões DHCP.

## Referências e validação

- [Produto e licença](https://technitium.com/dns/).
- [Binário oficial versionado e checksum](https://download.technitium.com/dns/archive/15.6.1/).
- [API oficial](https://github.com/TechnitiumSoftware/DnsServer/blob/master/APIDOCS.md).
- [ASP.NET Core no Ubuntu 26.04](https://learn.microsoft.com/en-us/dotnet/core/install/linux-ubuntu-install).
- [Relatório de validação](validation.md).

[Voltar ao sumário](../README.md)
