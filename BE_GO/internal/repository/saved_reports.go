package repository

import (
	"context"
	"database/sql"
	"errors"
	"strconv"

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

func (r *SavedReports) ReportIntegration(ctx context.Context) (bool, *string, error) {
	var row struct {
		SelfHosted bool    `db:"self_hosted"`
		Settings   *string `db:"integration_settings"`
	}
	err := r.db.GetContext(ctx, &row, `SELECT self_hosted, integration_settings::text AS integration_settings
 FROM instance_instanceconfiguration ORDER BY id LIMIT 1`)
	if errors.Is(err, sql.ErrNoRows) {
		return false, nil, nil
	}
	return row.SelfHosted, row.Settings, err
}

// RunReportStatements mirrors QueryBuilder's SET statement_timeout around
// the queries; database errors are ReportQueryError (the tool's exception).
func (r *SavedReports) RunReportStatements(ctx context.Context, timeoutMS int64, statements []domain.ReportStatement) ([][][]*string, error) {
	tx, err := r.db.BeginTxx(ctx, &sql.TxOptions{ReadOnly: true})
	if err != nil {
		return nil, err
	}
	defer tx.Rollback()
	if _, err = tx.ExecContext(ctx, `SELECT set_config('statement_timeout', $1, true)`, strconv.FormatInt(timeoutMS, 10)); err != nil {
		return nil, err
	}
	results := make([][][]*string, 0, len(statements))
	for _, statement := range statements {
		rows, err := tx.QueryContext(ctx, statement.SQL, statement.Args...)
		if err != nil {
			return nil, reportQueryError(ctx, err)
		}
		var records [][]*string
		for rows.Next() {
			texts := make([]sql.NullString, statement.Text)
			targets := make([]any, 0, statement.Text+statement.Hidden)
			for i := range texts {
				targets = append(targets, &texts[i])
			}
			for i := 0; i < statement.Hidden; i++ {
				targets = append(targets, new(any))
			}
			if err = rows.Scan(targets...); err != nil {
				rows.Close()
				return nil, err
			}
			record := make([]*string, statement.Text)
			for i, text := range texts {
				if text.Valid {
					value := text.String
					record[i] = &value
				}
			}
			records = append(records, record)
		}
		if err = rows.Err(); err != nil {
			rows.Close()
			return nil, reportQueryError(ctx, err)
		}
		rows.Close()
		results = append(results, records)
	}
	return results, nil
}

func reportQueryError(ctx context.Context, err error) error {
	if ctx.Err() != nil {
		return err
	}
	return &domain.ReportQueryError{Err: err}
}

func (r *SavedReports) DeleteSavedReport(ctx context.Context, id string) error {
	_, err := r.db.ExecContext(ctx, `DELETE FROM application_savedreport WHERE saved_report_id=$1`, id)
	return err
}
