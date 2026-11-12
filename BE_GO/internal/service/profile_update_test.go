package service

import (
	"context"
	"errors"
	"reflect"
	"testing"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
)

type profileUpdateStoreTest struct {
	tx     profileUpdateTxTest
	begins int
	err    error
}

func (s *profileUpdateStoreTest) BeginProfileUpdate(context.Context) (domain.ProfileUpdateTransaction, error) {
	s.begins++
	return &s.tx, s.err
}

type profileUpdateTxTest struct {
	person                          domain.ProfileUpdatePerson
	association                     domain.ProfileUpdateAssociation
	personWrites, associationWrites map[string]any
	audits                          []domain.ProfileUpdateAudit
	associations                    map[string]string
	fail                            string
	committed                       bool
	rollbacks, conflicts            int
}

var errProfileTest = errors.New("synthetic profile failure")

func (t *profileUpdateTxTest) check(step string) error {
	if t.fail == step {
		return errProfileTest
	}
	return nil
}
func (t *profileUpdateTxTest) ProfilePersonForUpdate(_ context.Context, id string) (domain.ProfileUpdatePerson, error) {
	if id != t.person.ID {
		return domain.ProfileUpdatePerson{}, domain.ErrAccountNotFound
	}
	return t.person, t.check("person")
}
func (t *profileUpdateTxTest) ProfileAssociationForUpdate(_ context.Context, owner string, scope *string) (domain.ProfileUpdateAssociation, error) {
	if owner != t.association.OwnerID || scope != nil && *scope != t.association.ID {
		return domain.ProfileUpdateAssociation{}, domain.ErrAssociationNotFound
	}
	return t.association, t.check("association")
}
func (t *profileUpdateTxTest) SetProfilePerson(_ context.Context, _ string, fields map[string]any) error {
	t.personWrites = fields
	return t.check("set-person")
}
func (t *profileUpdateTxTest) SetProfileAssociation(_ context.Context, _, _ string, fields map[string]any) error {
	t.associationWrites = fields
	return t.check("set-association")
}
func (t *profileUpdateTxTest) AuditAssociations(context.Context, string, *string) (map[string]string, error) {
	return t.associations, t.check("associations")
}
func (t *profileUpdateTxTest) AppendProfileAudit(_ context.Context, event domain.ProfileUpdateAudit) error {
	t.audits = append(t.audits, event)
	return t.check("audit")
}
func (t *profileUpdateTxTest) Commit() error {
	if t.conflicts > 0 {
		t.conflicts--
		return domain.ErrCredentialConflict
	}
	if err := t.check("commit"); err != nil {
		return err
	}
	t.committed = true
	return nil
}
func (t *profileUpdateTxTest) Rollback() error { t.rollbacks++; return nil }

type profileImageTest struct {
	err    error
	values []string
}

func (p *profileImageTest) CompressProfileImage(_ context.Context, value string) (string, error) {
	p.values = append(p.values, value)
	return "compressed:" + value, p.err
}

func profileMutationFixture() (*profileUpdateStoreTest, domain.ResolvedIdentity, dto.ProfileUpdateRequest) {
	u := &domain.Account{ID: "owner", Email: "owner@example.test", Role: 1}
	id := domain.ResolvedIdentity{Actor: u, Effective: u, ScopeUser: u}
	store := &profileUpdateStoreTest{tx: profileUpdateTxTest{person: domain.ProfileUpdatePerson{ID: "owner", Role: 1, FirstName: "First", LastName: "Last", Username: "BEFORE"}, association: domain.ProfileUpdateAssociation{ID: "association", OwnerID: "owner", Values: map[string]any{"denomination": "Before ASD", "checkout_info": "before"}}, associations: map[string]string{"owner": "association"}}}
	association := domain.RequestObject{}
	for _, key := range []string{"denomination", "tax_code", "address", "address_cap", "address_city", "enable_quotes_management", "configuration", "federation", "enroll_number", "sport", "president_first_name", "president_last_name", "stripe_available_methods", "invoice_template", "subscription_template", "extra_text_invoices", "checkout_info"} {
		var value any
		if key == "denomination" {
			value = "After ASD"
		}
		association = append(association, domain.RequestField{Name: key, Value: value})
	}
	return store, id, dto.ProfileUpdateRequest{Person: map[string]any{"first_name": "Next", "username": "straße", "avatar_image": "raw"}, Association: association}
}

func TestProfileMutationAtomicWritesAndAudit(t *testing.T) {
	store, id, input := profileMutationFixture()
	images := &profileImageTest{}
	now := time.Unix(1234567890, 0)
	state, err := NewProfileMutations(store, images, func() time.Time { return now }).Change(context.Background(), id, input, domain.LoginAuditContext{})
	if err != nil || !store.tx.committed || state.FirstName != "Next" || state.Username != "STRASSE" || store.tx.rollbacks != 1 {
		t.Fatal(state, err, store.tx)
	}
	if !reflect.DeepEqual(store.tx.personWrites, map[string]any{"first_name": "Next", "username": "STRASSE", "avatar_image": "compressed:raw"}) {
		t.Fatal(store.tx.personWrites)
	}
	if !reflect.DeepEqual(store.tx.associationWrites, map[string]any{"denomination": "After ASD", "checkout_info": nil}) || !reflect.DeepEqual(images.values, []string{"raw"}) {
		t.Fatal(store.tx.associationWrites, images.values)
	}
	if len(store.tx.audits) != 2 {
		t.Fatal(store.tx.audits)
	}
	person, association := store.tx.audits[0], store.tx.audits[1]
	if person.Model != "user" || person.ID != "owner" || person.ObjectRepresentation != "name: Next Last" || !reflect.DeepEqual(person.Changes, map[string][2]string{"first_name": {"First", "Next"}, "username": {"BEFORE", "STRASSE"}, "avatar_image": {"None", "compressed:raw"}}) {
		t.Fatal(person)
	}
	if association.Model != "sportassociation" || association.ObjectRepresentation != "SportAssociation object (association)" || !reflect.DeepEqual(association.Changes, map[string][2]string{"denomination": {"Before ASD", "After ASD"}, "checkout_info": {"before", "None"}}) {
		t.Fatal(association)
	}
	for _, event := range store.tx.audits {
		if event.Context.ActorID == nil || *event.Context.ActorID != "owner" || event.Context.ActorEmail == nil || *event.Context.ActorEmail != "owner@example.test" || event.AssociationID == nil || *event.AssociationID != "association" || event.At != now {
			t.Fatal(event)
		}
	}
	if *person.ResolutionPath != "user.sport_association" || *association.ResolutionPath != "self" {
		t.Fatal("audit resolution")
	}
}

func TestProfileMutationRollbackRetryAndPersonalScope(t *testing.T) {
	for _, step := range []string{"person", "association", "set-person", "set-association", "associations", "audit", "commit"} {
		t.Run(step, func(t *testing.T) {
			store, id, input := profileMutationFixture()
			store.tx.fail = step
			if _, err := NewProfileMutations(store, &profileImageTest{}, time.Now).Change(context.Background(), id, input, domain.LoginAuditContext{}); !errors.Is(err, errProfileTest) || store.tx.committed || store.tx.rollbacks != 1 {
				t.Fatal(step, err, store.tx)
			}
		})
	}
	store, id, input := profileMutationFixture()
	store.tx.conflicts = 2
	if _, err := NewProfileMutations(store, &profileImageTest{}, time.Now).Change(context.Background(), id, input, domain.LoginAuditContext{}); err != nil || store.begins != 3 || !store.tx.committed {
		t.Fatal("retry", store.begins, err)
	}
	store, id, input = profileMutationFixture()
	store.tx.person.Role = 3
	id.Effective.Role = 3
	id.ScopeUser = &domain.Account{ID: "mapped-owner", Role: 1}
	input.Association = "ignored"
	if _, err := NewProfileMutations(store, &profileImageTest{}, time.Now).Change(context.Background(), id, input, domain.LoginAuditContext{}); err != nil || store.tx.associationWrites != nil {
		t.Fatal("collaborator changed association", err)
	}
	store, id, input = profileMutationFixture()
	id.Actor = nil
	if _, err := NewProfileMutations(store, &profileImageTest{}, time.Now).Change(context.Background(), id, input, domain.LoginAuditContext{}); !errors.Is(err, domain.ErrInvalidScopedRequest) || store.begins != 0 {
		t.Fatal("missing trusted identity", err)
	}
	store, id, input = profileMutationFixture()
	input.Person["password"] = "forged"
	if _, err := NewProfileMutations(store, &profileImageTest{}, time.Now).Change(context.Background(), id, input, domain.LoginAuditContext{}); err == nil || store.tx.committed {
		t.Fatal("unlisted write accepted")
	}
}

func TestProfileMutationFailureBoundaries(t *testing.T) {
	for _, scenario := range []string{"begin", "image", "missing-codec", "cancelled", "exhausted-conflicts", "foreign-scope", "foreign-association", "no-op"} {
		t.Run(scenario, func(t *testing.T) {
			store, id, input := profileMutationFixture()
			ctx, cancel := context.WithCancel(context.Background())
			defer cancel()
			var images domain.ProfileUpdateImages = &profileImageTest{}
			want := errProfileTest
			begins, rollbacks := 1, 1
			switch scenario {
			case "begin":
				store.err = errProfileTest
				rollbacks = 0
			case "image":
				images = &profileImageTest{err: errProfileTest}
			case "missing-codec":
				images = nil
				want = ErrLoginImageProcessorUnavailable
			case "cancelled":
				cancel()
				want = context.Canceled
				begins, rollbacks = 0, 0
			case "exhausted-conflicts":
				store.tx.conflicts = 4
				want = domain.ErrCredentialConflict
				begins, rollbacks = 3, 3
			case "foreign-scope":
				id.ScopeUser = &domain.Account{ID: "foreign"}
				want = domain.ErrInvalidScopedRequest
			case "foreign-association":
				id.ImpersonationAssociationID = "foreign"
				want = domain.ErrAssociationNotFound
			case "no-op":
				input.Person = nil
				input.Association = nil
				want = nil
			}
			_, err := NewProfileMutations(store, images, time.Now).Change(ctx, id, input, domain.LoginAuditContext{})
			if !errors.Is(err, want) || store.begins != begins || store.tx.rollbacks != rollbacks || store.tx.committed != (want == nil) {
				t.Fatal(err, store.begins, store.tx)
			}
			if scenario == "no-op" && (store.tx.personWrites != nil || store.tx.associationWrites != nil || len(store.tx.audits) != 0) {
				t.Fatal("no-op wrote records", store.tx)
			}
		})
	}
}
