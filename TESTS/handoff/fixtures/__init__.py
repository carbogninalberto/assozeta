"""Load a pinned starting installation for the runtime handoff tests."""
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path

DIRECTORY = Path(__file__).resolve().parent


@dataclass(frozen=True)
class InstallationFixture:
    manifest: dict
    revision: str
    env: dict[str, str] | None
    database: dict[str, list[dict]] | None
    broker: dict[str, list[dict]]
    browser: list[dict]


def load(fixture_id):
    """Read the installation state and verify the manifest's file digests."""
    document = json.loads((DIRECTORY / 'manifest.json').read_text())
    if document['format'] != 1:
        raise ValueError('unsupported handoff fixture manifest format')
    manifest = document['fixtures'][fixture_id]
    state = {}
    for kind, name in manifest['files'].items():
        if name is None:
            state[kind] = None
            continue
        path = (DIRECTORY / name).resolve()
        if not path.is_relative_to(DIRECTORY):
            raise ValueError(f'fixture {fixture_id} path leaves the fixture directory: {name}')
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != manifest['sha256'][name]:
            raise ValueError(f'fixture {fixture_id} checksum mismatch: {name}')
        state[kind] = json.loads(data)
    return InstallationFixture(manifest=manifest, revision=manifest['revision'], **state)
