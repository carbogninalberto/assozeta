package service

import (
	"context"
	"errors"
	"reflect"
	"testing"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

// byIDTransaction is the subscription-create transaction reduced to the calls
// AssociateSerializer.create's associate_id branch makes (all_objects lookups,
// the create's savepoint attempt, save() and its audit entries).
type byIDTransaction struct {
	domain.SubscriptionCreateTransaction
	// instances are AssociateAny's answers, in call order (a missing list entry is nil).
	instances []*domain.SubscriptionEditAssociate
	anyCalls  int
	byTaxCode *domain.SubscriptionEditAssociate
	// insertErrs fails the n-th create attempt with that error.
	insertErrs []error
	// saveErrs fails the n-th save with that error.
	saveErrs []error
	// deleteErrs fails the n-th deleted-flag write with that error.
	deleteErrs []error
	inserts    []domain.SubscriptionEditAssociate
	saves      []domain.SubscriptionEditAssociate
	audits     []domain.SubscriptionEditAudit
	deleted    []bool
}

func (t *byIDTransaction) AssociateAny(context.Context, string) (*domain.SubscriptionEditAssociate, error) {
	index := t.anyCalls
	t.anyCalls++
	if index >= len(t.instances) {
		return nil, nil
	}
	return t.instances[index], nil
}

func (t *byIDTransaction) AssociateByTaxCode(context.Context, string, string) (*domain.SubscriptionEditAssociate, error) {
	return t.byTaxCode, nil
}

func (t *byIDTransaction) InsertAssociateAttempt(_ context.Context, row domain.SubscriptionEditAssociate) error {
	index := len(t.inserts)
	t.inserts = append(t.inserts, row)
	if index < len(t.insertErrs) && t.insertErrs[index] != nil {
		return t.insertErrs[index]
	}
	return nil
}

func (t *byIDTransaction) SaveAssociate(_ context.Context, row domain.SubscriptionEditAssociate) error {
	index := len(t.saves)
	t.saves = append(t.saves, row)
	if index < len(t.saveErrs) && t.saveErrs[index] != nil {
		return t.saveErrs[index]
	}
	return nil
}

func (t *byIDTransaction) SetAssociateDeleted(_ context.Context, _ string, deleted bool) error {
	index := len(t.deleted)
	t.deleted = append(t.deleted, deleted)
	if index < len(t.deleteErrs) && t.deleteErrs[index] != nil {
		return t.deleteErrs[index]
	}
	return nil
}

func (t *byIDTransaction) Audit(_ context.Context, entry domain.SubscriptionEditAudit) error {
	t.audits = append(t.audits, entry)
	return nil
}

const byIDAssociation = "a1000000-0000-4000-8000-000000700001"

// taxCodeConflict is the psycopg2 IntegrityError text of a live row holding the
// tax code (verified against psycopg2 on the harness PostgreSQL).
const taxCodeConflict = "duplicate key value violates unique constraint \"unique_tax_code_sport_assoc_upper_active\"\n" +
	"DETAIL:  Key (upper(tax_code::text), sport_association_id)=(ABCDE12345, " + byIDAssociation + ") already exists.\n"

func byIDIntegrity() error { return &domain.AssociateIntegrityError{Text: taxCodeConflict} }

// byIDObject is an associate payload with an associate_id (the serializer's
// associate_id branch); born_date is always present so the associate path runs.
func byIDObject(key, first, last, tax string) domain.RequestObject {
	return domain.RequestObject{
		{Name: "associate_id", Value: key},
		{Name: "first_name", Value: first},
		{Name: "last_name", Value: last},
		{Name: "tax_code", Value: tax},
		{Name: "born_date", Value: "01/01/1985"},
		{Name: "sex", Value: "M"},
	}
}

func storedAssociate(id, first, last, tax string) domain.SubscriptionEditAssociate {
	born := time.Date(1980, 2, 3, 0, 0, 0, 0, time.UTC)
	return domain.SubscriptionEditAssociate{ID: id, AssociationID: ptr(byIDAssociation), FirstName: ptr(first), LastName: ptr(last),
		TaxCode: ptr(tax), Sex: "M", BornDate: &born}
}

// TestSubscriptionCreateAssociateIDTaxCodeFallback pins the create's
// except IntegrityError block: the failed INSERT (its savepoint) leaves the
// key free, so the association's first row with the tax code (any deleted
// state) is updated with every field but the key and returned.
func TestSubscriptionCreateAssociateIDTaxCodeFallback(t *testing.T) {
	creates := tutorCreates()
	live := storedAssociate("a1000000-0000-4000-8000-000000700010", "Vecchio", "Titolare", "ABCDE12345")
	tx := &byIDTransaction{instances: []*domain.SubscriptionEditAssociate{nil}, byTaxCode: &live,
		insertErrs: []error{byIDIntegrity()}}
	audit := subscriptionAudit{tx: tx, now: creates.now}
	row, err := creates.saveAssociate(context.Background(), tx, audit,
		byIDObject("a1000000-0000-4000-8000-000000700011", "Nuovo", "Titolare", "abcde12345"), byIDAssociation, nil, nil)
	if err != nil || row == nil {
		t.Fatalf("saved=%v err=%v", row, err)
	}
	if len(tx.inserts) != 1 || tx.inserts[0].ID != "a1000000-0000-4000-8000-000000700011" || tx.inserts[0].Deleted {
		t.Fatalf("inserts %+v, want one attempt with the sent key", tx.inserts)
	}
	if len(tx.saves) != 1 {
		t.Fatalf("saves %+v, want the fallback row", tx.saves)
	}
	saved := tx.saves[0]
	if saved.ID != live.ID || saved.FirstName == nil || *saved.FirstName != "Nuovo" || saved.TaxCode == nil || *saved.TaxCode != "abcde12345" ||
		saved.Deleted || saved.Spelling != "" {
		t.Fatalf("saved %+v, want the tax code row with the new fields and its own key", saved)
	}
	if row.ID != live.ID || len(tx.deleted) != 0 {
		t.Fatalf("row %+v deleted %v", row, tx.deleted)
	}
	if len(tx.audits) != 1 {
		t.Fatalf("audits %+v, want one update entry", tx.audits)
	}
	entry := tx.audits[0]
	if entry.Model != "associate" || entry.Key != live.ID || entry.Row != "" || entry.Action != 1 || entry.Created {
		t.Fatalf("entry %+v", entry)
	}
	want := map[string][2]string{"born_date": {"1980-02-03", "1985-01-01"}, "first_name": {"Vecchio", "Nuovo"},
		"tax_code": {"ABCDE12345", "abcde12345"}}
	if !reflect.DeepEqual(entry.Changes, want) {
		t.Fatalf("changes %v, want %v", entry.Changes, want)
	}
}

// TestSubscriptionCreateAssociateIDTaxCodeFallbackSaveConflict is the same
// fallback when its save() breaks the index (a deleted row first by key while a
// live row holds the code): the IntegrityError is create_subscription's, so the
// whole registration rolls back (the 500 page / renew's 400 body).
func TestSubscriptionCreateAssociateIDTaxCodeFallbackSaveConflict(t *testing.T) {
	creates := tutorCreates()
	deleted := storedAssociate("a1000000-0000-4000-8000-000000700010", "Vecchio", "Cancellato", "ABCDE12345")
	deleted.Deleted = true
	tx := &byIDTransaction{instances: []*domain.SubscriptionEditAssociate{nil}, byTaxCode: &deleted,
		insertErrs: []error{byIDIntegrity()}, saveErrs: []error{byIDIntegrity()}}
	row, err := creates.saveAssociate(context.Background(), tx,
		subscriptionAudit{tx: tx, now: creates.now},
		byIDObject("a1000000-0000-4000-8000-000000700011", "Nuovo", "Titolare", "ABCDE12345"), byIDAssociation, nil, nil)
	var failure *domain.SubscriptionCreateFailure
	if row != nil || !errors.As(err, &failure) || failure.Text != taxCodeConflict {
		t.Fatalf("saved=%v err=%v, want psycopg2's IntegrityError text", row, err)
	}
	if !errors.Is(err, domain.ErrSubscriptionCreateServer) {
		t.Fatalf("err %v, want the 500 page on subscription/add", err)
	}
	if len(tx.audits) != 0 || len(tx.deleted) != 0 {
		t.Fatalf("a rolled-back registration wrote %+v %v", tx.audits, tx.deleted)
	}
}

// TestSubscriptionCreateAssociateIDDeletedRowRestoreConflict is the same
// fallback when its save() restores a soft-deleted row while a live row holds
// the code: Django writes deleted=false in that one UPDATE, so the index
// refuses it and no entry is kept.
func TestSubscriptionCreateAssociateIDDeletedRowRestoreConflict(t *testing.T) {
	creates := tutorCreates()
	deleted := storedAssociate("a1000000-0000-4000-8000-000000700010", "Vecchio", "Cancellato", "ABCDE12345")
	deleted.Deleted = true
	tx := &byIDTransaction{instances: []*domain.SubscriptionEditAssociate{nil}, byTaxCode: &deleted,
		insertErrs: []error{byIDIntegrity()}, deleteErrs: []error{byIDIntegrity()}}
	row, err := creates.saveAssociate(context.Background(), tx,
		subscriptionAudit{tx: tx, now: creates.now},
		byIDObject("a1000000-0000-4000-8000-000000700011", "Nuovo", "Titolare", "ABCDE12345"), byIDAssociation, nil, nil)
	var failure *domain.SubscriptionCreateFailure
	if row != nil || !errors.As(err, &failure) || failure.Text != taxCodeConflict {
		t.Fatalf("saved=%v err=%v, want psycopg2's IntegrityError text", row, err)
	}
	if len(tx.deleted) != 1 || tx.deleted[0] || len(tx.audits) != 0 {
		t.Fatalf("deleted %v audits %+v, want the refused restore and no entry", tx.deleted, tx.audits)
	}
}

// TestSubscriptionCreateAssociateIDExistingRowConflict pins the existing row's
// save() (the row the key names, or the target association's row with the tax
// code): an IntegrityError out of create_subscription, no entry kept.
func TestSubscriptionCreateAssociateIDExistingRowConflict(t *testing.T) {
	creates := tutorCreates()
	deleted := storedAssociate("a1000000-0000-4000-8000-000000700010", "Vecchio", "Cancellato", "ABCDE12345")
	deleted.Deleted = true
	tx := &byIDTransaction{instances: []*domain.SubscriptionEditAssociate{&deleted}, byTaxCode: &deleted,
		saveErrs: []error{byIDIntegrity()}}
	row, err := creates.saveAssociate(context.Background(), tx,
		subscriptionAudit{tx: tx, now: creates.now},
		byIDObject("a1000000-0000-4000-8000-000000700010", "Nuovo", "Titolare", "ABCDE12345"), byIDAssociation, nil, nil)
	var failure *domain.SubscriptionCreateFailure
	if row != nil || !errors.As(err, &failure) || failure.Text != taxCodeConflict {
		t.Fatalf("saved=%v err=%v, want psycopg2's IntegrityError text", row, err)
	}
	if len(tx.inserts) != 0 || len(tx.audits) != 0 || len(tx.deleted) != 0 {
		t.Fatalf("refused registration wrote %+v %+v %v", tx.inserts, tx.audits, tx.deleted)
	}
}

// TestSubscriptionCreateAssociateIDThreeAttempts pins max_retries = 3: when
// both fallbacks find no row the same INSERT runs three times and the last
// attempt re-raises.
func TestSubscriptionCreateAssociateIDThreeAttempts(t *testing.T) {
	creates := tutorCreates()
	tx := &byIDTransaction{insertErrs: []error{byIDIntegrity(), byIDIntegrity(), byIDIntegrity()}}
	row, err := creates.saveAssociate(context.Background(), tx,
		subscriptionAudit{tx: tx, now: creates.now},
		byIDObject("a1000000-0000-4000-8000-000000700011", "Nuovo", "Titolare", "ABCDE12345"), byIDAssociation, nil, nil)
	var failure *domain.SubscriptionCreateFailure
	if row != nil || !errors.As(err, &failure) || failure.Text != taxCodeConflict {
		t.Fatalf("saved=%v err=%v", row, err)
	}
	if len(tx.inserts) != 3 || tx.anyCalls != 4 {
		t.Fatalf("attempts %d, key lookups %d, want three attempts after the first lookup", len(tx.inserts), tx.anyCalls)
	}
	if len(tx.audits) != 0 {
		t.Fatalf("audits %+v, want none: post_save never ran", tx.audits)
	}
}

// TestSubscriptionCreateAssociateIDKeyAppearsAfterTheConflict is the except
// block's first fallback: the key names a row now (another request's create),
// updated with every validated field, associate_id included.
func TestSubscriptionCreateAssociateIDKeyAppearsAfterTheConflict(t *testing.T) {
	creates := tutorCreates()
	raced := storedAssociate("a1000000-0000-4000-8000-000000700011", "Altro", "Titolare", "ZZZZZ99999")
	tx := &byIDTransaction{instances: []*domain.SubscriptionEditAssociate{nil, &raced}, insertErrs: []error{byIDIntegrity()}}
	row, err := creates.saveAssociate(context.Background(), tx,
		subscriptionAudit{tx: tx, now: creates.now},
		byIDObject("a1000000-0000-4000-8000-000000700011", "Nuovo", "Titolare", "ABCDE12345"), byIDAssociation, nil, nil)
	if err != nil || row == nil || row.ID != raced.ID {
		t.Fatalf("saved=%v err=%v", row, err)
	}
	if len(tx.saves) != 1 || tx.saves[0].ID != raced.ID || tx.saves[0].FirstName == nil || *tx.saves[0].FirstName != "Nuovo" {
		t.Fatalf("saves %+v", tx.saves)
	}
	if len(tx.audits) != 1 || tx.audits[0].Key != raced.ID || tx.audits[0].Action != 1 {
		t.Fatalf("audits %+v", tx.audits)
	}
}

// TestSubscriptionCreateAssociateIDNonCanonicalSpelling pins the in-memory key:
// the column stores the canonical uuid, the instance keeps the text a client
// sent, and auditlog's create entry is written from the instance (object_pk,
// object_repr and associate_id), while the row is read through the stored key.
func TestSubscriptionCreateAssociateIDNonCanonicalSpelling(t *testing.T) {
	creates := tutorCreates()
	spelling := "A1000000000040008000000000700011"
	want := "a1000000-0000-4000-8000-000000700011"
	tx := &byIDTransaction{instances: []*domain.SubscriptionEditAssociate{nil}}
	audit := subscriptionAudit{tx: tx, now: creates.now}
	row, err := creates.saveAssociate(context.Background(), tx, audit,
		byIDObject(spelling, "Nuovo", "Titolare", "ABCDE12345"), byIDAssociation, nil, nil)
	if err != nil || row == nil || row.ID != want {
		t.Fatalf("saved=%v err=%v", row, err)
	}
	if len(tx.inserts) != 1 || tx.inserts[0].ID != want || tx.inserts[0].Spelling != spelling {
		t.Fatalf("inserts %+v, want the canonical key and the sent spelling", tx.inserts)
	}
	if row.AuditKey() != spelling {
		t.Fatalf("audit key %q, want %q", row.AuditKey(), spelling)
	}
	if len(tx.audits) != 1 {
		t.Fatalf("audits %+v", tx.audits)
	}
	entry := tx.audits[0]
	if entry.Key != spelling || entry.Row != want || entry.Action != 0 || !entry.Created {
		t.Fatalf("entry %+v, want object_pk %q reading row %q", entry, spelling, want)
	}
	if want := map[string][2]string{"associate_id": {"None", spelling}}; !reflect.DeepEqual(entry.Overrides, want) {
		t.Fatalf("overrides %v, want %v", entry.Overrides, want)
	}
}

// TestSubscriptionCreateAssociateIDSpellingUpdateDiff pins the later save()'s
// diff: the stored key against the in-memory spelling, next to the user change.
func TestSubscriptionCreateAssociateIDSpellingUpdateDiff(t *testing.T) {
	spelling := "A1000000000040008000000000700011"
	old := storedAssociate("a1000000-0000-4000-8000-000000700011", "Nuovo", "Titolare", "ABCDE12345")
	row := old
	row.Spelling = spelling
	user := "a1000000-0000-4000-8000-0000007000ff"
	row.UserID = &user
	want := map[string][2]string{"associate_id": {old.ID, spelling}, "user": {"None", user}}
	if got := associateChanges(old, row); !reflect.DeepEqual(got, want) {
		t.Fatalf("changes %v, want %v", got, want)
	}
}

// TestSubscriptionCreateTutorWithTheMinorsKey pins the two serializer saves of
// one row: the tutor's associate_id is the minor's own key, so its data is
// saved into the row the minor's save returned and the same key comes back;
// the tutor's user save and the stale minor instance's write-back follow, each
// entry diffing against the row as stored at that moment.
func TestSubscriptionCreateTutorWithTheMinorsKey(t *testing.T) {
	creates := tutorCreates()
	minor := "a1000000-0000-4000-8000-000000700011"
	minorRow := storedAssociate(minor, "Sara", "Piccola", "ABCDE12345")
	minorRow.AssociationID = ptr(byIDAssociation)
	minorRow.IsMinor = true
	// The key names the minor's row; its tax code names no other row of the association.
	tx := &byIDTransaction{instances: []*domain.SubscriptionEditAssociate{&minorRow}}
	audit := subscriptionAudit{tx: tx, now: creates.now}
	saved, err := creates.saveAssociate(context.Background(), tx, audit,
		byIDObject(minor, "Mario", "Piccolo", "FGHIJ67890"), byIDAssociation, &minor, nil)
	if err != nil || saved == nil || saved.ID != minor {
		t.Fatalf("saved=%v err=%v, want the minor's row", saved, err)
	}
	if len(tx.inserts) != 0 || len(tx.saves) != 1 {
		t.Fatalf("inserts %+v saves %+v", tx.inserts, tx.saves)
	}
	savedRow := tx.saves[0]
	// is_minor is not in the tutor's validated data: the row keeps the minor's value.
	if savedRow.ID != minor || savedRow.FirstName == nil || *savedRow.FirstName != "Mario" || !savedRow.IsMinor ||
		savedRow.Spelling != "" {
		t.Fatalf("saved %+v, want the tutor's fields in the same row", savedRow)
	}
	if len(tx.audits) != 1 || tx.audits[0].Action != 1 || tx.audits[0].Key != minor {
		t.Fatalf("audits %+v", tx.audits)
	}
	tutorChanges := map[string][2]string{"first_name": {"Sara", "Mario"}, "last_name": {"Piccola", "Piccolo"},
		"tax_code": {"ABCDE12345", "FGHIJ67890"}, "born_date": {"1980-02-03", "1985-01-01"}}
	if !reflect.DeepEqual(tx.audits[0].Changes, tutorChanges) {
		t.Fatalf("tutor update changes %v, want %v", tx.audits[0].Changes, tutorChanges)
	}
	// associate_tutor.user = user; save(): only the account differs.
	owner := "a1000000-0000-4000-8000-0000007000ff"
	tutorRow := *saved
	saved.UserID = &owner
	if got, want := associateChanges(tutorRow, *saved), map[string][2]string{"user": {"None", owner}}; !reflect.DeepEqual(got, want) {
		t.Fatalf("tutor user save changes %v, want %v", got, want)
	}
	// associate.user = user; associate.save() of the stale minor instance.
	stale := minorRow
	stale.UserID = &owner
	want := map[string][2]string{"first_name": {"Mario", "Sara"}, "last_name": {"Piccolo", "Piccola"},
		"tax_code": {"FGHIJ67890", "ABCDE12345"}, "born_date": {"1985-01-01", "1980-02-03"}}
	if got := associateChanges(*saved, stale); !reflect.DeepEqual(got, want) {
		t.Fatalf("stale save changes %v, want %v", got, want)
	}
}
