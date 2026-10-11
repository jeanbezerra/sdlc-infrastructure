# Argo CD

## Papel no SDLC

Aplica no Kubernetes o estado desejado versionado no Git. É a etapa de entrega contínua: sincroniza aplicações, mostra diferenças e permite acompanhar a saúde dos deployments. Jenkins compila e testa; Argo CD entrega a versão aprovada.

## Situação deste projeto

**Implementação pendente por decisão explícita:** o projeto agora exige instalação nativa, sem Docker, em Ubuntu Server 26.04 LTS. O servidor Argo CD requer Kubernetes; implantação adiada por decisão do projeto. Os wrappers em `linux/install.sh` e `linux/uninstall.sh` encerram com código 2 e não alteram a VM. Não os trate como instaladores funcionais.

Baixar o executável `argocd` instala somente a CLI; não instala o servidor. Uma implantação futura precisa de contexto e namespace explícitos, permissões do cluster, política de remoção dos Applications/CRDs e ingress HTTPS.

## Portas previstas para a implantação futura

| Porta | Uso | Exposição |
| --- | --- | --- |
| 443/TCP | UI e API via ingress/proxy HTTPS | Redes autorizadas |
| 80/TCP | Redirecionamento para HTTPS | Se necessário |
| 6443/TCP | API do cluster, conexão de saída do administrador | Conforme documentação do cluster |

Nenhuma regra de firewall é criada agora. Os detalhes serão definidos quando a estratégia de instalação for aprovada.

## Referências oficiais

- [Getting Started](https://argo-cd.readthedocs.io/en/stable/getting_started/)
- [Ingress](https://argo-cd.readthedocs.io/en/stable/operator-manual/ingress/)

[Voltar ao sumário](../README.md)
