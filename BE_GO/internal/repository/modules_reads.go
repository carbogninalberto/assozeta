package repository

import (
	"context"
	"database/sql"
	"errors"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/jmoiron/sqlx"
)

type ModulesReads struct{ db *sqlx.DB }

func NewModulesReads(db *sqlx.DB) *ModulesReads { return &ModulesReads{db: db} }

const modulesReadsColumns = `module_id, custom_link, sport_association_id, creation_date, title, require_approval,
 to_char(start_date, 'YYYY-MM-DD') AS start_date, to_char(end_date, 'YYYY-MM-DD') AS end_date, always_active, max_responses,
 queue_mode, only_users, payment_required, payment_data::text AS payment_data, response_message, allow_attachments,
 elements::text AS elements`

// OwnerAssociation is request.user.sport_association for an owner: the
// active (not deleted) association; a miss is domain.ErrAssociationNotFound.
func (r *ModulesReads) OwnerAssociation(ctx context.Context, owner string) (string, error) {
	return paymentCategoryAssociation(ctx, r.db, owner, nil, false)
}

// Modules is Module.objects.filter(sport_association=...): no ordering.
func (r *ModulesReads) Modules(ctx context.Context, association string) ([]domain.ModuleRow, error) {
	rows := []domain.ModuleRow{}
	err := r.db.SelectContext(ctx, &rows, `SELECT `+modulesReadsColumns+` FROM application_module WHERE sport_association_id=$1`, association)
	return rows, err
}

// Module is Module.objects.filter(module_id=..., sport_association=...).first()
// (ordered by pk); nil when there is none.
func (r *ModulesReads) Module(ctx context.Context, id, association string) (*domain.ModuleRow, error) {
	var row domain.ModuleRow
	err := r.db.GetContext(ctx, &row, `SELECT `+modulesReadsColumns+` FROM application_module
 WHERE module_id=$1 AND sport_association_id=$2 ORDER BY module_id LIMIT 1`, id, association)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	return &row, nil
}

// Responses is ModuleResponses.objects.filter(module=...) (no ordering) with
// each response's attachments (no ordering) as pks and as attachments_docs.
func (r *ModulesReads) Responses(ctx context.Context, module string) ([]domain.ModuleResponseRow, error) {
	rows := []domain.ModuleResponseRow{}
	err := r.db.SelectContext(ctx, &rows, `SELECT r.module_response_id, r.module_id, r.creation_date, r.progressive_response_number,
 r.queue_position, r.response::text AS response, r.approved, r.user_id, r.payment_id,
 COALESCE((SELECT json_agg(t.document_id) FROM application_moduleresponses_attachments t
   WHERE t.moduleresponses_id=r.module_response_id), '[]')::text AS attachments,
 COALESCE((SELECT json_agg(json_build_object('document_id', d.document_id, 'filename', d.filename, 'token', d.token))
   FROM application_moduleresponses_attachments t JOIN docmanager_document d ON d.document_id=t.document_id
   WHERE t.moduleresponses_id=r.module_response_id), '[]')::text AS attachments_docs
 FROM application_moduleresponses r WHERE r.module_id=$1`, module)
	return rows, err
}

// LinkModule is Module.objects.filter(custom_link=...) across every
// association: the first row's module_id by pk, or nil when there is none.
func (r *ModulesReads) LinkModule(ctx context.Context, link string) (*string, error) {
	var id string
	err := r.db.GetContext(ctx, &id, `SELECT module_id FROM application_module WHERE custom_link=$1 ORDER BY module_id LIMIT 1`, link)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	return &id, nil
}
