# Accepted HTTP/WebSocket divergences

`tools/go-migration/refusal_reach.py --http` exits non-zero when an HTTP or
WebSocket root reaches a refusal reason that is not accepted here. Each accepted
divergence is one line:

```
- <reason string> | <route or *> | <why>
```

`*` accepts the reason for every route. A route accepts it only when the
analyzed route string matches exactly (for example `/ws/agent/` or
`/instance/admin/backups`). A missing file and a file without list lines both
mean "no accepted divergence".

## Approved native answers

Go answers these cases itself, with the closest Django answer instead of a
byte-equal one. A line whose reason is a refusal reason removes those rows from
the `--http` count; the other lines record the approval, because the sites
answer natively and no longer return a refusal.

- mcp: coerced value in a JSON lookup not ported | /ws/agent/ | X11 extended, as above: the agent answers the generic tool result; approved: owner, 2026-11-16
- saved reports: environment setting beyond int64 not ported | /ws/agent/ | X11 extended, as above: the agent answers the generic tool result; approved: owner, 2026-11-16
- business MCP non-finite export label not ported | /ws/agent/ | X11 extended, as above: the agent answers the generic tool result; approved: owner, 2026-11-16
- X12 storage transport and retried errors, PutObject 8 MiB+, bodies over 250 MiB | documents, personas, docmanager | botocore's final error text (for example "reached max retries: 4"); bodies over 250 MiB are spooled to disk; approved: owner, 2026-11-16
- X13 Stripe payloads outside the SDK port | stripe routes, payment stats | the closest stripe-python answer; approved: owner, 2026-11-16
- X14 external caches and environment bytes | release cache, smtpsend environment | the closest Django answer (an unreadable cached catalog is a cache miss; environment bytes are decoded with surrogateescape); approved: owner, 2026-11-16
- X15 Python recursion-depth bands | audit JSON, automation_tree, invoice party JSON, modules, course and payment form JSON | Django's answer at the default recursion limit, as measured; approved: owner, 2026-11-16
- X16 years before 1000 | statistic report, subscription create, customer invoice dates | follow the production image (glibc, unpadded `%Y`); approved: owner, 2026-11-16
- X17 concurrent deletes and timing races | subscription lifecycle, e-mail task configuration, instance setup logo, agent cache decr | the answer Django gives when the race resolves the common way; approved: owner, 2026-11-16
- X18 Python object addresses in output | pdfexport `<seq template>`, mentions str methods, Stripe object reprs, reportlab texts | the same text with a stable placeholder address; approved: owner, 2026-11-16
- X19 image codec libraries | pdfexport header logos and inline `<img>` | whether Pillow's bundled libjpeg-turbo decodes a JPEG follows Go's decoder (a JPEG is embedded as its own bytes either way), JPEG data inside other containers is decoded by Go, and lossy WebP, AVIF, JPEG 2000 and the optional TIFF codecs answer as a Pillow built without them; approved: owner, 2026-11-16 (rule-4 blanket approval, platform)

PDF `<img>` sources (file paths and URLs) are ported with Django parity; they
are not a divergence.
