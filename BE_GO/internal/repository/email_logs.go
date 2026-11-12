package repository

import (
	"context"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/jmoiron/sqlx"
)

type EmailLogs struct{ db *sqlx.DB }

func NewEmailLogs(db *sqlx.DB) *EmailLogs { return &EmailLogs{db: db} }

var _ domain.EmailLogStore = (*EmailLogs)(nil)

const emailLogsListSQL = `SELECT email_log_id,recipient,subject,sent_at,result FROM application_emaillog
WHERE sport_association_id=$1 AND sent_at>=$2 ORDER BY sent_at DESC`

const emailLogsTodaySQL = `SELECT count(*) FROM application_emaillog WHERE sport_association_id=$1 AND sent_at>=$2`

// EmailLogs resolves the association through the active-association lookup
// (ActiveManager semantics), then runs the window query and the today count.
func (r *EmailLogs) EmailLogs(ctx context.Context, owner string, since, todaySince time.Time) ([]domain.EmailLogRecord, int, error) {
	association, err := messageReadsAssociation(ctx, r.db, owner)
	if err != nil {
		return nil, 0, err
	}
	rows := []domain.EmailLogRecord{}
	if err = r.db.SelectContext(ctx, &rows, emailLogsListSQL, association, since); err != nil {
		return nil, 0, err
	}
	var today int
	if err = r.db.GetContext(ctx, &today, emailLogsTodaySQL, association, todaySince); err != nil {
		return nil, 0, err
	}
	return rows, today, nil
}
