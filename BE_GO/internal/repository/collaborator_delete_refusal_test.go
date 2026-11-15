package repository

import "testing"

// auditDeleteJSONType is the JSON type to_jsonb renders an audited column type
// as (the collector reads rows with to_jsonb and decodes numbers as
// json.Number). logDelete renders strings and numbers in its default branch,
// BooleanField and TimeField in their own cases, and JSON in the jsonb
// renderer.
var auditDeleteJSONType = map[string]string{
	"CharField": "string", "TextField": "string", "UUIDField": "string", "DateField": "string", "TimeField": "string",
	"BooleanField": "bool", "IntegerField": "number", "PositiveIntegerField": "number", "PositiveSmallIntegerField": "number",
	"SmallIntegerField": "number", "BigIntegerField": "number", "AutoField": "number", "BigAutoField": "number",
	"DecimalField": "number",
}

// TestCollaboratorDeleteRefusalUnreachable pins the conditions of every
// ErrCollaboratorDeleteUnsupported return site against the committed graphs:
// none of them depends on the request or on stored row data, so no request and
// no stored row can reach the refusal (collaborator-delete-go.md, "Not
// ported"). It fails when a Django model change makes a site reachable again,
// which the graph drift fixtures report first.
func TestCollaboratorDeleteRefusalUnreachable(t *testing.T) {
	restore, err := NewRestoreDeletion()
	if err != nil {
		t.Fatalf("restore deletion graph: %v", err)
	}
	graphs := []struct {
		name  string
		graph *collaboratorDeleteGraph
	}{{"collaborator", collaboratorDeleteGraphLoaded}, {"restore", restore.graph}}

	// collect refuses a model loadCollaboratorDeleteGraph marks: a receiver,
	// relation, key, audit option or audit field type the collector does not
	// implement. These are properties of the model definitions.
	t.Run("no model is marked unsupported", func(t *testing.T) {
		for _, entry := range graphs {
			for _, label := range entry.graph.Order {
				if reason := entry.graph.Models[label].unsupported; reason != "" {
					t.Errorf("%s: %s is marked unsupported: %s", entry.name, label, reason)
				}
			}
		}
	})

	// collect refuses a fast delete whose model the graph lacks; every fast
	// relation of both graphs targets a model of its graph.
	t.Run("fast deletes stay inside the graph", func(t *testing.T) {
		for _, entry := range graphs {
			for _, label := range entry.graph.Order {
				for _, relation := range entry.graph.Models[label].Relations {
					if relation.Fast && entry.graph.Models[relation.Model] == nil {
						t.Errorf("%s: %s deletes %s by fast delete, outside the graph", entry.name, label, relation.Model)
					}
				}
			}
		}
	})

	// logDelete refuses an audit value outside string, number, bool and time;
	// every tracked field of both graphs is one of the rendered kinds, and a
	// value field's column type renders as a string or a number (or is one of
	// the two types with their own case).
	t.Run("every audited field is rendered", func(t *testing.T) {
		for _, entry := range graphs {
			for _, label := range entry.graph.Order {
				audit := entry.graph.Models[label].Audit
				if audit == nil {
					continue
				}
				for _, field := range audit.Fields {
					switch field.Kind {
					case "manager", "json", "fk", "datetime":
					case "reverse_one":
						related := entry.graph.Models[field.Related]
						if related == nil || related.Str != nil {
							t.Errorf("%s: %s.%s reverse one-to-one is not rendered", entry.name, label, field.Name)
						}
					case "value":
						kind, ok := auditDeleteJSONType[field.Type]
						if !ok {
							t.Errorf("%s: %s.%s value type %s is not rendered", entry.name, label, field.Name, field.Type)
							continue
						}
						if field.Type != "BooleanField" && field.Type != "TimeField" && kind != "string" && kind != "number" {
							t.Errorf("%s: %s.%s renders as %s", entry.name, label, field.Name, kind)
						}
					default:
						t.Errorf("%s: %s.%s has the unrendered kind %s", entry.name, label, field.Name, field.Kind)
					}
				}
			}
		}
	})

	// The delete entry points refuse a label their graph lacks. Each passes a
	// literal (the call site named beside it), never request input.
	documentGraph, err := documentDeleteGraph()
	if err != nil {
		t.Fatalf("document deletion graph: %v", err)
	}
	entryPoints := []struct {
		entry string
		label string
		graph *collaboratorDeleteGraph
	}{
		{"collaborators/<uid>/delete, user (collaborator_delete.go)", collaboratorDeleteUser, collaboratorDeleteGraphLoaded},
		{"collaborators/<uid>/delete, invitation (collaborator_delete.go)", collaboratorDeleteInvite, collaboratorDeleteGraphLoaded},
		{"balance-sheet/accounts/<uid>/delete (balance_sheet.go DeleteAccount)", "application.CustomAccounts", collaboratorDeleteGraphLoaded},
		{"personas/<pk> destroy (router_stock_deletes.go)", "application.Associate", collaboratorDeleteGraphLoaded},
		{"personas-subscriptions/<pk> destroy (router_stock_deletes.go)", "application.Subscription", collaboratorDeleteGraphLoaded},
		{"subscription/<uid>/delete (subscription_lifecycle.go DeleteSubscription)", "application.Subscription", collaboratorDeleteGraphLoaded},
		{"subscription/<uid>/archive (subscription_lifecycle.go DeleteInvoice)", "application.Invoice", collaboratorDeleteGraphLoaded},
		{"subscription/<uid>/edit (subscription_writes.go DeleteTutorCollected)", "application.Associate", collaboratorDeleteGraphLoaded},
		{"clear_deleted_subscription (clear_deleted_subscription.go)", "application.Payment", collaboratorDeleteGraphLoaded},
		{"clear_deleted_subscription (clear_deleted_subscription.go)", "application.Invoice", collaboratorDeleteGraphLoaded},
		{"document/<uid>/delete (document_delete.go)", documentDeleteLabel, documentGraph},
		{"documents bulk delete (documents_archive.go)", documentDeleteLabel, documentGraph},
		{"instructor/<uid>/hours delete (instructor_hours.go)", documentDeleteLabel, documentGraph},
		{"modules/response/<id>/add-attachment (modules_responses.go)", documentDeleteLabel, documentGraph},
	}
	t.Run("delete entry points name a model of their graph", func(t *testing.T) {
		for _, entry := range entryPoints {
			if entry.graph.Models[entry.label] == nil {
				t.Errorf("%s: %s is not in the graph", entry.entry, entry.label)
			}
		}
	})
}
