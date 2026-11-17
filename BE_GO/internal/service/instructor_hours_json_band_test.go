package service

import (
	"context"
	"errors"
	"strings"
	"testing"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/refusal"
)

// instructorHoursBandStore is the two-method store the update band needs; the
// embedded interface panics on anything else, so the test sees a wider call.
type instructorHoursBandStore struct {
	domain.InstructorHoursStore
	tx *instructorHoursBandTx
}

func (s *instructorHoursBandStore) BeginInstructorHours(context.Context) (domain.InstructorHoursTransaction, error) {
	return s.tx, nil
}

// instructorHoursBandTx records the transaction's writes in order.
type instructorHoursBandTx struct {
	domain.InstructorHoursTransaction
	old       *domain.InstructorHour
	calls     []string
	updated   *domain.InstructorHour
	audit     []domain.InstructorHoursAudit
	committed bool
}

func (t *instructorHoursBandTx) OwnedHour(context.Context, string, string, string) (*domain.InstructorHour, error) {
	return t.old, nil
}

func (t *instructorHoursBandTx) FirstAssociation(context.Context, string) (*string, error) {
	return nil, nil
}

func (t *instructorHoursBandTx) UpdateHour(_ context.Context, row domain.InstructorHour) error {
	t.calls = append(t.calls, "update")
	t.updated = &row
	return nil
}

func (t *instructorHoursBandTx) AppendAudit(_ context.Context, event domain.InstructorHoursAudit, _ *string, _ domain.LoginAuditContext,
	_ time.Time) error {
	t.calls = append(t.calls, "audit")
	t.audit = append(t.audit, event)
	return nil
}

func (t *instructorHoursBandTx) Commit() error {
	t.calls = append(t.calls, "commit")
	t.committed = true
	return nil
}

func (t *instructorHoursBandTx) Rollback() error {
	t.calls = append(t.calls, "rollback")
	return nil
}

const (
	instructorHoursBandUser       = "b1000000-0000-4000-8000-000000000001"
	instructorHoursBandInstructor = "b1000000-0000-4000-8000-000000000002"
	instructorHoursBandHour       = "b1000000-0000-4000-8000-000000000003"
)

// instructorHoursBandUpdate runs instructor_hours_update's form path with the
// given body pairs against the same stored hour every time.
func instructorHoursBandUpdate(t *testing.T, pairs ...string) (*instructorHoursBandTx, error) {
	t.Helper()
	notes := "stored"
	tx := &instructorHoursBandTx{old: &domain.InstructorHour{ID: instructorHoursBandHour, InstructorID: instructorHoursBandInstructor,
		CreationDate: time.Date(2026, 9, 10, 18, 0, 0, 0, time.UTC), Date: "2026-09-10", CompensationType: "hourly",
		Hours: "1.00", HourlyBilling: "0.00", Amount: "12.00", Notes: &notes}}
	store := &instructorHoursBandStore{tx: tx}
	service := NewInstructorHoursService(store, func() time.Time { return time.Date(2026, 9, 20, 10, 0, 0, 0, time.UTC) },
		func() string { return instructorHoursBandHour })
	account := &domain.Account{ID: instructorHoursBandUser, Email: "owner@example.test"}
	identity := domain.ResolvedIdentity{Actor: account, Effective: account, ScopeUser: account}
	body := domain.RequestObject{}
	for index := 0; index+1 < len(pairs); index += 2 {
		body = append(body, domain.RequestField{Name: pairs[index], Value: pairs[index+1]})
	}
	load := func() (any, bool, error) { return body, true, nil }
	return tx, service.Update(context.Background(), identity, instructorHoursBandInstructor, instructorHoursBandHour, load, "", domain.LoginAuditContext{})
}

// instructorHoursBandDumps is the auditlog changes text of a parsed form value:
// json.dumps(value, sort_keys=True).
func instructorHoursBandDumps(t *testing.T, text string) string {
	t.Helper()
	value, err := dto.ParseFormJSON(text)
	if err != nil {
		t.Fatalf("expected form value: %v", err)
	}
	return string(dto.PythonJSONDumpsSorted(value))
}

// The measured save-time band of instructor_hours_update (owner decision X15):
// a written JSON value nested up to 9938 stores the row and one audit entry;
// 9939..9941 keeps the auditlog pre_save entry, skips the UPDATE and answers the
// 500 page; from 9942 the parse answers before any write.
func TestInstructorHoursUpdateJSONBand(t *testing.T) {
	deep := func(depth int) string { return strings.Repeat("[", depth) + strings.Repeat("]", depth) }

	tx, err := instructorHoursBandUpdate(t, "courses", deep(9938), "notes", "probe")
	if err != nil {
		t.Fatalf("depth 9938: %v", err)
	}
	if tx.updated == nil || *tx.updated.Courses != instructorHoursBandDumps(t, deep(9938)) {
		t.Fatalf("depth 9938 stored %v", tx.updated)
	}
	if len(tx.audit) != 1 || tx.audit[0].Changes["courses"][0] != "null" ||
		tx.audit[0].Changes["courses"][1] != instructorHoursBandDumps(t, deep(9938)) ||
		tx.audit[0].Changes["notes"] != [2]string{"stored", "probe"} {
		t.Fatalf("depth 9938 audit %#v", tx.audit)
	}
	if !tx.committed || strings.Join(tx.calls, ",") != "update,audit,commit,rollback" {
		t.Fatalf("depth 9938 calls %v", tx.calls)
	}

	for _, name := range []string{"courses", "calculation_data"} {
		for _, depth := range []int{9939, 9941} {
			tx, err := instructorHoursBandUpdate(t, name, deep(depth), "notes", "probe")
			if !errors.Is(err, errInstructorHoursUpdateJSONTooDeep) || !errors.Is(err, refusal.ErrAppState) {
				t.Fatalf("%s depth %d: %v", name, depth, err)
			}
			if errors.Is(err, refusal.ErrNotPorted) {
				t.Fatalf("%s depth %d is handed off: %v", name, depth, err)
			}
			if tx.updated != nil {
				t.Fatalf("%s depth %d updated the row", name, depth)
			}
			// Django's pre_save entry is written before the failing UPDATE: it
			// keeps the same changes text and is committed, alone.
			if len(tx.audit) != 1 || tx.audit[0].Action != 1 || tx.audit[0].Changes[name][1] != instructorHoursBandDumps(t, deep(depth)) {
				t.Fatalf("%s depth %d audit %#v", name, depth, tx.audit)
			}
			if !tx.committed || strings.Join(tx.calls, ",") != "audit,commit,rollback" {
				t.Fatalf("%s depth %d calls %v", name, depth, tx.calls)
			}
		}
	}

	tx, err = instructorHoursBandUpdate(t, "courses", deep(9942), "notes", "probe")
	if err == nil || !errors.Is(err, refusal.ErrAppState) || errors.Is(err, refusal.ErrNotPorted) {
		t.Fatalf("depth 9942: %v", err)
	}
	if tx.updated != nil || len(tx.audit) != 0 || tx.committed {
		t.Fatalf("depth 9942 wrote %v %#v", tx.calls, tx.audit)
	}

	// A duplicate key overwrites the deep text: the row stores the shallow value
	// the dump walks, so a text past the band is still a 200.
	repeated := `{"a": ` + strings.Repeat(`{"a":`, 9900) + "1" + strings.Repeat("}", 9900) + `, "a": 1}`
	tx, err = instructorHoursBandUpdate(t, "courses", repeated)
	if err != nil {
		t.Fatalf("repeated key: %v", err)
	}
	if tx.updated == nil || *tx.updated.Courses != `{"a": 1}` || len(tx.audit) != 1 || tx.audit[0].Changes["courses"][1] != `{"a": 1}` {
		t.Fatalf("repeated key: row %v audit %#v", tx.updated, tx.audit)
	}
}
