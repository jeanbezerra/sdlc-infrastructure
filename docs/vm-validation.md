# Homologação na VM dedicada ao Codex

## Ambiente e resultado

Em **11/10/2026**, os nove serviços nativos passaram pelo ciclo de instalação, reexecução, remoção com preservação de dados, reinstalação, reboot e remoção completa na VM `192.168.0.250`. A VM foi inicialmente conferida sem esses serviços; as validações foram sequenciais, mantendo PostgreSQL para as aplicações que dependem dele.

| Recurso | Ambiente conferido |
| --- | --- |
| Sistema | Ubuntu Server 26.04.1 LTS amd64, systemd |
| Kernel ao final | `7.0.0-38-generic` |
| Memória | 7,2 GiB RAM e 4 GiB swap |
| Disco da VM | Aproximadamente 62 GiB |
| CDN | Apache HTTPD em `192.168.0.200` |
| URL utilizada | `http://cdn.trendsoftware.brasil/cdn/ubuntu26-amd64` |
| Redes dos proxies | `192.168.0.0/24`, `127.0.0.1/32`, `::1/128` |
| SSH | 22/TCP, permitido de `192.168.0.0/24` |
| TLS das aplicações | Certificados de laboratório, validados explicitamente pelos clientes de teste |

O manifesto foi transferido por SFTP, e os onze arquivos publicados no CDN passaram na conferência SHA256. O bundle Java foi corrigido durante os testes. SHA256 do manifesto final:

```text
02bfbb9ee1183c28526e2421499abcb96202bc0c17b5093638454b5754e78911
```

## Ciclo verificado por serviço

| Serviço | Instalar e reexecutar | `--keep-data` e reinstalar | Boot systemd / API ou protocolo | Remover completamente |
| --- | --- | --- | --- | --- |
| PostgreSQL 18 | OK | OK | OK | OK |
| Redis 8 | OK | OK | OK | OK |
| Prometheus 3.15.0 | OK | OK | OK | OK |
| Grafana 13.2.3 | OK | OK | OK | OK |
| Jenkins 2.580.1 LTS | OK | OK | OK | OK |
| Keycloak 26.8.0 | OK | OK | OK | OK |
| Nexus 3.96.4-01 | OK | OK | OK | OK |
| SonarQube 26.9.0.129388 | OK | OK | OK | OK |
| OpenSearch 3.9.0 | OK | OK | OK | OK |

A preservação dos diretórios foi conferida com uma sentinela em cada serviço. PostgreSQL e Redis também preservaram registros reais: tabela `app.codex_vm_probe` e chave `sdlc:codex-vm-validation`. Nas verificações finais, esses registros foram apenas lidos, sem recriação. Os bancos externos de Keycloak/SonarQube permaneceram depois da desinstalação das aplicações e da reinstalação do PostgreSQL; foram eliminados somente na remoção final do cluster de teste.

Também foram verificados: units ativas/habilitadas, contas sem login, listeners dos processos e JVMs filhas em loopback, sintaxe Nginx, TLS nos proxies, autenticação e APIs. PostgreSQL recusou DDL da role de aplicação e conexões sem TLS. Redis recusou comandos administrativos pela ACL da aplicação. Jenkins e OpenSearch recusaram acesso anônimo à API testada. SonarQube validou a senha inicial substituída pelo instalador.

Foram realizados sete reboots com reconexão SSH. Identificador do boot final: `78395827-5031-4b02-8dca-6910f56ea744`.

## Correções encontradas na VM

- APT offline: o diretório dos `.deb` verificados também é usado como cache de arquivos do APT; instalação e preparação usam `--no-install-recommends`.
- Extração dos binários: diretórios implícitos criados com umask restritivo recebem leitura/travessia pelo grupo do serviço, resolvendo o startup do Grafana.
- Java/Jenkins: bundle oficial Ubuntu inclui fontconfig, DejaVu e HarfBuzz. Bundle, manifesto e checksums foram publicados juntos; originais preservados no CDN.
- Nexus: `nexus.properties` fica legível pelo grupo do serviço. O listener foi confirmado em `127.0.0.1:18082`.
- Redis: a unit padrão habilitada pelo pacote é desabilitada quando ele é instalado pela primeira vez. Uma nova instalação do pacote e um reboot confirmaram a ausência do servidor adicional em 6379; instalações preexistentes são preservadas.
- OpenSearch: logs de GC/erros e heap dumps recebem caminhos no diretório de dados. O keystore é criado antes do startup, mantendo a configuração protegida. Uma instalação nova após a remoção completa confirmou a criação automática. O bootstrap oficial tenta criar o keystore quando ele está ausente, antes de configurar os logs: [código oficial](https://raw.githubusercontent.com/opensearch-project/OpenSearch/3.9/server/src/main/java/org/opensearch/bootstrap/Bootstrap.java).
- OpenSearch/Nginx: HTTPS do backend é validado pela CA interna e nome TLS `localhost`; os testes passaram com validação TLS também no cliente.

## Reproduzir as verificações

Use somente uma VM de homologação com os instaladores e o `.env` já configurado:

```bash
sudo python3 tests/vm-verify.py --service grafana --config /caminho/grafana.env
sudo reboot
# Após reconectar, conferir os dados existentes:
sudo python3 tests/vm-verify.py --service grafana --config /caminho/grafana.env --expect-existing
```

O verificador aceita `postgresql`, `redis`, `prometheus`, `grafana`, `jenkins`, `keycloak`, `nexus`, `sonarqube` e `opensearch`. A primeira execução cria a sentinela e os objetos de teste PostgreSQL/Redis. `--expect-existing` exige a presença deles. O script não instala nem desinstala serviços; use os comandos do README individual para exercitar essas fases.

Os logs de instalação, reexecução, reinstalação e remoção ficaram na VM, com acesso root, em `/home/trendsoftware/codex-sdlc-validation/logs/`. O resultado final está em `/home/trendsoftware/codex-sdlc-validation/RESULT.txt`.

## Estado final e limites

Os nove diretórios de binários/dados, contas/grupos `sdlc-*`, units, proxies, perfis/regras UFW e arquivos sysctl específicos foram removidos e conferidos ausentes. Nginx e SSH continuam ativos. UFW continua ativo e, ao final, permite somente SSH/22 da LAN. Não há cluster PostgreSQL do projeto nem servidor Redis padrão ativo/habilitado.

Pacotes Ubuntu compartilhados, cache de artefatos, projeto e logs privados de homologação permanecem para reutilizar a VM dedicada. O Apache existente foi mantido com sua configuração original; somente o bundle Java, manifesto e `SHA256SUMS` foram atualizados, com backup em `/home/trendsoftware/sdlc-cdn-backup-20261011/`.

Esta validação cobre o ciclo dos instaladores, sem certificar operação em produção. Ainda precisam ser exercitados backup/restore, PITR, carga, falhas de infraestrutura, HA, renovação TLS e integração com a CA/DNS definitivos. Não foi executada carga conjunta dos nove serviços nessa VM. Pipelines, quality gates, SSO, datasources, exporters e envio de logs precisam da configuração do ambiente.

Harbor e Argo CD continuam pendentes por decisão do projeto. Kubernetes permanece reservado à reutilização do outro projeto.

[Guia do CDN](cdn.md) · [Validação geral](validation.md) · [Voltar ao sumário](../README.md)
