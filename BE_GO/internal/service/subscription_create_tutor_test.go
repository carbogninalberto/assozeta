package service

import (
	"context"
	"errors"
	"reflect"
	"testing"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

// tutorTransaction is the subscription-create transaction reduced to the calls
// saveAssociate makes for the tutor edge cases; every other method is the
// embedded nil interface, so an unexpected call panics.
type tutorTransaction struct {
	domain.SubscriptionCreateTransaction
	matches  []domain.SubscriptionEditAssociate
	inserted []domain.SubscriptionEditAssociate
	saved    []domain.SubscriptionEditAssociate
	audits   []domain.SubscriptionEditAudit
}

func (t *tutorTransaction) TaxCodeMatches(context.Context, string, *string, *string) ([]domain.SubscriptionEditAssociate, error) {
	return t.matches, nil
}

func (t *tutorTransaction) InsertAssociate(_ context.Context, row domain.SubscriptionEditAssociate) error {
	t.inserted = append(t.inserted, row)
	return nil
}

func (t *tutorTransaction) SaveAssociate(_ context.Context, row domain.SubscriptionEditAssociate) error {
	t.saved = append(t.saved, row)
	return nil
}

func (t *tutorTransaction) Audit(_ context.Context, entry domain.SubscriptionEditAudit) error {
	t.audits = append(t.audits, entry)
	return nil
}

const tutorAssociation = "a1000000-0000-4000-8000-000000700001"

func tutorCreates() *SubscriptionCreates {
	return &SubscriptionCreates{now: func() time.Time { return time.Date(2026, 11, 12, 9, 0, 0, 0, time.UTC) }}
}

// tutorPersonObject is a tutor payload; born_date is appended only when present
// (a missing key is not in the serializer's validated_data, an explicit null is).
func tutorPersonObject(born any, present bool) domain.RequestObject {
	object := domain.RequestObject{
		{Name: "first_name", Value: "Tullio"},
		{Name: "last_name", Value: "Senonata"},
		{Name: "sex", Value: "M"},
		{Name: "tax_code", Value: "SNNTLL80A01H501C"},
	}
	if present {
		object = append(object, domain.RequestField{Name: "born_date", Value: born})
	}
	return object
}

// TestSubscriptionCreateTutorBornDateNone drives AssociateSerializer's
// DateField(null=True, blank=True): a tutor whose born_date key is missing or
// explicitly null is created with born_date NULL; an associate (minorOf nil)
// never reaches this call and keeps refusing.
func TestSubscriptionCreateTutorBornDateNone(t *testing.T) {
	creates := tutorCreates()
	minor := "a1000000-0000-4000-8000-000000700002"
	spellings := []struct {
		name    string
		object  domain.RequestObject
		missing bool
	}{
		{name: "missing key", object: tutorPersonObject(nil, false), missing: true},
		{name: "explicit null", object: tutorPersonObject(nil, true)},
	}
	for _, spelling := range spellings {
		t.Run("tutor/"+spelling.name, func(t *testing.T) {
			tx := &tutorTransaction{}
			audit := subscriptionAudit{tx: tx, now: creates.now}
			row, err := creates.saveAssociate(context.Background(), tx, audit, spelling.object, tutorAssociation, &minor, nil)
			if err != nil || row == nil {
				t.Fatalf("saved=%v err=%v", row, err)
			}
			if row.BornDate != nil {
				t.Fatalf("born_date %v, want None", *row.BornDate)
			}
			if len(tx.inserted) != 1 || tx.inserted[0].BornDate != nil {
				t.Fatalf("inserted %v, want one row with born_date None", tx.inserted)
			}
			if len(tx.audits) != 1 || tx.audits[0].Action != 0 || !tx.audits[0].Created {
				t.Fatalf("audits %v, want one create entry", tx.audits)
			}
		})
		t.Run("associate/"+spelling.name, func(t *testing.T) {
			tx := &tutorTransaction{}
			audit := subscriptionAudit{tx: tx, now: creates.now}
			row, err := creates.saveAssociate(context.Background(), tx, audit, spelling.object, tutorAssociation, nil, nil)
			if !errors.Is(err, domain.ErrSubscriptionCreateNotPorted) || row != nil {
				t.Fatalf("saved=%v err=%v, want the not-ported refusal", row, err)
			}
			if len(tx.inserted) != 0 || len(tx.saved) != 0 || len(tx.audits) != 0 {
				t.Fatalf("a refused associate wrote %v %v %v", tx.inserted, tx.saved, tx.audits)
			}
		})
	}
}

// TestSubscriptionCreateTutorBornDateNoneOnAnExistingRow is the get_or_create
// update: an explicit null clears the stored date, a missing key is not in
// validated_data and leaves it.
func TestSubscriptionCreateTutorBornDateNoneOnAnExistingRow(t *testing.T) {
	creates := tutorCreates()
	minor := "a1000000-0000-4000-8000-000000700002"
	storedBorn := time.Date(1980, 1, 1, 0, 0, 0, 0, time.UTC)
	existing := domain.SubscriptionEditAssociate{ID: "a1000000-0000-4000-8000-000000700003", Sex: "M",
		TaxCode: ptr("SNNTLL80A01H501C"), BornDate: &storedBorn, AssociationID: ptr(tutorAssociation),
		CreationDate: time.Date(2020, 1, 1, 0, 0, 0, 0, time.UTC)}
	cases := []struct {
		name   string
		object domain.RequestObject
		want   *time.Time
	}{
		{name: "missing key keeps the stored date", object: tutorPersonObject(nil, false), want: &storedBorn},
		{name: "explicit null clears it", object: tutorPersonObject(nil, true)},
	}
	for _, test := range cases {
		t.Run(test.name, func(t *testing.T) {
			tx := &tutorTransaction{matches: []domain.SubscriptionEditAssociate{existing}}
			audit := subscriptionAudit{tx: tx, now: creates.now}
			row, err := creates.saveAssociate(context.Background(), tx, audit, test.object, tutorAssociation, &minor, nil)
			if err != nil || row == nil {
				t.Fatalf("saved=%v err=%v", row, err)
			}
			if len(tx.saved) != 1 {
				t.Fatalf("saved rows %v, want one update", tx.saved)
			}
			if test.want == nil {
				if row.BornDate != nil || tx.saved[0].BornDate != nil {
					t.Fatalf("born_date %v (stored %v), want None", row.BornDate, tx.saved[0].BornDate)
				}
			} else if row.BornDate == nil || !row.BornDate.Equal(*test.want) {
				t.Fatalf("born_date %v, want the stored %v", row.BornDate, *test.want)
			}
			if len(tx.audits) != 1 || tx.audits[0].Action != 1 || tx.audits[0].Created {
				t.Fatalf("audits %v, want one update entry", tx.audits)
			}
			_, rendered := tx.audits[0].Changes["born_date"]
			if rendered != (test.want == nil) {
				t.Fatalf("born_date change %v, want rendered=%v", tx.audits[0].Changes["born_date"], test.want == nil)
			}
		})
	}
}

// TestSubscriptionCreateTutorMatchingTheMinorRow is the tutor whose tax code is
// the minor's: get_or_create returns the minor's own row and the serializer's
// setattr loop writes the tutor's fields over it (an update entry, no create).
// The view's stale minor instance then saves the minor's values back, the
// update entry auditlog renders as the reverse diff.
func TestSubscriptionCreateTutorMatchingTheMinorRow(t *testing.T) {
	creates := tutorCreates()
	minorID := "a1000000-0000-4000-8000-000000700004"
	minorBorn := time.Date(2015, 1, 1, 0, 0, 0, 0, time.UTC)
	stored := domain.SubscriptionEditAssociate{ID: minorID, Sex: "F", FirstName: ptr("Tina"), LastName: ptr("Uguale"),
		TaxCode: ptr("UGLTNI15A41H501D"), BornDate: &minorBorn, IsMinor: true, AssociationID: ptr(tutorAssociation),
		CreationDate: time.Date(2026, 11, 12, 9, 0, 0, 0, time.UTC)}
	object := domain.RequestObject{
		{Name: "first_name", Value: "Tino"},
		{Name: "last_name", Value: "Uguale"},
		{Name: "sex", Value: "M"},
		{Name: "tax_code", Value: "UGLTNI15A41H501D"},
		{Name: "born_date", Value: "01/01/1980"},
	}
	tx := &tutorTransaction{matches: []domain.SubscriptionEditAssociate{stored}}
	audit := subscriptionAudit{tx: tx, now: creates.now}
	saved, err := creates.saveAssociate(context.Background(), tx, audit, object, tutorAssociation, &minorID, nil)
	if err != nil || saved == nil {
		t.Fatalf("saved=%v err=%v", saved, err)
	}
	if saved.ID != minorID {
		t.Fatalf("id %q, want the minor's own row %q", saved.ID, minorID)
	}
	if saved.FirstName == nil || *saved.FirstName != "Tino" || saved.Sex != "M" || !saved.IsMinor {
		t.Fatalf("row %+v, want the tutor's names and sex over the minor's is_minor", saved)
	}
	if len(tx.inserted) != 0 || len(tx.saved) != 1 {
		t.Fatalf("inserted %v saved %v, want one update of the minor's row", tx.inserted, tx.saved)
	}
	if len(tx.audits) != 1 || tx.audits[0].Action != 1 || tx.audits[0].Created {
		t.Fatalf("audits %v, want one update entry", tx.audits)
	}
	tutorChanges := map[string][2]string{"first_name": {"Tina", "Tino"}, "sex": {"F", "M"},
		"born_date": {"2015-01-01", "1980-01-01"}}
	if !reflect.DeepEqual(tx.audits[0].Changes, tutorChanges) {
		t.Fatalf("tutor update changes %v, want %v", tx.audits[0].Changes, tutorChanges)
	}
	// associate.user = user; associate.save() of the stale minor instance.
	owner := "a1000000-0000-4000-8000-000000700001"
	saved.UserID = &owner
	minor := stored
	minor.UserID = &owner
	staleChanges := map[string][2]string{"first_name": {"Tino", "Tina"}, "sex": {"M", "F"},
		"born_date": {"1980-01-01", "2015-01-01"}}
	if got := associateChanges(*saved, minor); !reflect.DeepEqual(got, staleChanges) {
		t.Fatalf("stale save changes %v, want %v", got, staleChanges)
	}
}
