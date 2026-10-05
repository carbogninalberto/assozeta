#!/usr/bin/env python3
"""Inventory execution preparation for every reviewed MDX section, without live proof."""
import argparse
from collections import Counter
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[2]
NODE = r'''
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import {pathToFileURL} from 'node:url';
const root=process.argv[1];
const source=(relative,symbol,length)=>{
 const bytes=fs.readFileSync(path.join(root,relative));
 const lines=bytes.toString().split(/\r?\n/),start=lines.findIndex(line=>line.includes(symbol));
 if(start<0)throw new Error('Missing source symbol: '+relative+':'+symbol);
 const sha=value=>crypto.createHash('sha256').update(value).digest('hex');
 return {path:relative,symbol,start:start+1,end:Math.min(lines.length-1,start+length),
  sha256:sha(bytes),canonical_source_sha256:sha(lines.join('\n'))};
};
const {referenceCatalogue}=await import(pathToFileURL(path.join(root,'docs/manuale/reference-catalogue.mjs')));
const {authoredWorkflows}=await import(pathToFileURL(path.join(root,'docs/manuale/authored-workflows.mjs')));
const {editorialContracts}=await import(pathToFileURL(path.join(root,'docs/manuale/editorial-contracts.mjs')));
// Use the same reference families as publication: omission here falsely reports
// reviewed introduction, self-hosted billing and security claims as missing.
const references=referenceCatalogue(source);
console.log(JSON.stringify({editorial:editorialContracts().map(({path,id,status,reason,source_contracts})=>({path,id,status,reason,sources:source_contracts.map(item=>item.path)})),references:references.map(({path,id,title,status,reason})=>({path,id,title,status,reason})),
 workflows:Object.entries(authoredWorkflows).map(([id,spec])=>({id,script:spec.script,sections:spec.sections}))}));
'''


def inventory(manual):
    manual = Path(manual).resolve()
    authoring = json.loads((manual / '.manuale-authoring.json').read_text())
    bindings = json.loads((manual / '.manuale-evidence.json').read_text())['sections']
    metadata = json.loads(subprocess.run(['node', '--input-type=module', '-e', NODE, str(ROOT)],
                                        capture_output=True, text=True, check=True).stdout)
    registry = json.loads((ROOT / 'docs/manuale/recipes.json').read_text())['recipes']
    recipes = {recipe['id']: recipe for recipe in registry}
    references = {section['path'] + '#' + section['id']: section for section in metadata['references']}
    editorial = {section['path'] + '#' + section['id']: section for section in metadata['editorial']}
    prepared = {}
    for workflow in metadata['workflows']:
        recipe = recipes.get(workflow['id'])
        if not recipe or recipe['script'] != workflow['script'] or not (ROOT / workflow['script']).is_file():
            raise ValueError('Unregistered authored workflow: ' + workflow['id'])
        for section in workflow['sections']:
            key = section['path'] + '#' + section['id']
            if key in prepared:
                raise ValueError('Multiple complete authored bindings: ' + key)
            prepared[key] = workflow['id']
    entries = []
    for page in authoring['pages']:
        for section in page['sections']:
            key = page['path'] + '#' + section['id']
            if key not in bindings:
                raise ValueError('Missing reviewed MDX binding: ' + key)
            reference = references.get(key)
            descriptor = reference or editorial.get(key)
            if key in prepared:
                status = 'execution-prepared'
            elif reference and reference['status'] == 'verified':
                status = 'code-reference-prepared'
            elif descriptor and descriptor['status'] in ('unsupported', 'needs_external_verification'):
                status = descriptor['status']
            elif reference:
                status = 'execution-missing'
            else:
                # Older recipe templates do not constitute a complete direct
                # procedure binding. Candidate pages remain explicit, not proven.
                status = 'draft-needs-coverage-review'
            entries.append({'section': key, 'title': section['title'], 'status': status,
                'editorial_contract_available': key in editorial,
                'editorial_source_paths': sorted(set(editorial.get(key, {}).get('sources', []))),
                'workflow': prepared.get(key), 'recipe_module': bindings[key]['recipe_module'],
                'candidate_workflows': [recipe['id'] for recipe in registry if page['path'] in recipe['pages']],
                'reason': descriptor.get('reason', '') if descriptor else 'Complete procedure binding still needs review.',
                'verified': False})
    if len({entry['section'] for entry in entries}) != len(entries) or set(bindings) != {entry['section'] for entry in entries}:
        raise ValueError('Whole-manual inventory differs from reviewed MDX sections')
    return {'format': 1, 'purpose': 'execution-planning-only', 'complete': False,
            'browser_executed': False, 'verified_sections': 0,
            'navigation_pages': len(authoring['pages']), 'sections': len(entries),
            'editorial_contract_sections': len(set(editorial) & set(bindings)),
            'missing_editorial_contracts': sorted(set(bindings) - set(editorial)),
            'status_counts': dict(sorted(Counter(entry['status'] for entry in entries).items())),
            'entries': entries}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manual', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = inventory(args.manual)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({key: value for key, value in result.items() if key != 'entries'}, ensure_ascii=False))


if __name__ == '__main__':
    main()
