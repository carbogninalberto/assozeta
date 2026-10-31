package domain

import (
	"errors"
	"time"
)

// ErrModuleNotFound is modules_overview's 404 {'error': 'module not found'}.
var ErrModuleNotFound = errors.New("module not found")

// ModuleRow is one application_module row. Dates are ISO text
// (DRF DateField), JSON fields are jsonb text.
type ModuleRow struct {
	ID               string    `db:"module_id"`
	CustomLink       *string   `db:"custom_link"`
	AssociationID    *string   `db:"sport_association_id"`
	CreationDate     time.Time `db:"creation_date"`
	Title            *string   `db:"title"`
	RequireApproval  bool      `db:"require_approval"`
	StartDate        *string   `db:"start_date"`
	EndDate          *string   `db:"end_date"`
	AlwaysActive     bool      `db:"always_active"`
	MaxResponses     *int64    `db:"max_responses"`
	QueueMode        bool      `db:"queue_mode"`
	OnlyUsers        bool      `db:"only_users"`
	PaymentRequired  bool      `db:"payment_required"`
	PaymentData      string    `db:"payment_data"`
	ResponseMessage  *string   `db:"response_message"`
	AllowAttachments bool      `db:"allow_attachments"`
	Elements         string    `db:"elements"`
}

// ModuleResponseRow is one application_moduleresponses row with its
// attachment pks and attachments_docs as JSON arrays.
type ModuleResponseRow struct {
	ID                string    `db:"module_response_id"`
	ModuleID          string    `db:"module_id"`
	CreationDate      time.Time `db:"creation_date"`
	ProgressiveNumber *int64    `db:"progressive_response_number"`
	QueuePosition     *int64    `db:"queue_position"`
	Response          string    `db:"response"`
	Approved          bool      `db:"approved"`
	UserID            *string   `db:"user_id"`
	PaymentID         *string   `db:"payment_id"`
	Attachments       string    `db:"attachments"`
	AttachmentsDocs   string    `db:"attachments_docs"`
}
