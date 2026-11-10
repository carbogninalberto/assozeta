package repository

import (
	"context"
	"database/sql"
	"errors"
	"strconv"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/jmoiron/sqlx"
)

// ModulesResponses serves modules_custom_link_info, modules_response_add,
// modules_response_approve, modules_response_delete and
// modules_response_add_attachment.
type ModulesResponses struct{ db *sqlx.DB }

func NewModulesResponses(db *sqlx.DB) *ModulesResponses { return &ModulesResponses{db: db} }

// ModuleByLink is Module.objects.filter(custom_link=...).first() (ordered by pk).
func (r *ModulesResponses) ModuleByLink(ctx context.Context, link string) (*domain.ModuleRow, error) {
	var row domain.ModuleRow
	err := r.db.GetContext(ctx, &row, `SELECT `+modulesReadsColumns+` FROM application_module WHERE custom_link=$1
 ORDER BY module_id LIMIT 1`, link)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	return &row, nil
}

// ResponseAttachments is attachments_docs of
// ModuleResponses.objects.filter(module_response_id=..., module=module).first():
// the public module page only reads responses of its own module; nil
// without the row.
func (r *ModulesResponses) ResponseAttachments(ctx context.Context, id, module string) (*string, error) {
	var docs string
	err := r.db.GetContext(ctx, &docs, `SELECT COALESCE((SELECT json_agg(json_build_object('document_id', d.document_id, 'filename', d.filename))
   FROM application_moduleresponses_attachments t JOIN docmanager_document d ON d.document_id=t.document_id
   WHERE t.moduleresponses_id=r.module_response_id), '[]')::text
 FROM application_moduleresponses r WHERE r.module_response_id=$1 AND r.module_id=$2`, id, module)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	return &docs, nil
}

// PublicAssociation is module.sport_association (the row by id, any state):
// its name and its deleted flag.
func (r *ModulesResponses) PublicAssociation(ctx context.Context, association string) (domain.ModulePublicAssociation, error) {
	var row domain.ModulePublicAssociation
	err := r.db.GetContext(ctx, &row, `SELECT denomination, deleted FROM application_sportassociation WHERE sport_association_id=$1`, association)
	return row, err
}

// AssociationOwner is the owner of an association row (any state).
func (r *ModulesResponses) AssociationOwner(ctx context.Context, association string) (string, error) {
	var owner string
	err := r.db.GetContext(ctx, &owner, `SELECT user_id FROM application_sportassociation WHERE sport_association_id=$1`, association)
	return owner, err
}

type modulesResponsesTransaction struct{ *ownedTransaction }

func (r *ModulesResponses) BeginModulesResponses(ctx context.Context) (domain.ModulesResponsesTransaction, error) {
	tx, err := beginOwnedTransaction(ctx, r.db, &sql.TxOptions{Isolation: sql.LevelReadCommitted})
	if err != nil {
		return nil, err
	}
	return &modulesResponsesTransaction{ownedTransaction: tx}, nil
}

// OwnerAssociation is request.user.sport_association for an owner.
func (t *modulesResponsesTransaction) OwnerAssociation(ctx context.Context, user string) (string, error) {
	return paymentCategoryAssociation(ctx, t.tx, user, nil, false)
}

// ModuleByID is Module.objects.filter(module_id=...).first().
func (t *modulesResponsesTransaction) ModuleByID(ctx context.Context, id string) (*domain.ModuleRow, error) {
	var row domain.ModuleRow
	err := t.tx.GetContext(ctx, &row, `SELECT `+modulesReadsColumns+` FROM application_module WHERE module_id=$1`, id)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	return &row, nil
}

// ApprovedCount is Module.current_responses.
func (t *modulesResponsesTransaction) ApprovedCount(ctx context.Context, module string) (int64, error) {
	var count int64
	err := t.tx.GetContext(ctx, &count, `SELECT count(*) FROM application_moduleresponses WHERE module_id=$1 AND approved`, module)
	return count, err
}

// LastProgressive is filter(module=...).order_by('-progressive_response_number').first():
// PostgreSQL's DESC puts NULL first.
func (t *modulesResponsesTransaction) LastProgressive(ctx context.Context, module string) (bool, *int64, error) {
	var number *int64
	err := t.tx.GetContext(ctx, &number, `SELECT progressive_response_number FROM application_moduleresponses WHERE module_id=$1
 ORDER BY progressive_response_number DESC LIMIT 1`, module)
	if errors.Is(err, sql.ErrNoRows) {
		return false, nil, nil
	}
	return err == nil, number, err
}

// LastQueuePosition is the highest non-null queue position, or nil.
func (t *modulesResponsesTransaction) LastQueuePosition(ctx context.Context, module string) (*int64, error) {
	var position *int64
	err := t.tx.GetContext(ctx, &position, `SELECT max(queue_position) FROM application_moduleresponses WHERE module_id=$1`, module)
	return position, err
}

// responseIndex is the index of a ModuleResponses (or Document) entry: the
// given association through module.sport_association, else the actor's own
// association (actor.sport_association), else none.
func (t *modulesResponsesTransaction) responseIndex(ctx context.Context, association *string, audit domain.LoginAuditContext) (*string, string, error) {
	if association != nil {
		return association, "module.sport_association", nil
	}
	if audit.ActorID == nil {
		return nil, "", nil
	}
	found, err := paymentCategoryAssociation(ctx, t.tx, *audit.ActorID, nil, false)
	if errors.Is(err, domain.ErrAssociationNotFound) {
		return nil, "", nil
	}
	if err != nil {
		return nil, "", err
	}
	return &found, "actor.sport_association", nil
}

func (t *modulesResponsesTransaction) appendResponseAudit(ctx context.Context, app, model, key string, action int, changes map[string][2]string,
	association *string, audit domain.ModuleResponseAudit) error {
	index, path, err := t.responseIndex(ctx, association, audit.Context)
	if err != nil {
		return err
	}
	representation := "ModuleResponses object (" + key + ")"
	if model == "document" {
		representation = "Document object (" + key + ")"
	}
	entry := modelAuditEntry{Model: model, Key: key, Representation: representation, Action: action, Changes: changes,
		Context: audit.Context, At: audit.At}
	if index != nil {
		entry.AssociationID, entry.ResolutionPath = index, &path
	}
	return appendModelAuditForApp(ctx, t.tx, app, entry)
}

// CreateResponse is ModuleResponses.objects.create(...) and its create entry.
func (t *modulesResponsesTransaction) CreateResponse(ctx context.Context, row domain.ModuleResponseCreate, association *string,
	audit domain.ModuleResponseAudit) error {
	if _, err := t.tx.ExecContext(ctx, `INSERT INTO application_moduleresponses (module_response_id, module_id, creation_date,
 progressive_response_number, queue_position, response, approved, user_id, payment_id)
 VALUES ($1,$2,$3,$4,$5,$6::jsonb,$7,NULL,NULL)`, row.ID, row.ModuleID, row.CreationDate, row.ProgressiveNumber, row.QueuePosition,
		row.Response, row.Approved); err != nil {
		return err
	}
	changes, err := createdModelChanges(ctx, t.tx, "application_moduleresponses", "module_response_id", row.ID)
	if err != nil {
		return err
	}
	return t.appendResponseAudit(ctx, "application", "moduleresponses", row.ID, 0, changes, association, audit)
}

// SaveResponseUser is module_response.user = request.user; save().
func (t *modulesResponsesTransaction) SaveResponseUser(ctx context.Context, id, user string, association *string, audit domain.ModuleResponseAudit) error {
	if _, err := t.tx.ExecContext(ctx, `UPDATE application_moduleresponses SET user_id=$2 WHERE module_response_id=$1`, id, user); err != nil {
		return err
	}
	return t.appendResponseAudit(ctx, "application", "moduleresponses", id, 1, map[string][2]string{"user": {"None", user}}, association, audit)
}

// ResponseInAssociation is filter(module_response_id=..., module__sport_association=...).first().
func (t *modulesResponsesTransaction) ResponseInAssociation(ctx context.Context, id, association string) (*domain.ModuleResponseRef, error) {
	var ref domain.ModuleResponseRef
	err := t.tx.GetContext(ctx, &ref, `SELECT r.module_response_id, r.module_id, r.approved, m.queue_mode, m.sport_association_id
 FROM application_moduleresponses r JOIN application_module m ON m.module_id=r.module_id
 WHERE r.module_response_id=$1 AND m.sport_association_id=$2 FOR UPDATE OF r`, id, association)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	return &ref, nil
}

// ResponseExists is filter(module_response_id=...).first() is not None.
func (t *modulesResponsesTransaction) ResponseExists(ctx context.Context, id string) (bool, error) {
	var exists bool
	err := t.tx.GetContext(ctx, &exists, `SELECT EXISTS(SELECT 1 FROM application_moduleresponses WHERE module_response_id=$1)`, id)
	return exists, err
}

// ApproveResponse is approved = True; save(): an entry only when it changed.
func (t *modulesResponsesTransaction) ApproveResponse(ctx context.Context, ref domain.ModuleResponseRef, audit domain.ModuleResponseAudit) error {
	if ref.Approved {
		return nil
	}
	if _, err := t.tx.ExecContext(ctx, `UPDATE application_moduleresponses SET approved=true WHERE module_response_id=$1`, ref.ID); err != nil {
		return err
	}
	return t.appendResponseAudit(ctx, "application", "moduleresponses", ref.ID, 1, map[string][2]string{"approved": {"False", "True"}},
		ref.AssociationID, audit)
}

// DeleteResponse is module_response.delete(): its attachment links (the
// documents stay), the row, and the delete entry. The entry cannot resolve
// through the deleted row and its changes carry no sport_association: it is
// indexed under the actor's own association, if any.
func (t *modulesResponsesTransaction) DeleteResponse(ctx context.Context, id string, audit domain.ModuleResponseAudit) error {
	if _, err := t.tx.ExecContext(ctx, `DELETE FROM application_moduleresponses_attachments WHERE moduleresponses_id=$1`, id); err != nil {
		return err
	}
	changes, _, err := deleteModelWithAudit(ctx, t.tx, "application_moduleresponses", "module_response_id", id)
	if err != nil {
		return err
	}
	return t.appendResponseAudit(ctx, "application", "moduleresponses", id, 2, changes, nil, audit)
}

func queueText(value *int64) string {
	if value == nil {
		return "None"
	}
	return strconv.FormatInt(*value, 10)
}

// UpdateQueue is update_queue(module): approved responses with a queue
// position lose it, the others are renumbered from 1 by queue position;
// save() logs the changed position only.
func (t *modulesResponsesTransaction) UpdateQueue(ctx context.Context, module string, association *string, audit domain.ModuleResponseAudit) error {
	rows := []domain.ModuleQueueRow{}
	if err := t.tx.SelectContext(ctx, &rows, `SELECT module_response_id, approved, queue_position FROM application_moduleresponses
 WHERE module_id=$1 AND queue_position IS NOT NULL ORDER BY queue_position, module_response_id FOR UPDATE`, module); err != nil {
		return err
	}
	save := func(row domain.ModuleQueueRow, position *int64) error {
		if queueText(row.Queue) == queueText(position) {
			return nil
		}
		if _, err := t.tx.ExecContext(ctx, `UPDATE application_moduleresponses SET queue_position=$2 WHERE module_response_id=$1`,
			row.ID, position); err != nil {
			return err
		}
		return t.appendResponseAudit(ctx, "application", "moduleresponses", row.ID, 1,
			map[string][2]string{"queue_position": {queueText(row.Queue), queueText(position)}}, association, audit)
	}
	for _, row := range rows {
		if row.Approved {
			if err := save(row, nil); err != nil {
				return err
			}
		}
	}
	position := int64(0)
	for _, row := range rows {
		if !row.Approved {
			position++
			next := position
			if err := save(row, &next); err != nil {
				return err
			}
		}
	}
	return nil
}

// DocumentExists is Document.objects.filter(document_id=...).first() is not None.
func (t *modulesResponsesTransaction) DocumentExists(ctx context.Context, id string) (bool, error) {
	var exists bool
	err := t.tx.GetContext(ctx, &exists, `SELECT EXISTS(SELECT 1 FROM docmanager_document WHERE document_id=$1)`, id)
	return exists, err
}

// DocumentReferencedElsewhere reports a row of any table other than the
// module response attachment links that references the document: Django's
// delete collector would cascade to it or set it null.
func (t *modulesResponsesTransaction) DocumentReferencedElsewhere(ctx context.Context, id string) (bool, error) {
	type reference struct {
		Table  string `db:"table_name"`
		Column string `db:"column_name"`
	}
	references := []reference{}
	if err := t.tx.SelectContext(ctx, &references, `SELECT c.conrelid::regclass::text AS table_name, a.attname AS column_name
 FROM pg_constraint c JOIN pg_attribute a ON a.attrelid=c.conrelid AND a.attnum=c.conkey[1]
 WHERE c.contype='f' AND c.confrelid='docmanager_document'::regclass AND array_length(c.conkey, 1)=1
 ORDER BY 1, 2`); err != nil {
		return false, err
	}
	for _, item := range references {
		if item.Table == "application_moduleresponses_attachments" {
			continue
		}
		var found bool
		if err := t.tx.GetContext(ctx, &found, `SELECT EXISTS(SELECT 1 FROM `+item.Table+` WHERE `+
			quoteIdentifier(item.Column)+`=$1)`, id); err != nil {
			return false, err
		}
		if found {
			return true, nil
		}
	}
	return false, nil
}

// moduleDocumentReverse is Document's reverse accessor with a related_name, which
// auditlog renders as its empty manager.
var moduleDocumentReverse = instructorHoursDocumentReverse

// DeleteDocument is module_response.attachments.remove(document) and
// document.delete(): every attachment link of the document goes, then the row
// and its delete entry (indexed under the actor's own association, if any).
func (t *modulesResponsesTransaction) DeleteDocument(ctx context.Context, response, document string, audit domain.ModuleResponseAudit) error {
	if _, err := t.tx.ExecContext(ctx, `DELETE FROM application_moduleresponses_attachments WHERE moduleresponses_id=$1 AND document_id=$2`,
		response, document); err != nil {
		return err
	}
	if _, err := t.tx.ExecContext(ctx, `DELETE FROM application_moduleresponses_attachments WHERE document_id=$1`, document); err != nil {
		return err
	}
	changes, _, err := deleteModelWithAuditReverse(ctx, t.tx, "docmanager_document", "document_id", document, moduleDocumentReverse)
	if err != nil {
		return err
	}
	return t.appendResponseAudit(ctx, "docmanager", "document", document, 2, changes, nil, audit)
}

// DeleteReferencedDocument is module_response.attachments.remove(document) and
// document.delete() for a document rows of other tables reference: Django's
// collector deletes it with the rows that cascade from it, clears the
// nullable references and logs each deleted row.
func (t *modulesResponsesTransaction) DeleteReferencedDocument(ctx context.Context, response, document string, audit domain.ModuleResponseAudit) error {
	graph, err := documentDeleteGraph()
	if err != nil {
		return err
	}
	if _, err = t.tx.ExecContext(ctx, `DELETE FROM application_moduleresponses_attachments WHERE moduleresponses_id=$1 AND document_id=$2`,
		response, document); err != nil {
		return err
	}
	run := &collaboratorDeleteRun{tx: t.tx, graph: graph, audit: audit.Context, now: func() time.Time { return audit.At }}
	rows, err := run.load(ctx, graph.Models[documentDeleteLabel], `t.document_id = $1::uuid FOR UPDATE`, document)
	if err != nil {
		return err
	}
	return run.Delete(ctx, documentDeleteLabel, rows)
}

// CreateDocument is Document.objects.create(filename=...) and its create
// entry; the second document.save() changes nothing and logs nothing.
func (t *modulesResponsesTransaction) CreateDocument(ctx context.Context, row domain.ModuleDocumentCreate, audit domain.ModuleResponseAudit) error {
	if _, err := t.tx.ExecContext(ctx, `INSERT INTO docmanager_document (document_id, creation_date, filepath, filename, file_size_bytes, token)
 VALUES ($1,$2,NULL,$3,NULL,$4)`, row.ID, row.CreationDate, row.Filename, row.Token); err != nil {
		return err
	}
	changes, err := createdModelChanges(ctx, t.tx, "docmanager_document", "document_id", row.ID)
	if err != nil {
		return err
	}
	for name, manager := range moduleDocumentReverse {
		changes[name] = [2]string{"None", manager}
	}
	return t.appendResponseAudit(ctx, "docmanager", "document", row.ID, 0, changes, nil, audit)
}

// LinkDocument is module_response.attachments.add(document); the following
// module_response.save() changes nothing and logs nothing.
func (t *modulesResponsesTransaction) LinkDocument(ctx context.Context, response, document string) error {
	_, err := t.tx.ExecContext(ctx, `INSERT INTO application_moduleresponses_attachments (moduleresponses_id, document_id)
 VALUES ($1,$2) ON CONFLICT DO NOTHING`, response, document)
	return err
}
