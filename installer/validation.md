# Validação do instalador remoto

## Aceitação de chave e validação de login pelo menu — 12/10/2026

- Cadastro e acesso SSH oferecem aceitação da chave apresentada e senha sem eco. A chave é adicionada/substituída somente após autenticação concluída; uma mudança em relação à chave registrada exige confirmação de substituição. O inventário registra resultado, data UTC e destino, sem senha. A lista separa conectividade atual de último login (Válido/Falhou/Pendente).
- Senha validada é reutilizada apenas na sessão do menu, vinculada a inventário, host, endereço, usuário, porta e chave. Encerrar o menu, trocar inventário ou salvar ajustes de acesso descarta a senha em memória. Uma alteração de destino/chave invalida o estado de login para o destino atual.
- **127 testes aprovados no Linux/WSL**, inclusive Python 3.11; Windows/Python 3.14: **122 aprovados e 5 ignorados por dependerem de POSIX**. O menu tem 40 testes, incluindo cadastro e validação, substituição recusada/aceita, senha vazia/incorreta, redação de segredos, alterações concorrentes e reutilização limitada à sessão.
- Quatro cenários usam um servidor SSH real em loopback: autenticação válida, recusada, parcial e troca de chave entre sondagem e login. Só a autenticação completa registra Válido; a chave divergente impede enviar a senha. Nenhum canal de comandos é aberto pelo teste de login. Segredos não apareceram no inventário nem nos logs.
- A tabela e a seleção da nova opção foram conferidas em terminal PowerShell com sondagem do host conhecido. A autenticação com senha desta atualização foi validada no servidor local de teste; não foi solicitado nem utilizado um novo segredo das VMs do usuário.

## Abertura no Windows com controle de aplicativos — 12/10/2026

- Os eventos CodeIntegrity 3033/3077 apontaram bloqueio de `C:\Users\jeanbezerra\.local\bin\sdlc-installer.exe`; a assinatura Authenticode desse executável global está ausente. O [evento 3077 identifica bloqueio por política aplicada](https://learn.microsoft.com/en-us/windows/security/application-security/application-control/app-control-for-business/operations/event-id-explanations).
- `start.cmd` passou a usar `uv run --project installer --locked python -m sdlc_installer`. O Python 3.14.6 do ambiente do projeto apresenta assinatura Authenticode válida. A mesma entrada aceita os argumentos da CLI.
- Launcher executado de outro diretório em terminal Windows: abriu o menu com as quatro VMs cadastradas e encerrou com código 0. Listagem JSON de serviços pelo módulo também concluiu com código 0. Nenhum novo evento de bloqueio relacionado foi encontrado durante esses testes; nenhuma política de proteção foi alterada.
- Wheel/sdist reconstruídos e conferidos; estrutura, links Markdown e hashes dos 13 artefatos aprovados.

## Terminal interativo — 12/10/2026

- Iniciar `sdlc-installer` sem comando em um terminal abre o menu guiado; `menu` faz o mesmo. O launcher Windows `start.cmd` foi executado a partir de outro diretório e abriu o inventário do projeto corretamente.
- **106 testes aprovados no Linux/WSL e no Python 3.11**. Windows/Python 3.14: **101 aprovados e 5 ignorados por dependerem de POSIX**. A cobertura inclui 24 testes do menu: navegação, saída/EOF, validação e cancelamento de formulários, cadastro de VMs desligadas, preparação e ajustes, revisão antes de instalar, execução/acompanhamento simulado, histórico, confirmação de remoção, conferência SSH e redação de segredos.
- Navegação real em terminal PowerShell: abertura/saída, teste SSH do host `codex` em `192.168.0.250`, chave conferida e revisão do plano Grafana com cancelamento antes de iniciar. O fluxo de instalação do menu foi validado com transporte simulado; a homologação real das operações subjacentes permanece registrada nas seções anteriores.
- CLI para automação preservada; `--json` continua retornando um único documento. Menu com `--json` ou sem terminal retorna erro de uso e não aguarda entrada. Senha de chave privada tem prompt sem eco e entra no mesmo mecanismo de redação de SSH/sudo.
- Ruff, conferência do lock, estrutura/links e hashes dos 13 artefatos aprovados. Wheel e sdist incluem o módulo interativo; o sdist também contém `start.cmd`.

## Cadastro e descoberta do inventário — 12/10/2026

- `register` cadastra hosts e gera deployments usando os modelos e caminhos do projeto; `hosts --check` sonda DNS/SSH e confere a chave; `discover` aceita IPs, nomes DNS e CIDRs explícitos, com cadastro opcional.
- **82 testes Python aprovados no Linux/WSL e no Python 3.11**. Windows/Python 3.14: **77 aprovados e 5 ignorados por dependerem de POSIX**. A nova cobertura verifica cadastro repetido, conflitos, preservação de entradas, falha de gravação, alteração concorrente observável, valores inválidos, caminhos, arquivos privados, DNS, limites de descoberta e confiança SSH.
- Ruff, conferência do lock, estrutura/links Markdown, hashes dos 13 artefatos e conteúdo do wheel/sdist aprovados. Os dois módulos novos são conferidos no wheel.
- Teste real no host cadastrado `codex`, `192.168.0.250:22`: SSH acessível e chave `ssh-ed25519` correspondente ao pin existente. O cadastro de um deployment Grafana em `.validation/inventory-onboarding.json` passou pelo `plan`, sem iniciar instalação; a descoberta com cadastro reutilizou o mesmo host e preservou seu pin.
- As sondagens não autenticam, não executam comandos remotos e não estabelecem confiança automaticamente. Autenticação, sudo, compatibilidade do sistema e saúde dos serviços continuam sendo verificados pelas operações existentes. A descoberta real desta validação foi limitada à VM conhecida; a cobertura de faixas de rede usa testes locais simulados.

## Atualização uv, CLI e logging — 12/10/2026

- Pacote `sdlc-installer 0.2.0` sincronizado pelo uv 0.13.0, com dependências e build declarados em TOML e lockfile conferido por `uv lock --check`.
- **54 testes Python aprovados no Linux/WSL**, incluindo a cobertura anterior e novos testes de CLI, logging, rotação, templates, UTC/timezone local, JSON, histórico e redação de segredos divididos entre respostas.
- Windows/Python 3.14.6: **49 aprovados e 5 ignorados por dependerem de POSIX**. Ruff, sintaxe Bash, ShellCheck, contrato de configuração, links Markdown e hashes dos 13 artefatos aprovados.
- A versão mínima Python 3.11.17 foi validada no Linux com **54 testes aprovados**. A tentativa adicional no Windows/Python 3.11 foi impedida pela política de Controle de Aplicativo ao carregar a DLL `_overlapped`; não foi alterada nenhuma política do computador. A validação Windows concluída usa Python 3.14.6.
- `uv build` gerou wheel e sdist; wheel construído a partir do sdist. `scripts/check-dist.py` conferiu catálogo, executor, dependências, entrypoint e ausência de conteúdo privado nos dois artefatos.
- O wheel foi instalado com dependências do lock em um ambiente Python separado **fora do repositório**. O executável `sdlc-installer` foi chamado de outro diretório, usando `--repo`; sem essa opção ou `SDLC_REPO`, apresentou o erro de descoberta esperado.
- Ciclo real do Grafana na VM `192.168.0.250`: instalação com `--no-follow`, reconexão via `status --follow`, verificação e remoção completa, todos concluídos com sucesso. O fluxo utilizou o CDN local existente.
- O ciclo real foi repetido com o wheel final; **218 eventos JSON Lines** dos arquivos rotativos foram parseados e conferidos quanto à ausência da senha. Uma regex configurada também ocultou o IP nos eventos. A saída stdout de cada comando foi um único documento JSON. Recibos, histórico e logs por execução foram conferidos.

| Operação pelo wheel | Execução | Resultado |
| --- | --- | --- |
| Instalação sem acompanhamento + retomada | `25bbfddd02fb48a58177fa81` | `succeeded` |
| Verificação | `6a6d836829f647959272f6ab` | `succeeded` |
| Remoção completa | `92737ff3b8744c3cac234c74` | `succeeded` |

O executor Linux foi realocado como recurso do pacote, mantendo o protocolo e o uso exclusivo da biblioteca padrão no destino. Os logs rotativos pertencem ao controlador; a auditoria protegida do worker permanece na VM. Esta atualização não repetiu a homologação de todos os serviços: o ciclo real foi do Grafana, e a cobertura anterior dos outros serviços está registrada abaixo.

## Homologação inicial do MVP — 11/10/2026

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
