package service

import (
	"context"
	"testing"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
)

// campsDescriptionRow is a walk row whose payment has the given stored
// description and meta, and one matching meta_payment_categories item (so the
// first save runs inside the category loop).
func campsDescriptionRow(id string, description, meta *string) domain.CampsWalkRow {
	row := campsPaymentsTestRow(id, campsWalkText("category"), campsWalkText(campsPaymentsMeta))
	row.Payment.Description, row.Payment.Meta = description, meta
	return row
}

func campsDescriptionPlan(t *testing.T, row domain.CampsWalkRow) ([]campsPaymentSave, *dto.CampsPythonError) {
	t.Helper()
	request := &campsRequest{tx: campsWalkFake{rows: []domain.CampsWalkRow{row}}}
	saves, failure, rejected, err := request.campsWalkRow(context.Background(), row, campsPaymentsUpdate(9.5),
		map[string]*domain.CampsPayment{}, nil)
	if err != nil {
		t.Fatalf("walk refused: %v", err)
	}
	if rejected {
		t.Fatal("the description save was rejected by the database")
	}
	return saves, failure
}

// A truthy non-string meta description left on the instance by the first save
// fails the next save's .lower() before it writes: the first save stays and the
// walk stops with Python's AttributeError text.
func TestCampsWalkDescriptionValueTruthyNonStringFailsNextSave(t *testing.T) {
	tests := []struct {
		name string
		meta string
		text string
		kind string
	}{
		{"list", `{"description":["a","b"]}`, "['a', 'b']", "list"},
		{"dict", `{"description":{"a":"b"}}`, "{'a': 'b'}", "dict"},
		{"int", `{"description":5}`, "5", "int"},
		{"float", `{"description":5.5}`, "5.5", "float"},
		{"bool", `{"description":true}`, "True", "bool"},
		{"upper case placeholder", `{"description":["a","b"]}`, "['a', 'b']", "list"},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			description := "Quota Corso"
			if tc.name == "upper case placeholder" {
				description = "QUOTA CORSO"
			}
			row := campsDescriptionRow("visible", campsWalkText(description), campsWalkText(tc.meta))
			saves, failure := campsDescriptionPlan(t, row)
			if failure == nil || failure.Message != "'"+tc.kind+"' object has no attribute 'lower'" {
				t.Fatalf("expected the %s .lower() failure, got %+v", tc.kind, failure)
			}
			if len(saves) != 1 {
				t.Fatalf("expected only the first save, got %+v", saves)
			}
			first := saves[0]
			if first.row.Description == nil || *first.row.Description != tc.text {
				t.Fatalf("the first save stored %v, want %q", first.row.Description, tc.text)
			}
			if first.event == nil || first.event.Changes["description"] != [2]string{description, tc.text} {
				t.Fatalf("the first save's audit entry is %+v", first.event)
			}
			if _, ok := first.event.Changes["meta_payment_categories"]; !ok {
				t.Fatalf("the first save's audit entry misses the meta change: %+v", first.event)
			}
			if saves[0].row.Amount != "0.00" {
				t.Fatalf("the final amount was written by the first save: %+v", saves[0].row)
			}
		})
	}
}

// A falsy non-string keeps the text and skips the replacement: the second save
// writes the same description and the walk completes.
func TestCampsWalkDescriptionValueFalsyNonStringCompletes(t *testing.T) {
	tests := []struct {
		name string
		meta string
		text string
	}{
		{"int zero", `{"description":0}`, "0"},
		{"empty list", `{"description":[]}`, "[]"},
		{"false", `{"description":false}`, "False"},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			row := campsDescriptionRow("visible", campsWalkText("Quota Corso"), campsWalkText(tc.meta))
			saves, failure := campsDescriptionPlan(t, row)
			if failure != nil {
				t.Fatalf("the walk failed: %v", failure)
			}
			if len(saves) != 2 {
				t.Fatalf("expected the meta save and the final save, got %+v", saves)
			}
			for index, save := range saves {
				if save.row.Description == nil || *save.row.Description != tc.text {
					t.Fatalf("save %d stored %v, want %q", index, save.row.Description, tc.text)
				}
			}
			if saves[0].event.Changes["description"] != [2]string{"Quota Corso", tc.text} {
				t.Fatalf("first audit description %v", saves[0].event.Changes["description"])
			}
			if _, ok := saves[1].event.Changes["description"]; ok {
				t.Fatalf("the final save re-recorded the description: %+v", saves[1].event)
			}
			if saves[1].row.Amount != "29.50" {
				t.Fatalf("final amount %q, want 29.50", saves[1].row.Amount)
			}
		})
	}
}

// A null meta description leaves the instance NULL; the next save recomputes
// the default description: Iscrizione for a subscription, the course
// placeholder (overridden with NULL again) for a course.
func TestCampsWalkDescriptionValueNullRecomputes(t *testing.T) {
	t.Run("subscription", func(t *testing.T) {
		row := campsDescriptionRow("visible", campsWalkText("Quota Corso"), campsWalkText(`{"description":null}`))
		saves, failure := campsDescriptionPlan(t, row)
		if failure != nil {
			t.Fatalf("the walk failed: %v", failure)
		}
		if len(saves) != 2 {
			t.Fatalf("expected the meta save and the final save, got %+v", saves)
		}
		if saves[0].row.Description != nil || saves[0].event.Changes["description"] != [2]string{"Quota Corso", "None"} {
			t.Fatalf("first save %v / %+v", saves[0].row.Description, saves[0].event)
		}
		if saves[1].row.Description == nil || *saves[1].row.Description != "Iscrizione" ||
			saves[1].event.Changes["description"] != [2]string{"None", "Iscrizione"} {
			t.Fatalf("second save %v / %+v", saves[1].row.Description, saves[1].event)
		}
	})
	t.Run("course", func(t *testing.T) {
		row := campsDescriptionRow("visible", campsWalkText("Quota Corso"), campsWalkText(`{"description":null}`))
		row.Payment.Subject = 2
		saves, failure := campsDescriptionPlan(t, row)
		if failure != nil {
			t.Fatalf("the walk failed: %v", failure)
		}
		for index, save := range saves {
			if save.row.Description != nil {
				t.Fatalf("save %d stored %v, want NULL", index, save.row.Description)
			}
		}
		if saves[0].event.Changes["description"] != [2]string{"Quota Corso", "None"} {
			t.Fatalf("first audit description %v", saves[0].event.Changes["description"])
		}
		if _, ok := saves[1].event.Changes["description"]; ok {
			t.Fatalf("the recomputed second save recorded a description change: %+v", saves[1].event)
		}
	})
}

// A string replacement is the instance's text: the second save keeps it.
func TestCampsWalkDescriptionValueStringCompletes(t *testing.T) {
	for _, description := range []string{"Quota Corso", "QUOTA CORSO"} {
		row := campsDescriptionRow("visible", campsWalkText(description), campsWalkText(`{"description":"Danza"}`))
		saves, failure := campsDescriptionPlan(t, row)
		if failure != nil {
			t.Fatalf("%q: the walk failed: %v", description, failure)
		}
		if len(saves) != 2 {
			t.Fatalf("%q: expected two saves, got %+v", description, saves)
		}
		for index, save := range saves {
			if save.row.Description == nil || *save.row.Description != "Danza" {
				t.Fatalf("%q save %d stored %v, want Danza", description, index, save.row.Description)
			}
		}
	}
}

// The next subscription period's payment is a new instance: it starts from the
// stored text, not the previous instance's Python value.
func TestCampsWalkDescriptionValueClearedForNextRow(t *testing.T) {
	state := map[string]*domain.CampsPayment{}
	update := campsPaymentsUpdate(9.5)
	first := campsDescriptionRow("same", campsWalkText("Quota Corso"), campsWalkText(`{"description":0}`))
	request := &campsRequest{tx: campsWalkFake{rows: []domain.CampsWalkRow{first}}}
	if saves, failure, _, err := request.campsWalkRow(context.Background(), first, update, state, nil); err != nil || failure != nil ||
		len(saves) != 2 {
		t.Fatalf("first row: %+v/%v/%v", saves, failure, err)
	}
	stored := state["same"]
	if stored == nil || stored.DescriptionValue != nil || stored.Description == nil || *stored.Description != "0" {
		t.Fatalf("the state kept the instance's Python value: %+v", stored)
	}
	second := campsDescriptionRow("same", campsWalkText("0"), nil)
	if saves, failure, _, err := request.campsWalkRow(context.Background(), second, update, state, nil); err != nil || failure != nil ||
		len(saves) != 2 {
		t.Fatalf("second row: %+v/%v/%v", saves, failure, err)
	}
}
