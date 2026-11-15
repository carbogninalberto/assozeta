#!/usr/bin/env bash
# Regenerates ../unicode_tables.go, corpus.jsonl.gz and extras.jsonl.gz from
# the real python-dateutil. Usage: regen.sh /path/to/oracle/python
set -euo pipefail

python="${1:-}"
if [[ -z "$python" || ! -x "$python" ]]; then
  echo "usage: $0 /path/to/python (CPython 3.13 with python-dateutil 2.9.0.post0)" >&2
  exit 2
fi
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
generator="$here/gen_corpus.py"
extras="$here/gen_extras.py"
if [[ ! -s "$generator" || ! -s "$extras" ]]; then
  echo "missing $generator or $extras" >&2
  exit 2
fi
"$python" "$generator" tables "$here/../unicode_tables.go"
"$python" "$generator" corpus "$here/corpus.jsonl.gz"
"$python" "$extras" "$here/extras.jsonl.gz"
for output in "$here/../unicode_tables.go" "$here/corpus.jsonl.gz" "$here/extras.jsonl.gz"; do
  if [[ ! -s "$output" ]]; then
    echo "empty output: $output" >&2
    exit 1
  fi
done
