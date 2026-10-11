# Validação e limites da entrega

## Verificações reproduzíveis

```bash
bash tests/validate.sh
bash tests/validate.sh --native
```

O primeiro comando verifica sintaxe Bash, ShellCheck, extração segura de arquivos, checksums, HTTP privado com opt-in e validação de redirecionamentos, parsing declarativo de configuração, ownership/symlinks, wrappers pendentes, links Markdown e correspondência dos arquivos baixados ao manifesto. Precisa de Python 3.12+ e ShellCheck.

`--native` usa os pacotes/binários baixados em diretórios temporários, sem root. Não instala APT no host, não cria contas/units reais e não muda o firewall. Inicializa um cluster PostgreSQL separado e processos nativos, validando TLS via Nginx, SCRAM, grants de aplicação/migrations, ACL Redis e APIs dos servidores. Requer espaço temporário para extrair as distribuições e memória para a aplicação de maior consumo. Os logs temporários são informados ao final.

## Resultado desta entrega

Executado em Ubuntu 26.04 LTS via WSL, em **11/10/2026**, com os arquivos presentes no CDN local:

| Verificação | Resultado |
| --- | --- |
| Sintaxe Bash, ShellCheck e 16 testes Python (artefatos e configuração DNS) | Passou |
| Configuração declarativa, ownership, symlinks e remoção sem `--yes` | Passou |
| Estrutura, links Markdown e SHA256 dos 13 artefatos | Passou |
| Nove servidores: inicialização e APIs/health reais | Passou |
| Nginx oficial Ubuntu: TLS PostgreSQL/Redis e sintaxe dos nove proxies juntos | Passou |
| PostgreSQL: SCRAM, SQL repetível, login de migrations, DML e bloqueio de DDL/sem TLS/senha incorreta | Passou |
| Redis: ACL, bloqueio administrativo e persistência AOF após reinício | Passou |
| Jenkins/OpenSearch: bloqueio anônimo; SonarQube: troca da senha inicial e login | Passou |
| Listeners dos servidores HTTP, search e cache restritos a loopback | Passou |
| Modelo de regras UFW: perfis distintos compartilhando 80/443 | Passou |
| Assinatura GPG oficial SonarQube, com fingerprint fixado | Passou |

O Keycloak foi iniciado com `cache=ispn`, `jdbc-ping` e mTLS do transporte; os listeners 7800/57800 foram conferidos em loopback. Os processos dos testes foram encerrados ao final. Nenhum serviço do projeto foi instalado no host durante essas verificações.

## Homologação na VM dedicada

Em 11/10/2026, o ciclo dos nove instaladores também passou na VM inicialmente limpa `192.168.0.250`, Ubuntu Server 26.04.1 LTS: instalação APT real, reexecução, `--keep-data`, reinstalação, boot systemd, reconexão SSH com UFW ativo e remoção completa. Os registros PostgreSQL/Redis foram preservados e lidos sem recriação após o último reboot. Veja o [relatório da VM, correções e estado final](vm-validation.md).

O verificador reproduzível é `tests/vm-verify.py`, executado como root na VM de homologação após instalar. Use `--expect-existing` nas verificações após reinstalação/reboot. Ele cria sentinelas e objetos de teste PostgreSQL/Redis na primeira execução.

## Limites

O [Technitium também passou em homologação na VM](../technitium/validation.md), incluindo DNS TCP/UDP, painel TLS, API, registros A/AAAA/CNAME/TXT, persistência após reboot e remoção completa. Seu teste explícito é `tests/technitium-vm.py`; `--seed` cria dados e modifica o cache somente em uma VM de homologação. O comando `tests/validate.sh --native` continua cobrindo os nove serviços originais.

Renovação TLS, certificados da CA definitiva, restrições de redes externas à LAN e cenários de infraestrutura diferentes ainda exigem validação no ambiente de operação.

Esta entrega não é uma certificação de produção. PostgreSQL utiliza versão 18 do repositório Ubuntu e cluster de uma instância, TLS nativo e SCRAM. Backup/restore completo, PITR, carga concorrente, recuperação após falha e HA precisam de testes no ambiente de operação. O script de aplicação de SQL administrativo atua apenas no cluster explicitamente criado pelo instalador; exemplos de bancos externos exigem conferência pelo administrador.

Harbor e Argo CD permanecem explicitamente pendentes; Kubernetes permanece reservado para reutilização. Não há testes que os representem como instalações concluídas.

[Voltar ao sumário](../README.md)
