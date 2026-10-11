# Harbor

## Papel no SDLC

Armazena imagens OCI e charts, controla acesso, replica artefatos e verifica vulnerabilidades com Trivy. Jenkins publica os artefatos e o ambiente de execução os consome.

## Situação deste projeto

**Implementação pendente por decisão explícita:** o projeto agora exige instalação nativa, sem Docker, em Ubuntu Server 26.04 LTS. A distribuição suportada de Harbor requer containers/Kubernetes; implantação adiada por decisão do projeto. Os wrappers em `linux/install.sh` e `linux/uninstall.sh` encerram com código 2 e não alteram a VM. Não os trate como instaladores funcionais.

O tarball do instalador oficial contém a configuração de uma implantação com containers; ele não é um servidor Linux nativo. Não será publicado como se fosse um binário independente.

## Portas previstas para a implantação futura

| Porta | Uso | Exposição |
| --- | --- | --- |
| 443/TCP | UI e API via ingress/proxy HTTPS | Redes autorizadas |
| 80/TCP | Redirecionamento para HTTPS | Se necessário |


Nenhuma regra de firewall é criada agora. Os detalhes serão definidos quando a estratégia de instalação for aprovada.

## Referências oficiais

- [Instalação e configuração](https://goharbor.io/docs/2.14.0/install-config/)
- [Implantação via Helm](https://goharbor.io/docs/2.14.0/install-config/harbor-ha-helm/)

[Voltar ao sumário](../README.md)
