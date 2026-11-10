package service

import (
	"context"
	"encoding/json"
	"errors"
	"math"
	"math/big"
	"strings"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
)

// PaymentReads ports payment_list, payment_stats and subscription_payments.
type PaymentReads struct {
	store domain.PaymentReadsStore
	now   func() time.Time
	// stripeEnvironment is STRIPE_KEY and STRIPE_PUBLIC_KEY both set (the
	// environment branch of stripe_direct_credentials_configured()).
	stripeEnvironment bool
}

func NewPaymentReads(store domain.PaymentReadsStore, now func() time.Time, stripeEnvironment bool) *PaymentReads {
	return &PaymentReads{store: store, now: now, stripeEnvironment: stripeEnvironment}
}

// PaymentReadsRequest is the parsed request of one of the views.
type PaymentReadsRequest struct {
	Identity    domain.ResolvedIdentity
	Query       domain.RequestObject
	GroupHeader string
}

func paymentReadsParam(query domain.RequestObject, name string) (string, bool) {
	value, present := query.Lookup(name)
	text, _ := value.(string)
	return text, present
}

func paymentReadsScope(identity domain.ResolvedIdentity) *string {
	if identity.ImpersonationAssociationID == "" {
		return nil
	}
	scope := identity.ImpersonationAssociationID
	return &scope
}

// paymentReadsBoolean is BooleanField.to_python for a lookup value.
func paymentReadsBoolean(raw string) (bool, error) {
	switch raw {
	case "t", "True", "1":
		return true, nil
	case "f", "False", "0":
		return false, nil
	}
	return false, domain.ErrPaymentReadsServer
}

func paymentReadsUUID(raw string) (string, error) {
	id, err := paymentInfoParseUUID(raw)
	if err != nil {
		return "", domain.ErrPaymentReadsServer // UUIDField.to_python's ValidationError
	}
	return id.String(), nil
}

// paymentReadsSubject is PositiveSmallIntegerField's int() lookup value; out
// of range it is EmptyResultSet.
func paymentReadsSubject(filter *domain.PaymentReadsFilter, raw string) error {
	value, ok := dto.PythonIntText(raw)
	if !ok {
		return domain.ErrPaymentReadsServer
	}
	if !value.IsInt64() || value.Int64() < 0 || value.Int64() > 32767 {
		filter.Empty = true
		return nil
	}
	subject := value.Int64()
	filter.Subject = &subject
	return nil
}

func paymentReadsCategories(filter *domain.PaymentReadsFilter, raw string) error {
	if raw == "[]" {
		filter.NoCategory = true
		return nil
	}
	filter.Categories = []string{}
	for _, part := range strings.Split(raw, ",") {
		id, err := paymentReadsUUID(part)
		if err != nil {
			return err
		}
		filter.Categories = append(filter.Categories, id)
	}
	return nil
}

func paymentReadsAccount(filter *domain.PaymentReadsFilter, raw string) error {
	if raw == "[]" {
		filter.NoAccount = true
		return nil
	}
	id, err := paymentReadsUUID(raw)
	if err != nil {
		return err
	}
	filter.Account = &id
	return nil
}

// paymentReadsSearch is the views' general search: a d/m/Y date becomes
// Y-m-d, a float() searches the amount, anything else is smart_search
// (smart) or subscription_payments' icontains list. A NaN makes the
// DecimalField lookup raise ValidationError: payment_list and payment_stats
// do not catch it (500), subscription_payments falls back to the text search.
func (s *PaymentReads) paymentReadsSearch(filter *domain.PaymentReadsFilter, raw string, smart bool) error {
	if date, ok := dto.PythonStrptimeDMY(raw); ok {
		raw = date
	}
	if value, ok := dto.PythonFloatParse(strings.ReplaceAll(raw, ",", ".")); ok {
		switch {
		case math.IsInf(value, 0):
			return domain.ErrPaymentReadsNotPorted
		case math.IsNaN(value):
			if smart {
				return domain.ErrPaymentReadsServer
			}
		default:
			text, exact := dto.PythonFloatStr(value), dto.DecimalFromFloat9(value)
			filter.AmountText, filter.AmountExact = &text, &exact
			return nil
		}
	}
	if !smart {
		filter.MemberText = &raw
		return nil
	}
	search, err := personaSearch(raw, s.now().UTC().Year()%100)
	if err != nil {
		return domain.ErrPaymentReadsNotPorted
	}
	filter.Smart = &domain.PaymentReadsSmartSearch{Words: search.Words, Dates: search.Dates, Permutations: search.Permutations}
	return nil
}

// paymentReadsFields are Payment's concrete field names and attnames by column.
var paymentReadsFields = map[string]string{"payment_id": "payment_id", "pk": "payment_id", "type": "type", "subject": "subject",
	"amount": "amount", "creation_date": "creation_date", "payment_date": "payment_date", "paid": "paid", "expense": "expense",
	"description": "description", "meta_payment_categories": "meta_payment_categories", "payment_intent_id": "payment_intent_id",
	"archived": "archived", "invoice_fiscal_code": "invoice_fiscal_code", "meta": "meta", "notes": "notes", "deleted": "deleted",
	"course": "course", "payment_category_id": "payment_category_id", "invoice_id": "invoice_id", "user_id": "user_id",
	"associate_id": "associate_id", "supplier_id": "supplier_id", "instructor_id": "instructor_id",
	"sport_association_id": "sport_association_id", "custom_accounts_id": "custom_accounts_id", "signature_id": "signature_id",
	"group_id": "group_id"}

// paymentReadsRelations are Payment's relations: order_by() accepts them and
// paths through them, and Django resolves them only when compiling the rows.
var paymentReadsRelations = map[string]bool{"payment_category": true, "invoice": true, "user": true, "associate": true, "supplier": true,
	"instructor": true, "sport_association": true, "custom_accounts": true, "signature": true, "group": true, "attachments": true}

// errPaymentReadsLazyOrder marks an ordering Django resolves when compiling
// the rows (after the count and the page checks); Go refuses it there.
var errPaymentReadsLazyOrder = errors.New("payment ordering not ported")

// paymentReadsOrder is order_by(f"{'-' if desc else ”}{field}"): '?' is
// random; one leading '-' is stripped, then the name must resolve (FieldError,
// 500); relations and transforms are refused where Django compiles them.
func paymentReadsOrder(filter *domain.PaymentReadsFilter, field, direction string, annotations map[string]bool) error {
	if direction == "desc" {
		field = "-" + field
	}
	if field == "?" {
		filter.Random, filter.Order = true, nil
		return nil
	}
	name, descending := strings.CutPrefix(field, "-")
	// A relation whose model has no Meta.ordering orders by its column:
	// User (the list's "Informazioni" column sends sort[field]=user).
	if name == "user" {
		name = "user_id"
	}
	if column, ok := paymentReadsFields[name]; ok {
		filter.Order = []domain.PaymentReadsOrder{{Column: column, Descending: descending}}
		return nil
	}
	first, _, _ := strings.Cut(name, "__")
	if _, ok := paymentReadsFields[first]; ok || paymentReadsRelations[first] || annotations[name] {
		return errPaymentReadsLazyOrder
	}
	return domain.ErrPaymentReadsServer
}

// paymentReadsPage is KTDatatablePagination over count rows.
func paymentReadsPage(count int64, query domain.RequestObject) (number int64, size *big.Int, pages, limit, offset int64, err error) {
	number, size, pages, err = personaPage(count, query)
	if err != nil {
		return
	}
	limit = count
	if size.IsInt64() {
		offset = (number - 1) * size.Int64()
		limit = min(size.Int64(), count-offset)
	}
	return
}

func paymentReadsNumber(value int64) json.Number { return json.Number(big.NewInt(value).String()) }

// association is request.user.sportassociation, else the scoped live lookup (500 without one).
func (s *PaymentReads) userAssociation(ctx context.Context, identity domain.ResolvedIdentity) (string, error) {
	user := identity.ScopeUser.ID
	id, err := s.store.UserAssociation(ctx, user)
	if err != nil {
		return "", err
	}
	if id == nil {
		if id, err = s.store.ScopedAssociation(ctx, user, paymentReadsScope(identity)); err != nil {
			return "", err
		}
	}
	if id == nil {
		return "", domain.ErrPaymentReadsServer
	}
	return *id, nil
}

func (s *PaymentReads) scopedAssociation(ctx context.Context, identity domain.ResolvedIdentity) (string, error) {
	id, err := s.store.ScopedAssociation(ctx, identity.ScopeUser.ID, paymentReadsScope(identity))
	if err != nil {
		return "", err
	}
	if id == nil {
		return "", domain.ErrPaymentReadsServer
	}
	return *id, nil
}

// rows renders a page: PaymentOptimizedSerializer for each id (repeated ids rendered again).
func (s *PaymentReads) rows(ctx context.Context, ids []string, group *string) ([]map[string]any, error) {
	unique := []string{}
	seen := map[string]bool{}
	for _, id := range ids {
		if !seen[id] {
			seen[id] = true
			unique = append(unique, id)
		}
	}
	renders, err := s.store.Render(ctx, unique, group)
	if err != nil {
		return nil, err
	}
	items := make([]map[string]any, 0, len(ids))
	for _, id := range ids {
		render, ok := renders[id]
		if !ok {
			return nil, domain.ErrPaymentReadsServer
		}
		item, err := dto.PaymentReadsItem(render)
		if err != nil {
			return nil, err
		}
		items = append(items, item)
	}
	return items, nil
}

func paymentReadsMeta(count, number int64, size *big.Int, pages int64, ids []string) domain.RequestObject {
	rowIDs := make([]any, len(ids))
	for i, id := range ids {
		rowIDs[i] = id
	}
	return domain.RequestObject{{Name: "total", Value: paymentReadsNumber(count)}, {Name: "page", Value: paymentReadsNumber(number)},
		{Name: "pages", Value: paymentReadsNumber(pages)}, {Name: "perpage", Value: json.Number(size.String())},
		{Name: "rowIds", Value: rowIDs}}
}

// paymentRange parses 'from al to' with '%Y/%m/%d', else '%d/%m/%Y'.
// typeError reports Python's TypeError: the first date parsed with the first
// format and the second did not (the second attempt then receives a datetime).
func paymentRange(raw string) (from, to time.Time, parsed, split, typeError bool) {
	parts := strings.Split(raw, " al ")
	if len(parts) != 2 {
		return time.Time{}, time.Time{}, false, false, false
	}
	if start, ok := dto.PythonStrptimeYMD(parts[0]); ok {
		if end, ok := dto.PythonStrptimeYMD(parts[1]); ok {
			return start, end, true, true, false
		}
		return time.Time{}, time.Time{}, false, true, true
	}
	if start, ok := dto.PythonStrptimeDMYTime(parts[0]); ok {
		if end, ok := dto.PythonStrptimeDMYTime(parts[1]); ok {
			return start, end, true, true, false
		}
	}
	return time.Time{}, time.Time{}, false, true, false
}

// List follows payment_list.
func (s *PaymentReads) List(ctx context.Context, request PaymentReadsRequest) (any, error) {
	query, identity := request.Query, request.Identity
	if raw, _ := paymentReadsParam(query, "course_subscription_id"); raw != "" {
		if _, err := paymentInfoParseUUID(raw); err != nil {
			return nil, err
		}
	}
	group := supplierGroup(request.GroupHeader)
	// archived=request.GET.get('query[archived]', False) goes through the first filter().
	archived := func() (*bool, error) {
		value := false
		if raw, present := paymentReadsParam(query, "query[archived]"); present {
			parsed, err := paymentReadsBoolean(raw)
			if err != nil {
				return nil, err
			}
			value = parsed
		}
		return &value, nil
	}
	if mode, _ := paymentReadsParam(query, "mode"); mode == "compressed" {
		association, err := s.userAssociation(ctx, identity)
		if err != nil {
			return nil, err
		}
		flag, err := archived()
		if err != nil {
			return nil, err
		}
		if identity.ScopeUser.Role == domain.RoleAthlete && paymentReadsScope(identity) != nil {
			// scoped_queryset's athlete boundary on Payment (own and family payments) is not ported.
			return nil, domain.ErrPaymentReadsNotPorted
		}
		filter := domain.PaymentReadsFilter{AssociationID: association, Scope: paymentReadsScope(identity), Group: group, Archived: flag}
		rows, err := s.store.Compressed(ctx, filter)
		if err != nil {
			return nil, err
		}
		data := make([]any, 0, len(rows))
		for _, row := range rows {
			item, err := dto.PaymentReadsCompressedItem(row)
			if err != nil {
				return nil, err
			}
			data = append(data, item)
		}
		return domain.RequestObject{{Name: "data", Value: data}}, nil
	}
	if identity.ScopeUser.Role == domain.RoleAthlete {
		// The athlete branch (exclusions, PaymentCompressedSerializer, online
		// payment flags, invoice PDF generation) is not ported.
		return nil, domain.ErrPaymentReadsNotPorted
	}
	association, err := s.userAssociation(ctx, identity)
	if err != nil {
		return nil, err
	}
	flag, err := archived()
	if err != nil {
		return nil, err
	}
	filter := domain.PaymentReadsFilter{AssociationID: association, Scope: paymentReadsScope(identity), Group: group, Archived: flag}
	if raw, present := paymentReadsParam(query, "associate_id"); present {
		id, err := paymentReadsUUID(raw)
		if err != nil {
			return nil, err
		}
		filter.AssociateID = &id
	}
	search, _ := paymentReadsParam(query, "query[generalSearch]")
	skipRange := false
	if search != "" {
		_, err := paymentInfoParseUUID(search)
		skipRange = err == nil
	}
	if raw, _ := paymentReadsParam(query, "query[payment_range]"); raw != "" && !skipRange {
		from, to, parsed, split, typeError := paymentRange(raw)
		switch {
		case parsed:
			end := to.Add(23*time.Hour + 59*time.Minute + 59*time.Second)
			filter.From, filter.To = &from, &end
		case split && !typeError:
			// Both formats failed: the last year, the end at 23:59:59 (microseconds kept).
			now := s.now().UTC().Truncate(time.Microsecond)
			start := now.Add(-365 * 24 * time.Hour)
			end := time.Date(now.Year(), now.Month(), now.Day(), 23, 59, 59, now.Nanosecond()/1000*1000, time.UTC)
			filter.From, filter.To = &start, &end
		}
		// Otherwise the error is logged and the range ignored.
	}
	if raw, _ := paymentReadsParam(query, "query[subject]"); raw != "" {
		if err := paymentReadsSubject(&filter, raw); err != nil {
			return nil, err
		}
	}
	if raw, _ := paymentReadsParam(query, "query[type]"); raw != "" {
		filter.Type = &raw
	}
	if raw, _ := paymentReadsParam(query, "query[paid]"); raw != "" {
		value := raw == "true"
		filter.Paid = &value
	}
	if raw, _ := paymentReadsParam(query, "query[expense]"); raw != "" {
		value := raw == "true"
		filter.Expense = &value
	}
	if raw, _ := paymentReadsParam(query, "query[payment_categories]"); raw != "" {
		if err := paymentReadsCategories(&filter, raw); err != nil {
			return nil, err
		}
	}
	if raw, _ := paymentReadsParam(query, "query[account]"); raw != "" {
		if err := paymentReadsAccount(&filter, raw); err != nil {
			return nil, err
		}
	}
	filter.Order = []domain.PaymentReadsOrder{{Column: "payment_date", Descending: true}, {Column: "creation_date", Descending: true}}
	if search != "" {
		if err := s.paymentReadsSearch(&filter, search, true); err != nil {
			return nil, err
		}
	}
	var lazy error
	if field, _ := paymentReadsParam(query, "sort[field]"); field != "" {
		direction, _ := paymentReadsParam(query, "sort[sort]")
		if err := paymentReadsOrder(&filter, field, direction, nil); errors.Is(err, errPaymentReadsLazyOrder) {
			lazy = domain.ErrPaymentReadsNotPorted
		} else if err != nil {
			return nil, err
		}
		if filter.Random && filter.Smart != nil {
			// distinct() with RANDOM() in the select list repeats joined rows: not ported.
			lazy = domain.ErrPaymentReadsNotPorted
		}
	}
	return s.page(ctx, filter, query, lazy, func(ids []string) ([]map[string]any, error) {
		items, err := s.rows(ctx, ids, group)
		if err != nil {
			return nil, err
		}
		if err := s.instructorDocuments(ctx, ids, items); err != nil {
			return nil, err
		}
		return items, nil
	})
}

// page counts, checks the page, refuses a lazy ordering, then renders the rows.
func (s *PaymentReads) page(ctx context.Context, filter domain.PaymentReadsFilter, query domain.RequestObject, lazy error,
	render func([]string) ([]map[string]any, error)) (any, error) {
	count, err := s.store.CountPayments(ctx, filter)
	if err != nil {
		return nil, err
	}
	number, size, pages, limit, offset, err := paymentReadsPage(count, query)
	if err != nil {
		return nil, err
	}
	if lazy != nil {
		return nil, lazy
	}
	ids := []string{}
	if limit > 0 {
		if ids, err = s.store.Payments(ctx, filter, limit, offset); err != nil {
			return nil, err
		}
	}
	items, err := render(ids)
	if err != nil {
		return nil, err
	}
	data := make([]any, len(items))
	for i, item := range items {
		data[i] = item
	}
	return domain.RequestObject{{Name: "data", Value: data}, {Name: "meta", Value: paymentReadsMeta(count, number, size, pages, ids)}}, nil
}

// instructorDocuments adds the InstructorHours document of instructor
// payments; one without a document would run print_document_compensation
// (PDF generation, not ported). A later row of the same payment replaces an
// earlier one in Django's map: several rows are refused.
func (s *PaymentReads) instructorDocuments(ctx context.Context, ids []string, items []map[string]any) error {
	instructed := []string{}
	for i, item := range items {
		if item["instructor"] != nil {
			instructed = append(instructed, ids[i])
		}
	}
	hours, err := s.store.Hours(ctx, instructed)
	if err != nil {
		return err
	}
	byPayment := map[string]domain.PaymentReadsHours{}
	for _, row := range hours {
		if _, repeated := byPayment[row.PaymentID]; repeated {
			return domain.ErrPaymentReadsNotPorted
		}
		if row.DocumentID == nil {
			return domain.ErrPaymentReadsNotPorted
		}
		byPayment[row.PaymentID] = row
	}
	for i, item := range items {
		if item["instructor"] == nil {
			continue
		}
		if row, ok := byPayment[ids[i]]; ok {
			item["instructor_document"] = *row.DocumentID
			var token any
			if row.DocumentToken != nil {
				token = *row.DocumentToken
			}
			item["instructor_document_token"] = token
		}
	}
	return nil
}

// Stats follows payment_stats.
func (s *PaymentReads) Stats(ctx context.Context, request PaymentReadsRequest) (any, error) {
	query, identity := request.Query, request.Identity
	association, err := s.scopedAssociation(ctx, identity)
	if err != nil {
		return nil, err
	}
	now := s.now().UTC().Truncate(time.Microsecond)
	from := time.Date(now.Year(), now.Month(), 1, 0, 0, 0, 0, time.UTC)
	to := now
	if raw, present := paymentReadsParam(query, "query[payment_range]"); present {
		start, end, parsed, split, _ := paymentRange(raw)
		switch {
		case !split:
			return nil, domain.ErrPaymentReadsServer // the unpacking ValueError is not caught
		case parsed:
			from, to = start, end
		default:
			from, to = now.Add(-365*24*time.Hour), now.Add(24*time.Hour)
		}
	}
	to = time.Date(to.Year(), to.Month(), to.Day(), 0, 0, 0, 0, time.UTC)
	falseValue := false
	filter := domain.PaymentReadsFilter{AssociationID: association, Scope: paymentReadsScope(identity), Group: supplierGroup(request.GroupHeader),
		Archived: &falseValue, From: &from, To: &to}
	if raw, present := paymentReadsParam(query, "query[subject]"); present {
		if err := paymentReadsSubject(&filter, raw); err != nil {
			return nil, err
		}
	}
	if raw, present := paymentReadsParam(query, "query[type]"); present {
		filter.Type = &raw
	}
	if raw, present := paymentReadsParam(query, "query[paid]"); present {
		value := raw == "true"
		filter.Paid = &value
	}
	if raw, present := paymentReadsParam(query, "query[expense]"); present {
		value := raw == "true"
		filter.Expense = &value
	}
	if raw, _ := paymentReadsParam(query, "query[payment_categories]"); raw != "" {
		if err := paymentReadsCategories(&filter, raw); err != nil {
			return nil, err
		}
	}
	if raw, _ := paymentReadsParam(query, "query[account]"); raw != "" {
		if err := paymentReadsAccount(&filter, raw); err != nil {
			return nil, err
		}
	}
	if search, _ := paymentReadsParam(query, "query[generalSearch]"); search != "" {
		if err := s.paymentReadsSearch(&filter, search, true); err != nil {
			return nil, err
		}
	}
	instance, err := s.store.StripeInstanceSettings(ctx)
	if err != nil {
		return nil, err
	}
	if instance || s.stripeEnvironment {
		// The Stripe balance transactions (and their cache) are not ported.
		return nil, domain.ErrPaymentReadsNotPorted
	}
	totals, err := s.store.Totals(ctx, filter)
	if err != nil {
		return nil, err
	}
	changes, err := s.store.AccountChanges(ctx, filter)
	if err != nil {
		return nil, err
	}
	decimal := func(value *string) (*big.Rat, error) {
		if value == nil {
			return nil, nil
		}
		number, ok := dto.PaymentReadsDecimal(*value)
		if !ok {
			return nil, domain.ErrPaymentReadsServer
		}
		return number, nil
	}
	// aggregate or 0: NULL and a zero Decimal render as the integer 0.
	rendered := func(value *string) (any, *big.Rat, error) {
		number, err := decimal(value)
		if err != nil {
			return nil, nil, err
		}
		if number == nil || number.Sign() == 0 {
			return json.Number("0"), new(big.Rat), nil
		}
		return dto.PaymentReadsFloat(number), number, nil
	}
	values := map[string]*big.Rat{}
	data := domain.RequestObject{{Name: "stripe_charges", Value: domain.RequestObject{{Name: "total", Value: json.Number("0")},
		{Name: "stripe_fee", Value: json.Number("0")}}}}
	for _, field := range []struct {
		name  string
		value *string
	}{{"monthly_income", totals.MonthlyIncome}, {"monthly_expenses", totals.MonthlyExpenses}, {"monthly_to_pay", totals.MonthlyToPay},
		{"monthly_to_cash_in", totals.MonthlyToCashIn}, {"current_income", totals.CurrentIncome}, {"current_expenses", totals.CurrentExpenses}} {
		item, number, err := rendered(field.value)
		if err != nil {
			return nil, err
		}
		values[field.name] = number
		data = append(data, domain.RequestField{Name: field.name, Value: item})
	}
	names := map[int64]string{1: "cash", 2: "bank", 3: "other"}
	accounts := map[string]*big.Rat{}
	for _, change := range changes {
		if change.Type == nil {
			continue
		}
		name, ok := names[*change.Type]
		if !ok {
			continue
		}
		number, err := decimal(change.Net)
		if err != nil {
			return nil, err
		}
		if number == nil {
			number = new(big.Rat)
		}
		if accounts[name] == nil {
			accounts[name] = new(big.Rat)
		}
		accounts[name].Add(accounts[name], number)
	}
	balances := domain.RequestObject{}
	for _, name := range []string{"cash", "bank", "other"} {
		var value any = json.Number("0")
		if accounts[name] != nil {
			value = dto.PaymentReadsFloat(accounts[name])
		}
		balances = append(balances, domain.RequestField{Name: name, Value: value})
	}
	net := new(big.Rat).Sub(values["current_income"], values["current_expenses"])
	net.Add(net, values["monthly_to_pay"])
	net.Sub(net, values["monthly_to_cash_in"])
	data = append(data, domain.RequestField{Name: "accounts", Value: balances},
		domain.RequestField{Name: "total_transactions", Value: paymentReadsNumber(totals.Count)},
		domain.RequestField{Name: "net_balance", Value: dto.PaymentReadsFloat(net)})
	return domain.RequestObject{{Name: "data", Value: data}}, nil
}

// SubscriptionPayments follows subscription_payments.
func (s *PaymentReads) SubscriptionPayments(ctx context.Context, request PaymentReadsRequest, uid string) (any, error) {
	query, identity := request.Query, request.Identity
	if identity.ScopeUser.Role == domain.RoleAthlete {
		return nil, domain.ErrPaymentReadsAthlete
	}
	id, err := paymentInfoParseUUID(uid)
	if err != nil {
		return nil, err
	}
	association, err := s.scopedAssociation(ctx, identity)
	if err != nil {
		return nil, err
	}
	group, scope := supplierGroup(request.GroupHeader), paymentReadsScope(identity)
	subscription, err := s.store.SubscriptionOf(ctx, id.String(), group, scope)
	if err != nil {
		return nil, err
	}
	if subscription == nil {
		return nil, domain.ErrPaymentReadsServer // None.sport_association
	}
	if subscription.AssociationID != association {
		return nil, &domain.PermissionDenied{Detail: "User not allowed."}
	}
	if subscription.AssociateID == nil || !subscription.AssociateExists {
		return nil, domain.ErrPaymentReadsServer // None.tax_code
	}
	filter := domain.PaymentReadsFilter{AssociationID: association, Scope: scope, Group: group, Member: subscription.AssociateID,
		Order: []domain.PaymentReadsOrder{{Column: "creation_date", Descending: true}}}
	if subscription.TaxCode != nil {
		if tax := dto.PythonStripText(*subscription.TaxCode); tax != "" {
			filter.MemberTaxCode = &tax
		}
	}
	if raw, present := paymentReadsParam(query, "query[subject]"); present {
		if err := paymentReadsSubject(&filter, raw); err != nil {
			return nil, err
		}
	}
	if raw, present := paymentReadsParam(query, "query[type]"); present {
		filter.Type = &raw
	}
	if raw, present := paymentReadsParam(query, "query[paid]"); present {
		value := raw == "true"
		filter.Paid = &value
	}
	if raw, present := paymentReadsParam(query, "query[expense]"); present {
		value := raw == "true"
		filter.Expense = &value
	}
	if search, _ := paymentReadsParam(query, "query[generalSearch]"); search != "" {
		if err := s.paymentReadsSearch(&filter, search, false); err != nil {
			return nil, err
		}
	}
	var lazy error
	if field, _ := paymentReadsParam(query, "sort[field]"); field != "" {
		direction, _ := paymentReadsParam(query, "sort[sort]")
		annotations := map[string]bool{"full_name": filter.MemberText != nil}
		if err := paymentReadsOrder(&filter, field, direction, annotations); errors.Is(err, errPaymentReadsLazyOrder) {
			lazy = domain.ErrPaymentReadsNotPorted
		} else if err != nil {
			return nil, err
		}
	}
	return s.page(ctx, filter, query, lazy, func(ids []string) ([]map[string]any, error) {
		items, err := s.rows(ctx, ids, group)
		if err != nil {
			return nil, err
		}
		for i, item := range items {
			item["is_carnet"] = false
			if subject, _ := item["subject"].(int); subject != 2 {
				continue
			}
			link, err := s.store.Link(ctx, ids[i], group, scope)
			if err != nil {
				return nil, err
			}
			if !link.Found {
				continue
			}
			var title any
			if link.Title != nil {
				title = *link.Title
			}
			if link.Carnet {
				item["is_carnet"] = true
				item["carnet"] = map[string]any{"course_id": *link.CourseID, "title": title}
				continue
			}
			item["course_subscriptions"] = map[string]any{"course_id": *link.CourseID, "title": title, "deleted": link.CourseDeleted}
		}
		return items, nil
	})
}
