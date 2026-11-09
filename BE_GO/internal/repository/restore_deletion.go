package repository

import (
	"context"
	_ "embed"
	"encoding/json"
	"errors"
	"fmt"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/jmoiron/sqlx"
)

// restore_deletion_graph.json is Django's deletion graph for
// instance.restore.replacement.deletion_plan, written by
// TESTS/adapters/go/restore_deletion_graph.py in the format of
// collaborator_delete_graph.json (the restore harness fails when it drifts):
// every model the Collector reaches from the IMPORT_ORDER models, the scope
// (IMPORT_ORDER names and labels, the skipped ones, can_fast_delete of each
// root queryset) and, per scope model, the _meta.related_objects whose rows
// outside the scope refuse the replacement.
//
//go:embed restore_deletion_graph.json
var restoreDeletionGraphJSON []byte

type restoreDeletionScope struct {
	Name     string `json:"name"`
	Label    string `json:"label"`
	Skip     bool   `json:"skip"`
	FastRoot bool   `json:"fast_root"`
}

type restoreDeletionRelated struct {
	Model      string `json:"model"`
	Name       string `json:"name"`
	Table      string `json:"table"`
	PK         string `json:"pk"`
	PKType     string `json:"pk_type"`
	ManyToMany bool   `json:"many_to_many"`
	Column     string `json:"column"`
	Through    string `json:"through"`
	Source     string `json:"source"`
	Target     string `json:"target"`
}

type restoreDeletionExtra struct {
	Scope   []restoreDeletionScope              `json:"scope"`
	Related map[string][]restoreDeletionRelated `json:"related"`
}

// RestoreDeletion is the graph deletion_plan works on.
type RestoreDeletion struct {
	graph *collaboratorDeleteGraph
	extra restoreDeletionExtra
	names map[string]string // object name -> label for the scope models
}

// RestoreDeletionError is a RestoreError deletion_plan raises (its message).
type RestoreDeletionError struct{ Message string }

func (e *RestoreDeletionError) Error() string { return e.Message }

// ErrRestoreDeletionUnsupported marks a deletion the Go collector does not
// reproduce (a model the graph marks unsupported).
var ErrRestoreDeletionUnsupported = ErrCollaboratorDeleteUnsupported

// ErrRestoreDeletionProtected is the ProtectedError a PROTECT relation raises.
var ErrRestoreDeletionProtected = ErrCollaboratorDeleteProtected

// NewRestoreDeletion loads the embedded graph. auditlog renders a
// PositiveBigIntegerField like a BigIntegerField (smart_str of an int), the
// only audited type of the restore graph the shared collector table does not
// list.
func NewRestoreDeletion() (*RestoreDeletion, error) {
	var raw map[string]any
	if err := json.Unmarshal(restoreDeletionGraphJSON, &raw); err != nil {
		return nil, err
	}
	if models, ok := raw["models"].(map[string]any); ok {
		for _, model := range models {
			audit, _ := model.(map[string]any)["audit"].(map[string]any)
			fields, _ := audit["fields"].([]any)
			for _, field := range fields {
				entry := field.(map[string]any)
				if entry["type"] == "PositiveBigIntegerField" {
					entry["type"] = "BigIntegerField"
				}
			}
		}
	}
	normalized, err := json.Marshal(raw)
	if err != nil {
		return nil, err
	}
	deletion := &RestoreDeletion{graph: loadCollaboratorDeleteGraph(normalized), names: map[string]string{}}
	if err := json.Unmarshal(restoreDeletionGraphJSON, &deletion.extra); err != nil {
		return nil, err
	}
	if len(deletion.extra.Scope) == 0 {
		return nil, errors.New("restore deletion graph without scope")
	}
	for _, entry := range deletion.extra.Scope {
		deletion.names[entry.Name] = entry.Label
		if !entry.Skip && deletion.graph.Models[entry.Label] == nil {
			return nil, fmt.Errorf("restore deletion graph lacks %s", entry.Label)
		}
	}
	return deletion, nil
}

// RestoreCollector is deletion_plan(backup)'s Collector, ready to delete().
type RestoreCollector struct {
	collector *collaboratorDeleteCollector
}

// Plan is deletion_plan(backup) inside tx: for each scope model in
// IMPORT_ORDER order, the outsider checks of its related objects, then
// collector.collect(<base manager>.filter(pk__in=identities)); finally the
// collected models and fast deletes are checked against the scope.
// identities are the recovery export's keys by model name (canonical text).
// A RestoreError comes back as *RestoreDeletionError.
func (d *RestoreDeletion) Plan(ctx context.Context, tx *sqlx.Tx, identities map[string][]string, now func() time.Time) (*RestoreCollector, error) {
	run := &collaboratorDeleteRun{tx: tx, graph: d.graph, audit: domain.LoginAuditContext{}, now: now}
	collector := run.collector()
	scope := map[string]map[string]bool{}
	for name, keys := range identities {
		label, ok := d.names[name]
		if !ok {
			continue
		}
		set := map[string]bool{}
		for _, key := range keys {
			set[key] = true
		}
		scope[label] = set
	}
	scopeKeys := func(label string) []string {
		keys := []string{}
		for key := range scope[label] {
			keys = append(keys, key)
		}
		return keys
	}
	for _, entry := range d.extra.Scope {
		keys := identities[entry.Name]
		if entry.Skip || len(keys) == 0 {
			continue
		}
		model := d.graph.Models[entry.Label]
		for _, related := range d.extra.Related[entry.Label] {
			var exists bool
			var err error
			outside := scopeKeys(related.Model)
			if related.ManyToMany {
				err = tx.GetContext(ctx, &exists, `SELECT EXISTS(SELECT 1 FROM `+related.Table+` r JOIN `+related.Through+` t ON t.`+
					related.Source+` = r.`+related.PK+` WHERE t.`+related.Target+` = ANY($1`+collaboratorDeleteArray(model.PKType)+
					`) AND NOT (r.`+related.PK+` = ANY($2`+collaboratorDeleteArray(related.PKType)+`)))`, keys, outside)
			} else {
				err = tx.GetContext(ctx, &exists, `SELECT EXISTS(SELECT 1 FROM `+related.Table+` r WHERE r.`+related.Column+
					` = ANY($1`+collaboratorDeleteArray(model.PKType)+`) AND NOT (r.`+related.PK+` = ANY($2`+
					collaboratorDeleteArray(related.PKType)+`)))`, keys, outside)
			}
			if err != nil {
				return nil, err
			}
			if exists {
				return nil, &RestoreDeletionError{Message: entry.Name + ": esistono riferimenti esterni ai dati dell’associazione. " +
					"Nessun dato è stato sostituito."}
			}
		}
		if entry.FastRoot {
			collector.fast = append(collector.fast, collaboratorDeleteFast{model: model, columns: []string{model.PK}, keys: keys,
				keyType: model.PKType})
			continue
		}
		rows, err := run.loadBy(ctx, model, model.PK, model.PKType, keys)
		if err != nil {
			return nil, err
		}
		if len(rows) == 0 {
			continue
		}
		if err := collector.collect(ctx, model, rows, "", false); err != nil {
			return nil, err
		}
	}
	for _, label := range collector.order {
		bucket := collector.data[label]
		if bucket.model.AutoCreated {
			continue
		}
		outside := scope[label] == nil || label == collaboratorDeleteUser || label == "application.SportAssociation"
		for _, row := range bucket.rows {
			if !scope[label][row.key] {
				outside = true
			}
		}
		if outside {
			return nil, &RestoreDeletionError{Message: "La sostituzione coinvolgerebbe dati non inclusi nel backup di sicurezza: " +
				bucket.model.ObjectName + "."}
		}
	}
	for _, fast := range collector.fast {
		if fast.model.AutoCreated {
			continue
		}
		conditions := ""
		for index, column := range fast.columns {
			if index > 0 {
				conditions += " OR "
			}
			conditions += column + ` = ANY($1` + collaboratorDeleteArray(fast.keyType) + `)`
		}
		var exists bool
		if err := tx.GetContext(ctx, &exists, `SELECT EXISTS(SELECT 1 FROM `+fast.model.Table+` WHERE (`+conditions+`) AND NOT (`+
			fast.model.PK+` = ANY($2`+collaboratorDeleteArray(fast.model.PKType)+`)))`, fast.keys, scopeKeys(fast.model.Label)); err != nil {
			return nil, err
		}
		if exists {
			return nil, &RestoreDeletionError{Message: "La sostituzione coinvolgerebbe altri dati non esportati."}
		}
	}
	return &RestoreCollector{collector: collector}, nil
}

// Delete is Collector.delete() of the plan, in its transaction.
func (c *RestoreCollector) Delete(ctx context.Context) error { return c.collector.delete(ctx) }
