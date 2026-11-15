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

There are no accepted divergences yet.
