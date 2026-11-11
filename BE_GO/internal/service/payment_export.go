package service

import (
	"bytes"
	"context"
	"encoding/base64"
	"encoding/json"
	"errors"
	"math"
	"sort"
	"strconv"
	"strings"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/xlsxexport"
)

// PaymentExport ports payment_list_export's CSV export (the petty cash book included).
type PaymentExport struct {
	store domain.PaymentExportStore
	now   func() time.Time
}

func NewPaymentExport(store domain.PaymentExportStore, now func() time.Time) *PaymentExport {
	return &PaymentExport{store: store, now: now}
}

var paymentExportHeader = []string{"Informazioni intestatario", "Stato", "Modalità di pagamento", "Data creazione", "Data pagamento",
	"Numero ricevuta", "Attività", "Causali aggiuntive", "Causale", "Conto", "Tipo", "Corso", "Importo", "Note"}

// Export follows payment_list_export.
func (s *PaymentExport) Export(ctx context.Context, request PaymentReadsRequest) (any, error) {
	query, identity := request.Query, request.Identity
	if identity.ScopeUser.Role == domain.RoleAthlete {
		return nil, domain.ErrPaymentReadsServer // raise Exception("Cannot export payments for athlete")
	}
	mode, _ := paymentReadsParam(query, "m")
	xlsx := mode == "xlsx"
	exportType, _ := paymentReadsParam(query, "export_type")
	petty := exportType == "petty_cash_book"
	scope := paymentReadsScope(identity)
	association, err := s.store.ScopedAssociation(ctx, identity.ScopeUser.ID, scope)
	if err != nil {
		return nil, err
	}
	if association == nil {
		return nil, domain.ErrPaymentReadsServer
	}
	now := s.now().UTC().Truncate(time.Microsecond)
	from, to := now.Add(-3650*24*time.Hour), now
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
	to = to.Add(24 * time.Hour)
	to = time.Date(to.Year(), to.Month(), to.Day(), 0, 0, 0, 0, time.UTC)
	group := supplierGroup(request.GroupHeader)
	archived := false
	if raw, present := paymentReadsParam(query, "archived"); present {
		if archived, err = paymentReadsBoolean(raw); err != nil {
			return nil, err
		}
	}
	filter := domain.PaymentReadsFilter{AssociationID: association.ID, Scope: scope, Group: group, Archived: &archived, From: &from, To: &to}
	if raw, present := paymentReadsParam(query, "query[paid]"); present || petty {
		value := raw == "true" || petty
		filter.Paid = &value
	}
	if raw, present := paymentReadsParam(query, "query[expense]"); present && !petty {
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
	if raw, _ := paymentReadsParam(query, "query[generalSearch]"); raw != "" && !petty {
		if err := s.search(&filter, raw); err != nil {
			return nil, err
		}
	}
	payments, err := s.store.Payments(ctx, filter)
	if err != nil {
		return nil, err
	}
	ids := make([]string, len(payments))
	for i, payment := range payments {
		ids[i] = payment.ID
	}
	courses, err := s.store.Courses(ctx, ids, group, scope)
	if err != nil {
		return nil, err
	}
	if petty {
		transfers, err := s.store.Transfers(ctx, association.ID, from, to)
		if err != nil {
			return nil, err
		}
		for _, transfer := range transfers {
			if transfer.FromName == nil || transfer.ToName == nil {
				return nil, domain.ErrPaymentReadsServer // None.name
			}
			description := "Trasferimento da " + *transfer.FromName + " a " + *transfer.ToName
			for _, side := range []struct {
				expense bool
				id      *string
				name    *string
			}{{true, transfer.FromID, transfer.FromName}, {false, transfer.ToID, transfer.ToName}} {
				date := transfer.Date
				payments = append(payments, domain.PaymentExportRow{ID: transfer.ID, Type: "default", Subject: 3, Amount: transfer.Amount,
					CreationDate: date, PaymentDate: &date, Paid: true, Expense: side.expense, Description: &description,
					AccountID: side.id, AccountName: side.name, Transfer: true})
			}
		}
	}
	// payments.sort(key=payment_date or timezone.now()): stable.
	sort.SliceStable(payments, func(i, j int) bool {
		return paymentExportSortKey(payments[i], now).Before(paymentExportSortKey(payments[j], now))
	})
	progressive := []string{}
	progressiveValues := []xlsxexport.Value{} // the same running values as Python floats
	if petty {
		accounts := []string{}
		seen := map[string]bool{}
		for _, payment := range payments {
			if payment.AccountID != nil && !seen[*payment.AccountID] {
				seen[*payment.AccountID] = true
				accounts = append(accounts, *payment.AccountID)
			}
		}
		texts, err := s.store.Balances(ctx, accounts, from, group)
		if err != nil {
			return nil, err
		}
		balances := map[string]float64{}
		for id, text := range texts {
			value, err := strconv.ParseFloat(text, 64)
			if err != nil {
				return nil, domain.ErrPaymentReadsServer
			}
			balances[id] = value
		}
		for _, payment := range payments {
			if !payment.Paid {
				progressive = append(progressive, "-")
				progressiveValues = append(progressiveValues, xlsxexport.StrValue("-"))
				continue
			}
			if payment.AccountID == nil {
				continue // no account: nothing is appended
			}
			balance, ok := balances[*payment.AccountID]
			if !ok {
				return nil, domain.ErrPaymentReadsServer
			}
			amount, err := strconv.ParseFloat(payment.Amount, 64)
			if err != nil {
				return nil, domain.ErrPaymentReadsServer
			}
			sign := 1.0
			if payment.Expense {
				sign = -1
			}
			balance += amount * sign
			balances[*payment.AccountID] = balance
			progressive = append(progressive, dto.PythonFloatStr(balance))
			progressiveValues = append(progressiveValues, xlsxexport.FloatValue(balance))
		}
	}
	users, activities := make([]*string, len(payments)), []string{}
	for i, payment := range payments {
		users[i] = paymentExportUser(payment)
		switch payment.Subject {
		case 0:
			if payment.HasCategory {
				activities = append(activities, paymentExportText(payment.CategoryName))
			} else {
				activities = append(activities, "Altro")
			}
		case 1:
			activities = append(activities, "Iscrizione")
		case 2:
			activities = append(activities, "Corso")
		case 3:
			activities = append(activities, "Giroconto")
		}
	}
	categories, err := s.store.CategoryNames(ctx, association.ID)
	if err != nil {
		return nil, err
	}
	if xlsx {
		return s.workbook(association, payments, courses, users, activities, categories, petty, progressiveValues, now)
	}
	header := paymentExportHeader
	count := min(len(payments), len(activities))
	if petty {
		header = append(append([]string{}, header...), "Saldo progressivo")
		count = min(count, len(progressive))
	}
	var out bytes.Buffer
	paymentExportWriteRow(&out, header)
	for i := 0; i < count; i++ {
		payment := payments[i]
		meta, err := paymentExportMeta(payment.MetaCategories, categories)
		if err != nil {
			return nil, err
		}
		course := ""
		if found := courses[payment.ID]; found.Found && !payment.Transfer {
			course = paymentExportText(found.Title)
		} else if course, err = paymentExportCourseLabel(payment.Course); err != nil {
			return nil, err
		}
		status, kind, flow := "In Attesa", payment.Type, "Entrata"
		if payment.Paid {
			status = "Pagato"
		}
		if kind == "default" {
			kind = "-"
		}
		if payment.Expense {
			flow = "Uscita"
		}
		date := ""
		if payment.PaymentDate != nil {
			date = payment.PaymentDate.UTC().Format("2006-01-02")
		}
		invoice := ""
		if payment.HasInvoice && payment.InvoiceNumber != nil {
			invoice = strconv.FormatInt(*payment.InvoiceNumber, 10)
		}
		row := []string{paymentExportText(users[i]), status, kind, payment.CreationDate.UTC().Format("2006-01-02"), date, invoice,
			activities[i], meta}
		if petty {
			if payment.AccountID == nil {
				return nil, domain.ErrPaymentReadsServer // None.name
			}
			row = append(row, paymentExportText(payment.AccountName))
		} else {
			category, account := "-", "-"
			if payment.HasCategory {
				category = paymentExportText(payment.CategoryName)
			}
			if payment.AccountID != nil {
				account = paymentExportText(payment.AccountName)
			}
			row = append(row, category, account)
		}
		row = append(row, flow, course, payment.Amount, paymentExportText(payment.Notes))
		if petty {
			row = append(row, progressive[i])
		}
		paymentExportWriteRow(&out, row)
	}
	denomination := "None"
	if association.Denomination != nil {
		denomination = *association.Denomination
	}
	data := domain.RequestObject{{Name: "file", Value: base64.StdEncoding.EncodeToString(out.Bytes())}, {Name: "type", Value: "csv"},
		{Name: "filename", Value: "[" + now.Format("2006-01-02") + "] PAGAMENTI a " + denomination + ".csv"}}
	return domain.RequestObject{{Name: "data", Value: data}}, nil
}

// search is the export's general search: a d/m/Y date becomes Y-m-d; a
// float() filters the amount inside a try (a NaN falls back to smart_search).
func (s *PaymentExport) search(filter *domain.PaymentReadsFilter, raw string) error {
	if date, ok := dto.PythonStrptimeDMY(raw); ok {
		raw = date
	}
	if value, ok := dto.PythonFloatParse(strings.ReplaceAll(raw, ",", ".")); ok && !math.IsNaN(value) && !math.IsInf(value, 0) {
		text, exact := dto.PythonFloatStr(value), dto.DecimalFromFloat9(value)
		filter.AmountText, filter.AmountExact = &text, &exact
		return nil
	}
	search, err := personaSearch(raw, s.now().UTC().Year()%100)
	if err != nil {
		return domain.ErrPaymentReadsNotPorted
	}
	filter.Smart = &domain.PaymentReadsSmartSearch{Words: search.Words, Dates: search.Dates, Permutations: search.Permutations}
	return nil
}

func paymentExportSortKey(payment domain.PaymentExportRow, now time.Time) time.Time {
	if payment.PaymentDate != nil {
		return *payment.PaymentDate
	}
	return now
}

// paymentExportText is csv's rendering of a str or None.
func paymentExportText(value *string) string {
	if value == nil {
		return ""
	}
	return *value
}

// paymentExportUser is '{} {}'.format(first, last) of the associate, else the
// instructor, else the supplier's name, else the description.
func paymentExportUser(payment domain.PaymentExportRow) *string {
	pythonStr := func(value *string) string {
		if value == nil {
			return "None"
		}
		return *value
	}
	var user string
	switch {
	case payment.HasAssociate:
		user = pythonStr(payment.AssociateFirst) + " " + pythonStr(payment.AssociateLast)
	case payment.HasInstructor:
		user = pythonStr(payment.InstructorFirst) + " " + pythonStr(payment.InstructorLast)
	case payment.HasSupplier:
		return payment.SupplierName
	default:
		return payment.Description
	}
	return &user
}

// paymentExportWriteRow is csv.writer(f).writerow: excel dialect, QUOTE_MINIMAL, CRLF.
func paymentExportWriteRow(out *bytes.Buffer, fields []string) {
	for i, field := range fields {
		if i > 0 {
			out.WriteByte(',')
		}
		if strings.ContainsAny(field, ",\"\r\n") || (len(fields) == 1 && field == "") {
			out.WriteByte('"')
			out.WriteString(strings.ReplaceAll(field, `"`, `""`))
			out.WriteByte('"')
			continue
		}
		out.WriteString(field)
	}
	out.WriteString("\r\n")
}

// paymentExportJSON decodes a JSONField value as Python's json.loads (numbers kept as text).
func paymentExportJSON(text *string) (any, error) {
	if text == nil {
		return nil, nil
	}
	decoder := json.NewDecoder(strings.NewReader(*text))
	decoder.UseNumber()
	var value any
	if err := decoder.Decode(&value); err != nil {
		return nil, domain.ErrPaymentReadsServer
	}
	return value, nil
}

// paymentExportTruthy is Python's truth value of a decoded JSON value.
func paymentExportTruthy(value any) bool {
	switch typed := value.(type) {
	case nil:
		return false
	case bool:
		return typed
	case json.Number:
		number, err := typed.Float64()
		return err != nil || number != 0
	case string:
		return typed != ""
	case []any:
		return len(typed) > 0
	case map[string]any:
		return len(typed) > 0
	}
	return true
}

// paymentExportMeta is ','.join(names[mpc['payment_category_id']] for mpc in
// meta if mpc['payment_category_id'] in names) when meta is truthy: anything
// but a list of objects holding the key (a missing key, a str, a number, an
// unhashable id) raises.
func paymentExportMeta(text *string, names map[string]string) (string, error) {
	value, err := paymentExportJSON(text)
	if err != nil || !paymentExportTruthy(value) {
		return "", err
	}
	items, ok := value.([]any)
	if !ok {
		return "", domain.ErrPaymentReadsServer
	}
	parts := []string{}
	for _, item := range items {
		object, ok := item.(map[string]any)
		if !ok {
			return "", domain.ErrPaymentReadsServer
		}
		id, ok := object["payment_category_id"]
		if !ok {
			return "", domain.ErrPaymentReadsServer
		}
		switch typed := id.(type) {
		case []any, map[string]any:
			return "", domain.ErrPaymentReadsServer // unhashable
		case string:
			if name, ok := names[typed]; ok {
				parts = append(parts, name)
			}
		}
	}
	return strings.Join(parts, ","), nil
}

// paymentExportCourseLabel is payment.course['label'] if course is not None
// and course != "" and 'label' in course, else the empty string.
func paymentExportCourseLabel(text *string) (string, error) {
	value, err := paymentExportJSON(text)
	if err != nil {
		return "", err
	}
	switch typed := value.(type) {
	case nil:
		return "", nil
	case string:
		if strings.Contains(typed, "label") {
			return "", domain.ErrPaymentReadsServer // str indices must be integers
		}
		return "", nil
	case []any:
		for _, item := range typed {
			if item == "label" {
				return "", domain.ErrPaymentReadsServer // list indices must be integers
			}
		}
		return "", nil
	case map[string]any:
		label, ok := typed["label"]
		if !ok {
			return "", nil
		}
		switch label.(type) {
		case []any, map[string]any:
			return paymentExportLabelRepr(text)
		}
		return paymentExportPythonStr(label)
	}
	return "", domain.ErrPaymentReadsServer // 'in' on a number or bool
}

// paymentExportPythonStr is csv's str() of a decoded JSON scalar (None is empty).
func paymentExportPythonStr(value any) (string, error) {
	switch typed := value.(type) {
	case nil:
		return "", nil
	case string:
		return typed, nil
	case bool:
		if typed {
			return "True", nil
		}
		return "False", nil
	case json.Number:
		text := typed.String()
		if !strings.ContainsAny(text, ".eE") {
			return text, nil
		}
		number, err := strconv.ParseFloat(text, 64)
		if err != nil || math.IsInf(number, 0) {
			return "", domain.ErrPaymentReadsNotPorted
		}
		return dto.PythonFloatStr(number), nil
	}
	// A list or object label renders as its Python repr: not ported.
	return "", domain.ErrPaymentReadsNotPorted
}

// workbook is the m=xlsx branch: the column lists handed to
// get_excel_base64 (zip truncates them to the shortest), with 'Saldo
// progressivo' for the petty cash book.
func (s *PaymentExport) workbook(association *domain.PaymentExportAssociation, payments []domain.PaymentExportRow,
	courses map[string]domain.PaymentExportCourse, users []*string, activities []string, categories map[string]string, petty bool,
	progressive []xlsxexport.Value, now time.Time) (any, error) {
	columns := make([][]xlsxexport.Value, 14)
	for i, payment := range payments {
		status, kind, flow := "In Attesa", payment.Type, "Entrata"
		if payment.Paid {
			status = "Pagato"
		}
		if kind == "default" {
			kind = "-"
		}
		if payment.Expense {
			flow = "Uscita"
		}
		date := ""
		if payment.PaymentDate != nil {
			date = payment.PaymentDate.UTC().Format("2006-01-02")
		}
		invoice := xlsxexport.StrValue("")
		if payment.HasInvoice {
			invoice = xlsxexport.NoneValue()
			if payment.InvoiceNumber != nil {
				invoice = xlsxexport.IntValue(*payment.InvoiceNumber)
			}
		}
		meta, err := paymentExportMetaXlsx(payment.MetaCategories, categories)
		if err != nil {
			return nil, err
		}
		category, account := xlsxexport.StrValue("-"), xlsxexport.StrValue("-")
		if payment.HasCategory {
			category = xlsxexport.OptionalStr(payment.CategoryName)
		}
		if payment.AccountID != nil {
			account = xlsxexport.OptionalStr(payment.AccountName)
		}
		var course xlsxexport.Value
		if found := courses[payment.ID]; found.Found && !payment.Transfer {
			course = xlsxexport.OptionalStr(found.Title)
		} else if course, err = paymentExportCourseValue(payment.Course); err != nil {
			return nil, err
		}
		values := []xlsxexport.Value{xlsxexport.OptionalStr(users[i]), xlsxexport.StrValue(status), xlsxexport.StrValue(kind),
			xlsxexport.StrValue(payment.CreationDate.UTC().Format("2006-01-02")), xlsxexport.StrValue(date), invoice, {},
			xlsxexport.StrValue(meta), category, account, xlsxexport.StrValue(flow), course, xlsxexport.DecimalValue(payment.Amount),
			xlsxexport.OptionalStr(payment.Notes)}
		for c, value := range values {
			if c != 6 {
				columns[c] = append(columns[c], value)
			}
		}
	}
	for _, activity := range activities {
		columns[6] = append(columns[6], xlsxexport.StrValue(activity))
	}
	if columns[6] == nil {
		columns[6] = []xlsxexport.Value{}
	}
	for c := range columns {
		if columns[c] == nil {
			columns[c] = []xlsxexport.Value{}
		}
	}
	header := paymentExportHeader
	if petty {
		header = append(append([]string{}, header...), "Saldo progressivo")
		columns = append(columns, progressive)
	}
	workbook, err := xlsxexport.PandasOpenpyxl(columns, header, s.now())
	switch {
	case errors.Is(err, xlsxexport.ErrNotPorted):
		return nil, domain.ErrPaymentReadsNotPorted
	case err != nil:
		return nil, domain.ErrPaymentReadsServer // get_excel_base64 returned None: raise Exception
	}
	denomination := "None"
	if association.Denomination != nil {
		denomination = *association.Denomination
	}
	data := domain.RequestObject{{Name: "file", Value: base64.StdEncoding.EncodeToString(workbook)}, {Name: "type", Value: "xlsx"},
		{Name: "filename", Value: "[" + now.Format("2006-01-02") + "] PAGAMENTI a " + denomination + ".xlsx"}}
	return domain.RequestObject{{Name: "data", Value: data}}, nil
}

// paymentExportMetaXlsx is the xlsx branch's ','.join(names[mpc[k]] for mpc
// in meta if k in mpc and mpc[k] in names) when meta is truthy and
// len(meta) > 0 (k is 'payment_category_id'): a str meta iterates its
// characters and a dict its keys; 'in' on a number, bool or None raises, as
// does indexing a str or list that contains k, or an unhashable mpc[k].
func paymentExportMetaXlsx(text *string, names map[string]string) (string, error) {
	const key = "payment_category_id"
	value, err := paymentExportJSON(text)
	if err != nil || !paymentExportTruthy(value) {
		return "", err
	}
	var items []any
	switch typed := value.(type) {
	case []any:
		items = typed
	case string:
		return "", nil // single characters never contain the key
	case map[string]any:
		for name := range typed {
			if strings.Contains(name, key) {
				return "", domain.ErrPaymentReadsServer // str indices must be integers
			}
		}
		return "", nil
	default:
		return "", domain.ErrPaymentReadsServer // len() of a number or bool
	}
	parts := []string{}
	for _, item := range items {
		switch typed := item.(type) {
		case map[string]any:
			id, ok := typed[key]
			if !ok {
				continue
			}
			switch idValue := id.(type) {
			case []any, map[string]any:
				return "", domain.ErrPaymentReadsServer // unhashable
			case string:
				if name, ok := names[idValue]; ok {
					parts = append(parts, name)
				}
			}
		case string:
			if strings.Contains(typed, key) {
				return "", domain.ErrPaymentReadsServer
			}
		case []any:
			for _, element := range typed {
				if element == key {
					return "", domain.ErrPaymentReadsServer
				}
			}
		default:
			return "", domain.ErrPaymentReadsServer // 'in' on a number, bool or None
		}
	}
	return strings.Join(parts, ","), nil
}

// paymentExportCourseValue is the Python value payment.course['label'] if
// course is not None and course != ” and 'label' in course, else ”.
func paymentExportCourseValue(text *string) (xlsxexport.Value, error) {
	value, err := paymentExportJSON(text)
	if err != nil {
		return xlsxexport.Value{}, err
	}
	switch typed := value.(type) {
	case nil:
		return xlsxexport.StrValue(""), nil
	case string:
		if strings.Contains(typed, "label") {
			return xlsxexport.Value{}, domain.ErrPaymentReadsServer
		}
		return xlsxexport.StrValue(""), nil
	case []any:
		for _, item := range typed {
			if item == "label" {
				return xlsxexport.Value{}, domain.ErrPaymentReadsServer
			}
		}
		return xlsxexport.StrValue(""), nil
	case map[string]any:
		label, ok := typed["label"]
		if !ok {
			return xlsxexport.StrValue(""), nil
		}
		switch item := label.(type) {
		case nil:
			return xlsxexport.NoneValue(), nil
		case string:
			return xlsxexport.StrValue(item), nil
		case bool:
			return xlsxexport.BoolValue(item), nil
		case []any, map[string]any:
			repr, err := paymentExportLabelRepr(text)
			if err != nil {
				return xlsxexport.Value{}, err
			}
			return xlsxexport.ObjectValue(repr), nil
		case json.Number:
			if number, err := strconv.ParseInt(item.String(), 10, 64); err == nil {
				return xlsxexport.IntValue(number), nil
			}
			if !strings.ContainsAny(item.String(), ".eE") {
				return xlsxexport.BigIntValue(item.String()), nil
			}
			// float() of a huge exponent is inf, of a tiny one 0.0.
			number, err := strconv.ParseFloat(item.String(), 64)
			if err != nil && !errors.Is(err, strconv.ErrRange) {
				return xlsxexport.Value{}, domain.ErrPaymentReadsNotPorted
			}
			return xlsxexport.FloatValue(number), nil
		}
		return xlsxexport.Value{}, domain.ErrPaymentReadsNotPorted
	}
	return xlsxexport.Value{}, domain.ErrPaymentReadsServer
}

// paymentExportOrderedValue decodes JSON keeping the key order (jsonb text order).
func paymentExportOrderedValue(decoder *json.Decoder) (any, error) {
	token, err := decoder.Token()
	if err != nil {
		return nil, err
	}
	delim, ok := token.(json.Delim)
	if !ok {
		return token, nil
	}
	if delim == '[' {
		items := []any{}
		for decoder.More() {
			item, err := paymentExportOrderedValue(decoder)
			if err != nil {
				return nil, err
			}
			items = append(items, item)
		}
		_, err = decoder.Token()
		return items, err
	}
	object := domain.RequestObject{}
	for decoder.More() {
		key, err := decoder.Token()
		if err != nil {
			return nil, err
		}
		item, err := paymentExportOrderedValue(decoder)
		if err != nil {
			return nil, err
		}
		object = dto.PythonSet(object, key.(string), item)
	}
	_, err = decoder.Token()
	return object, err
}

// paymentExportLabelRepr is str(course['label']) of a list or object label.
func paymentExportLabelRepr(text *string) (string, error) {
	decoder := json.NewDecoder(strings.NewReader(*text))
	decoder.UseNumber()
	whole, err := paymentExportOrderedValue(decoder)
	object, ok := whole.(domain.RequestObject)
	if err != nil || !ok {
		return "", domain.ErrPaymentReadsServer
	}
	label, _ := object.Lookup("label")
	return dto.PythonRepr(label), nil
}
