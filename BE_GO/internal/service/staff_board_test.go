package service

import (
	"context"
	"errors"
	"testing"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
)

const (
	sbOwner        = "a1000000-0000-4000-8000-000000700001"
	sbCollaborator = "a1000000-0000-4000-8000-000000700002"
	sbOther        = "a1000000-0000-4000-8000-000000700003"
	sbAssociation  = "a1000000-0000-4000-8000-000000703001"
	sbMessage      = "a1000000-0000-4000-8000-000000704001"
)

type sbStore struct {
	rows      map[string]domain.StaffBoardMessage
	events    []string
	commitErr error
	eventErr  error
	begun     int
}

type sbTx struct {
	store    *sbStore
	pending  map[string]domain.StaffBoardMessage
	finished bool
}

func (s *sbStore) StaffBoardMessages(context.Context, string) ([]domain.StaffBoardMessage, error) {
	var out []domain.StaffBoardMessage
	for _, row := range s.rows {
		out = append(out, row)
	}
	return out, nil
}
func (s *sbStore) BeginStaffBoardMutations(context.Context) (domain.StaffBoardTransaction, error) {
	s.begun++
	pending := map[string]domain.StaffBoardMessage{}
	for k, v := range s.rows {
		pending[k] = v
	}
	s.events = append(s.events, "begin")
	return &sbTx{store: s, pending: pending}, nil
}
func (t *sbTx) OwnerForUpdate(_ context.Context, id string) (int, bool, bool, error) {
	t.store.events = append(t.store.events, "lock-owner:"+id)
	return domain.RoleAssociation, true, false, nil
}
func (t *sbTx) AssociationForOwner(context.Context, string, *string) (string, error) {
	return sbAssociation, nil
}
func (t *sbTx) StaffBoardMessageForUpdate(_ context.Context, id, association string) (domain.StaffBoardMessage, error) {
	row, ok := t.pending[id]
	if !ok || row.AssociationID != association {
		return domain.StaffBoardMessage{}, domain.ErrStaffBoardNotFound
	}
	return row, nil
}
func (t *sbTx) InsertStaffBoardMessage(_ context.Context, row domain.StaffBoardWrite) error {
	t.store.events = append(t.store.events, "insert")
	t.pending[row.ID] = domain.StaffBoardMessage{ID: row.ID, AssociationID: row.AssociationID, AssociationOwnerID: sbOwner,
		AssociationDenomination: "ASD", Author: &domain.StaffBoardAuthor{ID: row.AuthorID, Username: "u"},
		Content: row.Content, Document: row.Document, Pinned: row.Pinned, CreatedAt: row.At, UpdatedAt: row.At}
	return nil
}
func (t *sbTx) UpdateStaffBoardMessage(_ context.Context, id, _ string, change domain.StaffBoardChange) error {
	t.store.events = append(t.store.events, "update")
	row := t.pending[id]
	if change.Content != nil {
		row.Content = *change.Content
	}
	if change.DocumentSet {
		row.Document = change.Document
	}
	if change.Pinned != nil {
		row.Pinned = *change.Pinned
	}
	row.UpdatedAt = change.At
	t.pending[id] = row
	return nil
}
func (t *sbTx) DeleteStaffBoardMessage(_ context.Context, id, _ string) error {
	t.store.events = append(t.store.events, "delete")
	delete(t.pending, id)
	return nil
}
func (t *sbTx) AppendRealtimeEvent(_ context.Context, kind, target string) error {
	if t.store.eventErr != nil {
		return t.store.eventErr
	}
	t.store.events = append(t.store.events, "event:"+kind+":"+target)
	return nil
}
func (t *sbTx) Commit() error {
	if t.store.commitErr != nil {
		return t.store.commitErr
	}
	t.finished = true
	t.store.rows = t.pending
	t.store.events = append(t.store.events, "commit")
	return nil
}
func (t *sbTx) Rollback() error {
	if !t.finished {
		t.store.events = append(t.store.events, "rollback")
		t.finished = true
	}
	return nil
}

func sbIdentity(collaborator bool) domain.ResolvedIdentity {
	owner := &domain.Account{ID: sbOwner, Role: domain.RoleAssociation, Active: true}
	if !collaborator {
		return domain.ResolvedIdentity{Actor: owner, Effective: owner, ScopeUser: owner}
	}
	connected := sbOwner
	member := &domain.Account{ID: sbCollaborator, Role: domain.RoleCollaborator, Active: true, ConnectedUserID: &connected}
	return domain.ResolvedIdentity{Actor: member, Effective: member, ScopeUser: owner, Collaborator: true}
}

func sbService(rows ...domain.StaffBoardMessage) (*StaffBoard, *sbStore) {
	store := &sbStore{rows: map[string]domain.StaffBoardMessage{}}
	for _, row := range rows {
		store.rows[row.ID] = row
	}
	at := time.Date(2026, 10, 27, 10, 0, 0, 123456789, time.UTC)
	return NewStaffBoard(store, func() time.Time { return at }), store
}

func sbExisting(author *string) domain.StaffBoardMessage {
	row := domain.StaffBoardMessage{ID: sbMessage, AssociationID: sbAssociation, AssociationOwnerID: sbOwner,
		AssociationDenomination: "ASD", Content: "old", Document: []byte(`{"type": "doc"}`)}
	if author != nil {
		row.Author = &domain.StaffBoardAuthor{ID: *author, Username: "u"}
	}
	return row
}

func sbBody(fields ...any) domain.RequestObject {
	object := domain.RequestObject{}
	for i := 0; i < len(fields); i += 2 {
		object = append(object, domain.RequestField{Name: fields[i].(string), Value: fields[i+1]})
	}
	return object
}

func sbEvents(t *testing.T, store *sbStore, want ...string) {
	t.Helper()
	if len(store.events) != len(want) {
		t.Fatalf("events %v, want %v", store.events, want)
	}
	for i := range want {
		if store.events[i] != want[i] {
			t.Fatalf("events %v, want %v", store.events, want)
		}
	}
}

func TestStaffBoardAddAuthorsAsActingUserAndPublishesAfterCommit(t *testing.T) {
	for _, collaborator := range []bool{false, true} {
		service, store := sbService()
		row, err := service.Add(context.Background(), sbIdentity(collaborator), sbBody("content", " ciao "), false)
		if err != nil {
			t.Fatal(err)
		}
		author := sbOwner
		if collaborator {
			author = sbCollaborator
		}
		if row.Author == nil || *row.Author != author || !row.IsOwner || row.Content != "ciao" || row.Document != nil || row.Pinned {
			t.Fatalf("row %+v", row)
		}
		if row.CreatedAt != "2026-10-27T10:00:00.123456Z" || row.SportAssociation != sbAssociation {
			t.Fatalf("time/association %+v", row)
		}
		sbEvents(t, store, "begin", "lock-owner:"+sbOwner, "insert", "event:staff_board_changed:"+sbAssociation, "commit")
	}
}

func TestStaffBoardAddValidationPrecedesTransaction(t *testing.T) {
	service, store := sbService()
	_, err := service.Add(context.Background(), sbIdentity(false), sbBody("content", ""), false)
	var invalid *dto.StaffBoardInputError
	if !errors.As(err, &invalid) || store.begun != 0 || len(store.events) != 0 {
		t.Fatal(err, store.begun, store.events)
	}
}

func TestStaffBoardCommitFailureLeavesNoRow(t *testing.T) {
	service, store := sbService()
	store.commitErr = errors.New("commit failed")
	if _, err := service.Add(context.Background(), sbIdentity(false), sbBody("content", "x"), false); err == nil || len(store.rows) != 0 {
		t.Fatal(err, store.rows)
	}
}

// The event and the write are one transaction: an outbox failure rolls back.
func TestStaffBoardOutboxFailureRollsBackWrite(t *testing.T) {
	service, store := sbService()
	store.eventErr = errors.New("outbox insert failed")
	if _, err := service.Add(context.Background(), sbIdentity(false), sbBody("content", "x"), false); err == nil || len(store.rows) != 0 {
		t.Fatal(err, store.rows)
	}
	sbEvents(t, store, "begin", "lock-owner:"+sbOwner, "insert", "rollback")
}

func TestStaffBoardUpdatePrecedence(t *testing.T) {
	other, owner := sbOther, sbOwner
	cases := []struct {
		name   string
		raw    string
		row    *domain.StaffBoardMessage
		body   any
		want   error
		events []string
	}{
		{"malformed uuid before transaction", "nope", nil, sbBody("content", ""), &dto.MessageReadUUIDError{}, nil},
		{"missing before validation", sbMessage, nil, sbBody("content", ""), domain.ErrStaffBoardNotFound, []string{"begin", "lock-owner:" + sbOwner, "rollback"}},
		{"non-author edit before validation", sbMessage, ptr(sbExisting(&other)), sbBody("content", ""), domain.ErrStaffBoardEditNotAuthor, []string{"begin", "lock-owner:" + sbOwner, "rollback"}},
		{"deleted author edit", sbMessage, ptr(sbExisting(nil)), sbBody("document", nil), domain.ErrStaffBoardEditNotAuthor, []string{"begin", "lock-owner:" + sbOwner, "rollback"}},
		{"author invalid content", sbMessage, ptr(sbExisting(&owner)), sbBody("content", ""), &dto.StaffBoardInputError{}, []string{"begin", "lock-owner:" + sbOwner, "rollback"}},
		{"null body type error after lookup", sbMessage, ptr(sbExisting(&owner)), nil, ErrStaffBoardUnsupportedBody, []string{"begin", "lock-owner:" + sbOwner, "rollback"}},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			var rows []domain.StaffBoardMessage
			if c.row != nil {
				rows = append(rows, *c.row)
			}
			service, store := sbService(rows...)
			_, err := service.Update(context.Background(), sbIdentity(false), c.raw, c.body, false)
			switch want := c.want.(type) {
			case *dto.MessageReadUUIDError:
				var got *dto.MessageReadUUIDError
				if !errors.As(err, &got) {
					t.Fatal(err)
				}
			case *dto.StaffBoardInputError:
				var got *dto.StaffBoardInputError
				if !errors.As(err, &got) {
					t.Fatal(err)
				}
			default:
				if !errors.Is(err, want) {
					t.Fatal(err)
				}
			}
			sbEvents(t, store, c.events...)
		})
	}
}

func ptr[T any](value T) *T { return &value }

func TestStaffBoardUpdateSemantics(t *testing.T) {
	other := sbOther
	// Pin changes need only the route permission, not authorship.
	service, store := sbService(sbExisting(&other))
	row, err := service.Update(context.Background(), sbIdentity(false), sbMessage, sbBody("pinned", "true"), false)
	if err != nil || !row.Pinned || row.Content != "old" || string(row.Document) != `{"type": "doc"}` || row.IsOwner {
		t.Fatal(err, row)
	}
	sbEvents(t, store, "begin", "lock-owner:"+sbOwner, "update", "event:staff_board_changed:"+sbAssociation, "commit")
	// Content without document clears the rich document.
	owner := sbOwner
	service, _ = sbService(sbExisting(&owner))
	row, err = service.Update(context.Background(), sbIdentity(false), sbMessage, sbBody("content", "nuovo"), false)
	if err != nil || row.Content != "nuovo" || row.Document != nil || row.UpdatedAt != "2026-10-27T10:00:00.123456Z" {
		t.Fatal(err, row)
	}
}

func TestStaffBoardTextMembershipFollowsPython(t *testing.T) {
	for _, c := range []struct {
		body any
		want bool
		err  bool
	}{
		{sbBody("pinned", true), false, false}, {sbBody("content", nil), true, false}, {sbBody("document", ""), true, false},
		{[]any{"x", "document"}, true, false}, {[]any{[]any{"content"}}, false, false},
		{"xcontentx", true, false}, {"pinned", false, false}, {nil, false, true}, {true, false, true},
	} {
		got, err := staffBoardNamesText(c.body)
		if got != c.want || (err != nil) != c.err {
			t.Fatalf("%v: %v %v", c.body, got, err)
		}
	}
}

func TestStaffBoardDeleteAuthorOnly(t *testing.T) {
	owner, other := sbOwner, sbOther
	for _, c := range []struct {
		row  domain.StaffBoardMessage
		want error
	}{{sbExisting(&other), domain.ErrStaffBoardDeleteNotAuthor}, {sbExisting(nil), domain.ErrStaffBoardDeleteNotAuthor}, {sbExisting(&owner), nil}} {
		service, store := sbService(c.row)
		err := service.Delete(context.Background(), sbIdentity(false), sbMessage)
		if !errors.Is(err, c.want) && !(c.want == nil && err == nil) {
			t.Fatal(err)
		}
		if c.want == nil {
			sbEvents(t, store, "begin", "lock-owner:"+sbOwner, "delete", "event:staff_board_changed:"+sbAssociation, "commit")
			if len(store.rows) != 0 {
				t.Fatal(store.rows)
			}
		} else if len(store.rows) != 1 {
			t.Fatal("rejected delete changed state")
		}
	}
}

func TestStaffBoardRejectsNonAssociationScopes(t *testing.T) {
	athlete := &domain.Account{ID: sbOther, Role: domain.RoleAthlete, Active: true}
	identity := domain.ResolvedIdentity{Actor: athlete, Effective: athlete, ScopeUser: athlete}
	service, store := sbService()
	if err := service.Admit(context.Background(), identity); !errors.Is(err, domain.ErrStaffBoardForbidden) {
		t.Fatal(err)
	}
	if _, err := service.Add(context.Background(), identity, sbBody("content", "x"), false); !errors.Is(err, domain.ErrStaffBoardForbidden) || store.begun != 0 {
		t.Fatal(err)
	}
}
