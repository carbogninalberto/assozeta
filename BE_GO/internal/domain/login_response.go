package domain

import (
	"context"
	"encoding/json"
)

type RegistrationChoices struct{ Family, Federation, Multiple bool }

// LoginResponseSettingsReader only accepts the account selected by admission.
type LoginResponseSettingsReader interface {
	LoginResponseSettings(context.Context, string, bool) (LoginResponseSettings, error)
}
type LoginResponseSettings struct {
	UserID                   string          `db:"user_id"`
	TemporaryInvoiceDeletion bool            `db:"temporary_invoice_deletion"`
	TablesSettings           json.RawMessage `db:"tables_settings"`
	InstructorID             *string
}

// Image processing must implement the inspected compress_base64 contract before
// a complete public login can be wired. A nil processor must never silently pass
// through association images that Django transforms.
type LoginImageProcessor interface {
	CompressLoginImage(context.Context, string) (string, error)
}
