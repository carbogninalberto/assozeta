package service

import (
	"context"
	"encoding/json"
	"errors"
	"math/big"
	"regexp"
	"strconv"
	"strings"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
)

type InstructorLists struct{ store domain.InstructorListStore }

func NewInstructorLists(store domain.InstructorListStore) *InstructorLists {
	return &InstructorLists{store: store}
}

// List follows instructor_list: the scope user's instructors for any role.
func (s *InstructorLists) List(ctx context.Context, identity domain.ResolvedIdentity) (map[string]any, error) {
	rows, err := s.store.Instructors(ctx, identity.ScopeUser.ID)
	if err != nil {
		return nil, err
	}
	items := make([]dto.InstructorItem, 0, len(rows))
	for _, row := range rows {
		item, err := dto.InstructorItemFrom(row)
		if err != nil {
			return nil, err
		}
		items = append(items, item)
	}
	return map[string]any{"data": items}, nil
}

var (
	ErrInstructorNotFound   = errors.New("instructor not found")
	ErrInstructorNotAllowed = errors.New("instructor not allowed")
	// ErrInstructorRange is the 500 of unpacking a date_range without exactly one ' al '.
	ErrInstructorRange = errors.New("instructor date range cannot be unpacked")
)

type InstructorInfos struct {
	store domain.InstructorInfoStore
	now   func() time.Time
}

func NewInstructorInfos(store domain.InstructorInfoStore, now func() time.Time) *InstructorInfos {
	return &InstructorInfos{store: store, now: now}
}

var (
	isoSlashDate     = regexp.MustCompile(`^(\d{4})/(\d{1,2})/(\d{1,2})$`)
	italianSlashDate = regexp.MustCompile(`^(\d{1,2})/(\d{1,2})/(\d{4})$`)
)

// strptimeDate is datetime.strptime for '%Y/%m/%d' (iso) or '%d/%m/%Y'.
func strptimeDate(text string, iso bool) (time.Time, bool) {
	pattern, order := italianSlashDate, [3]int{3, 2, 1}
	if iso {
		pattern, order = isoSlashDate, [3]int{1, 2, 3}
	}
	match := pattern.FindStringSubmatch(text)
	if match == nil {
		return time.Time{}, false
	}
	year, _ := strconv.Atoi(match[order[0]])
	month, _ := strconv.Atoi(match[order[1]])
	day, _ := strconv.Atoi(match[order[2]])
	if month < 1 || month > 12 || day < 1 {
		return time.Time{}, false
	}
	date := time.Date(year, time.Month(month), day, 0, 0, 0, 0, time.UTC)
	return date, date.Day() == day && date.Month() == time.Month(month)
}

// InstructorWindow is instructor_info's date window: 'A al B' in either
// format, else the last 365 days to tomorrow; absent, the current month.
func InstructorWindow(now time.Time, raw string, present bool) (time.Time, time.Time, error) {
	day := func(value time.Time) time.Time {
		return time.Date(value.Year(), value.Month(), value.Day(), 0, 0, 0, 0, time.UTC)
	}
	if !present {
		start := time.Date(now.Year(), now.Month(), 1, 0, 0, 0, 0, time.UTC)
		return start, start.AddDate(0, 1, 0).AddDate(0, 0, -1), nil
	}
	parts := strings.Split(raw, " al ")
	if len(parts) != 2 {
		return time.Time{}, time.Time{}, ErrInstructorRange
	}
	for _, iso := range []bool{true, false} {
		from, ok := strptimeDate(parts[0], iso)
		to, ok2 := strptimeDate(parts[1], iso)
		if ok && ok2 {
			return from, to, nil
		}
	}
	return day(now.AddDate(0, 0, -365)), day(now.AddDate(0, 0, 1)), nil
}

// decimalSum renders sum(values) the way DRF encodes it: a float of the
// exact Decimal sum, or int 0 for an empty sum.
func decimalSum(values []string) (json.Number, error) {
	if len(values) == 0 {
		return "0", nil
	}
	total := new(big.Rat)
	for _, value := range values {
		parsed, ok := new(big.Rat).SetString(value)
		if !ok {
			return "", ErrPaymentSaveFailed
		}
		total.Add(total, parsed)
	}
	return dto.PythonDecimalFloat(total.FloatString(6))
}

// Info follows instructor_info.
func (s *InstructorInfos) Info(ctx context.Context, identity domain.ResolvedIdentity, raw, dateRange string, rangePresent bool) (map[string]any, error) {
	id, err := messageReadsParseUUID(raw)
	if err != nil {
		return nil, err
	}
	row, err := s.store.Instructor(ctx, id)
	if err != nil {
		return nil, err
	}
	if row == nil {
		return nil, ErrInstructorNotFound
	}
	instructorAssociation, err := s.store.FirstAssociation(ctx, row.UserID)
	if err != nil {
		return nil, err
	}
	requesting, err := s.store.FirstAssociation(ctx, identity.ScopeUser.ID)
	if err != nil {
		return nil, err
	}
	if requesting == nil || instructorAssociation == nil || *requesting != *instructorAssociation {
		return nil, ErrInstructorNotAllowed
	}
	acting := identity.Effective
	if acting == nil {
		acting = identity.Actor
	}
	own, err := s.store.OwnInstructor(ctx, identity.ScopeUser.ID, acting.ID)
	if err != nil {
		return nil, err
	}
	if own != nil && *own != row.ID {
		return nil, ErrInstructorNotAllowed
	}
	item, err := dto.InstructorItemFrom(*row)
	if err != nil {
		return nil, err
	}
	from, to, err := InstructorWindow(s.now(), dateRange, rangePresent)
	if err != nil {
		return nil, err
	}
	hours, err := s.store.Hours(ctx, row.ID, from, to)
	if err != nil {
		return nil, err
	}
	stats := map[string]any{"hours": 0, "total_amount": 0, "total_amount_paid": 0, "total_amount_to_pay": 0}
	if len(hours) > 0 {
		var all, amounts, paid, unpaid []string
		for _, hour := range hours {
			all, amounts = append(all, hour.Hours), append(amounts, hour.Amount)
			if hour.Paid {
				paid = append(paid, hour.Amount)
			} else {
				unpaid = append(unpaid, hour.Amount)
			}
		}
		for key, values := range map[string][]string{"hours": all, "total_amount": amounts, "total_amount_paid": paid, "total_amount_to_pay": unpaid} {
			if stats[key], err = decimalSum(values); err != nil {
				return nil, err
			}
		}
	}
	return map[string]any{"data": item, "stats": stats}, nil
}
