package service

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"strings"
	"testing"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
)

// Expected values from fuzzywuzzy 0.18.0 (difflib) and Python 3.13 str.lower().
func TestSubscriptionMiscFuzzRatio(t *testing.T) {
	for _, item := range []struct {
		a, b string
		want int
	}{
		{"palestra olimpia", "palestra olimpya", 94}, {"palestra olimpia", "palazzo dello sport", 34},
		{"palestra olimpia", "palestra olimpia ", 97}, {"ali", "ali", 100}, {"", "x", 0}, {"", "", 100}, {"abcd", "dcba", 25},
		{"karate club", "club karate", 55}, {strings.Repeat("a", 150) + strings.Repeat("b", 100), strings.Repeat("b", 100) + strings.Repeat("a", 150), 0},
		{"dojo", "d", 40},
	} {
		if got := fuzzRatio(item.a, item.b); got != item.want {
			t.Errorf("fuzzRatio(%q, %q) = %d, want %d", item.a, item.b, got, item.want)
		}
	}
}

func TestSubscriptionMiscPythonLower(t *testing.T) {
	for input, want := range map[string]string{"İSTANBUL": "i̇stanbul", "ΟΔΟΣ": "οδος", "Σ": "σ", "ΑΣ Β": "ας β", "Palestra": "palestra"} {
		if got := pythonLower(input); got != want {
			t.Errorf("pythonLower(%q) = %q, want %q", input, got, want)
		}
	}
}

func TestSubscriptionMiscTaxCodeInputs(t *testing.T) {
	misc := &SubscriptionMisc{now: func() time.Time { return time.Date(2026, 11, 9, 0, 0, 0, 0, time.UTC) }}
	person := func(changes ...domain.RequestField) domain.RequestObject {
		body := domain.RequestObject{{Name: "last_name", Value: "Rossi"}, {Name: "first_name", Value: "Mario"}, {Name: "sex", Value: "M"},
			{Name: "born_date", Value: "10/05/1990"}, {Name: "born_city", Value: "Roma"}}
		for _, change := range changes {
			body = dto.SetRequestField(body, change.Name, change.Value)
		}
		return body
	}
	result, err := misc.TaxCode(person())
	if err != nil || result.(domain.RequestObject)[0].Value != "RSSMRA90E10H501G" {
		t.Fatalf("TaxCode = %v, %v", result, err)
	}
	for _, item := range []struct {
		body any
		want error
	}{
		{person(domain.RequestField{Name: "born_city", Value: "Atlantide"}), ErrSubscriptionMiscUnmapped},
		{person(domain.RequestField{Name: "sex", Value: json.Number("1")}), domain.ErrSubscriptionMiscServer},
		{person(domain.RequestField{Name: "sex", Value: "X"}), ErrSubscriptionMiscMissingData},
		{person(domain.RequestField{Name: "last_name", Value: json.Number("5")}), ErrSubscriptionMiscInvalidData},
		{person(domain.RequestField{Name: "last_name", Value: "王"}), nil},
		{person(domain.RequestField{Name: "born_date", Value: "10 maggio 1990"}), ErrSubscriptionMiscInvalidData},
		// Django decodes the byte as U+FFFD, which python-slugify drops:
		// codicefiscale.encode(..., 'Roma\ufffd') is RSSMRA90E10H501G.
		{person(domain.RequestField{Name: "born_city", Value: "Roma\xff"}), nil},
		{[]any{"Rossi"}, domain.ErrSubscriptionMiscServer},
	} {
		if _, err := misc.TaxCode(item.body); !errors.Is(err, item.want) {
			t.Errorf("TaxCode(%v) error = %v, want %v", item.body, err, item.want)
		}
	}
}

// subscriptionMiscLargeFile is the gym_name upload of the over-one-chunk case:
// 'riga\n' lines, then a line whose '\r\n' straddles byte 65,536, then 'coda'.
const (
	subscriptionMiscBeforeStraddle = 13000
	subscriptionMiscAfterStraddle  = 1000
)

func subscriptionMiscLargeFileContent() string {
	return strings.Repeat("riga\n", subscriptionMiscBeforeStraddle) + strings.Repeat("x", 535) + "\r\n" +
		strings.Repeat("riga\n", subscriptionMiscAfterStraddle) + "coda"
}

// generateLinkStore is GenerateLink's link insert: the association lookup, the
// stored name and the commit.
type generateLinkStore struct{ gymName string }

func (s *generateLinkStore) BeginSubscriptionMisc(context.Context) (domain.SubscriptionMiscTransaction, error) {
	return &generateLinkTransaction{store: s}, nil
}

type generateLinkTransaction struct {
	domain.SubscriptionMiscTransaction
	store *generateLinkStore
}

func (t *generateLinkTransaction) Association(context.Context, string) (string, error) {
	return "a1000000-0000-4000-8000-000000000001", nil
}

func (t *generateLinkTransaction) CreateLink(_ context.Context, _, _, _, gymName string, _, _ time.Time, _ domain.LoginAuditContext,
	_ time.Time) error {
	t.store.gymName = gymName
	return nil
}

func (t *generateLinkTransaction) Commit() error { return nil }

func (t *generateLinkTransaction) Rollback() error { return nil }

func TestSubscriptionMiscGenerateLinkWholeLargeFile(t *testing.T) {
	store := &generateLinkStore{}
	counter := 0
	misc := NewSubscriptionMisc(store, nil, func() time.Time { return time.Date(2026, 11, 9, 0, 0, 0, 0, time.UTC) },
		func() string {
			counter++
			return fmt.Sprintf("a1000000-0000-4000-8000-0000014%04d", counter)
		})
	identity := domain.ResolvedIdentity{Actor: &domain.Account{ID: "owner", Email: "owner@example.test"},
		ScopeUser: &domain.Account{ID: "owner", Role: 1}}
	content := subscriptionMiscLargeFileContent()
	if len(content) <= domain.RequestUploadHead {
		t.Fatalf("content is %d bytes, not over one chunk", len(content))
	}
	body := domain.RequestObject{{Name: "gym_name", Value: domain.RequestUpload{Name: "palestra.txt", Size: int64(len(content)),
		Content: content, Whole: true}}}
	result, err := misc.GenerateLink(context.Background(), identity, body, domain.LoginAuditContext{})
	if err != nil {
		t.Fatal("GenerateLink:", err)
	}
	if store.gymName != "palestra.txt" {
		t.Errorf("stored gym_name = %q, want the uploaded file name", store.gymName)
	}
	lines, ok := result.(domain.RequestObject)[1].Value.([]any)
	if result.(domain.RequestObject)[1].Name != "gym_name" || !ok {
		t.Fatalf("gym_name = %v, want the file's lines", result.(domain.RequestObject)[1].Value)
	}
	want := 1 + subscriptionMiscBeforeStraddle + subscriptionMiscAfterStraddle + 1
	if len(lines) != want {
		t.Fatalf("%d lines echoed, want %d", len(lines), want)
	}
	if lines[subscriptionMiscBeforeStraddle] != strings.Repeat("x", 535)+"\r\n" {
		t.Errorf("straddling line = %q", lines[subscriptionMiscBeforeStraddle])
	}
	if lines[0] != "riga\n" || lines[len(lines)-1] != "coda" ||
		strings.Join(toStrings(lines), "") != content {
		t.Errorf("the echoed lines do not rebuild the whole file")
	}
}

func toStrings(values []any) []string {
	result := make([]string, len(values))
	for i, value := range values {
		result[i], _ = value.(string)
	}
	return result
}
