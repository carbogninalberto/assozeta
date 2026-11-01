package service

import (
	"context"
	"errors"
	"reflect"
	"testing"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

func TestPersonaSearchWords(t *testing.T) {
	search, err := personaSearch("  Bassi, ANNA_x 50%  ", 26)
	if err != nil {
		t.Fatal(err)
	}
	if search.Term != "bassi, anna_x 50%" || search.Prefix != `bassi, anna\_x 50\%%` || search.Contains != `%bassi, anna\_x 50\%%` {
		t.Fatalf("term %q prefix %q contains %q", search.Term, search.Prefix, search.Contains)
	}
	if want := []string{"%bassi%", `%anna\_x%`, "%50%"}; !reflect.DeepEqual(search.Words, want) {
		t.Fatalf("words %q", search.Words)
	}
	if want := []string{`%50 anna\_x bassi%`, `%bassi anna\_x 50%`}; !reflect.DeepEqual(search.Permutations, want) {
		t.Fatalf("permutations %q", search.Permutations)
	}
	if len(search.Dates[2]) != 0 {
		t.Fatalf("no date in the term, got %q", search.Dates[2])
	}
}

func TestPersonaSearchWithoutWords(t *testing.T) {
	for _, raw := range []string{"   ", "%", "\x1c-\x1f"} {
		search, err := personaSearch(raw, 26)
		if err != nil || len(search.Words) != 0 || len(search.Permutations) != 0 {
			t.Fatalf("%q: %+v %v", raw, search, err)
		}
	}
}

func TestPersonaSearchLatinLettersAndNumbers(t *testing.T) {
	search, err := personaSearch("FORLÌ ½µ×ª", 26)
	if err != nil {
		t.Fatal(err)
	}
	// × is not \w; ½ (No), µ (Ll) and ª (Lo) are.
	if want := []string{"%forlì%", "%½µ%", "%ª%"}; !reflect.DeepEqual(search.Words, want) {
		t.Fatalf("words %q", search.Words)
	}
}

func TestPersonaSearchRefusals(t *testing.T) {
	for _, raw := range []string{"Ωmega", "İstanbul", "a\xffb", "名前"} {
		if _, err := personaSearch(raw, 26); !errors.Is(err, ErrPersonaNotPorted) {
			t.Fatalf("%q: %v", raw, err)
		}
	}
}

func TestPersonaDateVariations(t *testing.T) {
	search, err := personaSearch("99 12/3/10", 26)
	if err != nil {
		t.Fatal(err)
	}
	want := []string{"%12.3.10%", "%12.3.2010%", "%12/3/10%", "%12/3/2010%", "%12-3-10%", "%12-3-2010%", "%2010-03-12%",
		"%3.12.10%", "%3/12/10%", "%3-12-10%"}
	for i := range search.Words {
		if !reflect.DeepEqual(search.Dates[i], want) {
			t.Fatalf("word %d dates %q", i, search.Dates[i])
		}
	}
	// A two-digit year above the current one is the previous century; the first matching pattern wins.
	if got := personaDateVariations("1.2.30 4/5/2001", 26); got[1] != "1.2.1930" || got[6] != "1930-02-01" {
		t.Fatalf("%q", got)
	}
	// re.search: the leftmost match, \d{1,2} backtracking inside a longer digit run.
	if got := personaDateVariations("123.45.67890", 26); got[0] != "23.45.6789" {
		t.Fatalf("%q", got)
	}
	if got := personaDateVariations("nessuna data", 26); got != nil {
		t.Fatalf("%q", got)
	}
}

func TestPersonaAgeBounds(t *testing.T) {
	store := &personaTestStore{}
	s := NewPersonas(store, func() time.Time { return time.Date(2028, 2, 29, 10, 0, 0, 0, time.UTC) })
	identity := domain.ResolvedIdentity{ScopeUser: &domain.Account{ID: "owner", Role: domain.RoleAssociation}}
	query := func(pairs ...string) domain.RequestObject {
		object := domain.RequestObject{}
		for i := 0; i < len(pairs); i += 2 {
			object = append(object, domain.RequestField{Name: pairs[i], Value: pairs[i+1]})
		}
		return object
	}
	// today.replace(year=...) on 29 February needs a leap target year.
	if _, err := s.queryset(t.Context(), identity, query("query[minAge]", "1"), ""); !errors.Is(err, ErrPersonaUnhandled) {
		t.Fatalf("common year: %v", err)
	}
	result, err := s.queryset(t.Context(), identity, query("query[minAge]", "4", "query[maxAge]", " +8 "), "")
	if err != nil || result.BornOnOrBefore.Format("2006-01-02") != "2024-02-29" || result.BornOnOrAfter.Format("2006-01-02") != "2020-02-29" {
		t.Fatalf("%+v %v", result, err)
	}
	for _, value := range []string{"", "x", "2029", "-7972", "1e3"} {
		if _, err := s.queryset(t.Context(), identity, query("query[maxAge]", value), ""); !errors.Is(err, ErrPersonaUnhandled) {
			t.Fatalf("%q: %v", value, err)
		}
	}
}

func TestPersonaOrdering(t *testing.T) {
	s := NewPersonas(&personaTestStore{}, time.Now)
	identity := domain.ResolvedIdentity{ScopeUser: &domain.Account{ID: "owner", Role: domain.RoleAssociation}}
	order := func(field, direction string, extra ...domain.RequestField) (*domain.PersonaOrder, error) {
		result, err := s.queryset(t.Context(), identity, append(domain.RequestObject{{Name: "sort[field]", Value: field},
			{Name: "sort[sort]", Value: direction}}, extra...), "")
		return result.Order, err
	}
	for _, c := range []struct {
		field, direction, key string
		descending, random    bool
	}{{"age", "asc", "born_date", true, false}, {"age", "x", "born_date", false, false}, {"tutors", "x", "tutors_count", true, false},
		{"full_name", "asc", "last_name", false, false}, {"-last_name", "asc", "last_name", true, false}, {"user", "asc", "user", false, false},
		{"?", "asc", "", false, true}} {
		got, err := order(c.field, c.direction)
		if err != nil || got.Key != c.key || got.Descending != c.descending || got.Random != c.random {
			t.Fatalf("%s %s: %+v %v", c.field, c.direction, got, err)
		}
	}
	for _, field := range []string{"?x", "nope", "relevance", "tutor_relations", "user__username", "last_name.x", "LAST_NAME"} {
		if _, err := order(field, "asc"); !errors.Is(err, ErrPersonaUnhandled) {
			t.Fatalf("%s: %v", field, err)
		}
	}
	if _, err := order("-last_name", "desc"); !errors.Is(err, ErrPersonaUnhandled) {
		t.Fatalf("--last_name: %v", err)
	}
	if got, err := order("relevance", "desc", domain.RequestField{Name: "query[generalSearch]", Value: "x"}); err != nil || got.Key != "relevance" {
		t.Fatalf("relevance with a search: %+v %v", got, err)
	}
}

func TestPersonaAthleteIsNotAnAssociation(t *testing.T) {
	s := NewPersonas(&personaTestStore{}, time.Now)
	identity := domain.ResolvedIdentity{ScopeUser: &domain.Account{ID: "athlete", Role: domain.RoleAthlete}}
	if _, err := s.All(t.Context(), identity, ""); !errors.Is(err, ErrPersonaNotSportAssociation) {
		t.Fatal(err)
	}
	if err := s.Delete(t.Context(), identity, nil, nil, ""); !errors.Is(err, ErrPersonaNotSportAssociation) {
		t.Fatal(err)
	}
}

type personaTestStore struct{ domain.PersonasStore }

func (personaTestStore) OwnerAssociation(_ context.Context, owner string) (string, error) {
	return "association", nil
}
