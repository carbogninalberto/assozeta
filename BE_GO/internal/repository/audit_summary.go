package repository

import (
	"context"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/jmoiron/sqlx"
)

type AuditSummaries struct{ db *sqlx.DB }

func NewAuditSummaries(db *sqlx.DB) *AuditSummaries { return &AuditSummaries{db: db} }

var _ domain.AuditSummaryStore = (*AuditSummaries)(nil)

func (r *AuditSummaries) OwnerAssociation(ctx context.Context, owner string) (string, error) {
	return paymentCategoryAssociation(ctx, r.db, owner, nil, false)
}

func (r *AuditSummaries) IndexedCount(ctx context.Context, association string) (int64, error) {
	var count int64
	err := r.db.GetContext(ctx, &count, `SELECT count(*) FROM application_auditlogindex WHERE sport_association_id=$1`, association)
	return count, err
}

func (r *AuditSummaries) ActionCounts(ctx context.Context, association string) ([]domain.AuditActionCount, error) {
	rows := []domain.AuditActionCount{}
	err := r.db.SelectContext(ctx, &rows, `SELECT e.action, count(e.id) AS count FROM auditlog_logentry e
 WHERE e.id IN (SELECT log_entry_id FROM application_auditlogindex WHERE sport_association_id=$1) GROUP BY e.action`, association)
	return rows, err
}

func (r *AuditSummaries) IndexedModels(ctx context.Context, association string) ([]string, error) {
	rows := []string{}
	err := r.db.SelectContext(ctx, &rows, `SELECT ct.model FROM django_content_type ct WHERE ct.id IN (SELECT e.content_type_id FROM auditlog_logentry e
 WHERE e.id IN (SELECT log_entry_id FROM application_auditlogindex WHERE sport_association_id=$1))`, association)
	return rows, err
}
