"""Write evaluate-manuale-corpus.json from a published manual index.

    python3 -I TESTS/fixtures/generate_evaluate_manuale_corpus.py --source PATH/index.json

Keeps the public projection fields of the chunks evaluate_manuale's case list
names (the published manual text), plus the first gaps and the catalog
summary. Readers, screenshots, evidence and capture provenance are dropped:
the evaluation harness (TESTS/adapters/go/evaluate_manuale.py) fits the
semantics and seals a runtime index from these chunks for every run.
"""
import argparse
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[2]
COMMAND = ROOT / 'BE/application/management/commands/evaluate_manuale.py'
OUTPUT = ROOT / 'TESTS/fixtures/evaluate-manuale-corpus.json'
FIELDS = ('id', 'page', 'section', 'title', 'text', 'status', 'url', 'scenario_ids', 'page_title', 'language', 'intent',
          'page_description', 'page_order', 'section_order', 'features', 'audience', 'content_sha256')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True)
    args = parser.parse_args()
    source = json.loads(Path(args.source).read_text())
    wanted = set(re.findall(r"'((?:docs|faq|tutorials)/[^']+#[^']+)'", COMMAND.read_text()))
    chunks = [{key: chunk[key] for key in FIELDS if key in chunk} for chunk in source['chunks'] if chunk['id'] in wanted]
    value = {
        'scope': 'Published manual sections named by evaluate_manuale (public projection fields only).',
        'source_release': source['metadata']['release'],
        'chunks': chunks,
        'gaps': [{key: gap[key] for key in ('page', 'section', 'status', 'reason')} for gap in source['gaps'][:3]],
        'catalog_summary': source.get('catalog', {}).get('summary'),
    }
    OUTPUT.write_text(json.dumps(value, ensure_ascii=False, indent=1) + '\n')
    print(f'{len(chunks)} chunks')


if __name__ == '__main__':
    main()
