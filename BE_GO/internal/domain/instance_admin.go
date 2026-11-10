package domain

import (
	"encoding/json"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/refusal"
)

// ErrInstanceAdminRefused marks an instance administration request the Go
// port does not serve (outbound probes, the update runner, unsupported stored
// shapes or form bodies). Handlers answer with the server error page before
// any write.
var ErrInstanceAdminRefused = refusal.New("instance administration request not ported")

// InstanceAdminRow is the singleton InstanceConfiguration as the owner-only
// administration views read it, including encrypted credentials.
type InstanceAdminRow struct {
	ID                  int64           `db:"id"`
	SelfHosted          bool            `db:"self_hosted"`
	DisplaySettings     json.RawMessage `db:"display_settings"`
	GoogleClientID      string          `db:"google_client_id"`
	AppleClientID       string          `db:"apple_client_id"`
	EmailSettings       json.RawMessage `db:"email_settings"`
	EmailPassword       string          `db:"email_password_encrypted" json:"-"`
	EmailRevision       int64           `db:"email_revision"`
	DiagnosticResults   json.RawMessage `db:"diagnostic_results"`
	IntegrationSettings json.RawMessage `db:"integration_settings" json:"-"`
}

// InstanceAdminUpdate is one save(update_fields=[...]) of the row; nil
// fields are not written. UpdatedAt is written when set.
type InstanceAdminUpdate struct {
	EmailSettings       json.RawMessage
	EmailPassword       *string
	EmailRevision       *int64
	DiagnosticResults   json.RawMessage
	IntegrationSettings json.RawMessage
	UpdatedAt           *time.Time
}

// InstanceUIAPIVersionChange is the UPDATE log entry InstanceAdminView.put
// writes when the web UI API version changes.
type InstanceUIAPIVersionChange struct {
	ActorID, Previous, Version string
	Audit                      LoginAuditContext
	At                         time.Time
}
