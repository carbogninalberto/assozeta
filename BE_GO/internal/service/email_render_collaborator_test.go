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

// The collaborators/add e-mail value: Django's 5.2 validate_email over the
// Python str the body decoder produced, then the INSERT's UTF-8 encoding
// (psycopg raises UnicodeEncodeError on a lone surrogate: the 500 page).

// collaboratorBody is the body a request reader hands the view for a JSON
// document, so a lone surrogate keeps its three-byte form.
func collaboratorBody(t *testing.T, document string) any {
	t.Helper()
	body, err := dto.ParseMessageMutationsJSON([]byte(document))
	if err != nil {
		t.Fatal(err)
	}
	return body
}

// collaboratorStore records the invitation the view creates.
type collaboratorStore struct {
	domain.EmailRenderStore
	created      []domain.CollaborationInvite
	associations []domain.EmailRenderAssociation
}

func (s *collaboratorStore) BeginCollaboratorInvite(context.Context) (domain.CollaboratorInviteTransaction, error) {
	return &collaboratorTx{store: s}, nil
}

func (s *collaboratorStore) InstanceConfig(context.Context) (domain.EmailRenderInstanceConfig, error) {
	return domain.EmailRenderInstanceConfig{}, nil
}

type collaboratorTx struct {
	domain.CollaboratorInviteTransaction
	store *collaboratorStore
}

func (tx *collaboratorTx) CreateInvite(_ context.Context, invite domain.CollaborationInvite) error {
	tx.store.created = append(tx.store.created, invite)
	return nil
}
func (*collaboratorTx) AppendInviteAudit(context.Context, string, domain.CollaborationInviteAudit) error {
	return nil
}
func (*collaboratorTx) ActiveAssociationOf(context.Context, string) (*string, error) { return nil, nil }
func (tx *collaboratorTx) LiveAssociations(context.Context, string, *string) ([]domain.EmailRenderAssociation, error) {
	return tx.store.associations, nil
}
func (*collaboratorTx) Commit() error   { return nil }
func (*collaboratorTx) Rollback() error { return nil }

func collaboratorFixture() (*EmailRenderRoutes, *collaboratorStore, *orderedPublisher, *[]string) {
	events := []string{}
	store := &collaboratorStore{associations: []domain.EmailRenderAssociation{{ID: emailAssociation}}}
	publisher := &orderedPublisher{events: &events}
	routes := NewEmailRenderRoutes(store, publisher,
		EmailRenderSettings{AppURL: "https://app.example.test", WhitelabelName: "W", DefaultFromEmail: "W <None>"}, time.Now)
	return routes, store, publisher, &events
}

func addCollaborator(t *testing.T, body any) (*collaboratorStore, *orderedPublisher, error) {
	t.Helper()
	routes, store, publisher, _ := collaboratorFixture()
	err := routes.AddCollaborator(context.Background(), inviteIdentity(), body, domain.LoginAuditContext{}, "en")
	return store, publisher, err
}

// A lone surrogate passes validate_email in the domain part (U+00A1..U+FFFF)
// and psycopg cannot encode it: the view raises before any row is written.
func TestCollaboratorEmailSurrogateDomainIsServerError(t *testing.T) {
	store, publisher, err := addCollaborator(t, collaboratorBody(t, `{"email": "a@b\ud800.it"}`))
	if !errors.Is(err, ErrEmailRenderServer) || errors.Is(err, refusal.ErrNotPorted) {
		t.Fatalf("error %v", err)
	}
	if len(store.created) != 0 || len(publisher.tasks) != 0 {
		t.Fatalf("wrote %v", store.created)
	}
	// A surrogate pair is one code point above U+FFFF, which no domain label
	// range accepts: 400, nothing written.
	store, _, err = addCollaborator(t, collaboratorBody(t, `{"email": "a@\ud83d\ude00.com"}`))
	var invalid *EmailInputError
	if !errors.As(err, &invalid) || len(store.created) != 0 {
		t.Fatalf("emoji domain: %v %v", err, store.created)
	}
}

// The user regex is dot-atom or quoted-string over ASCII: a surrogate in the
// local part never matches, so the view answers 400 and inserts nothing.
func TestCollaboratorEmailSurrogateLocalIsInvalid(t *testing.T) {
	for _, document := range []string{`{"email": "\ud800@b.it"}`, `{"email": "\ud800@\ud800.it"}`, `{"email": "\"\ud800\"@b.it"}`} {
		store, publisher, err := addCollaborator(t, collaboratorBody(t, document))
		var invalid *EmailInputError
		if !errors.As(err, &invalid) || invalid.Detail != "Email is not valid." {
			t.Fatalf("%s: %v", document, err)
		}
		if len(store.created) != 0 || len(publisher.tasks) != 0 {
			t.Fatalf("%s wrote", document)
		}
	}
}

// U+FFFD is what a urlencoded or multipart body decodes invalid bytes to: the
// domain accepts it and the row stores it.
func TestCollaboratorEmailReplacementCharacterIsStored(t *testing.T) {
	store, publisher, err := addCollaborator(t, collaboratorBody(t, `{"email": "a@b\ufffd.it"}`))
	if err != nil {
		t.Fatal(err)
	}
	if len(store.created) != 1 || store.created[0].Email != "a@b\ufffd.it" {
		t.Fatalf("invites %#v", store.created)
	}
	if len(publisher.tasks) != 1 {
		t.Fatalf("tasks %d", len(publisher.tasks))
	}
	if recipients := publisher.tasks[0].Kwargs[3].Value.([]any); len(recipients) != 1 || recipients[0] != "a@b\ufffd.it" {
		t.Fatalf("recipients %#v", recipients)
	}
}

// A multipart file part named email is an UploadedFile: validate_email reads
// "@" in its byte lines (never true) and len(value), so every upload is 400.
func TestCollaboratorEmailUploadIsInvalid(t *testing.T) {
	for _, upload := range []domain.RequestUpload{
		{Name: "a@b.c", Size: 5, Content: "a@b.c", Whole: true},
		{Name: "empty", Size: 0, Whole: true},
		{Name: "@", Size: 1, Content: "@", Whole: true},
		{Name: "big.txt", Size: 3 << 20, Temp: true},
	} {
		store, publisher, err := addCollaborator(t, updateBody("email", upload))
		var invalid *EmailInputError
		if !errors.As(err, &invalid) || invalid.Detail != "Email is not valid." {
			t.Fatalf("%+v: %v", upload, err)
		}
		if len(store.created) != 0 || len(publisher.tasks) != 0 {
			t.Fatalf("%+v wrote", upload)
		}
	}
}

// The validator's code points, each measured with the reference interpreter
// (Django 5.2's validate_email): dto.SupplierEmail alone misses the four code
// points re.IGNORECASE folds onto [A-Z], which the route folds for the check.
func TestCollaboratorEmailDjangoValidator(t *testing.T) {
	valid := []string{"a@b.it", "\u0130@b.it", "\u0131@b.it", "\u017f@b.it", "\u212a@b.it", "\"\u0130\"@b.it",
		"o'brien+tag@example.test", "a@\u00a1.it", "a@b.\u00a1\u00a1", "a@\ufffd.com", "a@b.\ufffd\ufffd",
		"a@localhost", "a@[127.0.0.1]", "a@[::ffff:1.2.3.4]", "a@xn--b.it", "a@b.xn--abc1"}
	invalid := []string{"\ufffd@b.it", "a@b\ufffd", "\U0001f600@b.it", "a@\U0001f600.com", "a@b.\U0001f600\U0001f600",
		"a b@b.it", "a@b", "a@-b.it", "a@b-.it", "a@b.i", "a@b.it2", "a@b..it", "a@b.it.", "a@[::1", "a@[010.1.1.1]",
		"a@b.it\x00", "\x00@b.it", "a@" + strings.Repeat("c", 64) + ".it", strings.Repeat("a", 321) + "@b.it"}
	for _, value := range valid {
		if !collaboratorEmailValid([]rune(value)) {
			t.Errorf("rejected %q", value)
		}
	}
	for _, value := range invalid {
		if collaboratorEmailValid([]rune(value)) {
			t.Errorf("accepted %q", value)
		}
	}
}
