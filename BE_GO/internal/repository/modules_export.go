package repository

import (
	"context"
	"database/sql"
	"errors"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/jmoiron/sqlx"
)

// ModulesExport reads what modules_response_export reads, query for query.
type ModulesExport struct{ db *sqlx.DB }

func NewModulesExport(db *sqlx.DB) *ModulesExport { return &ModulesExport{db: db} }

func (r *ModulesExport) OwnerAssociation(ctx context.Context, user string) (string, error) {
	return paymentCategoryAssociation(ctx, r.db, user, nil, false)
}

// modulesExportQuery is the queryset's SQL as Django builds it.
const modulesExportQuery = `SELECT "application_moduleresponses"."module_response_id", "application_moduleresponses"."module_id",
 "application_moduleresponses"."creation_date", "application_moduleresponses"."progressive_response_number",
 "application_moduleresponses"."queue_position", "application_moduleresponses"."response"::text AS "response",
 "application_moduleresponses"."approved", "application_moduleresponses"."user_id", "application_moduleresponses"."payment_id"
 FROM "application_moduleresponses" INNER JOIN "application_module"
 ON ("application_moduleresponses"."module_id" = "application_module"."module_id")
 WHERE ("application_moduleresponses"."module_id" = $1 AND "application_module"."sport_association_id" = $2)`

func (r *ModulesExport) Responses(ctx context.Context, module, association string) ([]domain.ModuleExportResponse, error) {
	rows := []domain.ModuleExportResponse{}
	err := r.db.SelectContext(ctx, &rows, modulesExportQuery, module, association)
	return rows, err
}

func (r *ModulesExport) FirstResponse(ctx context.Context, module, association string) (*domain.ModuleExportResponse, error) {
	var row domain.ModuleExportResponse
	err := r.db.GetContext(ctx, &row, modulesExportQuery+` ORDER BY "application_moduleresponses"."module_response_id" ASC LIMIT 1`,
		module, association)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	return &row, nil
}

func (r *ModulesExport) Module(ctx context.Context, id string) (*string, *string, error) {
	var row struct {
		Title    *string `db:"title"`
		Elements *string `db:"elements"`
	}
	err := r.db.GetContext(ctx, &row, `SELECT title, elements::text AS elements FROM application_module WHERE module_id=$1`, id)
	return row.Title, row.Elements, err
}

func (r *ModulesExport) User(ctx context.Context, id string) (*domain.ModuleExportUser, error) {
	var row domain.ModuleExportUser
	err := r.db.GetContext(ctx, &row, `SELECT first_name, last_name FROM bakney_user WHERE user_id=$1`, id)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	return &row, err
}

func (r *ModulesExport) PaymentPaid(ctx context.Context, id string) (bool, error) {
	var paid bool
	err := r.db.GetContext(ctx, &paid, `SELECT paid FROM application_payment WHERE payment_id=$1`, id)
	return paid, err
}

func (r *ModulesExport) Attachments(ctx context.Context, response string) ([]string, error) {
	names := []string{}
	err := r.db.SelectContext(ctx, &names, `SELECT "docmanager_document"."filename" FROM "docmanager_document"
 INNER JOIN "application_moduleresponses_attachments"
 ON ("docmanager_document"."document_id" = "application_moduleresponses_attachments"."document_id")
 WHERE "application_moduleresponses_attachments"."moduleresponses_id" = $1`, response)
	return names, err
}
