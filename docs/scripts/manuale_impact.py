#!/usr/bin/env python3
"""Deterministic change-impact analysis for the manual CI.

Maps changed repository paths to source -> behavior -> workflows -> pages and
decides how much of the manual pipeline must run:

    python3 docs/scripts/manuale_impact.py --base <rev> --head <rev> \
        [--changed-file PATH ...] --output impact.json

Every workflow's dependency set is the union of its registered recipe inputs
(docs/manuale/recipes.json), the shared capture inputs declared in
docs/manuale/impact-map.json, and the bounded transitive closure of literal
Python imports, string module/template references, JS/Svelte imports and UI
API calls (UI/endpoints.js -> Django URL table -> view module). URL/route
tables are "hubs": they are dependencies themselves but their imports are not
expanded, so routed code must be declared per workflow or be reachable from a
declared file. Anything the analysis cannot place fails closed (full
verification) with a diagnostic explaining which mapping is missing.
"""
import argparse
import ast
import fnmatch
import hashlib
import json
import os
import posixpath
import re
import subprocess
import sys
import time
from pathlib import Path

FORMAT = 1
ROOT = Path(__file__).resolve().parents[2]
MAP_PATH = 'docs/manuale/impact-map.json'
REGISTRY_PATH = 'docs/manuale/recipes.json'
EVIDENCE_PATH = 'manuale/.manuale-evidence.json'
MANUAL_ROOT = 'manuale/'
CATEGORIES = ('unrelated', 'docs-text', 'behavior', 'capture-tooling', 'shared-contract', 'bot', 'mcp', 'security',
              'unknown')
FAIL_CLOSED = ('unknown', 'security', 'shared-contract')
JS_SUFFIXES = ('.js', '.mjs', '.cjs', '.ts', '.svelte')
JS_RESOLVE = ('', '.js', '.mjs', '.cjs', '.ts', '.svelte', '.json', '/index.js', '/index.mjs', '/index.svelte',
              '/index.ts')
TEMPLATE_SUFFIXES = ('.html', '.txt', '.xml')
TOOLING_ROOTS = ('selfhost/tests/browser/', 'docs/manuale/', 'docs/scripts/')
WHOLE = '*'
MODULE = '<module>'

_JS_STATIC = re.compile(r'''\b(?:import|export)\s+(?:[\w$*{},\s]{1,2000}?\s*from\s*)?['"]([^'"\n]+)['"]''')
_JS_DYNAMIC = re.compile(r'''\bimport\s*\(\s*['"`]([^'"`\n$]+)['"`]\s*\)''')
_JS_REQUIRE = re.compile(r'''\brequire\s*\(\s*['"]([^'"\n]+)['"]\s*\)''')
_API_REFERENCE = re.compile(r'\bAPI((?:\.[A-Z][A-Z0-9_]*)+)')
_API_LITERAL = re.compile(r'''['"`](/api/[^'"`\s?#]*)''')
_TEMPLATE_TAG = re.compile(r'''{%\s*(?:extends|include)\s+['"]([^'"]+)['"]''')
_ENDPOINT_LEXER = re.compile(r'''//[^\n]*|/\*.*?\*/|`((?:[^`\\]|\\.)*)`|'((?:[^'\\\n]|\\.)*)'|"((?:[^"\\\n]|\\.)*)"|'''
                             r'''([A-Za-z_$][\w$]*)\s*:(?!:)|(\{)|(\})''', re.S)
_REVIEWED = re.compile(r'''["']((?:BE|UI)/[^"'\s]+)["']\s*:\s*["'][a-f0-9]{64}["']''')
_MDX_IMAGE = re.compile(r'''["'(]/?(images/[^"')\s]+)''')


# --------------------------------------------------------------------------- trees

class WorkTree:
    """The working tree at root: tracked plus untracked, non-ignored files."""

    def __init__(self, root):
        self.root = Path(root).resolve()
        self.label = 'worktree'
        try:
            names = subprocess.run(['git', '-C', str(self.root), 'ls-files', '--cached', '--others',
                                    '--exclude-standard', '-z'], check=True, capture_output=True).stdout.decode()
            names = [name for name in names.split('\0') if name]
        except (OSError, subprocess.CalledProcessError):
            names = []
            for directory, folders, files in os.walk(self.root):
                folders[:] = sorted(folder for folder in folders if folder not in ('.git', 'node_modules'))
                names.extend(Path(directory, file).relative_to(self.root).as_posix() for file in files)
        self.files = frozenset(name for name in names if (self.root / name).is_file())
        self._text = {}

    def signature(self):
        digest = hashlib.sha256()
        for name in sorted(self.files):
            stat = (self.root / name).stat()
            digest.update(f'{name}\0{stat.st_mtime_ns}\0{stat.st_size}\n'.encode())
        return digest.hexdigest()

    def exists(self, relative):
        return relative in self.files

    def read(self, relative):
        if relative not in self._text:
            try:
                self._text[relative] = (self.root / relative).read_text(errors='replace') if relative in self.files else None
            except OSError:
                self._text[relative] = None
        return self._text[relative]


class GitTree:
    """A committed revision, read through one `git cat-file --batch` process."""

    def __init__(self, root, revision):
        self.root = Path(root).resolve()
        self.revision = subprocess.run(['git', '-C', str(self.root), 'rev-parse', '--verify', revision + '^{commit}'],
                                       check=True, capture_output=True, text=True).stdout.strip()
        self.label = self.revision
        listing = subprocess.run(['git', '-C', str(self.root), 'ls-tree', '-r', '-z', self.revision],
                                 check=True, capture_output=True).stdout.decode()
        self._blobs = {}
        for line in filter(None, listing.split('\0')):
            meta, name = line.split('\t', 1)
            mode, kind, blob = meta.split()
            if kind == 'blob':
                self._blobs[name] = blob
        self.files = frozenset(self._blobs)
        self._text = {}
        self._process = None

    def signature(self):
        return self.revision

    def exists(self, relative):
        return relative in self._blobs

    def read(self, relative):
        if relative in self._text:
            return self._text[relative]
        value = None
        blob = self._blobs.get(relative)
        if blob:
            if self._process is None:
                self._process = subprocess.Popen(['git', '-C', str(self.root), 'cat-file', '--batch'],
                                                 stdin=subprocess.PIPE, stdout=subprocess.PIPE)
            self._process.stdin.write((blob + '\n').encode())
            self._process.stdin.flush()
            header = self._process.stdout.readline().decode().split()
            size = int(header[2])
            value = self._process.stdout.read(size).decode(errors='replace')
            self._process.stdout.read(1)
        self._text[relative] = value
        return value

    def close(self):
        if self._process is not None:
            self._process.stdin.close()
            self._process.stdout.close()
            self._process.wait()
            self._process = None

    def __del__(self):
        try:
            self.close()
        except Exception:  # noqa: BLE001 - interpreter shutdown
            pass


# --------------------------------------------------------------------------- patterns

def matches(path, pattern):
    """Prefix match; with wildcards an fnmatch (`*` spans '/'), still a prefix when ending in '/'."""
    if any(character in pattern for character in '*?['):
        return fnmatch.fnmatchcase(path, pattern + '*' if pattern.endswith('/') else pattern)
    return path.startswith(pattern)


def matches_any(path, patterns):
    return any(matches(path, pattern) for pattern in patterns)


def expand(tree, patterns):
    return {name for name in tree.files if matches_any(name, patterns)}


# --------------------------------------------------------------------------- graph

def _dotted_name(node):
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return '.'.join(reversed(parts))


class Graph:
    """Bounded, deterministic dependency graph over one tree."""

    def __init__(self, tree, impact_map):
        self.tree = tree
        self.map = impact_map
        python = impact_map['python']
        self.py_root = python['root'].rstrip('/')
        self.py_packages = set(python['packages'])
        self.template_roots = [root.rstrip('/') for root in python.get('template_roots', [])]
        self.template_roots += sorted({name.split('/templates/')[0] + '/templates' for name in tree.files
                                       if name.startswith(self.py_root + '/') and '/templates/' in name})
        self.template_roots = list(dict.fromkeys(self.template_roots))
        ui = impact_map['ui']
        self.ui_root = ui['root'].rstrip('/')
        self.ui_top = self.ui_root.split('/')[0] + '/'
        self.aliases = ui['aliases']
        self.hubs = impact_map.get('hubs', {})
        self.pure_decorators = set(python.get('pure_decorators', []))
        self._edges = {}
        self._py_info = {}
        self._api = None
        self._routes = None
        self.unresolved = {}  # candidate path -> importers (dangling imports, e.g. removed files)
        self._dotted = re.compile(r'^(?:%s)(?:\.[A-Za-z_]\w*)+$' % '|'.join(sorted(map(re.escape, self.py_packages))))

    # ---- generic
    # Nodes are (file, symbol). WHOLE follows the entire file; MODULE only the code
    # that runs on import (imports, module-level statements, definitions registered
    # by a decorator); any other symbol is the top-level function, class or
    # assignment an importer references. JS/Svelte/template nodes are always WHOLE.
    def edges(self, node, respect_hubs=True):
        key = (node, respect_hubs)
        if key in self._edges:
            return self._edges[key]
        relative, symbol = node
        hub = self.hubs.get(relative, {}).get('follow') if respect_hubs else None
        result = set()
        if hub != 'none' and self.tree.exists(relative):
            if relative.endswith('.py'):
                result = self._python_edges(relative, symbol)
            elif relative.endswith(JS_SUFFIXES):
                result = {(target, WHOLE) for target in self._js_edges(relative, static_only=hub == 'static')}
                if relative.startswith(self.ui_top):
                    result |= set(self.api_links(relative))
            elif relative.endswith(TEMPLATE_SUFFIXES) and any(relative.startswith(root + '/') for root in self.template_roots):
                result = {(target, WHOLE) for target in self._template_edges(relative)}
        result = sorted(edge for edge in result if edge[0] and edge != node)
        self._edges[key] = result
        return result

    def closure(self, seeds, respect_hubs=True):
        """Visited (file, symbol) nodes reachable from seeds (files or nodes)."""
        seen = set()
        stack = sorted((seed if isinstance(seed, tuple) else (seed, WHOLE)) for seed in seeds)
        stack.reverse()
        while stack:
            node = stack.pop()
            if node in seen or not self.tree.exists(node[0]):
                continue
            seen.add(node)
            stack.extend(edge for edge in reversed(self.edges(node, respect_hubs)) if edge not in seen)
        return seen

    def _missing(self, importer, candidates):
        for candidate in candidates:
            self.unresolved.setdefault(candidate, set()).add(importer)

    # ---- JavaScript / Svelte
    def _js_resolve(self, importer, specifier):
        specifier = specifier.split('?')[0].split('#')[0]
        if not specifier:
            return None, []
        if specifier.startswith('.'):
            base = posixpath.normpath(posixpath.join(posixpath.dirname(importer), specifier))
        else:
            head = specifier.split('/')[0]
            if importer.startswith(self.ui_top) and head in self.aliases:
                base = posixpath.normpath(self.aliases[head].rstrip('/') + specifier[len(head):])
            else:
                return None, []  # package import: covered by the lockfile global dependency
        if base.startswith('..'):
            return None, []
        candidates = [base + suffix for suffix in JS_RESOLVE]
        found = next((candidate for candidate in candidates if self.tree.exists(candidate)), None)
        return found, candidates

    def _js_edges(self, relative, static_only=False):
        text = self.tree.read(relative) or ''
        specifiers = list(_JS_STATIC.findall(text))
        if not static_only:
            specifiers += _JS_DYNAMIC.findall(text) + _JS_REQUIRE.findall(text)
        result = set()
        for specifier in specifiers:
            found, candidates = self._js_resolve(relative, specifier)
            if found:
                result.add(found)
            elif candidates:
                self._missing(relative, candidates)
        return result

    # ---- Python
    def _module_file(self, parts):
        if not parts:
            return None
        base = self.py_root + '/' + '/'.join(parts)
        for candidate in (base + '.py', base + '/__init__.py'):
            if self.tree.exists(candidate):
                return candidate
        return None

    def _absolute(self, relative, module, level):
        parts = module.split('.') if module else []
        if not level:
            return parts
        package = relative[len(self.py_root) + 1:].split('/')[:-1]
        if level - 1 > len(package):
            return None
        return package[:len(package) - (level - 1)] + parts

    def _parse(self, relative):
        text = self.tree.read(relative)
        if text is None:
            return None
        try:
            return ast.parse(text)
        except (SyntaxError, ValueError):
            return False

    def python_info(self, relative):
        """Per-file symbol table: bindings, star sources, symbol references, module-level references.

        Also records an AST fingerprint per top-level symbol (comments and
        formatting excluded) so a change can be narrowed to the symbols it touches.
        """
        if relative in self._py_info:
            return self._py_info[relative]
        tree = self._parse(relative)
        info = {'opaque': not tree, 'bindings': {}, 'stars': [], 'symbols': {}, 'module': set(), 'digests': {},
                'task_names': {}}
        self._py_info[relative] = info
        if not tree:
            if tree is False:
                text = self.tree.read(relative) or ''
                for module in re.findall(r'^\s*(?:from|import)\s+(\.*[A-Za-z_][\w.]*)', text, re.M):
                    level = len(module) - len(module.lstrip('.'))
                    parts = self._absolute(relative, module.lstrip('.'), level)
                    if parts:
                        info['module'].add(('import', ('mod', tuple(parts)), ()))
            return info

        def bind(statement, refs):
            if isinstance(statement, ast.Import):
                for alias in statement.names:
                    parts = tuple(alias.name.split('.'))
                    if alias.asname:
                        info['bindings'].setdefault(alias.asname, set()).add(('mod', parts))
                    else:
                        info['bindings'].setdefault(parts[0], set()).add(('mod', parts[:1]))
                    refs.add(('import', ('mod', parts), ()))
            else:
                parts = self._absolute(relative, statement.module, statement.level)
                if parts is None:
                    return
                parts = tuple(parts)
                for alias in statement.names:
                    if alias.name == '*':
                        info['stars'].append(parts)
                        refs.add(('import', ('mod', parts), ()))
                    else:
                        info['bindings'].setdefault(alias.asname or alias.name, set()).add(('from', parts, alias.name))
                        refs.add(('import', ('from', parts, alias.name), ()))

        def collect(nodes, refs):
            pending = list(nodes)
            while pending:
                node = pending.pop()
                if node is None:
                    continue
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    bind(node, refs)
                    continue
                if isinstance(node, ast.Attribute):
                    chain, base = [], node
                    while isinstance(base, ast.Attribute):
                        chain.append(base.attr)
                        base = base.value
                    if isinstance(base, ast.Name):
                        refs.add(('chain', base.id, tuple(reversed(chain))))
                        continue
                    pending.append(base)
                    continue
                if isinstance(node, ast.Name):
                    refs.add(('chain', node.id, ()))
                    continue
                if isinstance(node, ast.Constant) and isinstance(node.value, str) and len(node.value) < 200:
                    if self._dotted.match(node.value):
                        refs.add(('dotted', node.value, ()))
                    elif node.value.endswith(TEMPLATE_SUFFIXES) and '/' in node.value and ' ' not in node.value:
                        refs.add(('template', node.value, ()))
                    continue
                pending.extend(ast.iter_child_nodes(node))
            return refs

        def define(name, statement, refs):
            info['symbols'].setdefault(name, set()).update(refs)

        body = list(tree.body)
        index = 0
        while index < len(body):
            statement = body[index]
            index += 1
            if isinstance(statement, (ast.If, ast.Try)) or type(statement).__name__ == 'TryStar':
                collect([getattr(statement, 'test', None)], info['module'])
                body[index:index] = (statement.body + getattr(statement, 'orelse', []) +
                                     [line for handler in getattr(statement, 'handlers', []) for line in handler.body] +
                                     getattr(statement, 'finalbody', []))
                continue
            if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                define(statement.name, statement, collect([statement], set()))
                # Decorators, defaults, bases and class bodies belong to the symbol: they matter only
                # where the symbol is used. Only registering decorators make the body reachable on import.
                for decorator in statement.decorator_list:
                    call = decorator if isinstance(decorator, ast.Call) else None
                    dotted = _dotted_name(call.func if call else decorator)
                    for keyword in (call.keywords if call else []):
                        if keyword.arg == 'name' and isinstance(keyword.value, ast.Constant):
                            info['task_names'][str(keyword.value.value)] = statement.name
                    if dotted.split('.')[-1] in ('shared_task', 'task'):
                        module_name = relative[len(self.py_root) + 1:-3].replace('/', '.')
                        info['task_names'].setdefault(module_name + '.' + statement.name, statement.name)
                    # A decorator that registers the definition (signal receivers, template tags,
                    # admin) makes its body reachable on import; known pure wrappers do not.
                    if dotted not in self.pure_decorators and dotted.split('.')[-1] not in self.pure_decorators:
                        info['module'].add(('chain', statement.name, ()))
                continue
            if isinstance(statement, (ast.Import, ast.ImportFrom)):
                bind(statement, info['module'])
                continue
            refs = collect([statement], set())
            info['module'] |= refs
            targets = (statement.targets if isinstance(statement, ast.Assign) else
                       [statement.target] if isinstance(statement, (ast.AnnAssign, ast.AugAssign)) else [])
            for target in targets:
                for name in ast.walk(target):
                    if isinstance(name, ast.Name):
                        define(name.id, statement, refs)
        return info

    def digests(self, relative):
        """AST fingerprint per top-level symbol (comments and formatting excluded); None if unparseable."""
        info = self.python_info(relative)
        if info['opaque']:
            return None
        if not info['digests']:
            parts = {MODULE: []}
            body = list(self._parse(relative).body)
            index = 0
            while index < len(body):
                statement = body[index]
                index += 1
                if isinstance(statement, (ast.If, ast.Try)) or type(statement).__name__ == 'TryStar':
                    test = getattr(statement, 'test', None)
                    parts[MODULE].append(type(statement).__name__ + (ast.dump(test) if test else ''))
                    body[index:index] = (statement.body + getattr(statement, 'orelse', []) +
                                         [line for handler in getattr(statement, 'handlers', [])
                                          for line in [handler.type, *handler.body] if line is not None] +
                                         getattr(statement, 'finalbody', []))
                    continue
                dumped = ast.dump(statement)
                if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    parts.setdefault(statement.name, []).append(dumped)
                    continue
                parts[MODULE].append(dumped)
                targets = (statement.targets if isinstance(statement, ast.Assign) else
                           [statement.target] if isinstance(statement, (ast.AnnAssign, ast.AugAssign)) else [])
                for target in targets:
                    for name in ast.walk(target):
                        if isinstance(name, ast.Name):
                            parts.setdefault(name.id, []).append(dumped)
            info['digests'] = {name: hashlib.sha256('\n'.join(values).encode()).hexdigest()
                               for name, values in parts.items()}
        return info['digests']

    def _definers(self, relative, name, depth=0):
        """Files that provide `name` through star imports of relative."""
        info = self.python_info(relative)
        found = []
        for parts in info['stars']:
            source = self._module_file(list(parts))
            if not source:
                continue
            nested = self.python_info(source)
            if nested['opaque'] or name in nested['symbols'] or name in nested['bindings']:
                found.append(source)
            elif depth < 6 and self._definers(source, name, depth + 1):
                found.append(source)
        return found

    def _resolve_chain(self, relative, name, chain):
        """Nodes referenced by `name.chain...` inside relative."""
        info = self.python_info(relative)
        if name in info['symbols']:
            return {(relative, name)}
        if name not in info['bindings']:
            return {(source, name) for source in self._definers(relative, name)}
        result = set()
        for binding in sorted(info['bindings'][name]):
            if binding[0] == 'mod':
                module = list(binding[1])
            else:
                parts, original = list(binding[1]), binding[2]
                if parts and parts[0] not in self.py_packages:
                    continue
                if not self._module_file(parts + [original]):
                    target = self._module_file(parts)
                    if target:
                        result.add((target, original))
                    continue
                module = parts + [original]
            if module[0] not in self.py_packages:
                continue
            rest = list(chain)
            while rest and self._module_file(module + rest[:1]):
                module.append(rest.pop(0))
            target = self._module_file(module)
            if target:
                result.add((target, rest[0] if rest else WHOLE))
        return result

    def _import_nodes(self, relative, binding):
        """Nodes executed by an import statement (module code of every package on the path)."""
        if binding[0] == 'mod':
            parts, names = list(binding[1]), []
        else:
            parts, names = list(binding[1]), [binding[2]]
        if not parts or parts[0] not in self.py_packages:
            return set()
        result = set()
        for length in range(1, len(parts) + 1):
            found = self._module_file(parts[:length])
            if found:
                result.add((found, MODULE))
        if not self._module_file(parts):
            base = self.py_root + '/' + '/'.join(parts)
            self._missing(relative, [base + '.py', base + '/__init__.py'])
        for name in names:
            submodule = self._module_file(parts + [name])
            if submodule:
                result.add((submodule, MODULE))
        return result

    def _python_edges(self, relative, symbol):
        info = self.python_info(relative)
        if info['opaque']:
            refs = info['module']
            symbol = WHOLE
        elif symbol == WHOLE:
            refs = set(info['module']).union(*info['symbols'].values())
        elif symbol == MODULE:
            refs = info['module']
        elif symbol in info['symbols']:
            refs = info['symbols'][symbol]
        elif symbol in info['bindings']:
            return {(relative, MODULE)} | self._resolve_chain(relative, symbol, ())
        else:
            definers = self._definers(relative, symbol)
            if not definers:
                return {(relative, WHOLE)}
            return {(relative, MODULE)} | {(source, symbol) for source in definers}
        result = set() if symbol in (WHOLE, MODULE) else {(relative, MODULE)}
        for kind, value, chain in refs:
            if kind == 'import':
                result |= self._import_nodes(relative, value)
            elif kind == 'chain':
                result |= self._resolve_chain(relative, value, chain)
            elif kind == 'dotted':
                parts = value.split('.')
                rest = []
                while parts and not self._module_file(parts):
                    rest.insert(0, parts.pop())
                if parts:
                    result.add((self._module_file(parts), rest[0] if rest else WHOLE))
            elif kind == 'template':
                result |= {(target, WHOLE) for target in self._template_targets(value)}
        if info['opaque']:
            result = {(target, WHOLE) for target, _ in result}
        return result

    # ---- templates
    def _template_targets(self, name):
        return [root + '/' + name for root in self.template_roots if self.tree.exists(root + '/' + name)]

    def _template_edges(self, relative):
        text = self.tree.read(relative) or ''
        return [target for name in _TEMPLATE_TAG.findall(text) for target in self._template_targets(name)]

    # ---- UI -> API -> view links
    def _endpoints(self):
        if self._api is not None:
            return self._api
        self._api = {}
        text = self.tree.read(self.map['ui']['endpoints']) or ''
        stack, pending = [], None
        for match in _ENDPOINT_LEXER.finditer(text):
            template, single, double, key, opening, closing = match.groups()
            if key is not None:
                pending = key
            elif opening:
                stack.append(pending)
                pending = None
            elif closing:
                if stack:
                    stack.pop()
                pending = None
            elif template is not None or single is not None or double is not None:
                value = template if template is not None else single if single is not None else double
                if pending:
                    path = [part for part in stack if part] + [pending]
                    if 'API' in path:
                        self._api['.'.join(path[path.index('API') + 1:])] = value
                pending = None
        return self._api

    def _url_routes(self):
        if self._routes is not None:
            return self._routes
        self._routes = []
        self._walk_urlconf(self.map['ui'].get('root_urlconf', ''), '', 0)
        return self._routes

    @staticmethod
    def _route_regex(route, regex):
        if regex:
            return route.lstrip('^').rstrip('$')
        pattern = ''
        for literal, converter in re.findall(r'([^<]*)(<[^>]+>)?', route):
            pattern += re.escape(literal)
            if converter:
                pattern += '.+' if converter.startswith('<path:') else '[^/]+'
        return pattern

    def _walk_urlconf(self, relative, prefix, depth):
        tree = self._parse(relative) if relative else None
        if not tree or depth > 4:
            return
        routers = {}
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == 'register'
                    and isinstance(node.func.value, ast.Name) and len(node.args) >= 2
                    and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str)):
                for view in self._view_nodes(relative, node.args[1]):
                    routers.setdefault(node.func.value.id, []).append((node.args[0].value, view))
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id in ('path', 're_path') and len(node.args) >= 2
                    and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str)):
                continue
            route = prefix + self._route_regex(node.args[0].value, node.func.id == 're_path')
            target = node.args[1]
            if isinstance(target, ast.Call) and isinstance(target.func, ast.Name) and target.func.id == 'include':
                argument = target.args[0] if target.args else None
                if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
                    module = self._module_file(argument.value.split('.'))
                    if module:
                        self._walk_urlconf(module, route, depth + 1)
                elif (isinstance(argument, ast.Attribute) and argument.attr == 'urls'
                      and isinstance(argument.value, ast.Name)):
                    for register_prefix, view in routers.get(argument.value.id, []):
                        self._add_route(route + re.escape(register_prefix) + '(?:/.*)?', view)
                continue
            for view in self._view_nodes(relative, target):
                self._add_route(route, view)

    def _add_route(self, pattern, view):
        try:
            self._routes.append((re.compile(pattern), view))
        except re.error:
            pass

    def _view_nodes(self, relative, node):
        while isinstance(node, ast.Call):
            node = node.func
        chain = []
        while isinstance(node, ast.Attribute):
            chain.append(node.attr)
            node = node.value
        if not isinstance(node, ast.Name):
            return []
        return sorted(self._resolve_chain(relative, node.id, tuple(reversed(chain))))

    def resolve_url(self, url):
        prefix = self.map['ui'].get('api_prefix', '')
        # `${apiHost}/x` style hosts are the API prefix; other placeholders are path parameters.
        url = re.sub(r'\$\{[^}]*\}', 'X', re.sub(r'^\$\{[^}]*\}', prefix, url.split('?')[0]))
        if prefix and url.startswith(prefix):
            url = url[len(prefix):]
        url = url.lstrip('/')
        views = set()
        # Endpoints are often a prefix completed with an identifier at the call site.
        stem = url.rstrip('/')
        for candidate in sorted({url, stem, stem + '/', stem + '/X', stem + '/X/'}):
            for pattern, view in self._url_routes():
                if pattern.fullmatch(candidate):
                    views.add(view)
        return views

    def api_links(self, relative):
        key = ('api', relative)
        if key in self._edges:
            return self._edges[key]
        result = set()
        if relative.endswith(JS_SUFFIXES):
            text = self.tree.read(relative) or ''
            endpoints = self._endpoints()
            urls = [endpoints[name[1:]] for name in _API_REFERENCE.findall(text) if name[1:] in endpoints]
            urls += _API_LITERAL.findall(text)
            for url in urls:
                result |= self.resolve_url(url)
        self._edges[key] = sorted(result)
        return self._edges[key]


# --------------------------------------------------------------------------- context

class Context:
    """Everything derived from one tree: registry, graph, per-workflow dependencies."""

    def __init__(self, tree, impact_map=None):
        self.tree = tree
        self.map = impact_map or json.loads(tree.read(MAP_PATH) or 'null') or {}
        if self.map.get('format') != 1:
            raise ValueError('Missing or invalid ' + MAP_PATH)
        self.registry = json.loads(tree.read(REGISTRY_PATH) or 'null') or {}
        if self.registry.get('format') != 1:
            raise ValueError('Missing or invalid ' + REGISTRY_PATH)
        self.recipes = {recipe['id']: recipe for recipe in self.registry['recipes']}
        self.ids = sorted(self.recipes)
        self.graph = Graph(tree, self.map)
        self.global_seeds = set()
        for group in self.map.get('global_dependencies', []):
            symbol = MODULE if group.get('symbols') == 'module' else WHOLE
            self.global_seeds |= {(name, symbol) for name in expand(tree, group['match'])}
        self.global_seeds |= self._beat_seeds()
        self._dependencies = None
        self._runtime = None
        self._ui_reachable = None
        self._evidence = None
        self._reviewed = None
        self.all_pages = sorted(name for name in tree.files if name.startswith(MANUAL_ROOT) and name.endswith('.mdx')
                                and not name.startswith(MANUAL_ROOT + '_snippets/'))

    def _seeds(self, recipe):
        seeds = set()
        for relative in [*recipe.get('dependencies', []), *recipe.get('sources', []), recipe.get('script', '')]:
            if not relative:
                continue
            if self.tree.exists(relative):
                seeds.add(relative)
            else:
                seeds |= {name for name in self.tree.files if name.startswith(relative)}
        return seeds

    def _build(self):
        """Per-workflow closures, computed on first use (a base tree often needs none)."""
        if self._dependencies is not None:
            return
        self.global_nodes = self.graph.closure(self.global_seeds)
        self._dependencies, self._nodes, self._reverse = {}, {}, {}
        for identifier in self.ids:
            nodes = self.graph.closure(self._seeds(self.recipes[identifier])) | self.global_nodes
            self._nodes[identifier] = frozenset(nodes)
            self._dependencies[identifier] = frozenset(relative for relative, _ in nodes)
            for relative, symbol in nodes:
                self._reverse.setdefault(relative, {}).setdefault(symbol, set()).add(identifier)

    @property
    def dependencies(self):
        """workflow id -> files."""
        self._build()
        return self._dependencies

    @property
    def reverse(self):
        """file -> {symbol -> workflow ids}."""
        self._build()
        return self._reverse

    def _beat_seeds(self):
        """Periodic tasks run during captures: scheduled task bodies plus every task module's import code."""
        beat = self.map['python'].get('beat')
        if not beat:
            return set()
        modules = sorted(expand(self.tree, beat['task_modules']))
        seeds = {(module, MODULE) for module in modules}
        names = {}
        for module in modules:
            for name, symbol in self.graph.python_info(module)['task_names'].items():
                names.setdefault(name, set()).add((module, symbol))
        schedule = self.tree.read(beat['schedule']) or ''
        try:
            tree = ast.parse(schedule)
        except (SyntaxError, ValueError):
            return {(module, WHOLE) for module in modules}
        for node in ast.walk(tree):
            if isinstance(node, ast.Dict):
                for key, value in zip(node.keys, node.values):
                    if (isinstance(key, ast.Constant) and key.value == 'task' and isinstance(value, ast.Constant)
                            and isinstance(value.value, str)):
                        # An unresolvable scheduled name fails closed to every task body.
                        seeds |= names.get(value.value) or {(module, WHOLE) for module in modules}
        return seeds

    def chain(self, identifier, relative):
        """Shortest seed -> ... -> relative file path inside one workflow's graph (diagnostics)."""
        seeds = sorted({(seed, WHOLE) for seed in self._seeds(self.recipes[identifier])} | self.global_seeds)
        previous = {seed: None for seed in seeds}
        queue = list(seeds)
        for node in queue:
            if node[0] == relative:
                files = []
                while node is not None:
                    if not files or files[-1] != node[0]:
                        files.append(node[0])
                    node = previous[node]
                return files[::-1]
            for edge in self.graph.edges(node):
                if edge not in previous:
                    previous[edge] = node
                    queue.append(edge)
        return []

    def workflow_pages(self, identifiers):
        return {MANUAL_ROOT + page for identifier in identifiers for page in self.recipes[identifier].get('pages', [])}

    def dependents(self, relative, symbols=None):
        """Workflows depending on relative; `symbols` narrows a Python file to changed top-level symbols."""
        visited = self.reverse.get(relative, {})
        if symbols is None:
            result = set().union(*visited.values()) if visited else set()
        elif not symbols:
            result = set()  # syntax tree unchanged: no behavior can change
        else:
            result = set().union(set(), *(visited.get(symbol, ()) for symbol in {*symbols, WHOLE}))
        for importer in self.graph.unresolved.get(relative, ()):
            result |= set().union(set(), *self.reverse.get(importer, {}).values())
        # Explicit recipe paths that no longer exist (removed files) still bind.
        if not self.tree.exists(relative):
            for identifier, recipe in self.recipes.items():
                if relative in recipe.get('dependencies', []) or relative in recipe.get('sources', []):
                    result.add(identifier)
        return result

    def runtime_reachable(self):
        if self._runtime is None:
            python = self.map['python']
            entries = {name for name in expand(self.tree, python.get('runtime_entries', []))
                       if not matches_any(name, python.get('runtime_entry_exclude', []))}
            self._runtime = {relative for relative, _ in self.graph.closure(entries, respect_hubs=False)}
        return self._runtime

    def ui_reachable(self):
        if self._ui_reachable is None:
            self._ui_reachable = {relative for relative, _ in self.graph.closure({self.map['ui']['entry']}, respect_hubs=False)}
        return self._ui_reachable

    def evidence(self):
        if self._evidence is None:
            try:
                self._evidence = json.loads(self.tree.read(EVIDENCE_PATH) or '{}').get('sections', {})
            except ValueError:
                self._evidence = {}
        return self._evidence

    def module_pages(self, module):
        return {MANUAL_ROOT + key.split('#')[0] for key, value in self.evidence().items()
                if isinstance(value, dict) and value.get('recipe_module') == module}

    def reviewed_pages(self, relative):
        if self._reviewed is None:
            self._reviewed = {}
            for module in self.registry.get('reference_modules', []):
                for source in set(_REVIEWED.findall(self.tree.read(module) or '')):
                    self._reviewed.setdefault(source, set()).add(module)
        return {page for module in sorted(self._reviewed.get(relative, ())) for page in self.module_pages(module)}

    def rule(self, relative):
        for rule in self.map.get('rules', []):
            if matches_any(relative, rule['match']) and not matches_any(relative, rule.get('exclude', [])):
                return rule
        return None


_CONTEXTS = {}


def context(tree, impact_map=None):
    """Cached Context; `impact_map` applies another tree's policy (the head map is used for the base)."""
    policy = hashlib.sha256(json.dumps(impact_map, sort_keys=True).encode()).hexdigest() if impact_map else ''
    key = (str(tree.root), tree.signature(), policy)
    if key not in _CONTEXTS:
        if len(_CONTEXTS) > 8:
            _CONTEXTS.clear()
        _CONTEXTS[key] = Context(tree, impact_map)
    return _CONTEXTS[key]


def workflow_dependencies(root, workflow_id):
    """Sorted repo-relative paths of every file that can affect `workflow_id`'s captured behavior.

    Includes the registered recipe inputs, shared capture/runtime inputs from
    impact-map.json `global_dependencies`, and their transitive closure. Only
    files that exist in the working tree at root are returned. Raises KeyError
    for an unknown workflow id.
    """
    ctx = context(WorkTree(root))
    if workflow_id not in ctx.dependencies:
        raise KeyError('Unknown manual workflow: ' + str(workflow_id))
    return sorted(ctx.dependencies[workflow_id])


def all_workflow_dependencies(root):
    """{workflow_id: workflow_dependencies(root, workflow_id)} computed in one pass."""
    ctx = context(WorkTree(root))
    return {identifier: sorted(ctx.dependencies[identifier]) for identifier in ctx.ids}


# --------------------------------------------------------------------------- classification

def _entry(path, category, workflows=(), reason='', pages=(), checks=()):
    return {'path': path, 'category': category, 'workflows': sorted(set(workflows)), 'reason': reason,
            '_pages': set(pages), '_checks': set(checks)}


def _short(items, limit=6):
    items = sorted(items)
    return ', '.join(items[:limit]) + (f' (+{len(items) - limit} more)' if len(items) > limit else '')


def _json(tree, relative):
    try:
        return json.loads(tree.read(relative) or 'null')
    except ValueError:
        return None


def _unknown(path, status):
    if path.startswith(('BE/', 'UI/')):
        hint = ('add it to the `sources`/`dependencies` of the workflow(s) that exercise it in '
                'docs/manuale/*-authored-workflows.mjs (then run node docs/scripts/manuale-registry.mjs), make it '
                'reachable from a declared file, or declare a rule in ' + MAP_PATH +
                ' if it provably cannot affect captures')
    else:
        hint = 'add a rule for this path (or its top-level area) to ' + MAP_PATH + ' with a category and reason'
    return _entry(path, 'unknown', reason=f'Unknown production dependency{status}: no workflow closure or impact '
                                          f'rule covers it; full verification required. To fix, {hint}.')


def _classify(head, base, path):
    in_head = head.tree.exists(path)
    in_base = base.tree.exists(path) if base else None
    status = (' (added)' if base and in_head and not in_base else ' (removed)' if not in_head and in_base else
              ' (absent from the analysed tree)' if not in_head else '')
    contexts = [ctx for ctx in (head, base) if ctx]
    symbols = _changed_symbols(head, base, path) if in_head and in_base else None
    # Base-side closures matter only for paths gone from head (removed or renamed away): a
    # modified file's former importers are changed files themselves and classified at head.
    dependents = head.dependents(path, symbols) | (base.dependents(path) if base and not in_head else set())
    narrowed = f' Changed symbols: {_short(symbols, 4)}.' if symbols else ''
    reviewed = set().union(*(ctx.reviewed_pages(path) for ctx in contexts))
    rule = head.rule(path) or (base.rule(path) if base else None)
    category = rule['category'] if rule else None
    reason = rule['reason'] if rule else ''
    checks = set(rule.get('checks', [])) if rule else set()
    handler = rule.get('handler') if rule else None

    if category in ('security', 'shared-contract'):
        return _entry(path, category, dependents, reason + status, head.workflow_pages(dependents) | reviewed, checks)
    if dependents and handler not in ('recipes-registry', 'evidence-bindings', 'manual-image'):
        tooling = path.startswith(TOOLING_ROOTS) or category == 'capture-tooling'
        extra = f' {reason}' if category in ('bot', 'mcp') else ''
        example = min(dependents)
        path_chain = next((ctx.chain(example, path) for ctx in contexts if example in ctx.recipes), [])
        if len(path_chain) > 1:
            extra = f' Via {example}: {" -> ".join(path_chain)}.' + extra
        if category in ('bot', 'mcp'):
            checks.add(category)
        return _entry(path, 'capture-tooling' if tooling else 'behavior', dependents,
                      f'In the dependency closure of {len(dependents)} workflow(s){status}: {_short(dependents)}.'
                      f'{narrowed}{extra}',
                      head.workflow_pages(dependents) | reviewed, checks)
    if symbols is not None and not dependents and head.reverse.get(path):
        if reviewed:
            return _reviewed(path, status, reviewed)
        detail = (f'changed top-level symbol(s) {_short(symbols, 4)} are not referenced by any workflow\'s '
                  'dependency slice' if symbols else 'the syntax tree is unchanged (comments or formatting only)')
        return _entry(path, 'unrelated', (), f'Python module used by captured workflows, but {detail}.')
    if handler:
        return _HANDLERS[handler](head, base, path, rule, status, reviewed, checks)
    if category in ('capture-tooling', 'behavior'):
        everything = rule.get('scope') == 'all' or not dependents
        workflows = head.ids if everything else dependents
        return _entry(path, category, workflows, reason + status, head.workflow_pages(workflows), checks)
    if category and category != 'unrelated':
        return _entry(path, category, (), reason + status, reviewed, checks)
    if reviewed:
        return _reviewed(path, status, reviewed)
    if category == 'unrelated':
        return _entry(path, 'unrelated', (), reason + status)
    python_root = head.map['python']['root'].rstrip('/') + '/'
    if path.startswith(python_root) and path.endswith('.py'):
        reachable = [ctx for ctx in contexts if ctx.tree.exists(path) and path in ctx.runtime_reachable()]
        if not reachable and (in_head or base):
            return _entry(path, 'unrelated', (), 'Backend module not importable from any runtime entry point '
                          f'(dead code; any future importer is classified on its own){status}.')
    return _unknown(path, status)


def _changed_symbols(head, base, path):
    """Top-level Python symbols whose AST changed; None means the whole file (module code changed)."""
    if not path.endswith('.py'):
        return None
    current, previous = head.graph.digests(path), base.graph.digests(path)
    if current is None or previous is None:
        return None
    changed = {name for name in set(current) | set(previous) if current.get(name) != previous.get(name)}
    if MODULE in changed:
        return None
    return changed


def _reviewed(path, status, pages):
    return _entry(path, 'docs-text', (), f'Source of reviewed prose contracts{status}; recompile and re-verify '
                  f'{len(pages)} page(s) from reused evidence.', pages)


def _recipes_registry(head, base, path, rule, status, reviewed, checks):
    current, previous = _json(head.tree, path), _json(base.tree, path) if base else None
    if not current or not previous:
        return _entry(path, 'capture-tooling', head.ids, 'Workflow registry changed without a comparable base '
                      f'revision{status}; every workflow is recaptured.', head.all_pages)
    old = {recipe['id']: recipe for recipe in previous.get('recipes', [])}
    new = {recipe['id']: recipe for recipe in current.get('recipes', [])}
    changed = sorted(identifier for identifier in new if old.get(identifier) != new[identifier])
    removed = sorted(set(old) - set(new))
    derived = ('recipes', 'reference_dependencies', 'reference_modules')
    other = sorted(key for key in set(current) | set(previous)
                   if key not in derived and current.get(key) != previous.get(key))
    if set(other) - {'shared_dependencies', 'non_capture_dependencies'}:
        return _entry(path, 'capture-tooling', head.ids, f'Workflow registry format keys changed ({", ".join(other)}); '
                      'every workflow is recaptured.', head.all_pages)
    note = (f'; removed workflow(s): {_short(removed)}' if removed else '') + (
        f'; legacy selection keys changed: {", ".join(other)}' if other else '')
    if changed:
        return _entry(path, 'capture-tooling', changed, f'Registry entries changed for {len(changed)} workflow(s): '
                      f'{_short(changed)}{note}.', head.workflow_pages(changed))
    return _entry(path, 'docs-text' if note or current != previous else 'unrelated', (),
                  'Registry workflow entries unchanged' + (note or '; only derived reference metadata changed') + '.')


def _evidence_bindings(head, base, path, rule, status, reviewed, checks):
    current = (_json(head.tree, path) or {}).get('sections', {})
    previous = (_json(base.tree, path) or {}).get('sections', {}) if base else None
    if previous is None:
        pages = {MANUAL_ROOT + key.split('#')[0] for key in current}
        return _entry(path, 'docs-text', (), 'Evidence bindings changed without a base revision; every bound page is '
                      're-verified.', pages)
    keys = sorted(key for key in set(current) | set(previous) if current.get(key) != previous.get(key))
    pages = {MANUAL_ROOT + key.split('#')[0] for key in keys}
    return _entry(path, 'docs-text', (), f'{len(keys)} section binding(s) changed on {len(pages)} page(s).', pages)


def _image_owners(ctx, image):
    return {identifier for identifier, recipe in ctx.recipes.items()
            if recipe.get('capture_prefix') and image.startswith(recipe['capture_prefix'])}


def _manual_page(head, base, path, rule, status, reviewed, checks):
    page = path[len(MANUAL_ROOT):]
    bound = sorted(identifier for identifier, recipe in head.recipes.items() if page in recipe.get('pages', []))
    text = head.tree.read(path) or ''
    missing = sorted({image for image in _MDX_IMAGE.findall(text) if not head.tree.exists(MANUAL_ROOT + image)})
    owners = set().union(*(_image_owners(head, image) for image in missing)) if missing else set()
    if owners:
        return _entry(path, 'behavior', owners, f'Page references {len(missing)} screenshot(s) that no evidence '
                      f'provides yet ({_short(missing, 3)}); the owning workflow(s) must capture them{status}.',
                      {path} | head.workflow_pages(owners))
    note = f' Bound workflow screenshots are reused: {_short(bound)}.' if bound else ''
    broken = f' Unresolved image reference(s) fail compilation: {_short(missing, 3)}.' if missing else ''
    return _entry(path, 'docs-text', (), f'Manual page text{status}; evidence bindings unaffected, recompile only.'
                  f'{note}{broken}', {path} if in_any(head, base, path) else set())


def in_any(head, base, path):
    return head.tree.exists(path) or bool(base and base.tree.exists(path))


def _manual_snippet(head, base, path, rule, status, reviewed, checks):
    stem = posixpath.splitext(posixpath.basename(path))[0]
    pages = {page for page in head.all_pages if re.search(r'snippets/' + re.escape(stem) + r'(?:\.mdx)?\b',
                                                           head.tree.read(page) or '')}
    return _entry(path, 'docs-text', (), f'Shared snippet{status} included by {len(pages)} page(s).', pages)


def _manual_image(head, base, path, rule, status, reviewed, checks):
    image = path[len(MANUAL_ROOT):]
    owners = _image_owners(head, image) | (_image_owners(base, image) if base else set())
    if owners:
        return _entry(path, 'behavior', owners, f'Captured screenshot output changed outside capture{status}; '
                      f'recapture re-proves it: {_short(owners)}.', head.workflow_pages(owners))
    pages = {page for page in head.all_pages if image in (head.tree.read(page) or '')}
    return _entry(path, 'docs-text', (), f'Static manual image{status} not owned by any capture; referencing pages '
                  'are recompiled.', pages)


def _reference_module(head, base, path, rule, status, reviewed, checks):
    modules = set(head.registry.get('reference_modules', [])) | (
        set(base.registry.get('reference_modules', [])) if base else set())
    if path not in modules:
        return _entry(path, 'capture-tooling', head.ids, 'Recipe module is neither a registered reference module nor '
                      f'imported by a workflow{status}; fails closed to every workflow. Register it in '
                      'docs/manuale/editorial-contracts.mjs or a workflow definition.', head.all_pages)
    pages = head.module_pages(path) | (base.module_pages(path) if base else set())
    return _entry(path, 'docs-text', (), f'{rule["reason"]}{status} Pages: {len(pages)}.', pages)


def _workflow_definition(head, base, path, rule, status, reviewed, checks):
    found = set()
    for ctx in (head, base):
        if ctx:
            text = ctx.tree.read(path) or ''
            found |= {identifier for identifier in ctx.ids
                      if re.search(r'''['"]%s['"]\s*:''' % re.escape(identifier), text)}
    found &= set(head.ids) | (set(base.ids) if base else set())
    if not found:
        return _entry(path, 'capture-tooling', head.ids, 'Workflow definition module without a recognizable '
                      f'registered workflow{status}; fails closed to every workflow.', head.all_pages)
    found &= set(head.ids)
    return _entry(path, 'capture-tooling', found, f'Defines workflow(s){status}: {_short(found)}.',
                  head.workflow_pages(found))


def _all_pages(head, base, path, rule, status, reviewed, checks):
    return _entry(path, 'docs-text', (), rule['reason'] + status, head.all_pages)


def _ui_outside(head, base, path, rule, status, reviewed, checks):
    if reviewed:
        return _reviewed(path, status, reviewed)
    if not path.endswith(JS_SUFFIXES + ('.css', '.scss', '.json')) and head.tree.exists(path):
        bundled = path in head.ui_reachable()
    else:
        bundled = path in head.ui_reachable() or bool(base and path in base.ui_reachable())
    detail = ('bundled only for routes or components that no workflow declares or reaches'
              if bundled else 'not reachable from the UI entry point (not bundled)')
    return _entry(path, 'unrelated', (), f'{rule["reason"]} Module is {detail}{status}.')


_HANDLERS = {
    'recipes-registry': _recipes_registry, 'evidence-bindings': _evidence_bindings, 'manual-page': _manual_page,
    'manual-snippet': _manual_snippet, 'manual-image': _manual_image, 'reference-module': _reference_module,
    'workflow-definition': _workflow_definition, 'all-pages': _all_pages, 'ui-outside-closures': _ui_outside,
}


# --------------------------------------------------------------------------- report

def analyze(root, changed, *, base=None, head=None):
    """Impact report (format 1) for repo-relative `changed` paths.

    `head` (a git revision) selects the analysed tree; None analyses the working
    tree at root. `base` (a git revision) enables base-side dependency lookup
    for removed/renamed files and per-entry registry/binding comparisons.
    """
    root = Path(root).resolve()
    head_tree = GitTree(root, head) if head else WorkTree(root)
    notes = []
    base_tree = None
    if base:
        try:
            base_tree = GitTree(root, base)
        except (OSError, subprocess.CalledProcessError):
            # E.g. a shallow checkout: head-only analysis is coarser (whole files, no base-side
            # dependents for removed paths) and therefore still conservative.
            notes.append(f'Base revision {base} is unavailable; analysed the head tree only.')
    return analyze_trees(head_tree, base_tree, changed, notes=notes)


def analyze_trees(head_tree, base_tree, changed, *, notes=()):
    """analyze() over explicit trees (e.g. a retained baseline snapshot directory as base)."""
    head_ctx = context(head_tree)
    base_ctx = None
    if base_tree is not None:
        try:
            base_ctx = context(base_tree, head_ctx.map)
        except ValueError:
            base_ctx = None  # no comparable registry at base: head-only analysis stays conservative
    paths = sorted({posixpath.normpath(str(path)).lstrip('/') for path in changed if str(path).strip()})
    files = [_classify(head_ctx, base_ctx, path) for path in paths]
    categories = {entry['category'] for entry in files}
    full = bool(categories & set(FAIL_CLOSED))
    if full:
        mode = 'full'
    elif categories & {'behavior', 'capture-tooling'}:
        mode = 'behavioral'
    elif 'docs-text' in categories:
        mode = 'content'
    else:
        mode = 'unaffected'
    affected = set(head_ctx.ids) if full else {identifier for entry in files
                                               if entry['category'] in ('behavior', 'capture-tooling')
                                               for identifier in entry['workflows'] if identifier in head_ctx.recipes}
    pages = set(head_ctx.all_pages) if full else set().union(*(entry['_pages'] for entry in files)) if files else set()
    pages |= head_ctx.workflow_pages(affected)
    pages = {page for page in pages if head_ctx.tree.exists(page)}
    extra = set().union(*(entry['_checks'] for entry in files)) if files else set()
    corpus = mode != 'unaffected'
    checks = {'bot': full or corpus or 'bot' in categories or 'bot' in extra,
              'mcp': full or corpus or 'mcp' in categories or 'mcp' in extra,
              'security': full or 'security' in categories,
              'runtime': mode in ('behavioral', 'full')}
    reasons = list(notes)
    if not paths:
        reasons.append('No changed files.')
    counts = {category: sum(entry['category'] == category for entry in files) for category in CATEGORIES}
    if paths:
        reasons.append(f'{len(paths)} changed file(s): ' + ', '.join(f'{count} {category}' for category, count
                                                                    in counts.items() if count) + '.')
    for category in FAIL_CLOSED:
        flagged = [entry['path'] for entry in files if entry['category'] == category]
        if flagged:
            reasons.append(f'Full verification required by {category} file(s): {_short(flagged)}.')
    if not full and affected:
        reasons.append(f'{len(affected)} of {len(head_ctx.ids)} workflow(s) recaptured; '
                       f'{len(head_ctx.ids) - len(affected)} reused from verified evidence.')
    elif mode in ('content', 'unaffected'):
        reasons.append(f'No recapture: all {len(head_ctx.ids)} workflow(s) reuse verified evidence.')
    if mode == 'content':
        reasons.append(f'{len(pages)} page(s) recompiled from reused evidence.')
    for category in ('bot', 'mcp'):
        if category in categories:
            reasons.append(f'{category.upper() if category == "mcp" else "Bot"} contract checks required.')
    return {'format': FORMAT, 'mode': mode, 'full_verification_required': full,
            'affected_workflows': sorted(affected), 'reused_workflows': sorted(set(head_ctx.ids) - affected),
            'affected_pages': sorted(pages), 'checks': checks,
            'files': [{key: value for key, value in entry.items() if not key.startswith('_')} for entry in files],
            'reasons': reasons}


def changed_files(root, base, head=None):
    """Paths changed between merge-base(base, head) and head (or the working tree).

    Renames are reported as both the old (removed) and the new (added) path.
    """
    root = str(Path(root).resolve())
    if head:
        spec = [f'{base}...{head}']
    else:
        merge_base = subprocess.run(['git', '-C', root, 'merge-base', base, 'HEAD'], check=True,
                                    capture_output=True, text=True).stdout.strip()
        spec = [merge_base]
    output = subprocess.run(['git', '-C', root, 'diff', '--name-status', '-z', '-M', *spec, '--'],
                            check=True, capture_output=True).stdout.decode()
    fields = output.split('\0')
    paths = set()
    index = 0
    while index < len(fields) and fields[index]:
        status = fields[index]
        count = 2 if status[0] in 'RC' else 1
        paths.update(fields[index + 1:index + 1 + count])
        index += 1 + count
    if not head:
        untracked = subprocess.run(['git', '-C', root, 'ls-files', '--others', '--exclude-standard', '-z'],
                                   check=True, capture_output=True).stdout.decode()
        paths.update(filter(None, untracked.split('\0')))
    return sorted(paths)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('--base', help='Base git revision (the merge base with --head is used).')
    parser.add_argument('--head', help='Head git revision; default: the working tree.')
    parser.add_argument('--changed-file', action='append', default=[], metavar='PATH',
                        help='Additional changed repository path (repeatable).')
    parser.add_argument('--output', help='Write the JSON report here (default: stdout).')
    parser.add_argument('--root', default=str(ROOT), help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    started = time.monotonic()
    changed = list(args.changed_file)
    if args.base:
        changed += changed_files(args.root, args.base, args.head)
    elif args.head:
        parser.error('--head requires --base')
    report = analyze(Path(args.root), changed, base=args.base, head=args.head)
    text = json.dumps(report, indent=2, ensure_ascii=False) + '\n'
    if args.output:
        Path(args.output).write_text(text)
    else:
        sys.stdout.write(text)
    print(f'manual impact: mode={report["mode"]} affected={len(report["affected_workflows"])} '
          f'reused={len(report["reused_workflows"])} pages={len(report["affected_pages"])} '
          f'files={len(report["files"])} in {time.monotonic() - started:.2f}s', file=sys.stderr)
    return 0


if __name__ == '__main__':
    sys.exit(main())
