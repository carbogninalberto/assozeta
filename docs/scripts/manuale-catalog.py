#!/usr/bin/env python3
"""Validate the whole manual's review targets against source and real Git blobs.

Catalog entries discover implementation and authorization; only reviewed section
recipes can verify claims. This runs on the host where linked worktree Git refs
are available, before the backend validates the resulting evidence bundle.
"""
import argparse
import ast
import hashlib
import importlib.util
import json
import re
import subprocess
from pathlib import Path
from urllib.parse import quote


class CatalogError(ValueError):
    pass


def sha(contents):
    return hashlib.sha256(contents).hexdigest()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def owned(root, relative):
    root = Path(root).resolve()
    path = (root / relative).resolve()
    if Path(relative).is_absolute() or not path.is_relative_to(root) or path == root:
        raise CatalogError('Path escapes repository: ' + relative)
    return path


def git(root, *args, optional=False):
    result = subprocess.run(['git', '-C', str(root), *args], capture_output=True)
    if result.returncode:
        if optional:
            return None
        raise CatalogError('Cannot validate Git reference: ' + ' '.join(args))
    return result.stdout


class Sources:
    def __init__(self, root, revision):
        self.root = Path(root).resolve()
        if not re.fullmatch(r'[0-9a-f]{40}', revision):
            raise CatalogError('Evidence requires a full Git commit')
        if git(root, 'rev-parse', revision + '^{commit}').decode().strip() != revision:
            raise CatalogError('Evidence revision is not the requested commit')
        self.revision = revision
        remote = git(root, 'remote', 'get-url', 'origin', optional=True)
        remote = remote.decode().strip() if remote else ''
        match = re.fullmatch(r'(?:git@github.com:|https://github.com/)([\w.-]+/[\w.-]+?)(?:\.git)?', remote)
        self.repository_url = 'https://github.com/' + match[1] if match else None
        self.files = {}

    def file(self, relative):
        if relative in self.files:
            return self.files[relative]
        allowed = Path(relative).suffix in ('.py', '.js', '.svelte', '.json') or (
            relative.startswith('BE/templates/') and Path(relative).suffix == '.html')
        if not relative.startswith(('BE/', 'UI/')) or '..' in Path(relative).parts or not allowed:
            raise CatalogError('Review targets must be application source: ' + relative)
        path = owned(self.root, relative)
        contents = path.read_bytes()
        committed = git(self.root, 'show', self.revision + ':' + relative, optional=True)
        matches = committed is not None and committed == contents
        item = {'path': relative, 'sha256': sha(contents), 'base_revision': self.revision,
                'source_state': 'committed' if matches else 'working_tree',
                'repository_url': self.repository_url}
        if committed is not None:
            item['base_blob_sha256'] = sha(committed)
            item['base_blob_oid'] = git(self.root, 'rev-parse', self.revision + ':' + relative).decode().strip()
        self.files[relative] = item
        return item

    def reference(self, relative, start, end, symbol):
        item = dict(self.file(relative))
        lines = owned(self.root, relative).read_text().splitlines()
        if not isinstance(start, int) or not isinstance(end, int) or not 1 <= start <= end <= len(lines):
            raise CatalogError('Invalid source range: ' + relative)
        snippet = '\n'.join(lines[start - 1:end])
        if not symbol or symbol not in snippet:
            raise CatalogError('Source symbol absent: ' + relative + ':' + symbol)
        item.update(start=start, end=end, symbol=symbol, snippet_sha256=sha(snippet.encode()))
        if item['source_state'] == 'committed':
            item['commit_revision'] = self.revision
            if self.repository_url:
                item['commit_url'] = (self.repository_url + '/blob/' + self.revision + '/' + quote(relative)
                                      + '#L' + str(start) + '-L' + str(end))
        else:
            item['snapshot_reference'] = 'application/' + relative + '#L' + str(start) + '-L' + str(end)
        return item

    def symbol(self, relative, symbol):
        path = owned(self.root, relative)
        tree = ast.parse(path.read_text(), filename=relative)
        candidates = [node for node in ast.walk(tree)
                      if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == symbol]
        if len(candidates) != 1:
            raise CatalogError('Missing or ambiguous Python symbol: ' + relative + ':' + symbol)
        node = candidates[0]
        start = min([node.lineno] + [decorator.lineno for decorator in node.decorator_list])
        ref = self.reference(relative, start, node.end_lineno, symbol)
        ref['declarations'] = [ast.get_source_segment(path.read_text(), decorator) for decorator in node.decorator_list]
        return ref


def routes(sources):
    relative = 'UI/src/routes.js'
    text = owned(sources.root, relative).read_text()
    config_path = 'UI/vite.config.js'
    config = owned(sources.root, config_path).read_text()
    aliases = {match[1]: match[2] for match in re.finditer(
        r"['\"]([^'\"]+)['\"]:\s*path\.resolve\(projectRootDir,\s*['\"]([^'\"]+)['\"]\)", config)}
    sources.file(config_path)
    starts = list(re.finditer(r"^    ['\"]([^'\"]+)['\"]: wrap\(", text, re.M))
    result = {}
    for i, match in enumerate(starts):
        end = starts[i + 1].start() if i + 1 < len(starts) else len(text)
        block = text[match.start():end]
        start_line = text[:match.start()].count('\n') + 1
        end_line = text[:end].rstrip('\n').count('\n') + 1
        components = []
        for target in sorted(set(re.findall(r"\bimport\(['\"]([^'\"]+)['\"]\)", block))):
            if target.startswith('./'):
                path = 'UI/src/' + target[2:]
            else:
                alias, _, rest = target.partition('/')
                if alias not in aliases or not rest:
                    raise CatalogError('Unresolved route component: ' + target)
                path = 'UI/' + aliases[alias] + '/' + rest
            lines = owned(sources.root, path).read_text().splitlines()
            components.append(sources.reference(path, 1, len(lines), next(line for line in lines if line.strip())))
        result[match[1]] = {'route': match[1], 'source': sources.reference(relative, start_line, end_line, match[1]),
                            'components': components,
                            'permission_literals': sorted(set(re.findall(r"(?:canPerformAction|checkAssociationAccess)\(['\"]([^'\"]+)['\"]", block))),
                            'guards': sorted(set(re.findall(r'\b(isLogged|isPlanActive|isAssociation|checkAssociationAccess|canPerformAction)\(', block))),
                            'status': 'review_target'}
    return result


def endpoints(sources):
    """Resolve declared Django path/router targets without importing the app."""
    result = {}
    for file in sorted((sources.root / 'BE').rglob('urls.py')):
        relative = str(file.relative_to(sources.root))
        text = file.read_text()
        tree = ast.parse(text)
        imports = {}
        package = file.parent.relative_to(sources.root / 'BE').parts
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                base = package[:len(package) - node.level + 1] if node.level else ()
                module = '/'.join((*base, *node.module.split('.')))
                for alias in node.names:
                    imports[alias.asname or alias.name] = ('BE/' + module + '.py', alias.name)
        for call in ast.walk(tree):
            if not isinstance(call, ast.Call) or len(call.args) < 2:
                continue
            kind = 'path' if isinstance(call.func, ast.Name) and call.func.id == 'path' else (
                'router' if isinstance(call.func, ast.Attribute) and call.func.attr == 'register' else None)
            if not kind or not isinstance(call.args[0], ast.Constant) or not isinstance(call.args[0].value, str):
                continue
            target = call.args[1]
            if isinstance(target, ast.Call) and isinstance(target.func, ast.Attribute) and target.func.attr == 'as_view':
                target = target.func.value
            if not isinstance(target, ast.Name) or target.id not in imports:
                continue
            source_path, symbol = imports[target.id]
            endpoint = {'kind': kind, 'pattern': call.args[0].value,
                        'source': sources.reference(relative, call.lineno, call.end_lineno, call.args[0].value)}
            result.setdefault((source_path, symbol), []).append(endpoint)
    return result


def permission_rules(sources):
    relative = 'BE/application/permissions_registry.py'
    text = owned(sources.root, relative).read_text()
    tree = ast.parse(text)
    value = next(node.value for node in tree.body if isinstance(node, ast.Assign)
                 and any(isinstance(target, ast.Name) and target.id == 'PERMISSIONS_REGISTRY' for target in node.targets))
    result = []
    for key, permission in zip(value.keys, value.values):
        pattern = ast.literal_eval(key)
        method, pattern = pattern if isinstance(pattern, tuple) else ('*', pattern)
        result.append({'method': method, 'pattern': pattern, 'permission': ast.literal_eval(permission),
                       'source': sources.reference(relative, key.lineno, permission.end_lineno, pattern)})
    return result


def backlog(manual):
    category = ''
    result = []
    for line in (manual / 'SCREENSHOTS-NEEDED.md').read_text().splitlines():
        for prefix, folder in [('## Documentation Pages', 'docs'), ('## FAQ Pages', 'faq'), ('## Tutorial Pages', 'tutorials')]:
            if line.startswith(prefix):
                category = folder
        match = re.match(r'^\| `([^`]+\.mdx)` \| ([^|]+) \| ([^|]+) \| (.*) \|$', line)
        if match:
            result.append({'page': category + '/' + match[1], 'original_status': match[2].strip(),
                           'requested': match[3].strip(), 'description': match[4].strip(), 'status': 'review_target'})
    return result


def compile_catalog(code_root, manual_root, application_revision, manual_revision, mapping, manifest=None):
    code, manual = Path(code_root).resolve(), Path(manual_root).resolve()
    sources = Sources(code, application_revision)
    Sources(manual, manual_revision)  # Validate the actual companion Git commit.
    if mapping.get('format') != 1 or mapping.get('mapping_status') != 'review_targets':
        raise CatalogError('Unknown source mapping format/status')
    config = json.loads((manual / 'mint.json').read_text())
    navigation = sorted({page + '.mdx' for group in config['navigation'] for page in group['pages']})
    mapped_pages = {page['path']: page for page in mapping['pages']}
    if len(mapped_pages) != len(mapping['pages']) or set(mapped_pages) != set(navigation):
        raise CatalogError('Source mappings must cover exactly the complete navigation')
    route_map, endpoint_map, permissions = routes(sources), endpoints(sources), permission_rules(sources)
    domains = {}
    for name, domain in mapping['domains'].items():
        selected_routes = []
        for route in domain['routes']:
            if route not in route_map:
                raise CatalogError('Missing mapped UI route: ' + route)
            selected_routes.append(route_map[route])
        handlers, models = [], []
        for kind, target in [('handlers', handlers), ('models', models)]:
            for source in domain[kind]:
                for symbol in source['symbols']:
                    ref = sources.symbol(source['path'], symbol)
                    item = {'source': ref, 'status': 'review_target'}
                    if kind == 'handlers':
                        declared = endpoint_map.get((source['path'], symbol), [])
                        rules = []
                        for endpoint in declared:
                            pattern = re.sub(r'<[^>]+>', '*', endpoint['pattern']).strip('/')
                            rules.extend(rule for rule in permissions if rule['pattern'].strip('/') == pattern
                                         or endpoint['kind'] == 'router' and rule['pattern'].startswith(pattern + '/'))
                        item.update(endpoints=declared, permission_rules=rules,
                                    authorization_note='Registry entries concern collaborator permissions; inspect handler decorators, middleware and tenant scoping separately.')
                    target.append(item)
        discovered = {}
        for kind in ('ui_sources', 'templates'):
            discovered[kind] = []
            for relative in domain.get(kind, []):
                lines = owned(code, relative).read_text().splitlines()
                discovered[kind].append(sources.reference(relative, 1, len(lines), next(line for line in lines if line.strip())))
        domains[name] = {'routes': selected_routes, 'handlers': handlers, 'models': models, **discovered,
                         'constraints': domain.get('constraints', []), 'status': 'review_target'}
    spec = importlib.util.spec_from_file_location('manual_index_catalog', code / 'BE/application/manuale/index.py')
    index = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(index)
    reviewed = {page['path']: page for page in (manifest or {}).get('pages', [])}
    if manifest and (set(reviewed) != set(navigation) or len(reviewed) != len(manifest['pages'])
                     or manifest['metadata']['application_revision'] != application_revision
                     or manifest['metadata']['manual_revision'] != manual_revision):
        raise CatalogError('Manifest differs from the complete navigation or actual revisions')
    pages = []
    for relative in navigation:
        domain_names = mapped_pages[relative]['domains']
        if not domain_names or any(name not in domains for name in domain_names):
            raise CatalogError('Missing page domain: ' + relative)
        mdx = owned(manual, relative).read_text()
        available = index.sections(mdx)
        existing = {section['id']: section for section in reviewed.get(relative, {}).get('sections', [])}
        if set(existing) - set(available):
            raise CatalogError('Reviewed section was removed: ' + relative)
        sections = []
        for key, section in available.items():
            record = existing.get(key, {})
            content_hash = sha(section['mdx'].encode())
            if record.get('status') == 'verified' and record.get('content_sha256') != content_hash:
                raise CatalogError('Reviewed section text changed: ' + relative + '#' + key)
            sections.append({'id': key, 'title': section['title'], 'content_sha256': content_hash,
                             'status': record.get('status', 'pending'), 'scenario_ids': record.get('scenario_ids', []),
                             'reason': record.get('reason', '' if record.get('status') == 'verified' else 'No reviewed claim evidence and compatible scenario')})
        title = re.search(r'^title:\s*(.+?)\s*$', mdx, re.M)
        pages.append({'path': relative, 'title': title[1].strip().strip('\"\'') if title else relative,
                      'domains': domain_names, 'sections': sections,
                      'constraints': [constraint for name in domain_names for constraint in domains[name]['constraints']]})
        for section in reviewed.get(relative, {}).get('sections', []):
            for ref in section.get('evidence', []):
                if ref['sha256'] != sources.file(ref['path'])['sha256']:
                    raise CatalogError('Reviewed implementation changed: ' + ref['path'])
            section['evidence'] = [sources.reference(ref['path'], ref['start'], ref['end'], ref['symbol']) for ref in section.get('evidence', [])]
        if relative in reviewed:
            reviewed[relative]['source_map'] = {'domains': domain_names, 'status': 'review_targets'}
            reviewed[relative]['section_inventory'] = sections
            reviewed[relative]['constraints'] = pages[-1]['constraints']
            complete = bool(sections) and all(section['status'] == 'verified' for section in sections)
            reviewed[relative]['status'] = 'verified' if complete else 'partial'
            reviewed[relative]['reason'] = '' if complete else 'Other sections remain unverified or have explicit gaps'
    requests = backlog(manual)
    if len({entry['page'] for entry in requests}) != len(requests) or {entry['page'] for entry in requests} != set(navigation):
        raise CatalogError('Screenshot backlog must cover the complete navigation exactly once')
    result = {'format': 1, 'status': 'source_targets_validated', 'application_revision': application_revision,
              'manual_revision': manual_revision, 'mapping_sha256': sha(canonical(mapping).encode()),
              'domains': domains, 'pages': pages, 'screenshot_backlog': requests, 'files': sources.files,
              'manual_files': {relative: sha(owned(manual, relative).read_bytes())
                               for relative in ['mint.json', 'SCREENSHOTS-NEEDED.md', *navigation]},
              'summary': {'navigation_pages': len(pages), 'mapped_pages': len(pages), 'domains': len(domains),
                          'sections': sum(len(page['sections']) for page in pages),
                          'verified_sections': sum(section['status'] == 'verified' for page in pages for section in page['sections']),
                          'screenshot_backlog_entries': len(requests)}}
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', required=True)
    parser.add_argument('--preflight', action='store_true')
    args = parser.parse_args()
    run = Path(args.run).resolve()
    state = json.loads((run / 'run.json').read_text())
    mapping = json.loads((Path(state['application']) / 'docs/manuale/page-map.json').read_text())
    manifest = None if args.preflight else json.loads((run / 'manifest.json').read_text())
    catalog = compile_catalog(state['application'], state['manual'], state['application_input']['revision'],
                              state['manual_input']['revision'], mapping, manifest)
    output = run / ('source-catalog.preflight.json' if args.preflight else 'source-catalog.json')
    output.write_text(canonical(catalog) + '\n')
    if manifest is not None:
        manifest['metadata']['source_catalog'] = {'format': 1, 'path': output.name, 'sha256': sha(output.read_bytes())}
        (run / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
        coverage = json.loads((run / 'coverage.json').read_text())
        coverage.update(catalog['summary'])
        coverage['pending_sections'] = catalog['summary']['sections'] - catalog['summary']['verified_sections']
        coverage['verified_pages'] = sum(page['status'] == 'verified' for page in manifest['pages'])
        coverage['pending_pages'] = len(manifest['pages']) - coverage['verified_pages']
        coverage['pending'] = [page['path'] for page in manifest['pages'] if page['status'] != 'verified']
        (run / 'coverage.json').write_text(json.dumps(coverage, ensure_ascii=False, indent=2) + '\n')
    print(canonical(catalog['summary']))


if __name__ == '__main__':
    main()
