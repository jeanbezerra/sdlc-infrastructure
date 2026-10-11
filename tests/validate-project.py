from pathlib import Path
import hashlib
import json
import re

root = Path(__file__).resolve().parents[1]
services = ['jenkins', 'grafana', 'prometheus', 'keycloack', 'nexus', 'sonarqube', 'opensearch', 'postgresql', 'redis']
for service in services:
    for file in ['README.md', 'linux/install.sh', 'linux/uninstall.sh', 'linux/service.sh', 'linux/.env.example']:
        assert (root / service / file).is_file(), (service, file)
for file in root.rglob('*.md'):
    if 'artifacts/cdn' in str(file).replace('\\', '/'):
        continue
    for link in re.findall(r'\]\(([^)]+)\)', file.read_text(encoding='utf-8')):
        if link.startswith(('https://', 'http://', '#')):
            continue
        assert (file.parent / link.split('#')[0]).exists(), (file, link)
for file in list(root.glob('scripts/linux/*.sh')) + list(root.glob('*/linux/*.sh')):
    assert b'\r\n' not in file.read_bytes(), f'CRLF: {file}'
    text = file.read_text()
    assert not re.search(r'\bdocker\b|\bcompose\b', text, re.IGNORECASE), file
manifest_path = root / 'artifacts/cdn/ubuntu26-amd64/manifest.json'
if manifest_path.exists():
    manifest = json.loads(manifest_path.read_text())
    catalog = json.loads((root / 'scripts/linux/catalog.json').read_text())
    expected = {entry['name'] for entry in catalog['binaries']} | {'ubuntu26-' + group + '.tar.gz' for group in catalog['ubuntu_packages']}
    assert {entry['name'] for entry in manifest['artifacts']} == expected
    for entry in manifest['artifacts']:
        file = manifest_path.parent / entry['name']
        assert file.stat().st_size == entry['size']
        with file.open('rb') as stream:
            assert hashlib.file_digest(stream, 'sha256').hexdigest() == entry['sha256'], file
    print(f"Manifesto e SHA256 de {len(expected)} artefatos: OK")
print('Estrutura, links Markdown e ausência de Docker nos scripts: OK')
