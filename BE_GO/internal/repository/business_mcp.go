package repository

import (
	"context"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/jmoiron/sqlx"
)

// BusinessMCPTools is the storage of the ported business MCP tool functions:
// saved reports plus the two existence checks save_report depends on.
type BusinessMCPTools struct {
	reports *SavedReports
	db      *sqlx.DB
}

func NewBusinessMCPTools(db *sqlx.DB) *BusinessMCPTools {
	return &BusinessMCPTools{reports: NewSavedReports(db), db: db}
}

func (r *BusinessMCPTools) SavedReports(ctx context.Context, association, creator string) ([]domain.SavedReport, error) {
	return r.reports.SavedReports(ctx, association, creator)
}

func (r *BusinessMCPTools) InsertSavedReport(ctx context.Context, id, association, creator string, write domain.SavedReportWrite) error {
	return r.reports.InsertSavedReport(ctx, id, association, creator, write)
}

// ActiveUserExists is User.objects.get(user_id=id) finding a row (the
// default manager excludes deleted users).
func (r *BusinessMCPTools) ActiveUserExists(ctx context.Context, id string) (bool, error) {
	var exists bool
	err := r.db.GetContext(ctx, &exists, `SELECT EXISTS(SELECT 1 FROM bakney_user WHERE user_id=$1 AND NOT deleted)`, id)
	return exists, err
}

// AssociationExists checks the saved report's association foreign key target.
func (r *BusinessMCPTools) AssociationExists(ctx context.Context, id string) (bool, error) {
	var exists bool
	err := r.db.GetContext(ctx, &exists, `SELECT EXISTS(SELECT 1 FROM application_sportassociation WHERE sport_association_id=$1)`, id)
	return exists, err
}
