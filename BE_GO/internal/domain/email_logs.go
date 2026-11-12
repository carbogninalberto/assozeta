package domain

import (
	"context"
	"errors"
	"time"
)

// ErrEmailLogsNotAssociation is User.sport_association's PermissionDenied for
// athletes; Django renders it as a 403 detail.
var ErrEmailLogsNotAssociation = errors.New("User is not a sport association")

type EmailLogRecord struct {
	ID        string    `db:"email_log_id"`
	Recipient *string   `db:"recipient"`
	Subject   string    `db:"subject"`
	SentAt    time.Time `db:"sent_at"`
	Result    string    `db:"result"`
}

type EmailLogStore interface {
	// EmailLogs returns rows sent at or after since (newest first) and the
	// count of rows sent at or after todaySince, for one active association.
	EmailLogs(ctx context.Context, owner string, since, todaySince time.Time) ([]EmailLogRecord, int, error)
}
