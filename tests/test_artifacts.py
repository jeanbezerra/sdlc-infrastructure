import importlib.util
import io
from pathlib import Path
import tarfile
import tempfile
import unittest
import zipfile

spec = importlib.util.spec_from_file_location('artifacts', Path(__file__).resolve().parents[1] / 'scripts/linux/artifacts.py')
artifacts = importlib.util.module_from_spec(spec)
spec.loader.exec_module(artifacts)


class ArtifactSecurityTests(unittest.TestCase):
    def test_reject_path_escape(self):
        for path in ['../escape', '/etc/passwd', 'root/../../escape', 'root\\escape']:
            with self.assertRaises(ValueError):
                artifacts.safe_name(path)

    def test_tar_symlink_escape(self):
        with tempfile.TemporaryDirectory() as work:
            package = Path(work) / 'bad.tar'
            with tarfile.open(package, 'w') as stream:
                link = tarfile.TarInfo('root/link')
                link.type = tarfile.SYMTYPE
                link.linkname = '../../escape'
                stream.addfile(link)
            with self.assertRaises(tarfile.FilterError):
                artifacts.extract(package, Path(work) / 'output', strip_root=True)

    def test_tar_multiple_roots_nexus(self):
        with tempfile.TemporaryDirectory() as work:
            package = Path(work) / 'nexus.tar'
            with tarfile.open(package, 'w') as stream:
                for name in ['nexus-1/bin/nexus', 'sonatype-work/nexus3/empty']:
                    member = tarfile.TarInfo(name)
                    member.size = 3
                    member.mode = 0o755
                    stream.addfile(member, io.BytesIO(b'abc'))
            output = Path(work) / 'output'
            artifacts.extract(package, output, True, 'nexus-')
            self.assertEqual((output / 'bin/nexus').read_bytes(), b'abc')
            self.assertFalse((output / 'sonatype-work').exists())

    def test_zip_preserves_executable(self):
        with tempfile.TemporaryDirectory() as work:
            package = Path(work) / 'app.zip'
            with zipfile.ZipFile(package, 'w') as stream:
                info = zipfile.ZipInfo('app/bin/start.sh')
                info.external_attr = 0o100755 << 16
                stream.writestr(info, '#!/bin/sh\n')
            output = Path(work) / 'output'
            artifacts.extract(package, output, True)
            self.assertTrue((output / 'bin/start.sh').stat().st_mode & 0o111)

    def test_no_merge_into_existing_directory(self):
        with tempfile.TemporaryDirectory() as work:
            output = Path(work) / 'output'
            output.mkdir()
            (output / 'important').write_text('keep')
            with self.assertRaises(ValueError):
                artifacts.extract('missing.tar', output)
            self.assertEqual((output / 'important').read_text(), 'keep')

    def test_reject_checksum_corruption(self):
        with tempfile.TemporaryDirectory() as work:
            original_request = artifacts.request
            artifacts.request = lambda url: io.BytesIO(b'corrupt')
            try:
                output = Path(work) / 'app.tar'
                with self.assertRaises(ValueError):
                    artifacts.download('https://example.com/app.tar', output, '0' * 64)
                self.assertFalse(output.exists())
                self.assertFalse(output.with_name('app.tar.part').exists())
            finally:
                artifacts.request = original_request


if __name__ == '__main__':
    unittest.main()
