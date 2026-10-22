package repository

import (
	"bytes"
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"sort"
	"strings"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/jmoiron/sqlx"
)

type ProfileUpdates struct{ db *sqlx.DB }

func NewProfileUpdates(db *sqlx.DB) *ProfileUpdates { return &ProfileUpdates{db} }
func (r *ProfileUpdates) BeginProfileUpdate(ctx context.Context) (domain.ProfileUpdateTransaction, error) {
	tx, err := beginOwnedTransaction(ctx, r.db, &sql.TxOptions{Isolation: sql.LevelReadCommitted})
	if err != nil {
		return nil, err
	}
	return &profileUpdateTransaction{loginPreparationTransaction{ownedTransaction: tx}}, nil
}

type profileUpdateTransaction struct{ loginPreparationTransaction }

var profilePersonColumns = []string{"first_name", "last_name", "username", "avatar_image"}
var profileAssociationColumns = []string{"denomination", "tax_code", "address", "address_cap", "address_city", "document_header", "invoice_footer", "enable_quotes_management", "configuration", "federation", "enroll_number", "sport", "president_signature", "stamp", "president_first_name", "president_last_name", "stripe_available_methods", "invoice_template", "subscription_template", "extra_text_invoices", "iban", "abbreviated", "vat_number", "website", "whatsapp", "checkout_info"}

func (t *profileUpdateTransaction) ProfilePersonForUpdate(ctx context.Context, id string) (domain.ProfileUpdatePerson, error) {
	var result domain.ProfileUpdatePerson
	err := t.tx.GetContext(ctx, &result, `SELECT user_id,role,first_name,last_name,username,avatar_image FROM bakney_user WHERE user_id=$1 AND NOT deleted FOR UPDATE`, id)
	if errors.Is(err, sql.ErrNoRows) {
		return result, domain.ErrAccountNotFound
	}
	return result, credentialTransactionError(err)
}

func (t *profileUpdateTransaction) ProfileAssociationForUpdate(ctx context.Context, owner string, scope *string) (domain.ProfileUpdateAssociation, error) {
	var row struct {
		ID      string          `db:"sport_association_id"`
		OwnerID string          `db:"user_id"`
		Fields  json.RawMessage `db:"fields"`
	}
	// Identifiers come only from the fixed business-schema whitelist above.
	projection := make([]string, 0, len(profileAssociationColumns)*2)
	for _, column := range profileAssociationColumns {
		projection = append(projection, "'"+column+"'", column)
	}
	err := t.tx.GetContext(ctx, &row, `SELECT sport_association_id,user_id,jsonb_build_object(`+strings.Join(projection, ",")+`) AS fields FROM application_sportassociation WHERE user_id=$1 AND NOT deleted AND ($2::uuid IS NULL OR sport_association_id=$2::uuid) FOR UPDATE`, owner, scope)
	if errors.Is(err, sql.ErrNoRows) {
		return domain.ProfileUpdateAssociation{}, domain.ErrAssociationNotFound
	}
	if err != nil {
		return domain.ProfileUpdateAssociation{}, credentialTransactionError(err)
	}
	result := domain.ProfileUpdateAssociation{ID: row.ID, OwnerID: row.OwnerID}
	decoder := json.NewDecoder(bytes.NewReader(row.Fields))
	decoder.UseNumber()
	if err = decoder.Decode(&result.Values); err != nil {
		return domain.ProfileUpdateAssociation{}, err
	}
	return result, nil
}

func profileAssignments(fields map[string]any, allowed []string, offset int) (string, []any, error) {
	set := make(map[string]bool, len(allowed))
	for _, name := range allowed {
		set[name] = true
	}
	keys := make([]string, 0, len(fields))
	for key := range fields {
		if !set[key] {
			return "", nil, domain.ErrInvalidScopedRequest
		}
		keys = append(keys, key)
	}
	sort.Strings(keys)
	assignments := make([]string, 0, len(keys))
	args := make([]any, 0, len(keys))
	for _, key := range keys {
		value := fields[key]
		clause := fmt.Sprintf("%s=$%d", key, offset+len(args))
		if key == "configuration" || key == "stripe_available_methods" {
			raw, ok := value.(json.RawMessage)
			if !ok || !json.Valid(raw) {
				return "", nil, domain.ErrInvalidScopedRequest
			}
			value = string(raw)
			clause += "::jsonb"
		}
		assignments = append(assignments, clause)
		args = append(args, value)
	}
	return strings.Join(assignments, ","), args, nil
}
func (t *profileUpdateTransaction) SetProfilePerson(ctx context.Context, id string, fields map[string]any) error {
	if len(fields) == 0 {
		return nil
	}
	set, args, err := profileAssignments(fields, profilePersonColumns, 2)
	if err != nil {
		return err
	}
	result, err := t.tx.ExecContext(ctx, `UPDATE bakney_user SET `+set+` WHERE user_id=$1 AND NOT deleted`, append([]any{id}, args...)...)
	if err != nil {
		return credentialTransactionError(err)
	}
	count, err := result.RowsAffected()
	if err == nil && count != 1 {
		return domain.ErrAccountNotFound
	}
	return credentialTransactionError(err)
}
func (t *profileUpdateTransaction) SetProfileAssociation(ctx context.Context, id, owner string, fields map[string]any) error {
	if len(fields) == 0 {
		return nil
	}
	set, args, err := profileAssignments(fields, profileAssociationColumns, 3)
	if err != nil {
		return err
	}
	result, err := t.tx.ExecContext(ctx, `UPDATE application_sportassociation SET `+set+` WHERE sport_association_id=$1 AND user_id=$2 AND NOT deleted`, append([]any{id, owner}, args...)...)
	if err != nil {
		return credentialTransactionError(err)
	}
	count, err := result.RowsAffected()
	if err == nil && count != 1 {
		return domain.ErrAssociationNotFound
	}
	return credentialTransactionError(err)
}
func (t *profileUpdateTransaction) AppendProfileAudit(ctx context.Context, event domain.ProfileUpdateAudit) error {
	if event.Model != "user" && event.Model != "sportassociation" {
		return domain.ErrInvalidScopedRequest
	}
	return credentialTransactionError(appendModelAudit(ctx, t.tx, modelAuditEntry{Model: event.Model, Key: event.ID, Representation: event.ObjectRepresentation, Action: 1, Changes: event.Changes, Context: event.Context, AssociationID: event.AssociationID, ResolutionPath: event.ResolutionPath, At: event.At}))
}
