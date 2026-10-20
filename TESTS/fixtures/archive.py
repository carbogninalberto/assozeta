import io
import json
from pathlib import Path
import zipfile


def historical():
    return json.loads(Path(__file__).with_name('bakney-v1.json').read_text())


def archive_bytes(fixture=None):
    fixture = fixture or historical()
    target = io.BytesIO()
    with zipfile.ZipFile(target, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name, content in fixture['entries'].items():
            archive.writestr(name, json.dumps(content))
        for name, content in fixture.get('text_files', {}).items():
            archive.writestr(name, content)
    return target.getvalue()
