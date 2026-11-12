package service

import (
	"context"
	"encoding/json"
	"errors"
	"testing"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/google/uuid"
)

// TestSubscriptionCreateBornDateAnyJSON drives create_subscription's first
// step, person_date(str(associate_data['born_date'])), for the JSON types the
// port used to refuse. A failed dateutil reading is the 500 page
// (SubscriptionCreateFailure); a text dateutil reads reaches the next key read.
func TestSubscriptionCreateBornDateAnyJSON(t *testing.T) {
	creates := &SubscriptionCreates{now: func() time.Time { return time.Date(2026, 11, 12, 0, 0, 0, 0, time.UTC) }}
	nextKey := dto.PythonStringRepr("new_user_account")
	cases := []struct {
		name  string
		value any
		// parsed: dateutil reads the text and create_subscription continues.
		parsed bool
	}{
		{name: "float", value: json.Number("25.5"), parsed: true},
		{name: "float with a zero fraction", value: json.Number("2000.0")},
		{name: "list", value: []any{"01/01/2000"}},
		{name: "dict", value: domain.RequestObject{{Name: "d", Value: "01/01/2000"}}},
		{name: "true", value: true},
		{name: "none", value: nil},
		{name: "integer", value: json.Number("19900315"), parsed: true},
	}
	for _, test := range cases {
		t.Run(test.name, func(t *testing.T) {
			data := domain.RequestObject{
				{Name: "associate_data", Value: domain.RequestObject{{Name: "born_date", Value: test.value}}},
			}
			_, err := creates.createSubscription(context.Background(), nil, domain.ResolvedIdentity{}, data, SubscriptionCreateRequest{})
			var failure *domain.SubscriptionCreateFailure
			if !errors.As(err, &failure) {
				t.Fatalf("error %v is not the 500 page failure", err)
			}
			if errors.Is(err, domain.ErrSubscriptionCreateNotPorted) {
				t.Fatalf("born_date %v is still refused", test.value)
			}
			if test.parsed {
				if failure.Text != nextKey {
					t.Fatalf("failure text %q, want the next key error %q", failure.Text, nextKey)
				}
				return
			}
			if !errors.Is(err, domain.ErrSubscriptionCreateServer) || failure.Text == nextKey {
				t.Fatalf("failure text %q, want dateutil's error", failure.Text)
			}
		})
	}
}

// TestSubscriptionCreatePythonStrOfBornValues records the str() renderings the
// dateutil step relies on; dto.PythonStr is the one implementation.
func TestSubscriptionCreatePythonStrOfBornValues(t *testing.T) {
	cases := []struct {
		value any
		want  string
	}{
		{json.Number("25.5"), "25.5"},
		{json.Number("2000.0"), "2000.0"},
		{json.Number("1e16"), "1e+16"},
		{json.Number("19900315"), "19900315"},
		{json.Number("-0"), "0"},
		{json.Number("123456789012345678901234567890"), "123456789012345678901234567890"},
		{nil, "None"},
		{true, "True"},
		{false, "False"},
		{[]any{"01/01/2000"}, "['01/01/2000']"},
		{domain.RequestObject{{Name: "a", Value: json.Number("1")}}, "{'a': 1}"},
	}
	for _, test := range cases {
		if got := dto.PythonStr(test.value); got != test.want {
			t.Errorf("PythonStr(%v) = %q, want %q", test.value, got, test.want)
		}
	}
}

// TestSubscriptionCreateBornDateNonStringIsInvalid covers AssociateSerializer's
// DateField: strptime of a non-string raises TypeError, the same 'invalid'
// error as a text in another format. A tutor with one is skipped.
func TestSubscriptionCreateBornDateNonStringIsInvalid(t *testing.T) {
	creates := &SubscriptionCreates{}
	object := func(value any) domain.RequestObject {
		return domain.RequestObject{
			{Name: "first_name", Value: "Nina"},
			{Name: "last_name", Value: "Tutelata"},
			{Name: "sex", Value: "F"},
			{Name: "born_date", Value: value},
		}
	}
	for _, value := range []any{json.Number("25.5"), json.Number("2000.0"), true, []any{"x"},
		domain.RequestObject{{Name: "d", Value: "x"}}} {
		_, err := creates.saveAssociate(context.Background(), nil, subscriptionAudit{}, object(value), "association", nil, nil)
		var invalid *domain.SubscriptionCreateInvalid
		if !errors.As(err, &invalid) {
			t.Fatalf("born_date %v: error %v is not the invalid response", value, err)
		}
		codes := invalid.Fields["born_date"]
		if len(codes) != 1 || codes[0] != dto.SubscriptionCreateDateFormat {
			t.Fatalf("born_date %v: codes %v", value, codes)
		}
	}
	minorOf := "minor"
	if saved, err := creates.saveAssociate(context.Background(), nil, subscriptionAudit{}, object([]any{"x"}),
		"association", &minorOf, nil); saved != nil || err != nil {
		t.Fatalf("invalid tutor: saved=%v err=%v", saved, err)
	}
}

// TestSubscriptionCreateGroupPythonUUIDSpellings covers Python's
// uuid.UUID(hex=...), which payment_info already reproduces: Go's uuid.Parse
// wants the 8-4-4-4-12 grouping, while dashes may sit anywhere.
func TestSubscriptionCreateGroupPythonUUIDSpellings(t *testing.T) {
	const canonical = "a1000000-0000-4000-8000-000000714001"
	const spelled = "a1000000-0000-4000-8000-0000007-14001"
	if _, err := uuid.Parse(spelled); err == nil {
		t.Fatalf("uuid.Parse accepts %q; pick a spelling Go rejects", spelled)
	}
	stored, sent := subscriptionCreateGroup(spelled)
	if stored == nil || *stored != canonical || sent != spelled {
		t.Fatalf("stored=%v sent=%q, want %q and %q", stored, sent, canonical, spelled)
	}
}
