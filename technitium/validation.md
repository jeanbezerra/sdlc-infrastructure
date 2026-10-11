# Validação do Technitium

Homologação executada em **11/10/2026** na VM dedicada `192.168.0.250`, Ubuntu Server **26.04.1 LTS amd64**, kernel `7.0.0-38-generic`, com o CDN Apache em `192.168.0.200`.

## Resultados

| Verificação | Resultado |
| --- | --- |
| Bash, ShellCheck, parser declarativo e recusa de `/0` para DNS | Passou |
| 16 testes Python, incluindo conflitos, TTL, CNAME, ACL de novas zonas e substituição de conjuntos | Passou |
| Manifesto e SHA256 dos 13 artefatos locais e publicados | Passou |
| Instalação APT real offline de ASP.NET Core 10 e dependências | Passou |
| Bootstrap inicial isolado, senha aleatória e recusa da senha padrão | Passou |
| DNS A do CDN/painel em 53/TCP e UDP no IP da VM | Passou |
| JSON/API: A, AAAA, CNAME e TXT; aplicação repetida preserva registros iguais | Passou |
| Nginx HTTPS com certificado validado e redirecionamento HTTP/308 | Passou |
| Recursão externa: resolução real de `ubuntu.com` | Passou |
| Painel em `127.0.0.1:5380`; DNS sem listener wildcard; ACL de recursão e de novas zonas conferidas | Passou |
| Perfis UFW separados: painel 80/443 e DNS 53/TCP/UDP somente da LAN | Passou |
| Reexecução, remoção `--keep-data` e reinstalação | Passou |
| Registros e alteração posterior de cache preservados após reboot | Passou |
| Reinstalação do zero e remoção completa de recursos próprios | Passou |
| SSH/Nginx/systemd-resolved ativos; resolv.conf e stub preservados | Passou |

O boot ID mudou para `410de5a8-4c3d-47ae-8aee-4daa50cd2d7c`; o SSH reconectou com UFW ativo. O conteúdo de `/etc/resolv.conf` manteve SHA256 `ebdf560272a77357195c39e98340b77e18c8a8ce2025ee950e9e0c7b01467ab8` e o symlink original para `../run/systemd/resolve/stub-resolv.conf`.

Os testes criaram quatro tipos de registros em uma zona de homologação e mudaram o cache de 10000 para 8000 entradas. As verificações posteriores leram esses registros/settings existentes, sem recriá-los. Uma última instalação limpa confirmou o bootstrap automático completo antes da remoção final.

## Artefatos e memória

Technitium: **15.6.1**, portátil oficial do arquivo versionado, checksum oficial conferido. ASP.NET Core/runtime: **10.0.12-0ubuntu1~26.04.1**, pacotes do repositório Ubuntu e árvore completa de dependências.

A VM de testes tem aproximadamente **7,2 GiB de RAM**. O serviço foi executado com limite systemd/cgroup de **1536 MiB**, igual ao instalador. As medições funcionais registraram aproximadamente **33–132 MiB** em `MemoryCurrent`. Essas medições não representam benchmark de carga; uma VM inteira com 2 GiB e cargas maiores ainda precisa de validação de capacidade.

Os dois artefatos DNS foram acrescentados ao CDN, preservando as onze entradas anteriores. O backup dos metadados anteriores está em `/home/trendsoftware/sdlc-cdn-backup-technitium-20261011-eIE0Lg/` no servidor CDN. A configuração Apache existente foi preservada e passou em `apache2ctl configtest`.

## Reproduzir e estado final

Na raiz do projeto em Ubuntu, execute `bash tests/validate.sh`. Em uma **VM dedicada à homologação**, após instalar este serviço:

```bash
# Cria a zona de teste e modifica o cache para testar persistência:
sudo python3 tests/technitium-vm.py --seed
# Depois de reinstalar/reiniciar, verifica sem recriar os dados:
sudo python3 tests/technitium-vm.py
```

Esse verificador é separado do teste dos nove serviços anteriores. Ele pressupõe o exemplo `cdn → 192.168.0.200`, acesso root e ferramentas instaladas pelo bundle.

Ao finalizar, o Technitium foi completamente removido da VM: unit, usuário/grupo, dados, binários, certificado, proxy e ambos os perfis/regras UFW. SSH, Nginx, resolvedor Ubuntu, dependências compartilhadas, cache e logs privados de homologação permanecem. Nenhum cliente da rede teve seu DNS/DHCP alterado.

Projeto e evidências privadas: `/home/trendsoftware/codex-sdlc-validation/project/`, `logs/` e `TECHNITIUM-RESULT.txt` na VM. Os logs intermediários incluem tentativas corrigidas durante a homologação; os resultados finais são os registrados aqui.

DoH/DoT/DoQ, IPv6 como transporte DNS, DHCP, HA, restauração completa e testes de carga não fazem parte desta implementação.

[Guia de instalação](README.md)
