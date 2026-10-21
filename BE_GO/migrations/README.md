Django exclusively owns the existing business schema during coexistence. The Go
server never runs schema migrations at startup. No Go-owned tables exist yet.

Future additive operational migrations must use a distinct migration history and
only explicitly Go-owned tables. Importing Kuayle business migrations, forcing a
baseline version, or changing an existing Django table here is prohibited.

Rollback must preserve Go-written business data, pending durable jobs, file keys
and identities. Reverting routing alone does not prove rollback. Ownership
fencing, task draining and full restore execution remain unimplemented. HTTP
admission now uses the existing Django/CLI file lock and DataRestore active-state
query; see `internal/service/restore_admission.md`. This does not transfer migration
or restore ownership to Go.
