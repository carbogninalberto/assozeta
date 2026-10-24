"""Verify the narrowly patched external AWS config module without network access."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT/'BE_GO/third_party/aws-config'


def main():
    upstream = json.loads((SOURCE/'UPSTREAM.json').read_text())
    assert upstream['module'] == 'github.com/aws/aws-sdk-go-v2/config'
    assert upstream['version'] == 'v1.33.9'
    module = (ROOT/'BE_GO/go.mod').read_text()
    assert 'github.com/aws/aws-sdk-go-v2/config v1.33.9\n' in module
    assert 'replace github.com/aws/aws-sdk-go-v2/config => ./third_party/aws-config\n' in module
    assert f"{upstream['module']} {upstream['version']} {upstream['module_sum']}\n" in (ROOT/'BE_GO/go.sum').read_text()
    allowed = {'UPSTREAM.json', 'owned-http-trust.patch', 'PATCH.md'} | set(upstream['files_sha256'])
    actual = {str(path.relative_to(SOURCE)) for path in SOURCE.rglob('*') if path.is_file()}
    assert actual == allowed, ('unexpected/missing external module files', actual ^ allowed)
    with tempfile.TemporaryDirectory(prefix='az-aws-config-drift-') as directory:
        target = Path(directory)/'upstream'; shutil.copytree(SOURCE,target)
        subprocess.run(['patch','-R','-p1','-i',str((SOURCE/'owned-http-trust.patch').resolve())],
                       cwd=target, check=True, capture_output=True, timeout=10)
        for name,expected in upstream['files_sha256'].items():
            assert hashlib.sha256((target/name).read_bytes()).hexdigest() == expected, ('upstream drift',name)
    print('AWS config v1.33.9: exact upstream files plus declared opt-in patch verified')


if __name__ == '__main__': main()
