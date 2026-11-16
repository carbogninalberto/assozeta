package repository

import "testing"

// The committed document deletion graph is Django's Collector rooted at
// docmanager.Document and every model it reaches is implemented by the shared
// collector.
func TestDocumentDeletionGraphSupported(t *testing.T) {
	graph := loadCollaboratorDeleteGraph(documentDeletionGraphJSON)
	if len(graph.Order) != len(graph.Models) || graph.Order[0] != documentDeleteLabel {
		t.Fatalf("graph order: %d models, %d listed, first %q", len(graph.Models), len(graph.Order), graph.Order[0])
	}
	for _, label := range graph.Order {
		model := graph.Models[label]
		if reason := model.unsupported; reason != "" {
			t.Errorf("%s: %s", label, reason)
		}
		if len(model.PreDelete) > 0 {
			t.Errorf("%s: pre_delete receiver %v is not ported", label, model.PreDelete)
		}
		if len(model.PostDelete) > 0 && model.PostDelete[0] != "auditlog.receivers.log_delete" {
			t.Errorf("%s: post_delete receiver %v is not ported", label, model.PostDelete)
		}
	}
	document := graph.Models[documentDeleteLabel]
	if document.FastRoot {
		t.Error("Document.delete() has an auditlog post_delete receiver: it cannot fast delete")
	}
	for _, label := range []string{"application.MedicalCertificate", "application.MedicalAppointments",
		"application.SubscriptionFile", "application.SportAssociationDocumentsArchive"} {
		if graph.Models[label] == nil {
			t.Errorf("graph lacks the cascade model %s", label)
		}
	}
}

// The Document relations the export delete depends on: nullable and non-null
// CASCADE rows, the SET_NULL columns and the many-to-many through models.
func TestDocumentDeletionRelations(t *testing.T) {
	graph := loadCollaboratorDeleteGraph(documentDeletionGraphJSON)
	relations := map[string]collaboratorDeleteRelation{}
	for _, relation := range graph.Models[documentDeleteLabel].Relations {
		relations[relation.Model+"."+relation.Field] = relation
	}
	for key, want := range map[string]struct {
		onDelete string
		fast     bool
	}{
		"application.MedicalCertificate.document":                                   {"CASCADE", false},
		"application.MedicalAppointments.document":                                  {"CASCADE", false},
		"application.SubscriptionFile.document":                                     {"CASCADE", false},
		"application.SportAssociationDocumentsArchive.document":                     {"CASCADE", false},
		"application.Subscription.document_pdf":                                     {"SET_NULL", false},
		"application.Invoice.document_pdf":                                          {"SET_NULL", false},
		"application.CustomerInvoice.pdf":                                           {"SET_NULL", false},
		"application.Invoice_attached_documents.document":                           {"CASCADE", true},
		"application.Payment_attachments.document":                                  {"CASCADE", true},
		"application.CourseLocation_documents.document":                             {"CASCADE", true},
		"application.ModuleResponses_attachments.document":                          {"CASCADE", true},
		"application.SubscriptionMembership_attached_membership_documents.document": {"CASCADE", true},
		"application.Instructor_documents.document":                                 {"CASCADE", true},
	} {
		relation, ok := relations[key]
		if !ok {
			t.Errorf("graph lacks %s", key)
			continue
		}
		if relation.OnDelete != want.onDelete || relation.Fast != want.fast {
			t.Errorf("%s: on_delete %s fast %t, want %s fast %t", key, relation.OnDelete, relation.Fast, want.onDelete, want.fast)
		}
	}
	// MedicalCertificate's own dependent: Subscription.medical is cleared, not
	// collected.
	medical := graph.Models["application.MedicalCertificate"]
	if medical == nil {
		t.Fatal("graph lacks application.MedicalCertificate")
	}
	cleared := false
	for _, relation := range medical.Relations {
		if relation.Model == "application.Subscription" && relation.Field == "medical" && relation.OnDelete == "SET_NULL" {
			cleared = true
		}
	}
	if !cleared {
		t.Error("graph lacks the Subscription.medical SET_NULL dependent")
	}
}

// Every audited value field of the document graph renders as auditlog's
// smart_str of a string or a number (or with its own BooleanField/TimeField
// case); Document.file_size_bytes is a PositiveBigIntegerField.
func TestDocumentDeletionAuditedFields(t *testing.T) {
	graph := loadCollaboratorDeleteGraph(documentDeletionGraphJSON)
	for _, label := range graph.Order {
		audit := graph.Models[label].Audit
		if audit == nil {
			continue
		}
		for _, field := range audit.Fields {
			if field.Kind != "value" {
				continue
			}
			if !collaboratorDeleteValueTypes[field.Type] {
				t.Errorf("%s.%s value type %s is not rendered", label, field.Name, field.Type)
			}
		}
	}
	audited := false
	for _, field := range graph.Models[documentDeleteLabel].Audit.Fields {
		if field.Name == "file_size_bytes" && field.Type == "PositiveBigIntegerField" {
			audited = true
		}
	}
	if !audited {
		t.Error("Document.file_size_bytes is not in the delete diff")
	}
}
