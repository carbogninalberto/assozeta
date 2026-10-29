"""Reviewed-section recipe bindings.

A reviewed manual section is bound to the recipe module that verifies it. The
binding hashes the whole module except the reviewed hashes of sources the
section's own source contracts do not use: re-pinning one reviewed source
then invalidates only the sections whose contracts read it. Any other module
change, a section without registered contracts, or a module whose reviewed
hashes are not all in recognized one-entry-per-line maps keeps the whole-file
hash. docs/manuale/recipe-binding.mjs implements the same digest.
"""
import hashlib
import json
import re
from pathlib import Path

FORMAT = b'manuale-section-recipe-v1\0'
# A reviewed-source map: `const name = {` or `= Object.freeze({`, one
# "path": "sha256" entry per line.
_MAP = re.compile(r'^(?:export )?const \w+ = (?:Object\.freeze\()?\{\n((?:[ \t]*"[^"\n]+": "[a-f0-9]{64}",?\n)+)\}\)?;', re.M)
_ENTRY = re.compile(r'^([ \t]*)"([^"\n]+)": "([a-f0-9]{64})"(,?)$', re.M)
_HASH = re.compile(r'[a-f0-9]{64}')


def section_sources(root):
    """{"page#id": [source paths]} from the generated registry of `root`.

    A tree without the registry binds every section to its whole module.
    """
    try:
        registry = json.loads((Path(root) / 'docs/manuale/recipes.json').read_text())
    except FileNotFoundError:
        return {}
    sections = registry.get('reference_sections', {})
    if not isinstance(sections, dict):
        raise ValueError('Invalid reference_sections in docs/manuale/recipes.json')
    return sections


def section_recipe_sha256(module, sources):
    """The binding digest of module bytes for a section's source paths (None: unknown section)."""
    if sources is None:
        return hashlib.sha256(module).hexdigest()
    text = module.decode('utf-8')
    blocks = list(_MAP.finditer(text))
    if not blocks or sum(len(_HASH.findall(block[1])) for block in blocks) != len(_HASH.findall(text)):
        return hashlib.sha256(module).hexdigest()
    keep = set(sources)
    parts, last = [], 0
    for block in blocks:
        parts.append(text[last:block.start()])
        parts.append(_ENTRY.sub(lambda entry: entry[0] if entry[2] in keep else f'{entry[1]}"{entry[2]}": "elided"{entry[4]}', block[0]))
        last = block.end()
    parts.append(text[last:])
    return hashlib.sha256(FORMAT + ''.join(parts).encode('utf-8')).hexdigest()

