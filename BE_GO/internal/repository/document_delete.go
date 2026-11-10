package repository

import (
	"context"
	"database/sql"
	_ "embed"
	"encoding/json"
	"errors"
	"fmt"
	"sync"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/jmoiron/sqlx"
)

// document_delete_scope.json is the table application.document_scope walks
// (TESTS/adapters/django/document_delete_scope_graph.py writes it from the
// models' _meta; the document-delete family fails when it drifts):
// docmanager.Document, the models referencing it and the models referencing
// those, each with its audit resolver key and sport_association column.
//
//go:embed document_delete_scope.json
var documentDeleteScopeJSON []byte

type documentDeleteReferrer struct {
	Model   string `json:"model"`
	Kind    string `json:"kind"`
	Column  string `json:"column"`
	Through string `json:"through"`
	Source  string `json:"source"`
	Target  string `json:"target"`
}

type documentDeleteScopeModel struct {
	Table       string                   `json:"table"`
	PK          string                   `json:"pk"`
	PKType      string                   `json:"pk_type"`
	ContentType string                   `json:"content_type"`
	Association *string                  `json:"sport_association"`
	Referrers   []documentDeleteReferrer `json:"referrers"`
}

type documentDeleteScope struct {
	Root   string                               `json:"root"`
	Models map[string]*documentDeleteScopeModel `json:"models"`
}

const documentDeleteLabel = "docmanager.Document"

var documentDeleteScopeLoaded = func() *documentDeleteScope {
	scope := &documentDeleteScope{}
	if err := json.Unmarshal(documentDeleteScopeJSON, scope); err != nil {
		panic("document delete scope: " + err.Error())
	}
	if scope.Root != documentDeleteLabel || scope.Models[scope.Root] == nil {
		panic("document delete scope: root")
	}
	for label, model := range scope.Models {
		if model.PKType != "UUIDField" {
			panic("document delete scope: key type of " + label)
		}
		for _, referrer := range model.Referrers {
			if scope.Models[referrer.Model] == nil || (referrer.Kind != "fk" && referrer.Kind != "m2m") {
				panic("document delete scope: referrer of " + label)
			}
		}
	}
	return scope
}()

// documentDeleteGraph is the deletion graph that contains docmanager.Document
// (the restore graph, in the shared collector's format).
var documentDeleteGraph = sync.OnceValues(func() (*collaboratorDeleteGraph, error) {
	deletion, err := NewRestoreDeletion()
	if err != nil {
		return nil, err
	}
	if deletion.graph.Models[documentDeleteLabel] == nil {
		return nil, errors.New("deletion graph lacks " + documentDeleteLabel)
	}
	return deletion.graph, nil
})

// DocumentDelete is application.document_scope.delete_document's ownership
// walk and Document.delete().
type DocumentDelete struct {
	db  *sqlx.DB
	now func() time.Time
}

func NewDocumentDelete(db *sqlx.DB, now func() time.Time) *DocumentDelete {
	return &DocumentDelete{db: db, now: now}
}

var _ domain.DocumentDeleteStore = (*DocumentDelete)(nil)

// documentDeleteReferrers is document_scope.referrers for one object: the keys
// of every row (base manager) holding a foreign key or many-to-many link.
func documentDeleteReferrers(ctx context.Context, tx *sqlx.Tx, model *documentDeleteScopeModel, key string,
	visit func(label, key string) error) error {
	for _, referrer := range model.Referrers {
		related := documentDeleteScopeLoaded.Models[referrer.Model]
		query := `SELECT t.` + related.PK + `::text FROM ` + related.Table + ` t WHERE t.` + referrer.Column + ` = $1::uuid`
		if referrer.Kind == "m2m" {
			query = `SELECT t.` + related.PK + `::text FROM ` + related.Table + ` t JOIN ` + referrer.Through + ` m ON m.` +
				referrer.Source + ` = t.` + related.PK + ` WHERE m.` + referrer.Target + ` = $1::uuid`
		}
		keys := []string{}
		if err := tx.SelectContext(ctx, &keys, query, key); err != nil {
			return err
		}
		for _, found := range keys {
			if err := visit(referrer.Model, found); err != nil {
				return err
			}
		}
	}
	return nil
}

// documentDeleteResolve is tenant_scope.resolve_association for an object
// read through the base manager: the registered audit resolver, else the
// object's own sport_association. "" is None.
func documentDeleteResolve(ctx context.Context, tx *sqlx.Tx, label, key string) (string, error) {
	model := documentDeleteScopeLoaded.Models[label]
	if resolver, ok := auditIndexResolvers[model.ContentType]; ok && !resolver.none {
		resolver.deleted, resolver.grouped = false, false
		association, err := resolver.resolve(ctx, tx, key, "")
		if err != nil || association != "" {
			return association, err
		}
	}
	if model.Association == nil {
		return "", nil
	}
	var association *string
	err := tx.GetContext(ctx, &association, `SELECT `+*model.Association+`::text FROM `+model.Table+` WHERE `+model.PK+` = $1::uuid`, key)
	if errors.Is(err, sql.ErrNoRows) || association == nil {
		return "", nil
	}
	return *association, err
}

// documentDeleteOwners is the union of owners_of over the document's
// referrers; unknown reports an owner that cannot be determined (None).
func documentDeleteOwners(ctx context.Context, tx *sqlx.Tx, document string) (owners map[string]bool, unknown bool, err error) {
	owners = map[string]bool{}
	err = documentDeleteReferrers(ctx, tx, documentDeleteScopeLoaded.Models[documentDeleteLabel], document, func(label, key string) error {
		owner, err := documentDeleteResolve(ctx, tx, label, key)
		if err != nil {
			return err
		}
		if owner != "" {
			owners[owner] = true
			return nil
		}
		found := false
		err = documentDeleteReferrers(ctx, tx, documentDeleteScopeLoaded.Models[label], key, func(label, key string) error {
			owner, err := documentDeleteResolve(ctx, tx, label, key)
			if err != nil {
				return err
			}
			found = true
			if owner == "" {
				unknown = true
			} else {
				owners[owner] = true
			}
			return nil
		})
		if !found {
			unknown = true
		}
		return err
	})
	return owners, unknown, err
}

// documentDeleteCreatedFor is created_for: the associations the document's
// creation audit entries are indexed to are exactly this one.
func documentDeleteCreatedFor(ctx context.Context, tx *sqlx.Tx, document, association string) (bool, error) {
	indexed := []*string{}
	if err := tx.SelectContext(ctx, &indexed, `SELECT DISTINCT i.sport_association_id::text FROM application_auditlogindex i
 JOIN auditlog_logentry e ON e.id = i.log_entry_id JOIN django_content_type ct ON ct.id = e.content_type_id
 WHERE ct.app_label = 'docmanager' AND ct.model = 'document' AND e.object_pk = $1 AND e.action = 0`, document); err != nil {
		return false, err
	}
	return len(indexed) == 1 && indexed[0] != nil && *indexed[0] == association, nil
}

// Delete answers whether the document existed and belonged to the user's
// association; it is then deleted with Django's collector in the same
// transaction.
func (r *DocumentDelete) Delete(ctx context.Context, document, user string, audit domain.LoginAuditContext, group *string) (bool, error) {
	graph, err := documentDeleteGraph()
	if err != nil {
		return false, err
	}
	tx, err := beginOwnedTransaction(ctx, r.db, &sql.TxOptions{Isolation: sql.LevelReadCommitted})
	if err != nil {
		return false, err
	}
	defer tx.Rollback()
	run := &collaboratorDeleteRun{tx: tx.tx, graph: graph, audit: audit, group: group, now: r.now}
	rows, err := run.load(ctx, graph.Models[documentDeleteLabel], `t.document_id = $1::uuid FOR UPDATE`, document)
	if err != nil {
		return false, err
	}
	var association *string
	if err = tx.tx.GetContext(ctx, &association, `SELECT (SELECT sport_association_id::text FROM application_sportassociation
 WHERE user_id = $1::uuid AND NOT deleted ORDER BY sport_association_id LIMIT 1)`, user); err != nil {
		return false, err
	}
	if len(rows) == 0 || association == nil {
		return false, nil
	}
	owners, unknown, err := documentDeleteOwners(ctx, tx.tx, document)
	if err != nil {
		return false, err
	}
	for owner := range owners {
		if owner != *association {
			return false, nil
		}
	}
	if len(owners) == 0 || unknown {
		created, err := documentDeleteCreatedFor(ctx, tx.tx, document, *association)
		if err != nil || !created {
			return false, err
		}
	}
	if err = run.Delete(ctx, documentDeleteLabel, rows); err != nil {
		return false, fmt.Errorf("document delete: %w", err)
	}
	return true, tx.Commit()
}
