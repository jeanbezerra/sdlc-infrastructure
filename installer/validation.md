# Validação do instalador remoto

Homologação realizada em **11/10/2026**, usando controlador Windows com Python 3.14.6/Paramiko 5.0.0 e VM dedicada `192.168.0.250`, Ubuntu 26.04 LTS amd64, aproximadamente 7,2 GiB de RAM. Os testes locais Linux foram executados no Ubuntu 26.04 via WSL. O CDN existente em `192.168.0.200` foi utilizado sem alterar seus artefatos.

## Verificações locais

- **33 testes Python aprovados**, incluindo 15 do MVP, 9 do Technitium e 9 de segurança de artefatos.
- Sintaxe Bash, ShellCheck, contrato dos arquivos de configuração e wrappers pendentes aprovados.
- Estrutura e links Markdown aprovados; SHA256 dos 13 artefatos locais conferidos.
- Dependências do ambiente Python do controlador verificadas com `pip check`.
- Catálogo dos dez serviços nativos conferido contra os scripts reais; serviços pendentes recusados.
- Inventário rejeita senha em texto e porta SSH inválida; configuração rejeita expansão de shell, chaves desconhecidas e segredo inline não suportado.
- Payload contém somente código selecionado e entradas declaradas; extração rejeita traversal, symlinks, duplicatas e conteúdo alterado.
- Fingerprint incorreto é rejeitado antes da autenticação, mesmo quando existe um arquivo `known_hosts`.
- Logs ocultam senhas geradas e arquivos de senha enviados; acompanhamento recupera todos os trechos de logs maiores que uma resposta.

Durante a homologação, foi corrigida a leitura da quebra de linha do marcador `.managed` escrito pelo instalador nativo. O teste de regressão cobre essa leitura.

## Ciclos reais por SSH/sudo

| Serviço | Instalação | Verificação | Remoção completa | Sondagem |
| --- | --- | --- | --- | --- |
| Grafana | Aprovada, inclusive reexecução | Aprovada | Aprovada | Health API HTTP 200, Nginx e certificado/hostname do proxy |
| Redis | Aprovada | Aprovada | Aprovada | PING autenticado e TLS do proxy 6380 |
| Technitium | Aprovada | Aprovada | Aprovada | Consultas DNS UDP/TCP, painel HTTP e TLS do proxy |
| PostgreSQL | Aprovada | Aprovada | Aprovada | SELECT 1 e negociação TLS do proxy 5432 |

O worker de instalação Grafana foi iniciado e a conexão SSH controladora foi encerrada deliberadamente. Uma nova conexão, usando `status --follow`, recuperou os logs e o resultado final. A operação continuou no systemd.

No Grafana, também foram comprovados:

- Reexecução com a mesma configuração e mesmos hashes dos segredos.
- Remoção `--keep-data`, seguida de reinstalação, preservando um arquivo de dados de teste e os hashes dos segredos.
- Rejeição de configuração diferente antes de alterar a implantação instalada; a verificação seguinte permaneceu aprovada.
- Rejeição de outra operação enquanto o lock por serviço estava ocupado.
- Resultado de falha para verificar um serviço ausente e para chave SSH incorreta, conforme esperado.

A CLI executada diretamente no PowerShell também recuperou um resultado anterior com `status`, usando os prompts SSH/sudo sem eco.

## Evidências de execução

Os arquivos `result.json` e `operation.log` dessas execuções permanecem na VM em `/opt/sdlc-remote/<serviço>/jobs/<ID>/`. Cópias locais de homologação estão na pasta ignorada `.validation/mvp-state/runs/`.

| Operação | ID |
| --- | --- |
| Instalação Grafana e reconexão | `0192d056e28f42818d51ab8e` |
| Reinstalação Grafana após preservar dados | `e246dc03af574d66813fe657` |
| Remoção completa Grafana | `4baa4295d9264c59ac6f0564` |
| Instalação Redis | `0635e7ccbf504371bbfd24ad` |
| Remoção completa Redis | `d97a416640b84daabee116ec` |
| Instalação Technitium | `e0e7dc0bfc30475d904f0b55` |
| Remoção completa Technitium | `de5d0e7e74384a968a96affe` |
| Instalação PostgreSQL | `f989c466eb134e1d82119ebf` |
| Remoção completa PostgreSQL | `fd214e1af7a84565921729f6` |

## Estado final e limites

Os quatro serviços de teste foram removidos por completo, incluindo dados e cópias privadas das entradas do orquestrador. Auditoria, pacotes compartilhados, cache, Nginx, UFW e acesso SSH foram preservados. Após os três primeiros ciclos, SSH, Nginx e systemd-resolved estavam ativos, e o UFW tinha somente a regra SSH de `192.168.0.0/24`; o ciclo PostgreSQL também concluiu sua remoção.

As sondagens HTTPS de Grafana/Technitium a partir do loopback receberam 403 pela ACL, com certificado/hostname válidos; os respectivos backends retornaram 200. Isso comprova o proxy/TLS e a saúde local, sem afirmar acesso de todos os clientes da LAN.

Os outros seis serviços estão disponíveis no catálogo e utilizam seus instaladores existentes, mas seus ciclos não foram repetidos através do MVP nesta homologação. Os relatórios anteriores dos serviços continuam em [validação da infraestrutura](../docs/validation.md). Não foram realizados teste de carga, reboot durante operação, upgrade, instalação em outros sistemas/arquiteturas ou conexão SSH a partir de um controlador Linux. O executor não retoma automaticamente um job interrompido por reboot.

Para repetir as verificações locais:

```bash
bash tests/validate.sh
```

Para validar um deployment próprio, siga a ordem `plan`, `install`, `verify`, `uninstall --yes --keep-data`, `install` e `uninstall --yes` conforme o [README](README.md). Esses comandos modificam a VM selecionada.
