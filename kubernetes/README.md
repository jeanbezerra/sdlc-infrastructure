# Kubernetes

## Papel no SDLC

Orquestra a execução das aplicações: scheduling, deployments, services, recuperação de workloads e configuração do ambiente. Argo CD pode sincronizar o estado desejado no cluster; Prometheus e Grafana acompanham a operação.

## Situação deste projeto

Os scripts desta pasta foram despriorizados a pedido do responsável. O instalador será reutilizado de outro projeto. Nenhum instalador de Kubernetes foi criado ou executado aqui.

Ao trazer os scripts, documente a distribuição e versão, runtime, CNI, ingress, identidade dos nós e a matriz de portas correspondente. As portas variam conforme a distribuição e os componentes; não libere faixas genéricas de NodePort antes dessa definição.

Leia a [referência oficial de portas e protocolos](https://kubernetes.io/docs/reference/networking/ports-and-protocols/) e o [sumário do projeto](../README.md).
