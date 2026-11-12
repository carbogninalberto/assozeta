package service

import (
	"context"
	"encoding/json"
	"errors"
	"math/big"
	"regexp"
	"strings"
	"time"
	"unicode"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/refusal"
)

var (
	// ErrPersonaNotSportAssociation is User.sport_association for an athlete:
	// DRF PermissionDenied('User is not a sport association').
	ErrPersonaNotSportAssociation = errors.New("user is not a sport association")
	// ErrPersonaInvalidPK is get_object_or_404's bare Http404 for a pk the UUID field rejects.
	ErrPersonaInvalidPK = errors.New("associate pk rejected")
	// ErrPersonaUnhandled is an exception Django does not handle (the 500 page).
	ErrPersonaUnhandled = errors.New("unhandled persona queryset error")
	// ErrPersonaNotPorted refuses inputs whose Django semantics are not ported (the 500 page, never a guessed body).
	ErrPersonaNotPorted = refusal.New("persona input not ported")
)

type Personas struct {
	store domain.PersonasStore
	now   func() time.Time
}

func NewPersonas(store domain.PersonasStore, now func() time.Time) *Personas {
	return &Personas{store: store, now: now}
}

// today is date.today() in the Django process (TIME_ZONE = 'UTC').
func (s *Personas) today() time.Time {
	now := s.now().UTC()
	return time.Date(now.Year(), now.Month(), now.Day(), 0, 0, 0, 0, time.UTC)
}

// association is request.user.sport_association.
func (s *Personas) association(ctx context.Context, identity domain.ResolvedIdentity) (string, error) {
	if identity.ScopeUser.Role == domain.RoleAthlete {
		return "", ErrPersonaNotSportAssociation
	}
	return s.store.OwnerAssociation(ctx, identity.ScopeUser.ID)
}

func personaParam(query domain.RequestObject, name string) (string, bool) {
	value, present := query.Lookup(name)
	text, _ := value.(string)
	return text, present
}

// queryset follows AssociateViewSet.get_queryset in Django's order.
func (s *Personas) queryset(ctx context.Context, identity domain.ResolvedIdentity, query domain.RequestObject, groupHeader string) (domain.PersonaQuery, error) {
	association, err := s.association(ctx, identity)
	if err != nil {
		return domain.PersonaQuery{}, err
	}
	today := s.today()
	result := domain.PersonaQuery{AssociationID: association, GroupID: supplierGroup(groupHeader)}
	if term, _ := personaParam(query, "query[generalSearch]"); term != "" {
		if result.Search, err = personaSearch(term, s.now().UTC().Year()%100); err != nil {
			return result, err
		}
	}
	if raw, present := personaParam(query, "query[is_tutor]"); present && raw != "" {
		// BooleanField.to_python on the Exists annotation's lookup value.
		var value bool
		switch raw {
		case "t", "True", "1":
			value = true
		case "f", "False", "0":
			value = false
		default:
			return result, ErrPersonaUnhandled
		}
		result.IsTutor = append(result.IsTutor, value)
	}
	if raw, _ := personaParam(query, "query[type]"); raw != "" {
		athletes, tutors := false, false
		for _, token := range strings.Split(raw, ",") {
			athletes = athletes || token == "athletes"
			tutors = tutors || token == "tutors"
		}
		if athletes != tutors {
			result.IsTutor = append(result.IsTutor, tutors)
		}
	}
	for _, bound := range []struct {
		name   string
		target **time.Time
	}{{"query[minAge]", &result.BornOnOrBefore}, {"query[maxAge]", &result.BornOnOrAfter}} {
		raw, present := personaParam(query, bound.name)
		if !present {
			continue
		}
		// today.replace(year=today.year - int(value)): any error is unhandled.
		years, ok := dto.PythonIntText(raw)
		if !ok {
			return result, ErrPersonaUnhandled
		}
		year := new(big.Int).Sub(big.NewInt(int64(today.Year())), years)
		if !year.IsInt64() || year.Int64() < 1 || year.Int64() > 9999 {
			return result, ErrPersonaUnhandled
		}
		moment := time.Date(int(year.Int64()), today.Month(), today.Day(), 0, 0, 0, 0, time.UTC)
		if moment.Day() != today.Day() {
			return result, ErrPersonaUnhandled // 29 February in a common year
		}
		*bound.target = &moment
	}
	field, _ := personaParam(query, "sort[field]")
	direction, _ := personaParam(query, "sort[sort]")
	if field != "" && direction != "" {
		switch field {
		case "tutors":
			field = "tutors_count"
		case "age":
			field = "born_date"
			if direction == "asc" {
				direction = "desc"
			} else {
				direction = "asc"
			}
		case "full_name":
			field = "last_name"
		}
		if direction != "asc" {
			field = "-" + field
		}
		// Query.add_ordering: '?' is random; one leading '-' is stripped, then the name must resolve.
		if field == "?" {
			result.Order = &domain.PersonaOrder{Random: true}
		} else {
			name, descending := strings.CutPrefix(field, "-")
			if !domain.PersonaOrderable(name, result.Search != nil) {
				// An unknown name is a FieldError (500); relation paths and reverse
				// accessors, which Django could join, are not ported (also 500).
				return result, ErrPersonaUnhandled
			}
			result.Order = &domain.PersonaOrder{Key: name, Descending: descending}
		}
	}
	return result, nil
}

var personaDatePatterns = []*regexp.Regexp{
	regexp.MustCompile(`([0-9]{1,2})[.]([0-9]{1,2})[.]([0-9]{2,4})`),
	regexp.MustCompile(`([0-9]{1,2})[/]([0-9]{1,2})[/]([0-9]{2,4})`),
	regexp.MustCompile(`([0-9]{1,2})[-]([0-9]{1,2})[-]([0-9]{2,4})`),
}

var personaLikeEscape = strings.NewReplacer(`\`, `\\`, `%`, `\%`, `_`, `\_`)

// personaSearch prepares sqlite_search(queryset, term) for Associate: the term
// is decoded with replacement characters, stripped and lower-cased as Python
// does, and split into \w+ words.
func personaSearch(raw string, centuryYear int) (*domain.PersonaSearch, error) {
	term := pythonLower(dto.PythonStripText(pythonReplaceInvalidUTF8(raw)))
	escaped := personaLikeEscape.Replace(term)
	search := &domain.PersonaSearch{Term: term, Prefix: escaped + "%", Contains: "%" + escaped + "%"}
	var words []string
	var current strings.Builder
	for _, r := range term + " " {
		// CJK Extension I (Unicode 15.1) is a letter to the production Python and not yet to Go's tables.
		if r == '_' || unicode.IsLetter(r) || unicode.IsNumber(r) || r >= 0x2EBF0 && r <= 0x2EE5D {
			current.WriteRune(r)
			continue
		}
		if current.Len() > 0 {
			words = append(words, current.String())
			current.Reset()
		}
	}
	variations := personaDateVariations(term, centuryYear)
	for _, word := range words {
		search.Words = append(search.Words, "%"+personaLikeEscape.Replace(word)+"%")
		numeric := strings.Trim(word, "0123456789") == ""
		dates := []string{}
		if numeric {
			for _, variation := range variations {
				dates = append(dates, "%"+personaLikeEscape.Replace(variation)+"%")
			}
		}
		search.Dates = append(search.Dates, dates)
	}
	if len(words) > 1 {
		reversed := make([]string, len(words))
		for i, word := range words {
			reversed[len(words)-1-i] = word
		}
		for _, permutation := range []string{strings.Join(reversed, " "), strings.Join(words, " ")} {
			search.Permutations = append(search.Permutations, "%"+personaLikeEscape.Replace(permutation)+"%")
		}
	}
	return search, nil
}

// personaDateVariations is get_date_variations: the first pattern that matches.
func personaDateVariations(term string, centuryYear int) []string {
	for _, pattern := range personaDatePatterns {
		match := pattern.FindStringSubmatch(term)
		if match == nil {
			continue
		}
		day, month, year := match[1], match[2], match[3]
		full := year
		if len(year) == 2 {
			century := "19"
			if pythonDecimal(year) <= centuryYear {
				century = "20"
			}
			full = century + year
		}
		zfill := func(text string) string {
			if len(text) < 2 {
				return "0" + text
			}
			return text
		}
		return []string{day + "." + month + "." + year, day + "." + month + "." + full, day + "/" + month + "/" + year,
			day + "/" + month + "/" + full, day + "-" + month + "-" + year, day + "-" + month + "-" + full,
			full + "-" + zfill(month) + "-" + zfill(day), month + "." + day + "." + year, month + "/" + day + "/" + year,
			month + "-" + day + "-" + year}
	}
	return nil
}

func pythonDecimal(text string) int {
	value := 0
	for _, r := range text {
		value = value*10 + int(r-'0')
	}
	return value
}

func personaPage(count int64, query domain.RequestObject) (number int64, size *big.Int, pages int64, err error) {
	size = big.NewInt(10)
	if raw, present := personaParam(query, "pagination[perpage]"); present {
		if value, ok := dto.PythonIntText(raw); ok && value.Sign() > 0 {
			size = value
		}
	}
	pages = 1
	if count > 0 {
		// ceil(count / per_page) with Python's float division; exact for these magnitudes.
		total := new(big.Int).Add(big.NewInt(count), new(big.Int).Sub(size, big.NewInt(1)))
		pages = new(big.Int).Quo(total, size).Int64()
	}
	raw, _ := personaParam(query, "pagination[page]")
	switch raw {
	case "":
		number = 1
	case "last":
		number = pages
	default:
		parsed, ok := dto.PythonIntText(raw)
		if !ok || !parsed.IsInt64() || parsed.Int64() < 1 || parsed.Int64() > pages {
			return 0, nil, 0, dto.ErrDRFInvalidPage
		}
		number = parsed.Int64()
	}
	return number, size, pages, nil
}

// List follows AssociateViewSet.list: get_queryset, KTDatatablePagination and
// AssociateSearchSerializer with the page meta.
func (s *Personas) List(ctx context.Context, identity domain.ResolvedIdentity, query domain.RequestObject, groupHeader string) (any, error) {
	filter, err := s.queryset(ctx, identity, query, groupHeader)
	if err != nil {
		return nil, err
	}
	count, err := s.store.CountPersonas(ctx, filter)
	if err != nil {
		return nil, err
	}
	number, size, pages, err := personaPage(count, query)
	if err != nil {
		return nil, err
	}
	offset := int64(0)
	limit := count
	if size.IsInt64() {
		offset = (number - 1) * size.Int64()
		limit = min(size.Int64(), count-offset)
	}
	rows := []domain.PersonaListed{}
	if limit > 0 {
		if rows, err = s.store.Personas(ctx, filter, limit, offset); err != nil {
			return nil, err
		}
	}
	ids := make([]string, len(rows))
	for i, row := range rows {
		ids[i] = row.ID
	}
	relations, err := s.store.Relations(ctx, ids)
	if err != nil {
		return nil, err
	}
	today := s.today()
	data, rowIDs := make([]any, 0, len(rows)), make([]any, 0, len(rows))
	for _, row := range rows {
		data = append(data, dto.PersonaSearchItem(row, relations, today))
		rowIDs = append(rowIDs, row.ID)
	}
	meta := domain.RequestObject{{Name: "total", Value: personaNumber(count)}, {Name: "page", Value: personaNumber(number)},
		{Name: "pages", Value: personaNumber(pages)}, {Name: "perpage", Value: json.Number(size.String())},
		{Name: "rowIds", Value: rowIDs}}
	return domain.RequestObject{{Name: "data", Value: data}, {Name: "meta", Value: meta}}, nil
}

func personaNumber(value int64) json.Number { return json.Number(big.NewInt(value).String()) }

// object follows get_object(): the queryset, then the pk lookup.
func (s *Personas) object(ctx context.Context, identity domain.ResolvedIdentity, rawID string, query domain.RequestObject, groupHeader string) (domain.PersonaListed, domain.PersonaQuery, error) {
	filter, err := s.queryset(ctx, identity, query, groupHeader)
	if err != nil {
		return domain.PersonaListed{}, filter, err
	}
	id, err := paymentInfoParseUUID(rawID)
	if err != nil {
		return domain.PersonaListed{}, filter, ErrPersonaInvalidPK
	}
	text := id.String()
	filter.ID = &text
	rows, err := s.store.Personas(ctx, filter, -1, 0)
	if err != nil {
		return domain.PersonaListed{}, filter, err
	}
	if len(rows) == 0 {
		return domain.PersonaListed{}, filter, domain.ErrPersonaNotFound
	}
	return rows[0], filter, nil
}

// Info follows AssociateViewSet.info: get_object() and AssociateSerializer.
func (s *Personas) Info(ctx context.Context, identity domain.ResolvedIdentity, rawID string, query domain.RequestObject, groupHeader string) (any, error) {
	row, filter, err := s.object(ctx, identity, rawID, query, groupHeader)
	if err != nil {
		return nil, err
	}
	return s.render(ctx, row, filter.GroupID)
}

// Render is AssociateSerializer over an instance read by primary key (no
// queryset filters); the nested managers use the X-Group-Id filter.
func (s *Personas) Render(ctx context.Context, id string, groupHeader string) (any, error) {
	row, err := s.store.PersonaByID(ctx, id)
	if err != nil {
		return nil, err
	}
	if row == nil {
		return nil, domain.ErrPersonaNotFound
	}
	return s.render(ctx, *row, supplierGroup(groupHeader))
}

func (s *Personas) render(ctx context.Context, row domain.PersonaListed, group *string) (any, error) {
	relations, err := s.store.Relations(ctx, []string{row.ID})
	if err != nil {
		return nil, err
	}
	tutors := make([]string, len(relations))
	for i, relation := range relations {
		tutors[i] = relation.ID
	}
	// BasicAssociateSerializer(tutor).tutors and BasicTutorsSerializer(...).tutors use the M2M manager.
	second, err := s.store.Tutors(ctx, tutors, group)
	if err != nil {
		return nil, err
	}
	nested := make([]string, len(second))
	for i, relation := range second {
		nested[i] = relation.ID
	}
	third, err := s.store.Tutors(ctx, nested, group)
	if err != nil {
		return nil, err
	}
	family := []string{}
	if row.FamilyID != nil {
		if family, err = s.store.FamilyMembers(ctx, *row.FamilyID, row.ID, group); err != nil {
			return nil, err
		}
	}
	return dto.PersonaInfo(row, family, relations, second, third, s.today()), nil
}

// Delete follows the viewset's delete(): without a pk, get_object() fails its
// lookup-kwarg assertion after get_queryset (500); with one, the lookup's 404s
// apply and the soft delete is not ported (refused before any write).
func (s *Personas) Delete(ctx context.Context, identity domain.ResolvedIdentity, rawID *string, query domain.RequestObject, groupHeader string) error {
	if rawID == nil {
		if _, err := s.queryset(ctx, identity, query, groupHeader); err != nil {
			return err
		}
		return ErrPersonaUnhandled
	}
	if _, _, err := s.object(ctx, identity, *rawID, query, groupHeader); err != nil {
		return err
	}
	return ErrPersonaNotPorted
}

// All follows AssociateViewSet.all: {'data': [...]}, unordered.
func (s *Personas) All(ctx context.Context, identity domain.ResolvedIdentity, groupHeader string) (any, error) {
	names, err := s.names(ctx, identity, groupHeader)
	if err != nil {
		return nil, err
	}
	return domain.RequestObject{{Name: "data", Value: names}}, nil
}

// AllTutors follows AssociateViewSet.all_tutors: every live associate, as a bare list.
func (s *Personas) AllTutors(ctx context.Context, identity domain.ResolvedIdentity, groupHeader string) (any, error) {
	return s.names(ctx, identity, groupHeader)
}

func (s *Personas) names(ctx context.Context, identity domain.ResolvedIdentity, groupHeader string) ([]any, error) {
	association, err := s.association(ctx, identity)
	if err != nil {
		return nil, err
	}
	rows, err := s.store.PersonaNames(ctx, association, supplierGroup(groupHeader))
	if err != nil {
		return nil, err
	}
	items := make([]any, 0, len(rows))
	for _, row := range rows {
		items = append(items, domain.RequestObject{{Name: "associate_id", Value: row.ID},
			{Name: "first_name", Value: dto.PersonaOptional(row.FirstName)}, {Name: "last_name", Value: dto.PersonaOptional(row.LastName)}})
	}
	return items, nil
}

// AllWithSubscriptions follows AssociateViewSet.all_with_subscriptions: the
// current subscriptions keyed by associate id (a later row replaces the value).
func (s *Personas) AllWithSubscriptions(ctx context.Context, identity domain.ResolvedIdentity, groupHeader string) (any, error) {
	association, err := s.association(ctx, identity)
	if err != nil {
		return nil, err
	}
	rows, err := s.store.CurrentSubscriptions(ctx, association, supplierGroup(groupHeader), s.today())
	if err != nil {
		return nil, err
	}
	data := domain.RequestObject{}
	positions := map[string]int{}
	for _, row := range rows {
		value := domain.RequestObject{{Name: "subscription_id", Value: row.SubscriptionID}, {Name: "associate_id", Value: row.AssociateID},
			{Name: "first_name", Value: dto.PersonaOptional(row.FirstName)}, {Name: "last_name", Value: dto.PersonaOptional(row.LastName)},
			{Name: "email", Value: dto.PersonaOptional(row.Email)}, {Name: "tax_code", Value: dto.PersonaOptional(row.TaxCode)},
			{Name: "subscription_status", Value: personaNumber(row.StatusFlag)}, {Name: "type", Value: personaNumber(row.Type)}}
		if i, ok := positions[row.AssociateID]; ok {
			data[i].Value = value
			continue
		}
		positions[row.AssociateID] = len(data)
		data = append(data, domain.RequestField{Name: row.AssociateID, Value: value})
	}
	return domain.RequestObject{{Name: "data", Value: data}}, nil
}
