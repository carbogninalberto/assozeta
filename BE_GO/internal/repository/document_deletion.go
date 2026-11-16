package repository

import (
	"context"
	_ "embed"
	"fmt"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/jmoiron/sqlx"
)

// document_deletion_graph.json is Django's deletion graph for a
// docmanager.Document hard delete (TESTS/adapters/go/document_deletion_graph.py
// writes it from the models' _meta in the shared collector's format; its
// --check fails when it drifts): every model the Collector reaches from
// docmanager.Document through CASCADE, plus the models the ported pre_delete
// receivers delete, with candidate relations in
// _meta.get_fields(include_hidden=True) order, Django's own can_fast_delete
// verdicts, signal receivers and auditlog's delete-diff fields.
//
//go:embed document_deletion_graph.json
var documentDeletionGraphJSON []byte

var documentDeletionGraphLoaded = loadCollaboratorDeleteGraph(documentDeletionGraphJSON)

// deleteDocumentCascade is Document.delete() inside the caller's transaction:
// the stored row is collected and deleted with its full Django Collector
// cascade, its SET_NULL updates and its audit entries. A graph that reaches a
// receiver or value the collector does not implement refuses before any write
// (ErrCollaboratorDeleteUnsupported), never silently skips it.
func deleteDocumentCascade(ctx context.Context, tx *sqlx.Tx, id string, audit domain.LoginAuditContext, at time.Time) error {
	model := documentDeletionGraphLoaded.Models[documentDeleteLabel]
	if model == nil {
		return fmt.Errorf("%w: graph lacks %s", ErrCollaboratorDeleteUnsupported, documentDeleteLabel)
	}
	run := &collaboratorDeleteRun{tx: tx, graph: documentDeletionGraphLoaded, audit: audit,
		now: func() time.Time { return at }}
	rows, err := run.load(ctx, model, `t.document_id = $1::uuid`, id)
	if err != nil {
		return err
	}
	return run.Delete(ctx, documentDeleteLabel, rows)
}
