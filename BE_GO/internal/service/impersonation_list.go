package service

import (
	"context"
	"encoding/json"
	"math/big"
	"strings"
	"unicode"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
)

func impersonationQuery(query domain.RequestObject, key, fallback, otherwise string) string {
	value, present := query.Lookup(key)
	if !present && fallback != "" {
		value, present = query.Lookup(fallback)
	}
	if !present {
		return otherwise
	}
	text, _ := value.(string)
	return text
}

func impersonationInteger(input string) (*big.Int, bool) {
	text := strings.TrimFunc(input, unicode.IsSpace)
	var digits strings.Builder
	previousDigit := false
	count := 0
	for index, r := range []rune(text) {
		if index == 0 && (r == '+' || r == '-') {
			digits.WriteRune(r)
			continue
		}
		if r == '_' {
			if !previousDigit {
				return nil, false
			}
			previousDigit = false
			continue
		}
		digit := -1
		for _, entry := range unicode.Digit.R16 {
			if uint32(r) >= uint32(entry.Lo) && uint32(r) <= uint32(entry.Hi) && (uint32(r)-uint32(entry.Lo))%uint32(entry.Stride) == 0 {
				digit = int((uint32(r)-uint32(entry.Lo))/uint32(entry.Stride)) % 10
				break
			}
		}
		if digit < 0 {
			for _, entry := range unicode.Digit.R32 {
				if uint32(r) >= entry.Lo && uint32(r) <= entry.Hi && (uint32(r)-entry.Lo)%entry.Stride == 0 {
					digit = int((uint32(r)-entry.Lo)/entry.Stride) % 10
					break
				}
			}
		}
		if digit < 0 {
			return nil, false
		}
		digits.WriteByte(byte('0' + digit))
		previousDigit = true
		count++
	}
	if !previousDigit || count > 4300 {
		return nil, false
	}
	return new(big.Int).SetString(digits.String(), 10)
}

func (s *ImpersonationManagement) List(ctx context.Context, actor *domain.Account, query domain.RequestObject) (dto.ImpersonationList, error) {
	var zero dto.ImpersonationList
	page, ok := impersonationInteger(impersonationQuery(query, "pagination[page]", "page", "1"))
	perPage, perOK := impersonationInteger(impersonationQuery(query, "pagination[perpage]", "", "25"))
	if !ok || !perOK || page.Sign() < 1 || perPage.Sign() < 1 || perPage.Cmp(big.NewInt(100)) > 0 {
		return zero, &domain.ImpersonationValidationError{Detail: "Pagina non valida."}
	}
	scope, err := s.association(ctx, actor)
	if err != nil {
		return zero, err
	}
	search := strings.TrimFunc(impersonationQuery(query, "query[generalSearch]", "q", ""), func(r rune) bool { return unicode.IsSpace(r) || (r >= 0x1c && r <= 0x1f) })
	runes := []rune(search)
	if len(runes) > 150 {
		search = string(runes[:150])
	}
	roleText := impersonationQuery(query, "query[role]", "role", "")
	role := map[string]int{"association": 1, "athlete": 2, "collaborator": 3}[roleText]
	if roleText != "" && role == 0 {
		return zero, &domain.ImpersonationValidationError{Detail: "Ruolo non valido."}
	}
	ordering := impersonationQuery(query, "sort[field]", "", "username")
	direction := impersonationQuery(query, "sort[sort]", "", "asc")
	allowed := map[string]bool{"username": true, "email": true, "role": true, "first_name": true, "last_login": true, "association.name": true}
	if !allowed[ordering] || (direction != "asc" && direction != "desc") {
		return zero, &domain.ImpersonationValidationError{Detail: "Ordinamento non valido."}
	}
	offset := new(big.Int).Mul(new(big.Int).Sub(page, big.NewInt(1)), perPage)
	people, count, err := s.directory.ListImpersonationPeople(ctx, actor.ID, scopeAssociationID(scope), domain.ImpersonationListFilter{Search: search, Role: role, Sort: ordering, Descending: direction == "desc", Limit: int(perPage.Int64()), Offset: offset.String()})
	if err != nil {
		return zero, err
	}
	result := dto.ImpersonationList{Users: make([]dto.ImpersonationPerson, 0, len(people)), Meta: dto.ImpersonationListMeta{Page: json.Number(page.String()), PerPage: int(perPage.Int64()), Total: count, Pages: max(1, (count+perPage.Int64()-1)/perPage.Int64())}}
	if new(big.Int).Mul(new(big.Int).Set(page), perPage).Cmp(big.NewInt(count)) < 0 {
		next := json.Number(new(big.Int).Add(page, big.NewInt(1)).String())
		result.NextPage = &next
	}
	for _, person := range people {
		described, err := dto.DescribeImpersonationPerson(person, scope)
		if err != nil {
			return zero, err
		}
		result.Users = append(result.Users, described)
	}
	return result, nil
}
