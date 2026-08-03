#!/bin/sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
CLI="$ROOT/selfhost/bin/assozeta"
PDF=$(mktemp)
trap 'rm -f "$PDF"; "$CLI" dev-down >/dev/null 2>&1 || true' EXIT HUP INT TERM

"$CLI" dev-up

curl_check() {
    curl --fail --silent --show-error \
        --retry 20 --retry-delay 1 --retry-all-errors "$1" >/dev/null
}

curl_check http://localhost:5001/
curl_check http://localhost:5001/api/healthz
curl_check http://localhost:5001/api/readyz
curl_check http://localhost:5001/api/instance/status

"$CLI" dev-compose exec -T renderer node -e '
    fetch("http://127.0.0.1:3000/render", {
        method: "POST",
        headers: {"content-type": "application/json"},
        body: JSON.stringify({type: "pdf", url: "http://api:8000/healthz", pdf: {format: "A4"}}),
    }).then(async response => {
        if (!response.ok) throw new Error(await response.text());
        process.stdout.write(Buffer.from(await response.arrayBuffer()));
    }).catch(error => {
        console.error(error);
        process.exit(1);
    });
' > "$PDF"

first_bytes=$(dd if="$PDF" bs=4 count=1 2>/dev/null)
[ "$first_bytes" = '%PDF' ] || {
    printf 'Renderer did not return a PDF.\n' >&2
    exit 1
}

"$CLI" dev-compose exec -T renderer node -e '
    const render = url => fetch("http://127.0.0.1:3000/render", {
        method: "POST",
        headers: {"content-type": "application/json"},
        body: JSON.stringify({type: "pdf", url}),
    });
    Promise.all([
        render("http://api:8000/renderer-missing").then(response => {
            if (response.ok) throw new Error("Renderer accepted an HTTP error page");
        }),
        render("https://example.com/").then(response => {
            if (response.ok) throw new Error("Renderer accepted a disallowed host");
        }),
    ]).catch(error => {
        console.error(error);
        process.exit(1);
    });
'

printf 'Full development smoke test passed.\n'
