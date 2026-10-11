# Artefatos para o CDN S3

Os downloads preparados ficam em `cdn/ubuntu26-amd64/`. São binários oficiais dos fornecedores e bundles `.deb` obtidos dos repositórios oficiais Ubuntu 26.04 LTS, acompanhados de `manifest.json` e `SHA256SUMS`.

Não há imagens nem dependência de Docker. Consulte [preparação e publicação no CDN](../docs/cdn.md). Os arquivos grandes não devem ser versionados no Git; o manifesto é entregue ao administrador por um canal confiável.
