package repository

import "testing"

// Every model of the committed Django graph is implemented by the collector.
func TestCollaboratorDeleteGraphSupported(t *testing.T) {
	graph := loadCollaboratorDeleteGraph(collaboratorDeleteGraphJSON)
	if len(graph.Order) != len(graph.Models) || graph.Order[0] != collaboratorDeleteUser {
		t.Fatalf("graph order: %d models, %d listed", len(graph.Models), len(graph.Order))
	}
	for _, label := range graph.Order {
		if reason := graph.Models[label].unsupported; reason != "" {
			t.Errorf("%s: %s", label, reason)
		}
	}
}

// Collector.sort: repeated passes in insertion order; a cycle keeps the
// insertion order.
func TestCollaboratorDeleteSort(t *testing.T) {
	collector := &collaboratorDeleteCollector{order: []string{"User", "Payment", "Subscription", "CourseSubscription", "Installment"},
		dependencies: map[string]map[string]bool{"User": {"Subscription": true}, "CourseSubscription": {"Installment": true}}}
	collector.sort()
	want := []string{"Payment", "Subscription", "Installment", "User", "CourseSubscription"}
	for index := range want {
		if collector.order[index] != want[index] {
			t.Fatalf("sorted %v, want %v", collector.order, want)
		}
	}
	cycle := &collaboratorDeleteCollector{order: []string{"A", "B"}, dependencies: map[string]map[string]bool{"A": {"B": true}, "B": {"A": true}}}
	cycle.sort()
	if cycle.order[0] != "A" || cycle.order[1] != "B" {
		t.Fatalf("cycle reordered: %v", cycle.order)
	}
}

func TestCollaboratorDeleteKeyOrder(t *testing.T) {
	if !collaboratorDeleteLess("AutoField", "9", "10") || collaboratorDeleteLess("UUIDField", "b", "a") {
		t.Fatal("key order")
	}
	if collaboratorDeleteTime("10:30:00.5") != "10:30:00.500000" || collaboratorDeleteTime("10:30:00") != "10:30:00" {
		t.Fatal("time text")
	}
}
