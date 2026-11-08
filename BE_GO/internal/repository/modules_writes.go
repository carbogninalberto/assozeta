package repository

import (
	"context"
	"database/sql"
	"errors"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/jmoiron/sqlx"
)

type ModulesWrites struct{ db *sqlx.DB }

func NewModulesWrites(db *sqlx.DB) *ModulesWrites { return &ModulesWrites{db: db} }

type modulesWriteTransaction struct{ *ownedTransaction }

func (r *ModulesWrites) BeginModulesWrite(ctx context.Context) (domain.ModulesWriteTransaction, error) {
	tx, err := beginOwnedTransaction(ctx, r.db, &sql.TxOptions{Isolation: sql.LevelReadCommitted})
	if err != nil {
		return nil, err
	}
	return &modulesWriteTransaction{ownedTransaction: tx}, nil
}

// OwnerAssociation is SportAssociation.objects.get(user=...) (ActiveManager).
func (t *modulesWriteTransaction) OwnerAssociation(ctx context.Context, user string) (string, error) {
	return paymentCategoryAssociation(ctx, t.tx, user, nil, false)
}

// Module locks one module by id, optionally within one association.
func (t *modulesWriteTransaction) Module(ctx context.Context, id string, association *string) (*domain.ModuleRow, error) {
	var row domain.ModuleRow
	err := t.tx.GetContext(ctx, &row, `SELECT `+modulesReadsColumns+` FROM application_module
 WHERE module_id=$1 AND ($2::uuid IS NULL OR sport_association_id=$2::uuid) FOR UPDATE`, id, association)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	return &row, nil
}

// Taken is a UniqueValidator query on module_id or custom_link (exact),
// excluding the instance being updated.
func (t *modulesWriteTransaction) Taken(ctx context.Context, column, value string, exclude *string) (bool, error) {
	if column != "module_id" && column != "custom_link" {
		return false, errors.New("unsupported module unique column")
	}
	var taken bool
	err := t.tx.GetContext(ctx, &taken, `SELECT EXISTS(SELECT 1 FROM application_module WHERE `+column+`=$1
 AND ($2::uuid IS NULL OR module_id<>$2::uuid))`, value, exclude)
	return taken, err
}

func moduleAudit(id string, action int, changes map[string][2]string, association *string, path string, audit domain.LoginAuditContext,
	at time.Time) modelAuditEntry {
	entry := modelAuditEntry{Model: "module", Key: id, Representation: "Module object (" + id + ")", Action: action,
		Changes: changes, Context: audit, At: at}
	if association != nil {
		entry.AssociationID, entry.ResolutionPath = association, &path
	}
	return entry
}

// CreateModule is Module.objects.create(...): the row and auditlog's create
// entry, indexed through the direct resolver.
func (t *modulesWriteTransaction) CreateModule(ctx context.Context, row domain.ModuleRow, audit domain.LoginAuditContext, at time.Time) error {
	if _, err := t.tx.ExecContext(ctx, `INSERT INTO application_module (module_id, custom_link, sport_association_id, creation_date, title,
 require_approval, start_date, end_date, always_active, max_responses, queue_mode, only_users, payment_required, payment_data,
 response_message, allow_attachments, elements)
 VALUES ($1,$2,$3,$4,$5,$6,$7::date,$8::date,$9,$10,$11,$12,$13,$14::jsonb,$15,$16,$17::jsonb)`,
		row.ID, row.CustomLink, row.AssociationID, row.CreationDate, row.Title, row.RequireApproval, row.StartDate, row.EndDate,
		row.AlwaysActive, row.MaxResponses, row.QueueMode, row.OnlyUsers, row.PaymentRequired, row.PaymentData, row.ResponseMessage,
		row.AllowAttachments, row.Elements); err != nil {
		return err
	}
	changes, err := createdModelChanges(ctx, t.tx, "application_module", "module_id", row.ID)
	if err != nil {
		return err
	}
	return appendModelAudit(ctx, t.tx, moduleAudit(row.ID, 0, changes, row.AssociationID, "direct", audit, at))
}

// moduleAuditValues renders every stored field as auditlog's str() ('None' for null).
func (t *modulesWriteTransaction) moduleAuditValues(ctx context.Context, id string) (map[string]string, error) {
	changes, _, err := storedModelChanges(ctx, t.tx, "application_module", "module_id", id, true, false)
	if err != nil {
		return nil, err
	}
	values := make(map[string]string, len(changes))
	for name, pair := range changes {
		values[name] = pair[1]
	}
	return values, nil
}

// SaveModule is Model.save(): every field is written; auditlog records the
// changed fields only (no entry when nothing changed).
func (t *modulesWriteTransaction) SaveModule(ctx context.Context, row domain.ModuleRow, audit domain.LoginAuditContext, at time.Time) error {
	before, err := t.moduleAuditValues(ctx, row.ID)
	if err != nil {
		return err
	}
	if _, err = t.tx.ExecContext(ctx, `UPDATE application_module SET custom_link=$2, sport_association_id=$3, creation_date=$4, title=$5,
 require_approval=$6, start_date=$7::date, end_date=$8::date, always_active=$9, max_responses=$10, queue_mode=$11, only_users=$12,
 payment_required=$13, payment_data=$14::jsonb, response_message=$15, allow_attachments=$16, elements=$17::jsonb WHERE module_id=$1`,
		row.ID, row.CustomLink, row.AssociationID, row.CreationDate, row.Title, row.RequireApproval, row.StartDate, row.EndDate,
		row.AlwaysActive, row.MaxResponses, row.QueueMode, row.OnlyUsers, row.PaymentRequired, row.PaymentData, row.ResponseMessage,
		row.AllowAttachments, row.Elements); err != nil {
		return err
	}
	after, err := t.moduleAuditValues(ctx, row.ID)
	if err != nil {
		return err
	}
	value := func(values map[string]string, name string) string {
		if text, ok := values[name]; ok {
			return text
		}
		return "None"
	}
	changes := map[string][2]string{}
	for _, values := range []map[string]string{before, after} {
		for name := range values {
			if old, new := value(before, name), value(after, name); old != new {
				changes[name] = [2]string{old, new}
			}
		}
	}
	if len(changes) == 0 {
		return nil
	}
	return appendModelAudit(ctx, t.tx, moduleAudit(row.ID, 1, changes, row.AssociationID, "direct", audit, at))
}

// DeleteModule is module.delete(): the collector deletes the responses (and
// their attachment links; documents stay) before the module, then post_delete
// logs each response and the module. A response's entry cannot resolve
// through the deleted rows and its changes carry no sport_association: it is
// indexed under the actor's own association, if any. The module's entry is
// indexed through changes_json.sport_association.
func (t *modulesWriteTransaction) DeleteModule(ctx context.Context, id string, audit domain.LoginAuditContext, at time.Time) error {
	responses := []string{}
	if err := t.tx.SelectContext(ctx, &responses, `SELECT module_response_id FROM application_moduleresponses WHERE module_id=$1
 ORDER BY module_response_id FOR UPDATE`, id); err != nil {
		return err
	}
	if _, err := t.tx.ExecContext(ctx, `DELETE FROM application_moduleresponses_attachments
 WHERE moduleresponses_id IN (SELECT module_response_id FROM application_moduleresponses WHERE module_id=$1)`, id); err != nil {
		return err
	}
	entries := []modelAuditEntry{}
	var actorAssociation *string
	if audit.ActorID != nil {
		association, err := paymentCategoryAssociation(ctx, t.tx, *audit.ActorID, nil, false)
		if err != nil && !errors.Is(err, domain.ErrAssociationNotFound) {
			return err
		}
		if err == nil {
			actorAssociation = &association
		}
	}
	for _, response := range responses {
		changes, _, err := deleteModelWithAudit(ctx, t.tx, "application_moduleresponses", "module_response_id", response)
		if err != nil {
			return err
		}
		entry := modelAuditEntry{Model: "moduleresponses", Key: response, Representation: "ModuleResponses object (" + response + ")",
			Action: 2, Changes: changes, Context: audit, At: at}
		if actorAssociation != nil {
			path := "actor.sport_association"
			entry.AssociationID, entry.ResolutionPath = actorAssociation, &path
		}
		entries = append(entries, entry)
	}
	changes, _, err := deleteModelWithAudit(ctx, t.tx, "application_module", "module_id", id)
	if err != nil {
		return err
	}
	var association *string
	if value := changes["sport_association"][0]; value != "" && value != "None" {
		association = &value
	}
	entries = append(entries, moduleAudit(id, 2, changes, association, "changes_json.sport_association", audit, at))
	for _, entry := range entries {
		if err = appendModelAudit(ctx, t.tx, entry); err != nil {
			return err
		}
	}
	return nil
}
