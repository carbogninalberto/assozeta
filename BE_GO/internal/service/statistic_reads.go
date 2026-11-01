package service

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"math/big"
	"sort"
	"strconv"
	"time"
	_ "time/tzdata" // Europe/Rome for incomeAndExpenses on hosts without zoneinfo

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
)

// StatisticResult is a view's status and body (rendered as JSON).
type StatisticResult struct {
	Status int
	Body   any
}

// ErrStatisticPythonError is an exception the Django view does not handle
// (the stable 500 page).
var ErrStatisticPythonError = errors.New("statistic view raised")

// ErrStatisticAthleteFailure is an exception inside the athlete dashboard's
// try block: Django answers 500 with its JSON error body.
var ErrStatisticAthleteFailure = errors.New("athlete dashboard failed")

type statisticStore interface {
	domain.StatisticDashboardStore
	domain.StatisticReportStore
	domain.StatisticAthleteStore
}

type StatisticReads struct {
	store    statisticStore
	now      func() time.Time
	location *time.Location
	rome     *time.Location
}

func NewStatisticReads(store statisticStore, now func() time.Time, location *time.Location) *StatisticReads {
	rome, err := time.LoadLocation("Europe/Rome")
	if err != nil {
		panic(err)
	}
	return &StatisticReads{store: store, now: now, location: location, rome: rome}
}

// aware is timezone.now(); naive is datetime.now() read as UTC (USE_TZ, TZ setting of the process).
func (s *StatisticReads) aware() time.Time { return s.now().UTC().Truncate(time.Microsecond) }
func (s *StatisticReads) naive() time.Time { return DjangoNaiveNow(s.now(), s.location) }

func statisticDate(t time.Time) time.Time {
	return time.Date(t.Year(), t.Month(), t.Day(), 0, 0, 0, 0, time.UTC)
}

func statisticScope(identity domain.ResolvedIdentity, group *string, association string) domain.StatisticScope {
	return domain.StatisticScope{Association: association, Group: group, Impersonation: paymentCategoryAssociationScope(identity)}
}

// StatisticGroup is the X-Group-Id filter (ignored when not a UUID).
func StatisticGroup(header string) *string { return supplierGroup(header) }

// BalanceSheetRange is BalanceSheetData.get_range_from_year_and_starting_date
// (no custom end date) with TIME_ZONE='UTC': the balance year containing the
// instant, [start, next start - 1 day]. ErrStatisticPythonError is the
// ValueError datetime() raises for a day or month out of range, or a start
// outside years 1..9999 in the instant's year, the previous or the next one.
func BalanceSheetRange(moment time.Time, day, month int) (domain.StatisticYear, error) {
	moment = moment.UTC()
	valid := func(year int) bool {
		return year >= 1 && year <= 9999 && time.Date(year, time.Month(month), day, 0, 0, 0, 0, time.UTC).Day() == day
	}
	year := moment.Year()
	if month < 1 || month > 12 || day < 1 || day > 31 || !valid(year) || !valid(year-1) {
		return domain.StatisticYear{}, ErrStatisticPythonError
	}
	start := time.Date(year, time.Month(month), day, 0, 0, 0, 0, time.UTC)
	if moment.Before(start) {
		start = time.Date(year-1, time.Month(month), day, 0, 0, 0, 0, time.UTC)
	}
	if !valid(start.Year() + 1) {
		return domain.StatisticYear{}, ErrStatisticPythonError
	}
	end := time.Date(start.Year()+1, time.Month(month), day, 0, 0, 0, 0, time.UTC).AddDate(0, 0, -1)
	return domain.StatisticYear{From: start, To: end}, nil
}

// ---------------------------------------------------------------------------------------------
// statistic/dashboard
// ---------------------------------------------------------------------------------------------

type statisticPerson struct {
	AssociateID *string `json:"associate_id"`
	FirstName   *string `json:"first_name"`
	LastName    *string `json:"last_name"`
}

func personOf(p domain.StatisticPerson) statisticPerson {
	return statisticPerson{AssociateID: p.AssociateID, FirstName: p.FirstName, LastName: p.LastName}
}

func statisticData(value any) StatisticResult {
	return StatisticResult{Status: 200, Body: map[string]any{"data": value}}
}

// Dashboard follows statistic_dashboard for every widget except todaylessons
// and expiredPayments, which are refused (not ported).
func (s *StatisticReads) Dashboard(ctx context.Context, identity domain.ResolvedIdentity, widget any, present bool, group *string) (StatisticResult, error) {
	association, err := s.store.ScopedAssociation(ctx, identity.ScopeUser.ID, paymentCategoryAssociationScope(identity))
	if err != nil {
		return StatisticResult{}, err
	}
	if !present {
		return StatisticResult{Status: 400, Body: []string{"error: widget is required."}}, nil
	}
	name, _ := widget.(string)
	scope := statisticScope(identity, group, association)
	switch name {
	case "subscriptions":
		return s.subscriptions(ctx, scope)
	case "payments":
		return s.dailySeries(ctx, scope, true)
	case "associates":
		return s.dailySeries(ctx, scope, false)
	case "bestcourses":
		return s.bestCourses(ctx, scope)
	case "expiringcarnets":
		return s.expiringCarnets(ctx, scope, identity.ScopeUser.ID)
	case "expiringmedicalcertificates", "expiredmedicalcertificates":
		return s.medicals(ctx, scope, identity.ScopeUser.ID, name == "expiringmedicalcertificates")
	case "subscriptionstoapprove":
		return s.approvals(ctx, scope)
	case "incomeAndExpenses":
		return s.incomeAndExpenses(ctx, scope)
	case "todaylessons", "expiredPayments":
		return StatisticResult{}, domain.ErrStatisticNotPorted
	}
	return StatisticResult{Status: 404, Body: []string{"error: widget not found."}}, nil
}

func (s *StatisticReads) subscriptions(ctx context.Context, scope domain.StatisticScope) (StatisticResult, error) {
	rows, err := s.store.SubscriptionStatuses(ctx, scope, s.aware().Format("2006-01-02"))
	if err != nil {
		return StatisticResult{}, err
	}
	pie := []int64{0, 0, 0, 0}
	var total int64
	for _, row := range rows {
		pie[row.Key-1] = row.Count
		total += row.Count
	}
	return statisticData(map[string]any{"pie_subscriptions": pie, "total_subscriptions": total}), nil
}

// statisticDays lists the UTC dates from from.date() through to.date().
func statisticDays(from, to time.Time) []time.Time {
	days := []time.Time{}
	for day, last := statisticDate(from), statisticDate(to); !day.After(last); day = day.AddDate(0, 0, 1) {
		days = append(days, day)
	}
	return days
}

func (s *StatisticReads) dailySeries(ctx context.Context, scope domain.StatisticScope, payments bool) (StatisticResult, error) {
	end := s.aware()
	start := end.Add(-30 * 24 * time.Hour)
	var rows []domain.StatisticDaily
	var err error
	if payments {
		rows, err = s.store.DailyPayments(ctx, scope, start, end)
	} else {
		rows, err = s.store.DailySubscriptions(ctx, scope, start, end)
	}
	if err != nil {
		return StatisticResult{}, err
	}
	byDay := map[time.Time]string{}
	for _, row := range rows {
		byDay[statisticDate(row.Day)] = row.Value
	}
	days := statisticDays(start, end)
	if payments {
		series := make([]dto.PythonFloat, len(days))
		values := make([]float64, len(days))
		for i, day := range days {
			if text, ok := byDay[day]; ok {
				value, err := strconv.ParseFloat(text, 64)
				if err != nil {
					return StatisticResult{}, err
				}
				values[i], series[i] = value, dto.PythonFloat(value)
			}
		}
		return statisticData(map[string]any{"current_month_payments": series,
			"total_payments": dto.PythonFloat(dto.PythonFloatSum(values))}), nil
	}
	series, total, err := statisticCounts(days, byDay)
	if err != nil {
		return StatisticResult{}, err
	}
	return statisticData(map[string]any{"current_month_associates": series, "total_associates": total}), nil
}

func statisticCounts(days []time.Time, byDay map[time.Time]string) ([]int64, int64, error) {
	series := make([]int64, len(days))
	var total int64
	for i, day := range days {
		if text, ok := byDay[day]; ok {
			value, err := strconv.ParseInt(text, 10, 64)
			if err != nil {
				return nil, 0, err
			}
			series[i] = value
			total += value
		}
	}
	return series, total, nil
}

type statisticBestCourse struct {
	CourseID      string  `json:"course_id"`
	Title         *string `json:"title"`
	Description   *string `json:"description"`
	Subscriptions int64   `json:"subscriptions"`
}

func (s *StatisticReads) bestCourses(ctx context.Context, scope domain.StatisticScope) (StatisticResult, error) {
	end := s.aware()
	start := end.Add(-7 * 24 * time.Hour)
	courses, err := s.store.BestCourses(ctx, scope)
	if err != nil {
		return StatisticResult{}, err
	}
	best := make([]statisticBestCourse, len(courses))
	for i, course := range courses {
		best[i] = statisticBestCourse(course)
	}
	rows, err := s.store.DailyCourseSubscriptions(ctx, scope, start, end)
	if err != nil {
		return StatisticResult{}, err
	}
	byDay := map[time.Time]string{}
	for _, row := range rows {
		byDay[statisticDate(row.Day)] = row.Value
	}
	series, _, err := statisticCounts(statisticDays(start, end), byDay)
	if err != nil {
		return StatisticResult{}, err
	}
	total, err := s.store.CourseSubscriptionCount(ctx, scope)
	if err != nil {
		return StatisticResult{}, err
	}
	return statisticData(map[string]any{"current_week_course_associates": series, "total_course_associates": total, "best_courses": best}), nil
}

type statisticCarnetItem struct {
	ID           string `json:"carnet_subscription_id"`
	Subscription struct {
		ID        string          `json:"subscription_id"`
		Associate statisticPerson `json:"associate"`
	} `json:"subscription"`
	Carnet struct {
		ID            *string         `json:"carnet_id"`
		Title         *string         `json:"title"`
		Description   *string         `json:"description"`
		LessonsNumber *int64          `json:"lessons_number"`
		Fee           json.RawMessage `json:"fee"`
		CreationDate  string          `json:"creation_date"`
		Public        *bool           `json:"public"`
		Meta          json.RawMessage `json:"meta"`
	} `json:"carnet"`
	left *big.Rat
}

func referenceField(object domain.RequestObject, name string) (any, bool) { return object.Lookup(name) }

// expiringCarnets is the view's process_carnet over the PostgreSQL-filtered rows.
func (s *StatisticReads) expiringCarnets(ctx context.Context, scope domain.StatisticScope, owner string) (StatisticResult, error) {
	thirtyDaysAgo := s.aware().Add(-30 * 24 * time.Hour)
	rows, err := s.store.ExpiringCarnets(ctx, scope, owner)
	if err != nil {
		return StatisticResult{}, err
	}
	items := []statisticCarnetItem{}
	for _, row := range rows {
		parsed, err := dto.ParseReferenceJSON([]byte(row.Meta))
		if err != nil {
			return StatisticResult{}, err
		}
		meta, ok := parsed.(domain.RequestObject)
		if !ok {
			continue
		}
		raw, _ := referenceField(meta, "lessons_left")
		number, ok := raw.(json.Number)
		if !ok {
			continue // None, strings (and booleans, which PostgreSQL already excluded)
		}
		left, ok := dto.PythonNumberRat(number)
		if !ok {
			return StatisticResult{}, domain.ErrStatisticNotPorted
		}
		if left.Sign() == 0 {
			recent, err := statisticRecentLesson(meta, thirtyDaysAgo)
			if err != nil {
				return StatisticResult{}, err
			}
			if !recent {
				continue
			}
		}
		// Attribute access on a missing related row is an unhandled AttributeError.
		if row.AssociateID == nil || row.CarnetID == nil {
			return StatisticResult{}, ErrStatisticPythonError
		}
		if !dto.PythonJSONFinite(meta) {
			return StatisticResult{}, domain.ErrStatisticNotPorted // DRF's strict renderer refuses inf
		}
		item := statisticCarnetItem{ID: row.CarnetSubscriptionID, left: left}
		item.Subscription.ID = row.SubscriptionID
		item.Subscription.Associate = personOf(row.StatisticPerson)
		item.Carnet.ID, item.Carnet.Title, item.Carnet.Description = row.CarnetID, row.Title, row.Description
		item.Carnet.LessonsNumber, item.Carnet.Fee, item.Carnet.Public = row.LessonsNumber, dto.DecimalNumber(row.Fee), row.Public
		item.Carnet.CreationDate = dto.DRFUTCDateTime(row.CreationDate)
		item.Carnet.Meta = json.RawMessage(dto.PythonJSONDumpsCompact(meta))
		items = append(items, item)
	}
	sort.SliceStable(items, func(i, j int) bool { return items[i].left.Cmp(items[j].left) < 0 })
	return statisticData(map[string]any{"expiring_carnets": items}), nil
}

// statisticRecentLesson is any(...) over meta.get('lessons_registry', []):
// false when the carnet is skipped (including the caught TypeError/ValueError).
func statisticRecentLesson(meta domain.RequestObject, since time.Time) (bool, error) {
	registry, present := referenceField(meta, "lessons_registry")
	if !present {
		return false, nil
	}
	var lessons []any
	switch value := registry.(type) {
	case []any:
		lessons = value
	case domain.RequestObject, string:
		return false, nil // iterating keys or characters never yields a dict
	default:
		return false, nil // iter() of None/number/bool: TypeError, caught
	}
	for _, lesson := range lessons {
		object, ok := lesson.(domain.RequestObject)
		if !ok {
			continue
		}
		date, present := referenceField(object, "date")
		if !present {
			continue
		}
		text, ok := date.(string)
		if !ok {
			return false, nil // strptime TypeError, caught
		}
		if dto.StrptimeUnicodeDigits(text) {
			return false, domain.ErrStatisticNotPorted
		}
		moment, ok := dto.StrptimeLessonDate(text)
		if !ok {
			return false, nil // ValueError, caught
		}
		if moment.After(since) {
			return true, nil
		}
	}
	return false, nil
}

type statisticMedicalItem struct {
	SubscriptionID string          `json:"subscription_id"`
	DaysLeft       int             `json:"days_left"`
	Associate      statisticPerson `json:"associate"`
	Medical        struct {
		ExpirationDate string `json:"expiration_date"`
	} `json:"medical"`
}

func (s *StatisticReads) medicals(ctx context.Context, scope domain.StatisticScope, user string, expiring bool) (StatisticResult, error) {
	now := s.aware()
	today := statisticDate(now)
	balance, err := s.store.Balance(ctx, user)
	if err != nil {
		return StatisticResult{}, err
	}
	year, err := BalanceSheetRange(now, balance.Day, balance.Month)
	if err != nil {
		return StatisticResult{}, err
	}
	rows, err := s.store.Medicals(ctx, scope, today.Format("2006-01-02"), expiring, year)
	if err != nil {
		return StatisticResult{}, err
	}
	items := make([]statisticMedicalItem, len(rows))
	for i, row := range rows {
		items[i] = statisticMedicalItem{SubscriptionID: row.SubscriptionID, Associate: personOf(row.StatisticPerson),
			DaysLeft: int(statisticDate(row.Expiration).Sub(today).Hours() / 24)}
		items[i].Medical.ExpirationDate = row.Expiration.Format("2006-01-02")
	}
	sort.SliceStable(items, func(i, j int) bool { return items[i].DaysLeft < items[j].DaysLeft })
	key := "expired_medical_certificates"
	if expiring {
		key = "expiring_medical_certificates"
	}
	return statisticData(map[string]any{key: items}), nil
}

type statisticApprovalItem struct {
	CreationDate   string          `json:"creation_date"`
	SubscriptionID string          `json:"subscription_id"`
	Status         int             `json:"status"`
	Associate      statisticPerson `json:"associate"`
}

func (s *StatisticReads) approvals(ctx context.Context, scope domain.StatisticScope) (StatisticResult, error) {
	rows, err := s.store.Approvals(ctx, scope, s.aware().Format("2006-01-02"))
	if err != nil {
		return StatisticResult{}, err
	}
	items := make([]statisticApprovalItem, len(rows))
	for i, row := range rows {
		items[i] = statisticApprovalItem{CreationDate: dto.DRFUTCDateTime(row.CreationDate), SubscriptionID: row.SubscriptionID,
			Status: row.Status, Associate: personOf(row.StatisticPerson)}
	}
	return statisticData(map[string]any{"subscriptions_to_approve": items}), nil
}

// incomeAndExpenses: pytz's astimezone fixes the current Europe/Rome offset,
// and replace()/timedelta keep it for every derived bound.
func (s *StatisticReads) incomeAndExpenses(ctx context.Context, scope domain.StatisticScope) (StatisticResult, error) {
	now := s.aware()
	_, offset := now.In(s.rome).Zone()
	zone := time.FixedZone("Rome", offset)
	local := now.In(zone)
	todayStart := time.Date(local.Year(), local.Month(), local.Day(), 0, 0, 0, 0, zone)
	todayEnd := todayStart.Add(24 * time.Hour)
	starts := map[string]time.Time{"today": todayStart, "last_month": todayStart.Add(-31 * 24 * time.Hour),
		"total": time.Date(local.Year(), 1, 1, 0, 0, 0, 0, zone)}
	data := map[string]any{}
	for _, name := range []string{"today", "last_month", "total"} {
		sums, err := s.store.IncomeAndExpenses(ctx, scope, starts[name], todayEnd)
		if err != nil {
			return StatisticResult{}, err
		}
		data[name+"_income"], data[name+"_expenses"] = dto.DecimalNumber(sums.Income), dto.DecimalNumber(sums.Expenses)
	}
	return statisticData(data), nil
}

// ---------------------------------------------------------------------------------------------
// statistic/report
// ---------------------------------------------------------------------------------------------

var statisticMonthNames = []string{"Gen", "Feb", "Mar", "Apr", "Mag", "Giu", "Lug", "Ago", "Set", "Ott", "Nov", "Dic"}

func statisticMonths(start int) ([]string, []int) {
	names, numbers := make([]string, 12), make([]int, 12)
	for i := 0; i < 12; i++ {
		index := (start - 1 + i) % 12
		if index < 0 {
			index += 12
		}
		names[i], numbers[i] = statisticMonthNames[index], index+1
	}
	return names, numbers
}

type statisticYearItem struct {
	Year      int    `json:"year"`
	StartDate string `json:"start_date"`
}

// Report follows statistic_report after IsAssociationUser.
func (s *StatisticReads) Report(ctx context.Context, identity domain.ResolvedIdentity, currentDate any, present bool, group *string) (StatisticResult, error) {
	user := identity.ScopeUser
	if user.Role != domain.RoleAssociation {
		return StatisticResult{Status: 403, Body: []string{"error: forbidden."}}, nil
	}
	today := statisticDate(s.naive())
	current := today
	if text, _ := currentDate.(string); present && text != "" {
		parsed, ok := dto.PythonStrptimeDate(text)
		if !ok {
			return StatisticResult{}, ErrStatisticPythonError
		}
		if parsed.Year() < 1000 {
			// strftime('%Y') of a year below 1000 is platform dependent: not characterized.
			return StatisticResult{}, domain.ErrStatisticNotPorted
		}
		current = parsed
	}
	balance, err := s.store.Balance(ctx, user.ID)
	if err != nil {
		return StatisticResult{}, err
	}
	last, err := BalanceSheetRange(today, balance.Day, balance.Month)
	if err != nil {
		return StatisticResult{}, err
	}
	previous := time.Date(last.From.Year()-1, last.From.Month(), last.From.Day(), 0, 0, 0, 0, time.UTC)
	if previous.Day() != last.From.Day() || previous.Year() < 1 {
		return StatisticResult{}, ErrStatisticPythonError // datetime.replace(year=...) ValueError
	}
	years := []statisticYearItem{{Year: last.From.Year() - 1, StartDate: dto.DRFUTCDateTime(previous)},
		{Year: last.From.Year(), StartDate: dto.DRFUTCDateTime(last.From)}}
	if balance.Month < 1 || balance.Month > 12 {
		return StatisticResult{}, ErrStatisticPythonError
	}
	names, numbers := statisticMonths(balance.Month)
	year, err := BalanceSheetRange(current, balance.Day, balance.Month)
	if err != nil {
		return StatisticResult{}, err
	}
	association, err := s.store.ScopedAssociation(ctx, user.ID, paymentCategoryAssociationScope(identity))
	if err != nil {
		return StatisticResult{}, err
	}
	scope := statisticScope(identity, group, association)

	monthly := func(accepted bool) ([]int64, error) {
		rows, err := s.store.SubscriptionMonths(ctx, scope, year, accepted)
		if err != nil {
			return nil, err
		}
		out := make([]int64, 12)
		for i, number := range numbers {
			for _, row := range rows {
				if int(row.Month.Month()) == number {
					out[i], _ = strconv.ParseInt(row.Value, 10, 64)
					break
				}
			}
		}
		return out, nil
	}
	subscriptions, err := monthly(false)
	if err != nil {
		return StatisticResult{}, err
	}
	approved, err := monthly(true)
	if err != nil {
		return StatisticResult{}, err
	}
	count, err := s.store.ActiveCourseCount(ctx, scope, year)
	if err != nil {
		return StatisticResult{}, err
	}
	groups, err := s.store.CourseGroups(ctx, scope, year)
	if err != nil {
		return StatisticResult{}, err
	}
	if int64(len(groups)) > count {
		return StatisticResult{}, ErrStatisticPythonError // IndexError
	}
	courseTitles := make([]*string, count)
	courseCounts := make([]int64, count)
	courseRevenue := make([]json.RawMessage, count)
	empty := ""
	for i := range courseTitles {
		courseTitles[i], courseRevenue[i] = &empty, json.RawMessage("0")
	}
	for i, row := range groups {
		courseTitles[i], courseCounts[i], courseRevenue[i] = row.Title, row.Count, dto.DecimalNumber(row.Revenue)
	}
	statuses, err := s.store.StatusCounts(ctx, scope)
	if err != nil {
		return StatisticResult{}, err
	}
	pie := []int64{0, 0, 0, 0}
	for _, row := range statuses {
		index := row.Key - 1
		if index < 0 {
			index += 4 // Python's negative index
		}
		pie[index] = row.Count
	}
	paymentRows, err := s.store.PaymentMonths(ctx, scope, year)
	if err != nil {
		return StatisticResult{}, err
	}
	byMonth := map[int]string{}
	for _, row := range paymentRows {
		byMonth[int(row.Month.Month())] = row.Value // ordered by month: a later year overwrites
	}
	payments := make([]json.RawMessage, 12)
	for i, number := range numbers {
		payments[i] = json.RawMessage("0")
		if text, ok := byMonth[number]; ok {
			payments[i] = dto.DecimalNumber(&text)
		}
	}
	totals, err := s.store.PaymentTotals(ctx, scope, year)
	if err != nil {
		return StatisticResult{}, err
	}
	var approvedTotal int64
	for _, value := range approved {
		approvedTotal += value
	}
	accepted, err := s.store.Accepted(ctx, scope)
	if err != nil {
		return StatisticResult{}, err
	}
	var ageTotal int64
	sexes := map[string]int{}
	for _, row := range accepted {
		if !row.HasAssociate {
			return StatisticResult{}, ErrStatisticPythonError // sub.associate is None
		}
		if row.Born != nil {
			days := today.Sub(statisticDate(*row.Born)).Hours() / 24
			ageTotal += int64(days / 365.25)
		}
		if row.Sex != nil {
			sexes[*row.Sex]++
		}
	}
	averageAge := "0.00"
	if len(accepted) > 0 {
		averageAge = strconv.FormatFloat(float64(ageTotal)/float64(len(accepted)), 'f', 2, 64)
	}
	invoices, err := s.store.InvoiceCount(ctx, scope, year)
	if err != nil {
		return StatisticResult{}, err
	}
	data := map[string]any{
		"available_years": years,
		"subscriptions":   map[string]any{"x": names, "y": subscriptions},
		"kpi_subscriptions": map[string]any{
			"average_subscriptions_month": strconv.FormatFloat(float64(approvedTotal)/12, 'f', 2, 64),
			"total_subscriptions":         approvedTotal, "average_age": averageAge,
			"female": sexes["F"], "male": sexes["M"], "other": sexes["A"]},
		"kpi_payments": map[string]any{"total_subscriptions": dto.DecimalNumber(totals.Subscriptions), "total_payments": totals.Count,
			"total_activities": dto.DecimalNumber(totals.Activities), "total_invoices": invoices},
		"pie_subscriptions": map[string]any{"y": pie, "x": []string{"Non firmata", "In attesa", "Rifiutate", "Accettate"}},
		"payments":          map[string]any{"x": names, "y": payments},
		"pie_payments": map[string]any{"y": []json.RawMessage{dto.DecimalOrZero(totals.Paid), dto.DecimalOrZero(totals.Unpaid)},
			"x": []string{"Pagato", "Non pagato"}},
		"courses": map[string]any{"y": courseCounts, "x": courseTitles, "revenue": map[string]any{"y": courseRevenue}},
	}
	return statisticData(data), nil
}

// ---------------------------------------------------------------------------------------------
// statistic/athlete-dashboard
// ---------------------------------------------------------------------------------------------

type statisticCommunicationItem struct {
	Message string `json:"message"`
	SentOn  string `json:"sent_on"`
}

type statisticLesson struct {
	EventID              json.RawMessage `json:"event_id"`
	IsAbsent             bool            `json:"is_absent"`
	IsPresent            bool            `json:"is_present"`
	AttendanceDayID      string          `json:"attendance_day_id"`
	Title                json.RawMessage `json:"title"`
	Start                string          `json:"start"`
	CourseSubscriptionID string          `json:"course_subscription_id"`
	CourseID             string          `json:"course_id"`
	CourseTitle          *string         `json:"course_title"`
	Subscription         struct {
		ID        string          `json:"subscription_id"`
		Associate statisticPerson `json:"associate"`
	} `json:"subscription"`
	day time.Time
}

// Athlete follows statistic_athlete_dashboard after the role check; every
// failure inside the view's try block is ErrStatisticAthleteFailure.
func (s *StatisticReads) Athlete(ctx context.Context, identity domain.ResolvedIdentity, group *string) (StatisticResult, error) {
	if identity.ScopeUser.Role != domain.RoleAthlete {
		return StatisticResult{Status: 403, Body: []string{"error: forbidden."}}, nil
	}
	result, err := s.athlete(ctx, identity, group)
	if err != nil && !errors.Is(err, domain.ErrStatisticNotPorted) {
		return StatisticResult{}, errors.Join(ErrStatisticAthleteFailure, err)
	}
	return result, err
}

func (s *StatisticReads) athlete(ctx context.Context, identity domain.ResolvedIdentity, group *string) (StatisticResult, error) {
	impersonation := paymentCategoryAssociationScope(identity)
	primary, _, err := s.store.PrimaryAssociation(ctx)
	if err != nil {
		return StatisticResult{}, err
	}
	if impersonation == nil && primary == nil {
		return StatisticResult{Status: 503, Body: map[string]string{"error": "Primary sport association is not configured."}}, nil
	}
	association := primary
	if impersonation != nil {
		association = impersonation
	}
	card, err := s.store.AssociationCard(ctx, *association)
	if err != nil {
		return StatisticResult{}, err
	}
	user := identity.ScopeUser.ID
	scope := domain.StatisticAthleteScope{Association: impersonation, Person: user, Group: group}
	subscriptions, err := s.store.AthleteSubscriptions(ctx, scope, user, *association)
	if err != nil {
		return StatisticResult{}, err
	}
	today := statisticDate(s.aware())
	active := 0
	for _, row := range subscriptions {
		if row.Start == nil || row.End == nil || (!statisticDate(*row.Start).After(today) && !today.After(statisticDate(*row.End))) {
			active++
		}
	}
	communications, err := s.store.Communications(ctx, *association, s.aware().Add(-14*24*time.Hour))
	if err != nil {
		return StatisticResult{}, err
	}
	posts := make([]statisticCommunicationItem, len(communications))
	for i, row := range communications {
		posts[i] = statisticCommunicationItem{Message: row.Message, SentOn: dto.DRFUTCDateTime(row.SentOn)}
	}
	lessons := []statisticLesson{}
	for _, subscription := range subscriptions {
		if subscription.Archived {
			continue
		}
		courses, err := s.store.SubscriptionCourses(ctx, scope, subscription.SubscriptionID)
		if err != nil {
			return StatisticResult{}, err
		}
		for _, course := range courses {
			registries, err := s.store.CourseRegistries(ctx, scope, course.CourseID)
			if err != nil {
				return StatisticResult{}, err
			}
			for _, registry := range registries {
				found, err := s.registryLessons(ctx, scope, registry, course, subscription)
				if err != nil {
					return StatisticResult{}, err
				}
				lessons = append(lessons, found...)
			}
		}
	}
	sort.SliceStable(lessons, func(i, j int) bool { return lessons[i].day.Before(lessons[j].day) })
	data := []map[string]any{{
		"sport_association": map[string]any{
			"denomination": card.Denomination, "sport_association_id": card.ID,
			"user": map[string]any{"user_id": card.UserID, "avatar_image": card.AvatarImage, "first_name": card.FirstName,
				"last_name": card.LastName, "username": card.Username},
			"review_url": card.ReviewURL, "review_url_enabled": card.ReviewURLEnabled, "communications": posts,
		},
		"subscriptions": active,
	}}
	return StatisticResult{Status: 200, Body: map[string]any{"data": data, "upcoming_lessons": lessons}}, nil
}

var errStatisticTypeError = errors.New("TypeError")

// registryLessons iterates registry.events: anything but a list of objects
// (or an empty iterable) raises TypeError, which the view's outer handler turns
// into its 500 JSON body; ValueError/KeyError skip the event.
func (s *StatisticReads) registryLessons(ctx context.Context, scope domain.StatisticAthleteScope, registry domain.StatisticRegistry,
	course domain.StatisticAthleteCourse, subscription domain.StatisticAthleteSubscription) ([]statisticLesson, error) {
	if registry.Events == nil {
		return nil, errStatisticTypeError
	}
	parsed, err := dto.ParseReferenceJSON([]byte(*registry.Events))
	if err != nil {
		return nil, err
	}
	var events []any
	switch value := parsed.(type) {
	case []any:
		events = value
	case domain.RequestObject:
		if len(value) > 0 {
			return nil, errStatisticTypeError // keys are strings
		}
		return nil, nil
	case string:
		if value != "" {
			return nil, errStatisticTypeError
		}
		return nil, nil
	default:
		return nil, errStatisticTypeError
	}
	lessons := []statisticLesson{}
	for _, item := range events {
		event, ok := item.(domain.RequestObject)
		if !ok {
			return nil, errStatisticTypeError
		}
		startValue, present := event.Lookup("start")
		if !present {
			continue // KeyError
		}
		text, ok := startValue.(string)
		if !ok {
			return nil, errStatisticTypeError
		}
		if dto.StrptimeUnicodeDigits(text) {
			return nil, domain.ErrStatisticNotPorted
		}
		start, ok := dto.StrptimeEventStart(text)
		if !ok {
			continue // ValueError
		}
		now := s.naive()
		if start.Before(now) || start.After(now.Add(7*24*time.Hour)) {
			continue
		}
		day, err := s.store.FirstAttendanceDay(ctx, scope, registry.ID, start.Format("2006-01-02"))
		if err != nil {
			return nil, err
		}
		if day == nil {
			continue
		}
		absent, err := statisticListed(day.ExpectedAbsences, course.CourseSubscriptionID)
		if errors.Is(err, errStatisticKeyError) {
			continue
		}
		if err != nil {
			return nil, err
		}
		attending, err := statisticListed(day.Attendees, course.CourseSubscriptionID)
		if errors.Is(err, errStatisticKeyError) {
			continue
		}
		if err != nil {
			return nil, err
		}
		eventID, hasID := event.Lookup("event_id")
		title, hasTitle := event.Lookup("title")
		if !hasID || !hasTitle {
			continue // KeyError
		}
		if !subscription.HasAssociate {
			return nil, ErrStatisticPythonError // AttributeError on subscription.associate
		}
		if !dto.PythonJSONFinite(eventID) || !dto.PythonJSONFinite(title) {
			return nil, domain.ErrStatisticNotPorted
		}
		lesson := statisticLesson{EventID: dto.PythonJSONDumpsCompact(eventID), IsAbsent: absent, IsPresent: attending,
			AttendanceDayID: day.ID, Title: dto.PythonJSONDumpsCompact(title), Start: start.Format("02/01/2006"),
			CourseSubscriptionID: course.CourseSubscriptionID, CourseID: course.CourseID, CourseTitle: course.CourseTitle,
			day: statisticDate(start)}
		lesson.Subscription.ID = subscription.SubscriptionID
		lesson.Subscription.Associate = personOf(subscription.StatisticPerson)
		lessons = append(lessons, lesson)
	}
	return lessons, nil
}

var errStatisticKeyError = errors.New("KeyError")

// statisticListed is `value and any(str(id) == entry['course_subscription_id'] for entry in value)`.
func statisticListed(text *string, id string) (bool, error) {
	if text == nil {
		return false, nil
	}
	parsed, err := dto.ParseReferenceJSON([]byte(*text))
	if err != nil {
		return false, err
	}
	var entries []any
	switch value := parsed.(type) {
	case nil:
		return false, nil
	case bool:
		if !value {
			return false, nil
		}
		return false, errStatisticTypeError
	case json.Number:
		rat, ok := dto.PythonNumberRat(value)
		if ok && rat.Sign() == 0 {
			return false, nil
		}
		return false, errStatisticTypeError
	case string:
		if value == "" {
			return false, nil
		}
		return false, errStatisticTypeError
	case domain.RequestObject:
		if len(value) == 0 {
			return false, nil
		}
		return false, errStatisticTypeError
	case []any:
		entries = value
	default:
		return false, fmt.Errorf("unexpected JSON value %T", parsed)
	}
	for _, entry := range entries {
		object, ok := entry.(domain.RequestObject)
		if !ok {
			return false, errStatisticTypeError
		}
		value, present := object.Lookup("course_subscription_id")
		if !present {
			return false, errStatisticKeyError
		}
		if text, ok := value.(string); ok && text == id {
			return true, nil
		}
	}
	return false, nil
}
