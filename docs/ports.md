# Matriz de portas e firewall

As aplicações e bancos usam Nginx na VM, com backends em loopback; suas portas internas **não devem ser liberadas** no security group ou UFW. No Technitium, consultas DNS chegam diretamente a 53/TCP e UDP no IP privado escolhido; somente o painel usa Nginx. As portas abaixo são fixadas nos metadados de cada serviço.

| Serviço | Entrada no Nginx | Backend local | Outras portas locais | Liberação de entrada |
| --- | --- | --- | --- | --- |
| Technitium DNS | Painel 80/443 TCP | `127.0.0.1:5380` HTTP | DNS direto em `DNS_LISTEN_IPV4:53` TCP e UDP | Painel para `ACCESS_CIDRS`; DNS para `DNS_CIDRS`, sem `/0` |
| Jenkins | 80/443 TCP | `127.0.0.1:18081` HTTP | Agente TCP 50000 desativado | 80/443 para `ACCESS_CIDRS`; agentes usam WebSocket/443 |
| Grafana | 80/443 TCP | `127.0.0.1:13000` HTTP | — | 80/443 para `ACCESS_CIDRS` |
| Prometheus | 80/443 TCP | `127.0.0.1:19090` HTTP | — | 80/443 para `ACCESS_CIDRS`, com Basic Auth |
| Keycloak | 80/443 TCP | `127.0.0.1:18084` HTTP | Management `127.0.0.1:19084`; cache `127.0.0.1:7800/57800` | 80/443 para `ACCESS_CIDRS`; management/cache fechados |
| Nexus | 80/443 TCP | `127.0.0.1:18082` HTTP | Connectors adicionais não criados | 80/443 para `ACCESS_CIDRS` |
| SonarQube | 80/443 TCP | `127.0.0.1:19000` HTTP | Elasticsearch `127.0.0.1:19001` | 80/443 para `ACCESS_CIDRS`; search fechado |
| OpenSearch | 80/443 TCP | `127.0.0.1:19200` HTTPS | Transporte `127.0.0.1:19300` | 80/443 para `ACCESS_CIDRS`; transporte fechado |
| PostgreSQL | 5432 TCP | `127.0.0.1:15432`, TLS PostgreSQL | Socket Unix privado do cluster | 5432 para CIDRs específicos; `/0` recusado |
| Redis | 6380 TCP/TLS | `127.0.0.1:16379` Redis | — | 6380 para CIDRs específicos; `/0` recusado |
| Harbor / Argo CD | Não provisionadas | Pendentes | — | Nenhuma regra criada |
| Kubernetes | Definido no projeto de origem | Reutilização futura | — | Nenhuma regra criada aqui |

**SSH:** `SSH_PORT` (normalmente 22/TCP), permitido de `SSH_CIDRS` antes de ativar o UFW. Confirme seu IP/rede de administração e a porta real. As regras SSH permanecem ao remover serviços, para evitar perder acesso à VM.

**Saída:** HTTP/80 para `cdn.trendsoftware.brasil` (`192.168.0.200`) no ambiente local atual; HTTPS/443 se o CDN usar TLS; DNS/53 para o resolvedor e sincronização de hora conforme a VM. A preparação dos artefatos oficiais usa HTTPS/443. Keycloak e SonarQube precisam alcançar PostgreSQL/5432. Jenkins pode precisar acessar Git/HTTPS ou SSH, repositórios de dependências e APIs de deployment. Prometheus precisa alcançar as portas de exporters autorizados. Se houver firewall de saída corporativo, mapeie esses destinos conforme o seu ambiente; os scripts não abrem destinos indiscriminadamente.

PostgreSQL usa proxy TCP que preserva o handshake PostgreSQL e a criptografia nativa. Não se adiciona `ssl` ao listener stream de PostgreSQL. Redis termina TLS no Nginx e usa protocolo Redis somente em loopback; clientes usam `redis-cli --tls ... -p 6380`. Nginx não converte protocolos em HTTP.

Em uma VM compartilhada, serviços HTTP têm domínios diferentes e usam virtual hosts na mesma porta 443. Não crie regras públicas para 5380/18081/13000/19090/18084/18082/19000/19200/15432/16379.

[Voltar ao sumário](../README.md)
