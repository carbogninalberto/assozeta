package repository

import (
	"context"
	"database/sql"
	"errors"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/jmoiron/sqlx"
)

type SavedReports struct{ db *sqlx.DB }

func NewSavedReports(db *sqlx.DB) *SavedReports { return &SavedReports{db: db} }

var _ domain.SavedReportStore = (*SavedReports)(nil)

const savedReportColumns = `saved_report_id, name, description, tool_name, params::text AS params, ui_config::text AS ui_config,
 created_at, updated_at`

func (r *SavedReports) ActiveAssociationForUser(ctx context.Context, user string) (string, error) {
	return messageReadsAssociation(ctx, r.db, user)
}

func (r *SavedReports) SavedReports(ctx context.Context, association, creator string) ([]domain.SavedReport, error) {
	rows := []domain.SavedReport{}
	err := r.db.SelectContext(ctx, &rows, `SELECT `+savedReportColumns+` FROM application_savedreport
 WHERE sport_association_id=$1 AND created_by_id=$2 ORDER BY updated_at DESC`, association, creator)
	return rows, err
}

func (r *SavedReports) SavedReport(ctx context.Context, id, association, creator string) (domain.SavedReport, error) {
	var row domain.SavedReport
	err := r.db.GetContext(ctx, &row, `SELECT `+savedReportColumns+` FROM application_savedreport
 WHERE saved_report_id=$1 AND sport_association_id=$2 AND created_by_id=$3`, id, association, creator)
	if errors.Is(err, sql.ErrNoRows) {
		return row, domain.ErrSavedReportNotFound
	}
	return row, err
}

func (r *SavedReports) InsertSavedReport(ctx context.Context, id, association, creator string, write domain.SavedReportWrite) error {
	_, err := r.db.ExecContext(ctx, `INSERT INTO application_savedreport (saved_report_id, sport_association_id, created_by_id,
 name, description, tool_name, params, ui_config, created_at, updated_at) VALUES ($1,$2,$3,$4,$5,$6,$7::jsonb,$8::jsonb,$9,$9)`,
		id, association, creator, write.Name, write.Description, write.ToolName, write.Params, write.UIConfig, write.At)
	return err
}

// UpdateSavedReport is the full-row save: assigned fields plus auto_now updated_at.
func (r *SavedReports) UpdateSavedReport(ctx context.Context, id string, change domain.SavedReportChange) error {
	_, err := r.db.ExecContext(ctx, `UPDATE application_savedreport SET name=COALESCE($2, name),
 description=COALESCE($3, description), params=COALESCE($4::jsonb, params), ui_config=COALESCE($5::jsonb, ui_config), updated_at=$6
 WHERE saved_report_id=$1`, id, change.Name, change.Description, change.Params, change.UIConfig, change.At)
	return err
}

func (r *SavedReports) DeleteSavedReport(ctx context.Context, id string) error {
	_, err := r.db.ExecContext(ctx, `DELETE FROM application_savedreport WHERE saved_report_id=$1`, id)
	return err
}
