#!/usr/bin/env python3
"""Warm the Go build cache for the commands a TESTS adapter would run.

Adapter scripts call ``go test`` with a unique ``-tags`` value for nearly every
case family.  Each distinct tag set forces the race-instrumented package to be
compiled again.  This helper statically reads the adapter sources reachable
from the given entry files, collects every fully literal ``['go', 'test', ...]``
command, reduces it to the flags and packages that affect compilation, and runs
``go test -c -o <tmp>/ <flags> <packages>`` for every distinct
combination in parallel.

It only compiles: it never runs, skips or weakens a test.  A failed prewarm
build is reported but never fails the job; the real adapter command that needs
the build reports its own error.  Exit status is non-zero only for usage errors.
"""
from __future__ import annotations

import ast
import concurrent.futures
import dataclasses
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_GO_DIR = 'BE_GO'
TESTS_PACKAGE = 'TESTS'

_KEEP_VALUE_FLAGS = frozenset({'-tags', '-gcflags', '-ldflags'})
_KEEP_BOOL_FLAGS = frozenset({'-race'})
_DROP_VALUE_FLAGS = frozenset({
    '-run', '-bench', '-count', '-timeout', '-skip', '-benchtime', '-shard',
    '-parallel', '-cpu', '-coverprofile', '-covermode', '-mod', '-p', '-vet',
    '-exec', '-o', '-outputdir', '-blockprofile', '-memprofile',
    '-mutexprofile', '-trace',
})
_DROP_BOOL_FLAGS = frozenset({
    '-json', '-v', '-failfast', '-short', '-cover', '-coverpkg', '-msan',
    '-asan', '-trimpath', '-work', '-x', '-n',
})
_HOST_PATH = re.compile(r'^[A-Za-z0-9_.-]+(?:\.[A-Za-z0-9_.-]+)+/')
_MISSING = object()


@dataclasses.dataclass(frozen=True)
class Build:
    """A distinct (flags, packages, cwd) compilation key."""

    flags: tuple[str, ...]
    packages: tuple[str, ...]
    cwd: str = DEFAULT_GO_DIR

    @property
    def key(self) -> str:
        return ' '.join((*self.flags, *self.packages))

    @property
    def label(self) -> str:
        return f'{self.cwd}: {self.key}'


@dataclasses.dataclass(frozen=True)
class SkippedCommand:
    path: Path
    lineno: int
    reason: str


@dataclasses.dataclass(frozen=True)
class Extraction:
    builds: tuple[Build, ...]
    skipped: tuple[SkippedCommand, ...]
    sources: tuple[Path, ...]


@dataclasses.dataclass(frozen=True)
class BuildResult:
    build: Build
    seconds: float
    ok: bool
    output: str

    def tail(self, lines: int = 5) -> list[str]:
        content = [line for line in self.output.rstrip().splitlines() if line.strip()]
        return content[-lines:]


def _literal_path_parts(node: ast.AST) -> list[str] | None:
    """Return the constant segments of a ``ROOT / 'BE_GO'``-style expression."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [node.value]
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        left = _literal_path_parts(node.left)
        right = _literal_path_parts(node.right)
        if left is None or right is None:
            return None
        return left + right
    if isinstance(node, ast.Name) and node.id == 'ROOT':
        return []
    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id in {'Path', 'PurePath'} and len(node.args) == 1):
        return _literal_path_parts(node.args[0])
    return None


def _command_cwd(call: ast.Call) -> str:
    for keyword in call.keywords:
        if keyword.arg != 'cwd':
            continue
        parts = _literal_path_parts(keyword.value)
        if parts is None:
            return DEFAULT_GO_DIR
        if not parts:
            return '.'
        return Path(*parts).as_posix()
    return DEFAULT_GO_DIR


def _module_name_for(path: Path, root: Path) -> str:
    try:
        relative = path.resolve().relative_to(root)
    except ValueError:
        return ''
    parts = list(relative.parts)
    if not parts:
        return ''
    if parts[-1] == '__init__.py':
        parts = parts[:-1]
    elif parts[-1].endswith('.py'):
        parts[-1] = parts[-1][:-3]
    else:
        return ''
    if not parts:
        return ''
    return '.'.join(parts)


def _module_file(root: Path, module: str) -> Path | None:
    if not module:
        return None
    base = root.joinpath(*module.split('.'))
    for candidate in (base.with_suffix('.py'), base / '__init__.py'):
        if candidate.is_file():
            return candidate
    return None


def _package_of(module: str, path: Path) -> str:
    if path.name == '__init__.py':
        return module
    return module.rpartition('.')[0]


def _resolve_relative(module: str, path: Path, level: int, target: str) -> str:
    package = _package_of(module, path)
    if not package:
        return ''
    parts = package.split('.')
    remove = max(level - 1, 0)
    if remove > len(parts):
        return ''
    if remove:
        parts = parts[:-remove]
    if target:
        parts = parts + target.split('.')
    return '.'.join(part for part in parts if part)


def _module_prefixes(module: str) -> set[str]:
    parts = module.split('.')
    return {'.'.join(parts[:index]) for index in range(1, len(parts) + 1)}


def _imported_modules(tree: ast.AST, module: str, path: Path, root: Path) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                name = alias.name
                if name == TESTS_PACKAGE or name.startswith(TESTS_PACKAGE + '.'):
                    found.update(_module_prefixes(name))
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ''
            if node.level:
                base = _resolve_relative(module, path, node.level, base)
            if not base or not (base == TESTS_PACKAGE or base.startswith(TESTS_PACKAGE + '.')):
                continue
            found.update(_module_prefixes(base))
            for alias in node.names:
                if alias.name == '*':
                    continue
                candidate = f'{base}.{alias.name}'
                if _module_file(root, candidate):
                    found.add(candidate)
    return {name for name in found
            if name == TESTS_PACKAGE or name.startswith(TESTS_PACKAGE + '.')}


def collect_sources(entries, root: Path | None = None) -> dict[Path, str]:
    """Map every TESTS source file reachable from ``entries`` to its module name."""
    root = (root or REPOSITORY_ROOT).resolve()
    sources: dict[Path, str] = {}
    seen: set[Path] = set()
    pending: list[tuple[str, Path]] = []
    for entry in entries:
        path = Path(entry).resolve()
        if not path.is_file():
            raise FileNotFoundError(str(entry))
        pending.append((_module_name_for(path, root), path))
    while pending:
        module, path = pending.pop()
        if path in seen:
            continue
        seen.add(path)
        sources[path] = module
        try:
            tree = ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
        except (OSError, SyntaxError, UnicodeDecodeError):
            continue
        for name in sorted(_imported_modules(tree, module, path, root)):
            target = _module_file(root, name)
            if target is not None and target not in seen:
                pending.append((name, target))
    return sources


def _module_paths(root: Path) -> set[str]:
    paths: set[str] = set()
    candidates = [root / 'go.mod', *sorted(root.glob('*/go.mod'))]
    for gomod in candidates:
        try:
            text = gomod.read_text(encoding='utf-8')
        except OSError:
            continue
        for line in text.splitlines():
            line = line.strip()
            if line.startswith('module '):
                paths.add(line.split(None, 1)[1].strip())
    return paths


def _is_package(token: str, module_paths: set[str]) -> bool:
    if not token or token.startswith('-'):
        return False
    if token in {'.', '..', '...'}:
        return True
    if token.startswith('./') or token.startswith('../'):
        return True
    for module in module_paths:
        if token == module or token.startswith(module + '/'):
            return True
    return bool(_HOST_PATH.match(token))


def _literal_strings(node: ast.AST):
    if not isinstance(node, (ast.List, ast.Tuple)):
        return _MISSING
    values: list[str] = []
    for element in node.elts:
        if isinstance(element, ast.Constant) and isinstance(element.value, str):
            values.append(element.value)
        else:
            return None
    return values


def _visible_prefix(node: ast.AST) -> list[str]:
    if not isinstance(node, (ast.List, ast.Tuple)):
        return []
    prefix: list[str] = []
    for element in node.elts:
        if isinstance(element, ast.Constant) and isinstance(element.value, str):
            prefix.append(element.value)
        else:
            break
    return prefix


def _build_from(tokens: list[str], cwd: str, module_paths: set[str]) -> Build | None:
    flags: list[str] = []
    packages: set[str] = set()
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token == '-args':
            break
        if not token.startswith('-') or token == '-':
            if _is_package(token, module_paths):
                packages.add(token)
            index += 1
            continue
        name, separator, value = token.partition('=')
        if name in _KEEP_VALUE_FLAGS:
            if separator:
                flags.append(f'{name}={value}')
            elif index + 1 < len(tokens):
                flags.append(f'{name}={tokens[index + 1]}')
                index += 1
            else:
                flags.append(name)
        elif name in _KEEP_BOOL_FLAGS:
            flags.append(name)
        elif name in _DROP_VALUE_FLAGS and not separator:
            index += 1
        elif name in _DROP_BOOL_FLAGS:
            pass
        index += 1
    if not packages:
        return None
    return Build(flags=tuple(sorted(flags)), packages=tuple(sorted(packages)), cwd=cwd)


def extract(entries, root: Path | None = None) -> Extraction:
    """Collect the distinct compilation keys of the adapter sources."""
    root = (root or REPOSITORY_ROOT).resolve()
    sources = collect_sources(entries, root)
    module_paths = _module_paths(root)
    builds: set[Build] = set()
    skipped: set[SkippedCommand] = set()
    for path in sorted(sources):
        try:
            tree = ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
        except (OSError, SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not node.args:
                continue
            head = node.args[0]
            values = _literal_strings(head)
            if values is None:
                prefix = _visible_prefix(head)
                if prefix[:1] == ['go'] and (len(prefix) < 2 or prefix[1] == 'test'):
                    skipped.add(SkippedCommand(path, node.lineno, 'command list is not fully literal'))
                continue
            if values is _MISSING:
                continue
            if len(values) < 2 or values[0] != 'go' or values[1] != 'test':
                continue
            build = _build_from(values[2:], _command_cwd(node), module_paths)
            if build is not None:
                builds.add(build)
    return Extraction(
        builds=tuple(sorted(builds, key=lambda build: (build.cwd, build.flags, build.packages))),
        skipped=tuple(sorted(skipped, key=lambda item: (str(item.path), item.lineno))),
        sources=tuple(sorted(sources)),
    )


def run_build(build: Build, root: Path, timeout: float | None = None) -> BuildResult:
    """Compile one build key without running any test."""
    # `-c` compiles (and caches) every package without executing test binaries,
    # so integration TestMain functions never run against absent services here.
    cwd = root / build.cwd
    started = time.monotonic()
    try:
        with tempfile.TemporaryDirectory(prefix='go-prewarm-') as output_dir:
            command = ['go', 'test', '-c', '-o', output_dir + os.sep, *build.flags, *build.packages]
            completed = subprocess.run(command, cwd=cwd, capture_output=True, text=True,
                                       timeout=timeout)
        output = (completed.stdout or '') + (completed.stderr or '')
        ok = completed.returncode == 0
    except (OSError, subprocess.SubprocessError) as error:
        output = f'{type(error).__name__}: {error}'
        ok = False
    return BuildResult(build=build, seconds=time.monotonic() - started, ok=ok, output=output)


def run_builds(builds, root: Path, max_workers: int | None = None) -> list[BuildResult]:
    workers = max_workers or os.cpu_count() or 2
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(run_build, build, root) for build in builds]
        return [future.result() for future in futures]


def print_summary(extraction: Extraction, results: list[BuildResult], wall_seconds: float) -> None:
    print(f'Prewarm: {len(extraction.sources)} TESTS source files, '
          f'{len(extraction.builds)} build keys, '
          f'{len(extraction.skipped)} skipped non-literal command(s).', flush=True)
    for skipped in extraction.skipped:
        try:
            label = skipped.path.relative_to(Path.cwd())
        except ValueError:
            label = skipped.path
        print(f'  skipped {label}:{skipped.lineno}: {skipped.reason}', flush=True)
    for result in results:
        status = 'ok' if result.ok else 'failed'
        print(f'  [{status:<6}] {result.seconds:7.2f}s {result.build.label}', flush=True)
        if not result.ok:
            for line in result.tail():
                print(f'      {line}', flush=True)
    passed = sum(1 for result in results if result.ok)
    print(f'Prewarm complete: {passed}/{len(results)} build keys compiled in {wall_seconds:.2f}s wall time.',
          flush=True)


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if not arguments:
        print('usage: python TESTS/adapters/go/prewarm.py ENTRY.py [ENTRY.py ...]', file=sys.stderr)
        return 2
    entries = [Path(argument) for argument in arguments]
    missing = [str(entry) for entry in entries if not entry.is_file()]
    if missing:
        print('error: missing entry file(s): ' + ', '.join(missing), file=sys.stderr)
        return 2
    root = REPOSITORY_ROOT.resolve()
    extraction = extract(entries, root)
    workers = os.cpu_count() or 2
    print(f'Prewarm: compiling {len(extraction.builds)} build keys with {workers} workers...',
          flush=True)
    started = time.monotonic()
    results = run_builds(extraction.builds, root, max_workers=workers)
    print_summary(extraction, results, time.monotonic() - started)
    return 0


if __name__ == '__main__':
    sys.exit(main())
