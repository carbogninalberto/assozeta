package repository

import (
	"context"
	"database/sql"
	"errors"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/jmoiron/sqlx"
)

type LoginResponseSettings struct{ db *sqlx.DB }

func NewLoginResponseSettings(db *sqlx.DB) *LoginResponseSettings {
	return &LoginResponseSettings{db: db}
}
func (r *LoginResponseSettings) LoginResponseSettings(ctx context.Context, id string, instructor bool) (domain.LoginResponseSettings, error) {
	empty := domain.LoginResponseSettings{}
	owned, err := beginOwnedTransaction(ctx, r.db, &sql.TxOptions{ReadOnly: true, Isolation: sql.LevelRepeatableRead})
	if err != nil {
		return empty, err
	}
	defer owned.Rollback()
	tx := owned.tx
	var row struct {
		UserID                   string `db:"user_id"`
		TemporaryInvoiceDeletion bool   `db:"temporary_invoice_deletion"`
		TablesSettings           []byte `db:"tables_settings"`
	}
	err = tx.GetContext(ctx, &row, `SELECT user_id,temporary_invoice_deletion,COALESCE(tables_settings,'null'::jsonb) AS tables_settings FROM bakney_user WHERE user_id=$1 AND NOT deleted`, id)
	if errors.Is(err, sql.ErrNoRows) {
		return empty, domain.ErrAccountNotFound
	}
	if err != nil {
		return empty, err
	}
	result := domain.LoginResponseSettings{UserID: row.UserID, TemporaryInvoiceDeletion: row.TemporaryInvoiceDeletion, TablesSettings: row.TablesSettings}
	if instructor {
		var selected string
		// This is the existing login metadata lookup, not instructor authorization.
		// Django does not constrain owner/draft here and .first() orders by UUID.
		err = tx.GetContext(ctx, &selected, `SELECT instructor_id FROM application_instructor WHERE associated_user_id=$1 ORDER BY instructor_id LIMIT 1`, id)
		if err == nil {
			result.InstructorID = &selected
		} else if !errors.Is(err, sql.ErrNoRows) {
			return empty, err
		}
	}
	if err = owned.Commit(); err != nil {
		return empty, err
	}
	return result, nil
}
