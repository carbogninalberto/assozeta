package domain

import (
	"encoding/json"
	"errors"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/refusal"
)

// DataRestoreReceipt is one instance_datarestore row (instance.models.DataRestore).
type DataRestoreReceipt struct {
	ID                string          `db:"id"`
	OwnerID           string          `db:"owner_id"`
	AssociationID     string          `db:"association_id"`
	State             string          `db:"state"`
	Stage             string          `db:"stage"`
	CompletedAt       *time.Time      `db:"completed_at"`
	CreatedAt         time.Time       `db:"created_at"`
	UpdatedAt         time.Time       `db:"updated_at"`
	UploadPath        string          `db:"upload_path"`
	SHA256            string          `db:"sha256"`
	Version           string          `db:"version"`
	Preview           json.RawMessage `db:"preview"`
	BackupPath        string          `db:"backup_path"`
	BackupSHA256      string          `db:"backup_sha256"`
	Media             json.RawMessage `db:"media"`
	Error             string          `db:"error"`
	AllowMissingMedia bool            `db:"allow_missing_media"`
	Attempts          int64           `db:"attempts"`
}

// DataRestoreActive is DataRestore.ACTIVE.
func (r DataRestoreReceipt) Active() bool { return r.State == "queued" || r.State == "running" }

// DataRestoreInstance is the part of InstanceConfiguration.get_config() the
// restore views read, with the primary association's owner.
type DataRestoreInstance struct {
	SelfHosted                  bool    `db:"self_hosted"`
	SupportMultipleAssociations bool    `db:"support_multiple_associations"`
	PrimaryAssociationID        *string `db:"primary_association_id"`
	PrimaryOwnerID              *string `db:"primary_owner_id"`
}

// ErrDataRestoreNotPorted marks requests Django answers with code Go does not
// carry (archive validation of an upload, cached progress values outside the
// decoded subset, request bodies outside JSON/multipart); they are refused
// with the server error page before any write.
var ErrDataRestoreNotPorted = refusal.New("data restore path not ported")

// ErrDataRestoreNotFound is get_object_or_404's Http404 for DataRestore.
var ErrDataRestoreNotFound = errors.New("No DataRestore matches the given query.")
