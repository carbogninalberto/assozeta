package repository

import "testing"

// The export cleanup harness has no PROTECT seed: the committed document
// deletion graph declares no PROTECT relation reachable from
// docmanager.Document. A PROTECT relation added to the graph must revisit that
// harness and the cleanup services' collector-failure behaviour.
func TestExportCleanupDocumentGraphHasNoProtectRelation(t *testing.T) {
	graph := loadCollaboratorDeleteGraph(documentDeletionGraphJSON)
	models := []string{documentDeleteLabel}
	seen := map[string]bool{documentDeleteLabel: true}
	for len(models) > 0 {
		label := models[0]
		models = models[1:]
		model := graph.Models[label]
		if model == nil {
			t.Fatalf("graph lacks %s", label)
		}
		for _, relation := range model.Relations {
			if relation.OnDelete == "PROTECT" {
				t.Errorf("%s.%s is PROTECT", label, relation.Field)
			}
			if relation.OnDelete == "CASCADE" && !seen[relation.Model] {
				seen[relation.Model] = true
				models = append(models, relation.Model)
			}
		}
	}
}
