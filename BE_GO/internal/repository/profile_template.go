package repository

import (
	"context"
	"database/sql"
	"errors"
	"strconv"
	"strings"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/jmoiron/sqlx"
)

// ProfileTemplate stores profile_update_subscription_template's association save.
type ProfileTemplate struct{ db *sqlx.DB }

func NewProfileTemplate(db *sqlx.DB) *ProfileTemplate { return &ProfileTemplate{db: db} }

var (
	profileTemplateText    = []string{"regulation", "demand", "logo", "invoice_template", "subscription_template", "extra_text_invoices"}
	profileTemplateJSON    = []string{"additional_sections", "subscription_fee_plans", "membership_fee_plans", "enabled_for", "additional_fields", "stripe_available_methods"}
	profileTemplateBoolean = []string{"show_regulation_to_members", "show_regulation_to_both", "show_regulation_to_athletes", "show_demand_to_members",
		"show_demand_to_both", "show_demand_to_athletes", "multiple_subscription_fee", "multiple_membership_fee", "enable_quotes_management"}
	profileTemplateDecimal = []string{"subscription_fee", "membership_fee"}
)

type profileTemplateTransaction struct{ *ownedTransaction }

func (r *ProfileTemplate) BeginProfileTemplate(ctx context.Context) (domain.ProfileTemplateTransaction, error) {
	tx, err := beginOwnedTransaction(ctx, r.db, &sql.TxOptions{Isolation: sql.LevelReadCommitted})
	if err != nil {
		return nil, err
	}
	return &profileTemplateTransaction{ownedTransaction: tx}, nil
}

func (t *profileTemplateTransaction) Association(ctx context.Context, owner, association string) (*domain.ProfileTemplateAssociation, error) {
	columns := []string{"sport_association_id::text"}
	for _, name := range profileTemplateText {
		columns = append(columns, name)
	}
	for _, name := range profileTemplateJSON {
		columns = append(columns, name+"::text")
	}
	for _, name := range profileTemplateDecimal {
		columns = append(columns, name+"::text")
	}
	columns = append(columns, profileTemplateBoolean...)
	var scope *string
	if association != "" {
		scope = &association
	}
	rows, err := t.tx.QueryContext(ctx, `SELECT `+strings.Join(columns, ", ")+` FROM application_sportassociation
 WHERE user_id=$1::uuid AND NOT deleted AND ($2::uuid IS NULL OR sport_association_id=$2::uuid) LIMIT 21 FOR UPDATE`, owner, scope)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var found []*domain.ProfileTemplateAssociation
	for rows.Next() {
		row := &domain.ProfileTemplateAssociation{Text: map[string]*string{}, JSON: map[string]*string{}, Decimal: map[string]*string{},
			Boolean: map[string]bool{}}
		texts := make([]sql.NullString, len(profileTemplateText)+len(profileTemplateJSON)+len(profileTemplateDecimal))
		flags := make([]bool, len(profileTemplateBoolean))
		targets := []any{&row.ID}
		for i := range texts {
			targets = append(targets, &texts[i])
		}
		for i := range flags {
			targets = append(targets, &flags[i])
		}
		if err := rows.Scan(targets...); err != nil {
			return nil, err
		}
		value := func(i int) *string {
			if !texts[i].Valid {
				return nil
			}
			text := texts[i].String
			return &text
		}
		for i, name := range profileTemplateText {
			row.Text[name] = value(i)
		}
		for i, name := range profileTemplateJSON {
			row.JSON[name] = value(len(profileTemplateText) + i)
		}
		for i, name := range profileTemplateDecimal {
			row.Decimal[name] = value(len(profileTemplateText) + len(profileTemplateJSON) + i)
		}
		for i, name := range profileTemplateBoolean {
			row.Boolean[name] = flags[i]
		}
		found = append(found, row)
	}
	if err := rows.Err(); err != nil {
		return nil, err
	}
	if len(found) != 1 {
		// DoesNotExist or MultipleObjectsReturned.
		return nil, nil
	}
	return found[0], nil
}

func (t *profileTemplateTransaction) AppendAudit(ctx context.Context, event domain.ProfileMiscAudit) error {
	path := event.ResolutionPath
	return appendModelAudit(ctx, t.tx, modelAuditEntry{Model: event.Model, Key: event.Key, Representation: event.Representation,
		Action: 1, Changes: event.Changes, Context: event.Context, AssociationID: event.AssociationID, ResolutionPath: &path, At: event.At})
}

// Save is the model UPDATE inside a savepoint: a database error restores the
// transaction to the state before it and returns domain.ErrProfileTemplateSave.
func (t *profileTemplateTransaction) Save(ctx context.Context, id string, columns []domain.ProfileTemplateColumn) error {
	if len(columns) == 0 {
		return nil
	}
	kinds := map[string]string{}
	for _, name := range profileTemplateText {
		kinds[name] = "text"
	}
	for _, name := range profileTemplateJSON {
		kinds[name] = "jsonb"
	}
	for _, name := range profileTemplateDecimal {
		kinds[name] = "numeric"
	}
	for _, name := range profileTemplateBoolean {
		kinds[name] = "boolean"
	}
	args := []any{id}
	sets := make([]string, 0, len(columns))
	for _, column := range columns {
		kind, ok := kinds[column.Name]
		if !ok {
			return errors.New("unknown profile template column")
		}
		args = append(args, column.Value)
		sets = append(sets, column.Name+"=$"+strconv.Itoa(len(args))+"::"+kind)
	}
	if _, err := t.tx.ExecContext(ctx, `SAVEPOINT profile_template_save`); err != nil {
		return err
	}
	if _, err := t.tx.ExecContext(ctx, `UPDATE application_sportassociation SET `+strings.Join(sets, ", ")+
		` WHERE sport_association_id=$1::uuid`, args...); err != nil {
		if _, restore := t.tx.ExecContext(ctx, `ROLLBACK TO SAVEPOINT profile_template_save`); restore != nil {
			return restore
		}
		return domain.ErrProfileTemplateSave
	}
	_, err := t.tx.ExecContext(ctx, `RELEASE SAVEPOINT profile_template_save`)
	return err
}
