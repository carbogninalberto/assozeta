package service

import (
	"context"
	"errors"
	"testing"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
)

// campsEdgesFake is camps_and_retreats_periods_services_update's transaction:
// the walk's rows with the deep column, every write recorded.
type campsEdgesFake struct {
	domain.CampsTransaction
	rows   []domain.CampsWalkRow
	events *[]string
}

func (f *campsEdgesFake) log(event string) { *f.events = append(*f.events, event) }

func (f *campsEdgesFake) OwnerAssociation(context.Context, string) (string, error) {
	return "association", nil
}

func (f *campsEdgesFake) Service(context.Context, string, bool) (*domain.CampsService, error) {
	row := campsWalkService()
	row.PeriodID, row.CampAssociationID = "period", "association"
	return &row, nil
}

func (f *campsEdgesFake) PeriodRelated(context.Context, string) (domain.CampsRelated, error) {
	return domain.CampsRelated{AssociationID: campsWalkText("association")}, nil
}

func (f *campsEdgesFake) WalkRows(context.Context, string) ([]domain.CampsWalkRow, error) {
	return f.rows, nil
}

func (f *campsEdgesFake) UpdateService(context.Context, domain.CampsService) error {
	f.log("service-save")
	return nil
}

func (f *campsEdgesFake) SavePayment(context.Context, domain.CampsPayment) error {
	f.log("payment-save")
	return nil
}

func (f *campsEdgesFake) DefaultPaymentCategory(context.Context, *string) (*string, *string, error) {
	return campsWalkText("category"), campsWalkText("Categoria"), nil
}

func (f *campsEdgesFake) PaymentLinkedName(context.Context, string) (domain.PaymentLinkedName, error) {
	return domain.PaymentLinkedName{}, nil
}

func (f *campsEdgesFake) AppendAudit(context.Context, domain.CampsAudit, domain.LoginAuditContext, time.Time) error {
	f.log("audit")
	return nil
}

func (f *campsEdgesFake) Commit() error {
	f.log("commit")
	return nil
}

func (f *campsEdgesFake) Rollback() error {
	f.log("rollback")
	return nil
}

type campsEdgesStore struct {
	domain.CampsStore
	tx *campsEdgesFake
}

func (s campsEdgesStore) BeginCamps(context.Context) (domain.CampsTransaction, error) {
	return s.tx, nil
}

func campsEdgesRow(id string, depth int) domain.CampsWalkRow {
	row := campsWalkRow(id, 1, campsWalkText("Iscrizione"), campsWalkText("[]"))
	if depth > 0 {
		row.Payment.Course = jsonEdgesArray(depth)
	}
	return row
}

// camps_and_retreats_periods_services_update's measured edges: past Index the
// entry's audit index resolves through the resolver's fallbacks; past Save (the
// same level as Load) the view's own load raises before this row's saves, and
// the non-atomic view keeps the serializer's save and the earlier rows' saves.
func TestCampsPeriodsStoredJSONBands(t *testing.T) {
	for _, test := range []struct {
		name      string
		depth     int
		recursion bool
		fallback  bool
	}{
		{name: "index at", depth: 9925},
		{name: "index past", depth: 9926, fallback: true},
		{name: "save and load at", depth: 9933, fallback: true},
		{name: "save and load past", depth: 9934, recursion: true},
	} {
		t.Run(test.name, func(t *testing.T) {
			events := []string{}
			fake := &campsEdgesFake{events: &events, rows: []domain.CampsWalkRow{campsEdgesRow("payment", test.depth)}}
			service := NewCampsPeriods(campsEdgesStore{tx: fake}, func() time.Time { return time.Date(2026, 11, 9, 10, 0, 0, 0, time.UTC) },
				func() string { return "id" })
			owner := &domain.Account{ID: "owner", Role: domain.RoleAssociation}
			_, err := service.UpdateService(context.Background(), domain.ResolvedIdentity{Actor: owner, Effective: owner, ScopeUser: owner},
				"a1000000-0000-4000-8000-000000995010", domain.RequestObject{}, false, "", domain.LoginAuditContext{})
			if !test.recursion {
				if err != nil {
					t.Fatalf("err=%v", err)
				}
				if !containsEvent(events, "commit") || !containsEvent(events, "payment-save") || !containsEvent(events, "audit") {
					t.Fatalf("events %v: a write is missing", events)
				}
				return
			}
			var failure *CampsSaveFailure
			if !errors.As(err, &failure) {
				t.Fatalf("err=%v, want the view's 500 {'error': str(e)}", err)
			}
			if failure.Err.Message != recursionArrayText {
				t.Fatalf("message %q", failure.Err.Message)
			}
			// The serializer's save and the earlier rows' saves stay.
			if !containsEvent(events, "commit") || !containsEvent(events, "service-save") {
				t.Fatalf("events %v: the earlier writes were not kept", events)
			}
			if containsEvent(events, "payment-save") {
				t.Fatalf("events %v: the failing row was saved", events)
			}
		})
	}
}

// A deep row stops the walk after the rows before it were saved.
func TestCampsPeriodsStoredJSONStopsAfterEarlierRows(t *testing.T) {
	events := []string{}
	fake := &campsEdgesFake{events: &events, rows: []domain.CampsWalkRow{campsEdgesRow("first", 0), campsEdgesRow("second", 9934)}}
	service := NewCampsPeriods(campsEdgesStore{tx: fake}, func() time.Time { return time.Date(2026, 11, 9, 10, 0, 0, 0, time.UTC) },
		func() string { return "id" })
	owner := &domain.Account{ID: "owner", Role: domain.RoleAssociation}
	_, err := service.UpdateService(context.Background(), domain.ResolvedIdentity{Actor: owner, Effective: owner, ScopeUser: owner},
		"a1000000-0000-4000-8000-000000995010", domain.RequestObject{}, false, "", domain.LoginAuditContext{})
	var failure *CampsSaveFailure
	if !errors.As(err, &failure) || failure.Err.Message != recursionArrayText {
		t.Fatalf("err=%v", err)
	}
	if !containsEvent(events, "payment-save") || !containsEvent(events, "commit") {
		t.Fatalf("events %v: the first row's save was not kept", events)
	}
}

// The walk's plan carries the index fallback on the entry of a row nested past
// the resolver's reload, and refuses a row its own load cannot read.
func TestCampsWalkStoredJSONPlan(t *testing.T) {
	plan := func(row domain.CampsWalkRow) ([]campsPaymentSave, *dto.CampsPythonError) {
		request := &campsRequest{tx: campsWalkFake{rows: []domain.CampsWalkRow{row}}}
		saves, failure, _, err := request.campsWalkRow(context.Background(), row, campsPaymentsUpdate(9.5),
			map[string]*domain.CampsPayment{}, nil)
		if err != nil {
			t.Fatalf("walk failed: %v", err)
		}
		return saves, failure
	}
	for _, test := range []struct {
		name     string
		depth    int
		fallback bool
		failure  bool
	}{
		{name: "index at", depth: 9925},
		{name: "index past", depth: 9926, fallback: true},
		{name: "save and load at", depth: 9933, fallback: true},
		{name: "save and load past", depth: 9934, failure: true},
	} {
		t.Run(test.name, func(t *testing.T) {
			saves, failure := plan(campsEdgesRow("payment", test.depth))
			if test.failure {
				if failure == nil || failure.Message != recursionArrayText {
					t.Fatalf("failure %+v", failure)
				}
				if len(saves) != 0 {
					t.Fatalf("saves %+v before a failed load", saves)
				}
				return
			}
			if failure != nil {
				t.Fatalf("failure %v", failure)
			}
			for index, save := range saves {
				if save.event == nil || save.event.IndexFallback != test.fallback {
					t.Fatalf("save %d entry %+v: fallback want %v", index, save.event, test.fallback)
				}
			}
		})
	}
}
