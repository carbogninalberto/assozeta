package service

import (
	"context"
	"errors"
	"net/http"
	"testing"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
)

// personaStockPictures is default_storage for one stock create: every object
// Put stores is recorded, no key is taken.
type personaStockPictures struct {
	keys   []string
	bodies [][]byte
}

func (p *personaStockPictures) Location() string { return "" }

func (p *personaStockPictures) Exists(context.Context, string) (bool, error) { return false, nil }

func (p *personaStockPictures) Put(_ context.Context, key string, body []byte, _, _ string) error {
	p.keys = append(p.keys, key)
	p.bodies = append(p.bodies, body)
	return nil
}

func (p *personaStockPictures) PutACL(context.Context, string, string) error { return nil }

// personaStockTX is the request's transaction: it records what the service
// wrote, in order, and answers the lookups of the fixture's groups.
type personaStockTX struct {
	domain.PersonaWritesTransaction
	calls     []string
	rows      []domain.PersonaAssociate
	saves     []domain.PersonaAssociate
	audits    []domain.PersonaWriteAudit
	groups    map[string]string
	commits   int
	rollbacks int
}

func (t *personaStockTX) ActiveAssociationOf(context.Context, string) (*string, error) {
	t.calls = append(t.calls, "association-of")
	return nil, nil
}

func (t *personaStockTX) GroupAssociation(_ context.Context, id string) (string, bool, error) {
	t.calls = append(t.calls, "group")
	association, found := t.groups[id]
	return association, found, nil
}

func (t *personaStockTX) ActiveUserExists(context.Context, string) (bool, error) { return false, nil }

func (t *personaStockTX) ActiveAssociationExists(context.Context, string) (bool, error) {
	return false, nil
}

func (t *personaStockTX) FamilyExists(context.Context, string) (bool, error) { return false, nil }

func (t *personaStockTX) InsertAssociate(_ context.Context, row domain.PersonaAssociate) error {
	t.calls = append(t.calls, "insert")
	t.rows = append(t.rows, row)
	return nil
}

func (t *personaStockTX) SaveAssociate(_ context.Context, row domain.PersonaAssociate) error {
	t.calls = append(t.calls, "save")
	t.saves = append(t.saves, row)
	return nil
}

func (t *personaStockTX) Relations(context.Context, string) ([]domain.PersonaRelationRow, error) {
	return nil, nil
}

func (t *personaStockTX) Audit(_ context.Context, entry domain.PersonaWriteAudit) error {
	t.calls = append(t.calls, "audit")
	t.audits = append(t.audits, entry)
	return nil
}

func (t *personaStockTX) Commit() error {
	t.commits++
	t.calls = append(t.calls, "commit")
	return nil
}

func (t *personaStockTX) Rollback() error {
	t.rollbacks++
	t.calls = append(t.calls, "rollback")
	return nil
}

type personaStockStore struct {
	domain.PersonaWritesStore
	tx *personaStockTX
}

func (s personaStockStore) BeginPersonaWrites(context.Context) (domain.PersonaWritesTransaction, error) {
	s.tx.calls = append(s.tx.calls, "begin")
	return s.tx, nil
}

// personaStockReads is the reads store for request.user.sport_association.
type personaStockReads struct {
	domain.PersonasStore
	owner string
}

func (s personaStockReads) OwnerAssociation(context.Context, string) (string, error) {
	return s.owner, nil
}

const (
	personaStockOwner   = "a1000000-0000-4000-8000-0000000000aa"
	personaStockActor   = "a1000000-0000-4000-8000-0000000000bb"
	personaStockNewID   = "a1000000-0000-4000-8000-0000000000cc"
	personaStockPicture = "data:image/png;base64,QUJD"
)

var personaStockNow = time.Unix(1700000000, 0)

// personaStockFixture is the writes service and its fakes for one owner (or
// athlete) request.
func personaStockFixture() (*PersonaWrites, *personaStockTX, *personaStockPictures) {
	tx := &personaStockTX{groups: map[string]string{}}
	pictures := &personaStockPictures{}
	personas := NewPersonas(personaStockReads{owner: personaStockOwner}, func() time.Time { return personaStockNow })
	writes := NewPersonaWrites(personaStockStore{tx: tx}, personas, func() time.Time { return personaStockNow },
		func() string { return personaStockNewID })
	return writes.WithPictures(pictures, PersonaPictureSettings{Overwrite: true, Endpoint: "https://objects.example.test",
		Bucket: "media"}), tx, pictures
}

func personaStockIdentity(role int) domain.ResolvedIdentity {
	actor := &domain.Account{ID: personaStockActor}
	return domain.ResolvedIdentity{Actor: actor, Effective: actor, ScopeUser: &domain.Account{ID: personaStockOwner, Role: role}}
}

func personaStockBody(fields ...[2]string) domain.RequestObject {
	body := domain.RequestObject{}
	for _, field := range fields {
		body = append(body, domain.RequestField{Name: field[0], Value: field[1]})
	}
	return body
}

// A picture the base64 branch refuses answers to_internal_value's 400 before
// the field validation, the association (athletes) and the transaction.
func TestPersonaStockCreatePictureErrorFirst(t *testing.T) {
	for _, tc := range []struct{ name, value, want string }{
		{"padding", "data:image/png;base64,QUJDQ",
			"Error processing image: {'picture_path': ErrorDetail(string='Invalid base64 string', code='invalid')}"},
		{"non-ascii", "data:image/png;base64,QUJDè",
			"Error processing image: string argument should contain only ASCII characters"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			writes, tx, pictures := personaStockFixture()
			_, err := writes.StockCreate(context.Background(), personaStockIdentity(domain.RoleAthlete),
				personaStockBody([2]string{"email", "nope"}, [2]string{"picture_path", tc.value}), false, "", domain.LoginAuditContext{})
			var invalid *PersonaPictureInvalid
			if !errors.As(err, &invalid) || invalid.Text != tc.want {
				t.Fatalf("%v, want %q", err, tc.want)
			}
			if len(tx.calls) != 0 || len(pictures.keys) != 0 {
				t.Fatalf("effect before the picture error: tx %v, objects %v", tx.calls, pictures.keys)
			}
		})
	}
	// A list that reaches the branch (an element equal to 'base64') is the same
	// 400 (b64decode's text), also before the field validation.
	writes, tx, _ := personaStockFixture()
	list := domain.RequestObject{{Name: "email", Value: "nope"}, {Name: "picture_path", Value: []any{"base64"}}}
	_, err := writes.StockCreate(context.Background(), personaStockIdentity(domain.RoleAthlete), list, false, "",
		domain.LoginAuditContext{})
	var picture *PersonaPictureInvalid
	if !errors.As(err, &picture) || picture.Text != "Error processing image: argument should be a bytes-like object or ASCII string, not 'list'" {
		t.Fatalf("list picture: %v", err)
	}
	if len(tx.calls) != 0 {
		t.Fatalf("transaction after a refused picture: %v", tx.calls)
	}
}

// The field validation precedes perform_create's association: an athlete's
// invalid body is the 400, a valid one the 403 and nothing is written.
func TestPersonaStockCreateValidationBeforeAssociation(t *testing.T) {
	writes, tx, pictures := personaStockFixture()
	identity := personaStockIdentity(domain.RoleAthlete)
	_, err := writes.StockCreate(context.Background(), identity, personaStockBody([2]string{"email", "nope"}),
		false, "", domain.LoginAuditContext{})
	if invalid, ok := err.(*dto.PersonaInvalid); !ok || len(invalid.Fields["email"]) != 1 {
		t.Fatalf("validation first: %v", err)
	}
	if _, err = writes.StockCreate(context.Background(), identity, personaStockBody([2]string{"first_name", "Atleta"}),
		false, "", domain.LoginAuditContext{}); !errors.Is(err, ErrPersonaNotSportAssociation) {
		t.Fatalf("athlete: %v", err)
	}
	if len(pictures.keys) != 0 || len(tx.rows) != 0 {
		t.Fatalf("effect for a refused association: %v %v", pictures.keys, tx.rows)
	}
}

// create() stores the decoded picture under the new associate's id before
// Associate.objects.create; with tutors_data among the validated data the
// TypeError follows the upload: the object stays and no row is written.
func TestPersonaStockCreatePictureThenTutorsData(t *testing.T) {
	writes, tx, pictures := personaStockFixture()
	tutors := []any{map[string]any{"tutor": map[string]any{"first_name": "Tut"}}}
	body := domain.RequestObject{{Name: "first_name", Value: "Con"}, {Name: "picture_path", Value: personaStockPicture},
		{Name: "tutors_data", Value: tutors}}
	_, err := writes.StockCreate(context.Background(), personaStockIdentity(domain.RoleAssociation), body, false, "",
		domain.LoginAuditContext{})
	if !errors.Is(err, ErrPersonaUnhandled) {
		t.Fatalf("tutors_data with a picture: %v", err)
	}
	key := "associates/" + personaStockNewID + "/picture_1700000000.0.jpg"
	if len(pictures.keys) != 1 || pictures.keys[0] != key {
		t.Fatalf("objects %v, want %v", pictures.keys, key)
	}
	if string(pictures.bodies[0]) != "ABC" {
		t.Fatalf("stored bytes %q", pictures.bodies[0])
	}
	if len(tx.rows) != 0 || len(tx.audits) != 0 || tx.commits != 0 || tx.rollbacks != 1 {
		t.Fatalf("state after the TypeError: rows %v audits %v commits %d rollbacks %d",
			tx.rows, tx.audits, tx.commits, tx.rollbacks)
	}
}

// The row a picture create writes keeps the stored object's URL.
func TestPersonaStockCreatePictureRow(t *testing.T) {
	writes, tx, pictures := personaStockFixture()
	id, err := writes.StockCreate(context.Background(), personaStockIdentity(domain.RoleAssociation),
		personaStockBody([2]string{"first_name", "Fiamma"}, [2]string{"picture_path", personaStockPicture}), false, "",
		domain.LoginAuditContext{})
	if err != nil || id != personaStockNewID {
		t.Fatalf("create: %q %v", id, err)
	}
	key := "associates/" + personaStockNewID + "/picture_1700000000.0.jpg"
	url := "https://objects.example.test/media/" + key
	if len(pictures.keys) != 1 || pictures.keys[0] != key {
		t.Fatalf("objects %v", pictures.keys)
	}
	if len(tx.rows) != 1 {
		t.Fatalf("rows %v", tx.rows)
	}
	row := tx.rows[0]
	if row.PicturePath == nil || *row.PicturePath != url {
		t.Fatalf("picture_path %v, want %s", row.PicturePath, url)
	}
	if row.UserID == nil || *row.UserID != personaStockOwner || row.AssociationID == nil || *row.AssociationID != personaStockOwner {
		t.Fatalf("user/association %v %v", row.UserID, row.AssociationID)
	}
	if tx.commits != 1 || tx.rollbacks != 0 || len(tx.audits) != 1 || tx.audits[0].Action != 0 {
		t.Fatalf("state: commits %d rollbacks %d audits %v", tx.commits, tx.rollbacks, tx.audits)
	}
}

// A form body is validated like a non-partial form: the absent booleans are
// False, an absent nullable text field keeps the model default and the empty
// one is the empty string.
func TestPersonaStockCreateFormDefaults(t *testing.T) {
	writes, tx, pictures := personaStockFixture()
	body := personaStockBody([2]string{"first_name", "Modulo"}, [2]string{"last_name", "Html"}, [2]string{"sex", "F"},
		[2]string{"notes", ""})
	id, err := writes.StockCreate(context.Background(), personaStockIdentity(domain.RoleAssociation), body, true, "",
		domain.LoginAuditContext{})
	if err != nil || id != personaStockNewID {
		t.Fatalf("form create: %q %v", id, err)
	}
	if len(pictures.keys) != 0 {
		t.Fatalf("form body without a picture stored %v", pictures.keys)
	}
	row := tx.rows[0]
	if row.IsMinor || row.Disabled || row.Draft || row.Deleted || row.Migrated {
		t.Fatalf("form booleans: %+v", row)
	}
	if row.Sex != "F" || row.FirstName == nil || *row.FirstName != "Modulo" || row.Notes == nil || *row.Notes != "" {
		t.Fatalf("form fields: %+v", row)
	}
	if row.Nationality == nil || *row.Nationality != "Italiana" || row.PicturePath != nil || row.BornDate != nil {
		t.Fatalf("model defaults: %+v", row)
	}
	// The form's booleans are validated values, not the model defaults: a form
	// value False parses the same way.
	writes, tx, _ = personaStockFixture()
	if _, err = writes.StockCreate(context.Background(), personaStockIdentity(domain.RoleAssociation),
		personaStockBody([2]string{"is_minor", "false"}, [2]string{"draft", "on"}), true, "", domain.LoginAuditContext{}); err != nil {
		t.Fatalf("form booleans: %v", err)
	}
	if row := tx.rows[0]; row.IsMinor || !row.Draft {
		t.Fatalf("form boolean values: %+v", row)
	}
}

// Field.get_value on an HTML input: a present empty boolean input is empty, so
// the field is skipped and the row keeps the model default (every Associate
// boolean defaults to False); absent keys keep default_empty_html False.
func TestPersonaStockCreateFormEmptyBooleans(t *testing.T) {
	body := personaStockBody([2]string{"first_name", "Modulo"})
	for _, name := range dto.PersonaBooleanFields {
		body = append(body, domain.RequestField{Name: name, Value: ""})
	}
	writes, tx, _ := personaStockFixture()
	id, err := writes.StockCreate(context.Background(), personaStockIdentity(domain.RoleAssociation), body, true, "",
		domain.LoginAuditContext{})
	if err != nil || id != personaStockNewID || len(tx.rows) != 1 {
		t.Fatalf("form empty booleans: %q %v rows %v", id, err, tx.rows)
	}
	if row := tx.rows[0]; row.IsMinor || row.Disabled || row.Draft || row.Deleted || row.Migrated {
		t.Fatalf("form empty booleans took a value: %+v", row)
	}
}

// The same rule on the partial form update: a present empty boolean input is
// skipped, so the stored value stays.
func TestPersonaUpdateFormEmptyBooleans(t *testing.T) {
	writes, tx, _ := personaStockFixture()
	row := domain.PersonaAssociate{ID: personaStockNewID, IsMinor: true, Draft: true}
	err := writes.Update(context.Background(), personaStockIdentity(domain.RoleAssociation), row,
		personaStockBody([2]string{"is_minor", ""}, [2]string{"draft", ""}, [2]string{"notes", "x"}), true, "",
		domain.LoginAuditContext{})
	if err != nil {
		t.Fatalf("form update: %v", err)
	}
	if len(tx.saves) != 1 {
		t.Fatalf("saves %v", tx.saves)
	}
	saved := tx.saves[0]
	if !saved.IsMinor || !saved.Draft || saved.Notes == nil || *saved.Notes != "x" {
		t.Fatalf("stored values: %+v", saved)
	}
}

// ListField.get_value on an HTML form: the field's own values (getlist), else
// its indexed keys (parse_html_list); either reaches Associate() as a keyword
// (the 500 page), while an absent key or an index parse_html_list drops (not
// numeric) is skipped and the row is written.
func TestPersonaStockCreateFormTutorsData(t *testing.T) {
	for _, tc := range []struct {
		name    string
		body    domain.RequestObject
		refused bool
	}{
		{"plain", personaStockBody([2]string{"first_name", "Con"}, [2]string{"tutors_data", "x"}), true},
		{"plain-empty", personaStockBody([2]string{"first_name", "Con"}, [2]string{"tutors_data", ""}), true},
		{"indexed", personaStockBody([2]string{"first_name", "Con"}, [2]string{"tutors_data[0]", "x"}), true},
		{"indexed-key", personaStockBody([2]string{"first_name", "Con"}, [2]string{"tutors_data[0].first_name", "Tut"}), true},
		{"absent", personaStockBody([2]string{"first_name", "Con"}), false},
		{"index-not-numeric", personaStockBody([2]string{"first_name", "Con"}, [2]string{"tutors_data[x]", "x"}), false},
		{"other-key-prefix", personaStockBody([2]string{"first_name", "Con"}, [2]string{"tutors_data_x", "x"}), false},
	} {
		t.Run(tc.name, func(t *testing.T) {
			writes, tx, _ := personaStockFixture()
			_, err := writes.StockCreate(context.Background(), personaStockIdentity(domain.RoleAssociation), tc.body, true, "",
				domain.LoginAuditContext{})
			if tc.refused {
				if !errors.Is(err, ErrPersonaUnhandled) || len(tx.rows) != 0 || tx.rollbacks != 1 {
					t.Fatalf("%v: rows %v rollbacks %d", err, tx.rows, tx.rollbacks)
				}
				return
			}
			if err != nil || len(tx.rows) != 1 {
				t.Fatalf("%v: rows %v", err, tx.rows)
			}
		})
	}
}

// A form body with a picture stores it under the new associate's id and keeps
// the URL; the picture_path input is not validated as a field.
func TestPersonaStockCreateFormPicture(t *testing.T) {
	writes, tx, pictures := personaStockFixture()
	id, err := writes.StockCreate(context.Background(), personaStockIdentity(domain.RoleAssociation),
		personaStockBody([2]string{"first_name", "Fiora"}, [2]string{"picture_path", personaStockPicture}), true, "",
		domain.LoginAuditContext{})
	if err != nil || id != personaStockNewID {
		t.Fatalf("form picture: %q %v", id, err)
	}
	key := "associates/" + personaStockNewID + "/picture_1700000000.0.jpg"
	if len(pictures.keys) != 1 || pictures.keys[0] != key {
		t.Fatalf("objects %v", pictures.keys)
	}
	if row := tx.rows[0]; row.PicturePath == nil ||
		*row.PicturePath != "https://objects.example.test/media/"+key {
		t.Fatalf("picture_path %v", row.PicturePath)
	}
}

// The manager's raw X-Group-Id is read inside create(), after the picture
// upload: a header the UUID field rejects is the 500 page with the object
// stored and nothing written.
func TestPersonaStockCreateHeaderAfterUpload(t *testing.T) {
	writes, tx, pictures := personaStockFixture()
	_, err := writes.StockCreate(context.Background(), personaStockIdentity(domain.RoleAssociation),
		personaStockBody([2]string{"first_name", "Gruppo"}, [2]string{"picture_path", personaStockPicture}), false,
		"not-a-uuid", domain.LoginAuditContext{})
	if !errors.Is(err, ErrPersonaUnhandled) {
		t.Fatalf("raw header: %v", err)
	}
	if len(pictures.keys) != 1 {
		t.Fatalf("objects %v", pictures.keys)
	}
	if len(tx.rows) != 0 || tx.rollbacks != 1 {
		t.Fatalf("rows %v rollbacks %d", tx.rows, tx.rollbacks)
	}
	// A group that does not exist is the deferred foreign key's failure.
	writes, tx, pictures = personaStockFixture()
	group := "a1000000-0000-4000-8000-0000000000dd"
	_, err = writes.StockCreate(context.Background(), personaStockIdentity(domain.RoleAssociation),
		personaStockBody([2]string{"first_name", "Gruppo"}), false, group, domain.LoginAuditContext{})
	if !errors.Is(err, ErrPersonaUnhandled) || len(tx.rows) != 0 || len(pictures.keys) != 0 {
		t.Fatalf("missing group: %v rows %v", err, tx.rows)
	}
	// An existing group is stored in the header's spelling.
	writes, tx, _ = personaStockFixture()
	tx.groups[group] = personaStockOwner
	if _, err = writes.StockCreate(context.Background(), personaStockIdentity(domain.RoleAssociation),
		personaStockBody([2]string{"first_name", "Gemma"}), false, group, domain.LoginAuditContext{}); err != nil {
		t.Fatalf("group header: %v", err)
	}
	if row := tx.rows[0]; row.GroupID == nil || *row.GroupID != group || tx.audits[0].SentGroup != group {
		t.Fatalf("group %v sent %q", row.GroupID, tx.audits[0].SentGroup)
	}
}

// A picture the storage refuses is the serializer's 400 with botocore's text:
// no row, no audit entry.
func TestPersonaStockCreatePictureStorageFailure(t *testing.T) {
	writes, tx, _ := personaStockFixture()
	denied := storageResponseError("PutObject", http.StatusForbidden, consumerTestAPIError{"AccessDenied", "Access Denied."})
	writes = writes.WithPictures(&personaPictureObjects{putErr: denied}, writes.pictureSettings)
	_, err := writes.StockCreate(context.Background(), personaStockIdentity(domain.RoleAssociation),
		personaStockBody([2]string{"picture_path", personaStockPicture}), false, "", domain.LoginAuditContext{})
	var invalid *PersonaPictureInvalid
	if !errors.As(err, &invalid) ||
		invalid.Text != "Error processing image: An error occurred (AccessDenied) when calling the PutObject operation: Access Denied." {
		t.Fatalf("refused storage: %v", err)
	}
	if len(tx.rows) != 0 || tx.commits != 0 || tx.rollbacks != 1 {
		t.Fatalf("rows %v commits %d rollbacks %d", tx.rows, tx.commits, tx.rollbacks)
	}
}

// AssociateSerializer.to_internal_value copies the body: for a list,
// `'picture_path' in` copies is an element test and indexing the list by that
// name is the TypeError (the 500 page, before the transaction and
// perform_create). Any other list fails the dict check: the 400.
func TestPersonaStockCreateListBody(t *testing.T) {
	writes, tx, _ := personaStockFixture()
	_, err := writes.StockCreate(context.Background(), personaStockIdentity(domain.RoleAthlete), []any{"picture_path"}, false,
		"", domain.LoginAuditContext{})
	if !errors.Is(err, ErrPersonaUnhandled) {
		t.Fatalf("list with picture_path: %v", err)
	}
	if len(tx.calls) != 0 {
		t.Fatalf("transaction after the TypeError: %v", tx.calls)
	}
	// The element test is not recursive: a nested dict naming the key is not the
	// string, so the list reaches the dict check.
	writes, tx, _ = personaStockFixture()
	_, err = writes.StockCreate(context.Background(), personaStockIdentity(domain.RoleAthlete),
		[]any{"first_name", map[string]any{"picture_path": "x"}}, false, "", domain.LoginAuditContext{})
	var invalid *dto.PersonaInvalid
	if !errors.As(err, &invalid) || len(invalid.Fields["non_field_errors"]) != 1 ||
		invalid.Fields["non_field_errors"][0] != "dictionary:list" {
		t.Fatalf("list body: %v", err)
	}
	if len(tx.calls) != 0 {
		t.Fatalf("transaction after the 400: %v", tx.calls)
	}
}
