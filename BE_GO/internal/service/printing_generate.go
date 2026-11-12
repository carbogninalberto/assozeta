package service

import (
	"context"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"math/big"
	"sort"
	"strconv"
	"strings"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/pdfexport"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/xlsxexport"
)

// PrintingGenerate ports printing_generate (printing_views.py) and its
// eleven report builders (application/utils/printing.py) for the 'excel'
// format. The 'pdf' format renders in-process with reportlab and is refused
// before any write.
type PrintingGenerate struct {
	store domain.PrintingGenerateStore
	now   func() time.Time
}

func NewPrintingGenerate(store domain.PrintingGenerateStore, now func() time.Time) *PrintingGenerate {
	return &PrintingGenerate{store: store, now: now}
}

// PrintingGenerateRequest is one printing/generate request after identity.
type PrintingGenerateRequest struct {
	Identity    domain.ResolvedIdentity
	Body        any
	GroupHeader string
	Audit       domain.LoginAuditContext
}

// PrintingGenerateResponse is the view's Response: Empty for
// Response(status=403), otherwise Body rendered as JSON.
type PrintingGenerateResponse struct {
	Status int
	Body   any
	Empty  bool
}

// errPrintingFailure is any exception inside the view's try block: the
// builders re-raise everything as Exception('Error in parsing data').
var errPrintingFailure = errors.New("printing generate: builder exception")

func printingMessage(status int, text string) PrintingGenerateResponse {
	return PrintingGenerateResponse{Status: status, Body: domain.RequestObject{{Name: "msg", Value: text}}}
}

// Generate follows printing_generate.
func (s *PrintingGenerate) Generate(ctx context.Context, request PrintingGenerateRequest) (PrintingGenerateResponse, error) {
	data, ok := request.Body.(domain.RequestObject)
	if !ok {
		return PrintingGenerateResponse{}, domain.ErrPrintingGenerateServer // request.data.get: AttributeError
	}
	user := request.Identity.ScopeUser
	if user.Role == domain.RoleAthlete {
		return PrintingGenerateResponse{Status: 403, Empty: true}, nil
	}
	_, hasType := data.Lookup("type")
	_, hasFormat := data.Lookup("format")
	if !hasType || !hasFormat {
		missing := []string{}
		if !hasType {
			missing = append(missing, "type")
		}
		if !hasFormat {
			missing = append(missing, "format")
		}
		return printingMessage(400, "Mancano alcuni parametri obbligatori:"+strings.Join(missing, ", ")), nil
	}
	tx, err := s.store.BeginPrintingGenerate(ctx)
	if err != nil {
		return PrintingGenerateResponse{}, err
	}
	defer tx.Rollback()
	owner := user.ID
	if user.Role == domain.RoleCollaborator && user.ConnectedUserID != nil {
		owner = *user.ConnectedUserID
	}
	association, err := tx.Association(ctx, owner)
	if errors.Is(err, domain.ErrAssociationNotFound) {
		return printingMessage(400, "Errore nella generazione della stampa."), nil
	}
	if err != nil {
		return PrintingGenerateResponse{}, err
	}
	kind, _ := printingLast(data, "type").(string)
	builder, known := printingBuilders[kind]
	if !known {
		return printingMessage(400, "Tipo stampa non valido"), nil
	}
	now := s.now().UTC()
	run := &printingRun{ctx: ctx, tx: tx, data: data, association: association, group: supplierGroup(request.GroupHeader), now: now,
		today: time.Date(now.Year(), now.Month(), now.Day(), 0, 0, 0, 0, time.UTC), relations: map[string][]*domain.PrintingRelation{}}
	report, err := builder(run)
	if err == nil {
		report, err = run.finish(report)
	}
	if errors.Is(err, domain.ErrPrintingGenerateNotPorted) {
		return PrintingGenerateResponse{}, err // refused before the promotions are saved
	}
	if err != nil && !errors.Is(err, errPrintingFailure) {
		return PrintingGenerateResponse{}, err
	}
	// get_main_tutor's saves were autocommitted as the rows were built: they
	// stay when a later step raised.
	for _, promotion := range run.promotions {
		if err := s.promote(ctx, tx, request, promotion); err != nil {
			return PrintingGenerateResponse{}, err
		}
	}
	if err := tx.Commit(); err != nil {
		return PrintingGenerateResponse{}, err
	}
	if errors.Is(err, errPrintingFailure) || report == nil {
		return printingMessage(400, "Errore nella generazione della stampa."), nil
	}
	return PrintingGenerateResponse{Status: 200, Body: domain.RequestObject{{Name: "file", Value: report.file},
		{Name: "filename", Value: report.filename}}}, nil
}

func (s *PrintingGenerate) promote(ctx context.Context, tx domain.PrintingGenerateTransaction, request PrintingGenerateRequest,
	promotion printingPromotion) error {
	identity := request.Identity
	entry := domain.PrintingPromotion{RelationID: promotion.relation, Context: paymentCategoryAuditContext(identity, request.Audit),
		At: s.now().UTC().Truncate(time.Microsecond)}
	path := "associate.sport_association"
	association := promotion.association
	if association == nil {
		var err error
		if association, err = tx.ActiveAssociationOf(ctx, identity.Actor.ID); err != nil {
			return err
		}
		path = "actor.sport_association"
	}
	if association != nil {
		entry.AssociationID, entry.Path = association, &path
	}
	return tx.PromoteRelation(ctx, entry)
}

// printingLast is data[key] (the last value of a form field).
func printingLast(data domain.RequestObject, key string) any {
	value, _ := dto.PythonIndex(data, key)
	return value
}

type printingPromotion struct {
	relation    int64
	association *string
}

type printingRun struct {
	ctx         context.Context
	tx          domain.PrintingGenerateTransaction
	data        domain.RequestObject
	association *domain.PrintingAssociation
	group       *string
	now, today  time.Time
	relations   map[string][]*domain.PrintingRelation
	promotions  []printingPromotion
}

// printingReport is a builder's output before the format branch.
type printingReport struct {
	title    string
	columns  []string
	data     [][]xlsxexport.Value
	file     string
	filename string
}

type printingBuilder func(*printingRun) (*printingReport, error)

var printingBuilders = map[string]printingBuilder{
	"expiring_medical_certificates":   printingExpiringMedical,
	"expired_medical_certificates":    printingExpiredMedical,
	"empty_medical_certificates":      printingEmptyMedical,
	"exempt_medical_certificates":     printingExemptMedical,
	"expiring_subscriptions":          printingExpiringSubscriptions,
	"expiring_memberships":            printingExpiringMemberships,
	"all_subscriptions":               printingAllSubscriptions,
	"not_paid_quotes_subscriptions":   printingNotPaidQuotes,
	"not_paid_courses_subscriptions":  printingNotPaidCourses,
	"expired_payments":                printingExpiredPayments,
	"subscriptions_with_all_payments": printingWithAllPayments,
}

// finish is the builders' format branch: 'excel' is
// generate_xlsx_from_data (get_excel_base64; None raises), 'pdf' is
// generate_pdf_from_data over the same columns turned into rows (an exception
// of reportlab is the builder's failure), anything else raises.
func (r *printingRun) finish(report *printingReport) (*printingReport, error) {
	format, _ := printingLast(r.data, "format").(string)
	denomination := "None"
	if r.association.Denomination != nil {
		denomination = *r.association.Denomination
	}
	switch format {
	case "excel":
		workbook, err := xlsxexport.PandasOpenpyxl(report.data, report.columns, r.now)
		switch {
		case errors.Is(err, xlsxexport.ErrNotPorted):
			return nil, domain.ErrPrintingGenerateNotPorted
		case err != nil:
			return nil, errPrintingFailure
		}
		report.file = base64.StdEncoding.EncodeToString(workbook)
		report.filename = fmt.Sprintf("[%s] %s - %s.xlsx", printingDate(r.today), report.title, denomination)
		return report, nil
	case "pdf":
		document, err := pdfexport.DataTable{Columns: report.columns, Rows: pdfexport.RowsOf(report.data),
			Title: printingPDFTitle(report.title, r.today)}.Build(r.now)
		switch {
		case errors.Is(err, pdfexport.ErrNotPorted):
			return nil, domain.ErrPrintingGenerateNotPorted
		case err != nil:
			return nil, errPrintingFailure
		}
		report.file = base64.StdEncoding.EncodeToString(document)
		report.filename = fmt.Sprintf("[%s] %s - %s.pdf", printingDate(r.today), report.title, denomination)
		return report, nil
	}
	return nil, errPrintingFailure // raise Exception('Format not valid')
}

// printingPDFTitle is the document title of a report's 'pdf' branch (the
// filename keeps the report's own title).
func printingPDFTitle(title string, today time.Time) string {
	day := printingDate(today)
	switch title {
	case "Certificati medici in scadenza (entro 30 giorni)":
		return "Certificati medici in scadenza (entro 30 giorni dal " + day + ")"
	case "Certificati medici non presenti (esclusi soggetti esenti)":
		return "Certificati medici non presenti esclusi soggetti esenti (giorno stampa " + day + ")"
	case "Iscrizioni in scadenza (entro 30gg)":
		return "Iscrizioni in scadenza entro 30gg (giorno stampa " + day + ")"
	case "Tesseramenti in scadenza (entro 30gg o scaduti)":
		return "Tesseramenti in scadenza entro 30gg o scaduti (giorno stampa " + day + ")"
	case "Iscrizioni":
		return title
	case "Quote/Carnet corso non pagate":
		return "Quote corso/carnet non pagate (giorno stampa " + day + ")"
	case "Iscrizioni con tutti i pagamenti":
		return "Iscrizioni con tutti i pagamenti (stampa " + day + ")"
	}
	// Certificati medici scaduti, Atleti esenti dai certificati medici,
	// Quote associative non pagate, Pagamenti scaduti.
	return title + " (giorno stampa " + day + ")"
}

// --- Python semantics of the request values and the rows -----------------------------------------------------

func printingDate(value time.Time) string {
	return fmt.Sprintf("%02d/%02d/%04d", value.Day(), int(value.Month()), value.Year())
}

func printingDay(value time.Time) int64 {
	return time.Date(value.Year(), value.Month(), value.Day(), 0, 0, 0, 0, time.UTC).Unix() / 86400
}

// printingDays is (a - b).days of two dates.
func printingDays(a, b time.Time) int64 { return printingDay(a) - printingDay(b) }

// age is Associate.calculate_age: int((today - born_date).days / 365.25), 0 without a date.
func (r *printingRun) age(person *domain.PrintingPerson) int64 {
	if person.BornDate == nil {
		return 0
	}
	return int64(float64(printingDays(r.today, *person.BornDate)) / 365.25)
}

func printingOr(value *string, fallback string) string {
	if value == nil || *value == "" {
		return fallback
	}
	return *value
}

// filters is data['filters'].
func (r *printingRun) filters() (any, error) {
	value, err := dto.PythonIndex(r.data, "filters")
	if err != nil {
		return nil, errPrintingFailure
	}
	return value, nil
}

func printingIn(container any, key string) (bool, error) {
	found, err := dto.PythonContains(container, key)
	if err != nil {
		return false, errPrintingFailure
	}
	return found, nil
}

func printingIndex(container any, key string) (any, error) {
	value, err := dto.PythonIndex(container, key)
	if err != nil {
		return nil, errPrintingFailure
	}
	return value, nil
}

// subscriptionFilter is filter_subscriptions' queryset: the 'year' and
// 'status' filters with Python's `in` and indexing, and status_flag__in's
// IntegerField.get_prep_value (int()) per value, None dropped.
func (r *printingRun) subscriptionFilter(medical, onlyMembers bool) (domain.PrintingSubscriptionFilter, bool, error) {
	filter := domain.PrintingSubscriptionFilter{Association: r.association.ID, Group: r.group, OnlyMembers: onlyMembers, Medical: medical,
		Today: r.today}
	filters, err := r.filters()
	if err != nil {
		return filter, false, err
	}
	if found, err := printingIn(filters, "year"); err != nil {
		return filter, false, err
	} else if found {
		year, err := printingIndex(filters, "year")
		if err != nil {
			return filter, false, err
		}
		if text, ok := year.(string); ok && (text == "pre-iscrizioni" || text == "corrente" || text == "precedenti") {
			filter.Year = text
		}
	}
	found, err := printingIn(filters, "status")
	if err != nil {
		return filter, false, err
	}
	if !found {
		return filter, true, nil
	}
	value, err := printingIndex(filters, "status")
	if err != nil {
		return filter, false, err
	}
	items, iterable := dto.PythonIterate(value)
	if !iterable {
		return filter, false, errPrintingFailure
	}
	filter.StatusFilter = true
	present := false
	for _, item := range items {
		if item == nil {
			continue
		}
		number, result := dto.PythonInt(item)
		if result != dto.PythonIntOK {
			return filter, false, errPrintingFailure
		}
		present = true
		if number.IsInt64() {
			filter.Statuses = append(filter.Statuses, number.Int64())
		}
	}
	// No value left after dropping None is EmptyResultSet: no query, no rows.
	return filter, present, nil
}

func (r *printingRun) subscriptions(medical, onlyMembers bool) ([]domain.PrintingSubscription, domain.PrintingSubscriptionFilter, error) {
	filter, query, err := r.subscriptionFilter(medical, onlyMembers)
	if err != nil || !query {
		return nil, filter, err
	}
	rows, err := r.tx.Subscriptions(r.ctx, filter)
	return rows, filter, err
}

// mainTutor is Associate.get_main_tutor: the primary relation's tutor, else
// the first relation's, which is promoted (saved) on the way.
func (r *printingRun) mainTutor(person *domain.PrintingPerson) (*domain.PrintingPerson, error) {
	id := *person.ID
	relations, loaded := r.relations[id]
	if !loaded {
		rows, err := r.tx.Relations(r.ctx, []string{id})
		if err != nil {
			return nil, err
		}
		for i := range rows {
			relations = append(relations, &rows[i])
		}
		r.relations[id] = relations
	}
	for _, relation := range relations {
		if relation.Primary {
			return &relation.PrintingPerson, nil
		}
	}
	if len(relations) == 0 {
		return nil, nil
	}
	relations[0].Primary = true
	r.promotions = append(r.promotions, printingPromotion{relation: relations[0].ID, association: person.Association})
	return &relations[0].PrintingPerson, nil
}

// phone is _get_phone_columns: (phone, tutor phone, contact type).
func (r *printingRun) phone(person *domain.PrintingPerson) ([3]string, error) {
	if person.ID == nil {
		return [3]string{}, errPrintingFailure // None.phone: AttributeError
	}
	out := [3]string{printingOr(person.Phone, "-"), "-", "Socio"}
	if r.age(person) < 18 {
		tutor, err := r.mainTutor(person)
		if err != nil {
			return out, err
		}
		if tutor != nil && tutor.Phone != nil && *tutor.Phone != "" {
			out[1], out[2] = *tutor.Phone, "Tutore"
		}
	}
	return out, nil
}

func (r *printingRun) phones(people []*domain.PrintingPerson) ([][3]string, error) {
	out := make([][3]string, len(people))
	for i, person := range people {
		phone, err := r.phone(person)
		if err != nil {
			return nil, err
		}
		out[i] = phone
	}
	return out, nil
}

func printingColumn(n int) []xlsxexport.Value { return make([]xlsxexport.Value, 0, n) }

// personColumns are Nome, Cognome, Email, Telefono, Tel. Tutore, Contatto.
func printingPersonColumns(people []*domain.PrintingPerson, phones [][3]string) [][]xlsxexport.Value {
	out := make([][]xlsxexport.Value, 6)
	for i := range out {
		out[i] = printingColumn(len(people))
	}
	for i, person := range people {
		out[0] = append(out[0], xlsxexport.OptionalStr(person.FirstName))
		out[1] = append(out[1], xlsxexport.OptionalStr(person.LastName))
		out[2] = append(out[2], xlsxexport.StrValue(printingOr(person.Email, "-")))
		for c := 0; c < 3; c++ {
			out[3+c] = append(out[3+c], xlsxexport.StrValue(phones[i][c]))
		}
	}
	return out
}

// bornColumns are born_date.strftime('%d/%m/%Y') (None raises) and calculate_age().
func (r *printingRun) bornColumns(people []*domain.PrintingPerson) ([][]xlsxexport.Value, error) {
	born, ages := printingColumn(len(people)), printingColumn(len(people))
	for _, person := range people {
		if person.BornDate == nil {
			return nil, errPrintingFailure
		}
		born = append(born, xlsxexport.StrValue(printingDate(*person.BornDate)))
	}
	for _, person := range people {
		ages = append(ages, xlsxexport.IntValue(r.age(person)))
	}
	return [][]xlsxexport.Value{born, ages}, nil
}

var printingMedicalColumns = []string{"Nome", "Cognome", "Email", "Telefono", "Tel. Tutore", "Contatto", "Data di nascita", "età",
	"Scadenza certificato medico", "Giorni rimanenti"}

func printingPeople(subscriptions []domain.PrintingSubscription) []*domain.PrintingPerson {
	people := make([]*domain.PrintingPerson, len(subscriptions))
	for i := range subscriptions {
		people[i] = &subscriptions[i].PrintingPerson
	}
	return people
}

// personReport is the common report body: the person columns, birth date and age.
func (r *printingRun) personReport(people []*domain.PrintingPerson) ([][]xlsxexport.Value, error) {
	phones, err := r.phones(people)
	if err != nil {
		return nil, err
	}
	for _, person := range people {
		if person.ID == nil {
			return nil, errPrintingFailure
		}
	}
	born, err := r.bornColumns(people)
	if err != nil {
		return nil, err
	}
	return append(printingPersonColumns(people, phones), born...), nil
}

func (r *printingRun) medicalReport(title string, keep func(int64) bool) (*printingReport, error) {
	rows, _, err := r.subscriptions(true, true)
	if err != nil {
		return nil, err
	}
	kept := []domain.PrintingSubscription{}
	for _, row := range rows {
		if row.HasMedical && row.MedicalExpiration != nil && keep(printingDays(*row.MedicalExpiration, r.today)) {
			kept = append(kept, row)
		}
	}
	sort.SliceStable(kept, func(i, j int) bool {
		return printingDays(*kept[i].MedicalExpiration, r.today) < printingDays(*kept[j].MedicalExpiration, r.today)
	})
	data, err := r.personReport(printingPeople(kept))
	if err != nil {
		return nil, err
	}
	expiration, days := printingColumn(len(kept)), printingColumn(len(kept))
	for _, row := range kept {
		expiration = append(expiration, xlsxexport.StrValue(printingDate(*row.MedicalExpiration)))
		days = append(days, xlsxexport.IntValue(printingDays(*row.MedicalExpiration, r.today)))
	}
	return &printingReport{title: title, columns: printingMedicalColumns, data: append(data, expiration, days)}, nil
}

func printingExpiringMedical(r *printingRun) (*printingReport, error) {
	return r.medicalReport("Certificati medici in scadenza (entro 30 giorni)", func(days int64) bool { return days <= 30 && days >= 0 })
}

func printingExpiredMedical(r *printingRun) (*printingReport, error) {
	return r.medicalReport("Certificati medici scaduti", func(days int64) bool { return days < 0 })
}

func (r *printingRun) withoutMedical(title string, keep func(int64) bool, columns []string) (*printingReport, error) {
	rows, _, err := r.subscriptions(false, true)
	if err != nil {
		return nil, err
	}
	kept := []domain.PrintingSubscription{}
	for _, row := range rows {
		if row.HasMedical {
			continue
		}
		if row.PrintingPerson.ID == nil {
			return nil, errPrintingFailure // subscription.associate.calculate_age(): AttributeError
		}
		if keep(r.age(&row.PrintingPerson)) {
			kept = append(kept, row)
		}
	}
	data, err := r.personReport(printingPeople(kept))
	if err != nil {
		return nil, err
	}
	if len(columns) == len(printingMedicalColumns) {
		// s.medical is None for every kept row: '-' twice.
		dashes := printingColumn(len(kept))
		for range kept {
			dashes = append(dashes, xlsxexport.StrValue("-"))
		}
		data = append(data, dashes, append([]xlsxexport.Value(nil), dashes...))
	}
	return &printingReport{title: title, columns: columns, data: data}, nil
}

func printingEmptyMedical(r *printingRun) (*printingReport, error) {
	return r.withoutMedical("Certificati medici non presenti (esclusi soggetti esenti)", func(age int64) bool { return age >= 6 },
		printingMedicalColumns[:8])
}

func printingExemptMedical(r *printingRun) (*printingReport, error) {
	return r.withoutMedical("Atleti esenti dai certificati medici", func(age int64) bool { return age <= 6 }, printingMedicalColumns)
}

func printingExpiringSubscriptions(r *printingRun) (*printingReport, error) {
	rows, _, err := r.subscriptions(false, false)
	if err != nil {
		return nil, err
	}
	kept := []domain.PrintingSubscription{}
	for _, row := range rows {
		if row.End == nil {
			continue
		}
		if days := printingDays(*row.End, r.today); days <= 30 && days >= 0 {
			kept = append(kept, row)
		}
	}
	data, err := r.personReport(printingPeople(kept))
	if err != nil {
		return nil, err
	}
	days := printingColumn(len(kept))
	for _, row := range kept {
		days = append(days, xlsxexport.IntValue(printingDays(*row.End, r.today)))
	}
	columns := append(append([]string{}, printingMedicalColumns[:8]...), "Giorni rimanenti")
	return &printingReport{title: "Iscrizioni in scadenza (entro 30gg)", columns: columns, data: append(data, days)}, nil
}

func printingExpiringMemberships(r *printingRun) (*printingReport, error) {
	filter, query, err := r.subscriptionFilter(false, false)
	if err != nil {
		return nil, err
	}
	members := []domain.PrintingMembership{}
	if query {
		if members, err = r.tx.Memberships(r.ctx, filter, r.today.AddDate(0, 0, 30)); err != nil {
			return nil, err
		}
	}
	people := make([]*domain.PrintingPerson, len(members))
	kinds, days := printingColumn(len(members)), printingColumn(len(members))
	for i := range members {
		people[i] = &members[i].PrintingPerson
		kinds = append(kinds, xlsxexport.OptionalStr(members[i].Type))
	}
	data, err := r.personReport(people)
	if err != nil {
		return nil, err
	}
	for _, member := range members {
		days = append(days, xlsxexport.IntValue(printingDays(*member.End, r.today)))
	}
	columns := append(append([]string{"Tesseramento"}, printingMedicalColumns[:8]...), "Giorni rimanenti")
	return &printingReport{title: "Tesseramenti in scadenza (entro 30gg o scaduti)", columns: columns,
		data: append(append([][]xlsxexport.Value{kinds}, data...), days)}, nil
}

func printingAllSubscriptions(r *printingRun) (*printingReport, error) {
	rows, _, err := r.subscriptions(false, false)
	if err != nil {
		return nil, err
	}
	people := printingPeople(rows)
	data, err := r.personReport(people)
	if err != nil {
		return nil, err
	}
	first, last := printingColumn(len(people)), printingColumn(len(people))
	tutors := make([]*domain.PrintingPerson, len(people))
	for i, person := range people {
		if tutors[i], err = r.mainTutor(person); err != nil {
			return nil, err
		}
	}
	for _, tutor := range tutors {
		if tutor == nil {
			first, last = append(first, xlsxexport.StrValue("-")), append(last, xlsxexport.StrValue("-"))
		} else {
			first, last = append(first, xlsxexport.OptionalStr(tutor.FirstName)), append(last, xlsxexport.OptionalStr(tutor.LastName))
		}
	}
	columns := append(append([]string{}, printingMedicalColumns[:8]...), "Nome tutore", "Cognome tutore")
	return &printingReport{title: "Iscrizioni", columns: columns, data: append(data, first, last)}, nil
}

// period is data['filters']['period'] parsed with strptime('%d/%m/%Y'):
// (from, to) as UTC midnights, or ok=false without a period.
func (r *printingRun) period() (from, to time.Time, ok bool, err error) {
	filters, err := r.filters()
	if err != nil {
		return from, to, false, err
	}
	found, err := printingIn(filters, "period")
	if err != nil || !found {
		return from, to, false, err
	}
	value, err := printingIndex(filters, "period")
	if err != nil {
		return from, to, false, err
	}
	bounds := [2]time.Time{}
	for i, key := range []string{"from_date", "to_date"} {
		raw, err := printingIndex(value, key)
		if err != nil {
			return from, to, false, err
		}
		text, isText := raw.(string)
		if !isText {
			return from, to, false, errPrintingFailure
		}
		parsed, valid := dto.PythonStrptimeDMY(text)
		if !valid {
			return from, to, false, errPrintingFailure
		}
		if bounds[i], err = time.Parse("2006-01-02", parsed); err != nil {
			return from, to, false, errPrintingFailure
		}
	}
	return bounds[0], bounds[1], true, nil
}

func printingAmountComma(payment domain.PrintingPayment) string {
	return strings.ReplaceAll(payment.Amount, ".", ",")
}

func printingPaymentMoment(payment domain.PrintingPayment) time.Time {
	if payment.PaymentDate != nil {
		return payment.PaymentDate.UTC()
	}
	return payment.Created.UTC()
}

// paymentReport is the shared tail of the three payment reports.
func (r *printingRun) paymentReport(kind, title string, columns []string, named, sorted bool) (*printingReport, error) {
	rows, _, err := r.subscriptions(false, false)
	if err != nil {
		return nil, err
	}
	seen := map[string]bool{}
	associates := []string{}
	for _, row := range rows {
		if id := row.PrintingPerson.ID; id != nil && !seen[*id] {
			seen[*id] = true
			associates = append(associates, *id)
		}
	}
	from, to, period, err := r.period()
	if err != nil {
		return nil, err
	}
	payments, err := r.tx.Payments(r.ctx, domain.PrintingPaymentQuery{Kind: kind, Association: r.association.ID, Group: r.group,
		Associates: associates, Now: r.now, Period: period, From: from, To: to})
	if err != nil {
		return nil, err
	}
	if sorted {
		sort.SliceStable(payments, func(i, j int) bool {
			return printingDays(r.today, printingPaymentMoment(payments[i])) < printingDays(r.today, printingPaymentMoment(payments[j]))
		})
	}
	people := make([]*domain.PrintingPerson, len(payments))
	for i := range payments {
		people[i] = &payments[i].PrintingPerson
	}
	phones, err := r.phones(people)
	if err != nil {
		return nil, err
	}
	data := printingPersonColumns(people, phones)
	amounts := printingColumn(len(payments))
	for _, payment := range payments {
		amounts = append(amounts, xlsxexport.StrValue(printingAmountComma(payment)))
	}
	data = append(data, amounts)
	if named {
		names := printingColumn(len(payments))
		for _, payment := range payments {
			name, err := r.courseCarnetName(payment)
			if err != nil {
				return nil, err
			}
			names = append(names, name)
		}
		data = append(data, names)
	}
	dates, delays := printingColumn(len(payments)), printingColumn(len(payments))
	for _, payment := range payments {
		moment := printingPaymentMoment(payment)
		dates = append(dates, xlsxexport.StrValue(printingDate(moment)))
		delays = append(delays, xlsxexport.IntValue(printingDays(r.today, moment)))
	}
	return &printingReport{title: title, columns: columns, data: append(data, dates, delays)}, nil
}

var printingPaymentColumns = []string{"Nome", "Cognome", "Email", "Telefono", "Tel. Tutore", "Contatto", "quota (€)"}

func printingNotPaidQuotes(r *printingRun) (*printingReport, error) {
	columns := append(append([]string{}, printingPaymentColumns...), "data", "ritardo pagamento (gg)")
	return r.paymentReport("quotes", "Quote associative non pagate", columns, false, false)
}

func printingNotPaidCourses(r *printingRun) (*printingReport, error) {
	columns := append(append([]string{}, printingPaymentColumns...), "corso/carnet", "data", "ritardo pagamento (gg)")
	return r.paymentReport("courses", "Quote/Carnet corso non pagate", columns, true, false)
}

func printingExpiredPayments(r *printingRun) (*printingReport, error) {
	columns := append(append([]string{}, printingPaymentColumns...), "attività", "data", "ritardo pagamento (gg)")
	return r.paymentReport("expired", "Pagamenti scaduti", columns, true, true)
}

// printingJSON parses a stored JSONField value as Python's json.loads.
func printingJSON(text *string) (any, error) {
	if text == nil {
		return nil, nil
	}
	return dto.ParseReferenceJSON([]byte(*text))
}

// printingCell is a Python value (json.loads output) handed to pandas.
func printingCell(value any) (xlsxexport.Value, error) {
	switch v := value.(type) {
	case nil:
		return xlsxexport.NoneValue(), nil
	case string:
		return xlsxexport.StrValue(v), nil
	case bool:
		return xlsxexport.BoolValue(v), nil
	case json.Number:
		if !strings.ContainsAny(string(v), ".eE") {
			number, ok := new(big.Int).SetString(string(v), 10)
			if !ok || !number.IsInt64() {
				return xlsxexport.Value{}, domain.ErrPrintingGenerateNotPorted
			}
			return xlsxexport.IntValue(number.Int64()), nil
		}
		number, err := strconv.ParseFloat(string(v), 64)
		if err != nil && !errors.Is(err, strconv.ErrRange) {
			return xlsxexport.Value{}, domain.ErrPrintingGenerateNotPorted
		}
		return xlsxexport.FloatValue(number), nil // float('1e999') is inf
	}
	return xlsxexport.ObjectValue(dto.PythonStr(value)), nil
}

// courseCarnetName is Payment.get_course_carnet_name.
func (r *printingRun) courseCarnetName(payment domain.PrintingPayment) (xlsxexport.Value, error) {
	switch payment.Subject {
	case 2:
		link, err := r.tx.PaymentLink(r.ctx, payment.PaymentID, r.group)
		if err != nil {
			return xlsxexport.Value{}, err
		}
		if link.Slot >= 0 {
			meta, err := printingJSON(payment.Meta)
			if err != nil {
				return xlsxexport.Value{}, err
			}
			if dto.PythonTruthy(meta) {
				found, err := printingIn(meta, "description")
				if err != nil {
					return xlsxexport.Value{}, err
				}
				if found {
					description, err := printingIndex(meta, "description")
					if err != nil {
						return xlsxexport.Value{}, err
					}
					return printingCell(description)
				}
			}
			if link.MissingCarnet {
				return xlsxexport.Value{}, errPrintingFailure // None.title: AttributeError
			}
			return xlsxexport.OptionalStr(link.Title), nil
		}
		course, err := printingJSON(payment.Course)
		if err != nil {
			return xlsxexport.Value{}, err
		}
		if dto.PythonTruthy(course) {
			found, err := printingIn(course, "label")
			if err != nil {
				return xlsxexport.Value{}, err
			}
			if found {
				label, err := printingIndex(course, "label")
				if err != nil {
					return xlsxexport.Value{}, err
				}
				text, isText := label.(string)
				if !isText {
					return xlsxexport.Value{}, errPrintingFailure // str.join: TypeError
				}
				return xlsxexport.StrValue(text + ", " + printingOr(payment.Description, "")), nil
			}
		}
		return xlsxexport.OptionalStr(payment.Description), nil
	case 1:
		return xlsxexport.StrValue("Iscrizione"), nil
	case 0:
		if !payment.HasCategory {
			return xlsxexport.NoneValue(), nil
		}
		return xlsxexport.OptionalStr(payment.CategoryName), nil
	case 3:
		// custom_accounts.destination_account_name is not a CustomAccounts
		// field (and custom_accounts may be None): AttributeError.
		return xlsxexport.Value{}, errPrintingFailure
	}
	return xlsxexport.NoneValue(), nil
}

var printingSubjects = map[int64]string{0: "Altro", 1: "Iscrizione", 2: "Corso", 3: "Giroconto"}
var printingTypes = map[int64]string{1: "associato", 2: "associato e tesserato", 3: "tesserato"}

// printingDisplay is get_FOO_display(): the choice label, else the value itself.
func printingDisplay(labels map[int64]string, value int64) xlsxexport.Value {
	if label, ok := labels[value]; ok {
		return xlsxexport.StrValue(label)
	}
	return xlsxexport.IntValue(value)
}

var printingAllPaymentColumns = []string{"Cognome", "Nome", "Data nascita", "Email", "Telefono", "Tel. Tutore", "Contatto",
	"Tipo iscrizione", "Oggetto pagamento", "Descrizione", "Importo (€)", "Stato", "Data creazione", "Data pagamento", "N. Ricevuta"}

func printingWithAllPayments(r *printingRun) (*printingReport, error) {
	rows, _, err := r.subscriptions(false, false)
	if err != nil {
		return nil, err
	}
	type pair struct {
		subscription *domain.PrintingSubscription
		payment      *domain.PrintingPayment
	}
	pairs := []pair{}
	for i := range rows {
		// The period is read inside the loop: never without subscriptions.
		from, to, period, err := r.period()
		if err != nil {
			return nil, err
		}
		payments, err := r.tx.AssociatePayments(r.ctx, rows[i].PrintingPerson.ID, r.association.ID, r.group, period, from, to)
		if err != nil {
			return nil, err
		}
		for j := range payments {
			pairs = append(pairs, pair{&rows[i], &payments[j]})
		}
	}
	if len(pairs) == 0 {
		for i := range rows {
			pairs = append(pairs, pair{subscription: &rows[i]})
		}
	}
	people := make([]*domain.PrintingPerson, len(pairs))
	for i, item := range pairs {
		people[i] = &item.subscription.PrintingPerson
	}
	phones, err := r.phones(people)
	if err != nil {
		return nil, err
	}
	data := make([][]xlsxexport.Value, len(printingAllPaymentColumns))
	for c := range data {
		data[c] = printingColumn(len(pairs))
	}
	dash := xlsxexport.StrValue("-")
	for i, item := range pairs {
		person, payment := people[i], item.payment
		born := dash
		if person.BornDate != nil {
			born = xlsxexport.StrValue(printingDate(*person.BornDate))
		}
		values := []xlsxexport.Value{xlsxexport.OptionalStr(person.LastName), xlsxexport.OptionalStr(person.FirstName), born,
			xlsxexport.StrValue(printingOr(person.Email, "-")), xlsxexport.StrValue(phones[i][0]), xlsxexport.StrValue(phones[i][1]),
			xlsxexport.StrValue(phones[i][2]), printingDisplay(printingTypes, item.subscription.Type)}
		if payment == nil {
			values = append(values, dash, dash, dash, dash, dash, dash, dash)
		} else {
			status, paidOn, receipt := "Non pagato", dash, dash
			if payment.Paid {
				status = "Pagato"
			}
			if payment.PaymentDate != nil {
				paidOn = xlsxexport.StrValue(printingDate(payment.PaymentDate.UTC()))
			}
			if payment.HasInvoice {
				receipt = xlsxexport.StrValue("None")
				if payment.InvoiceNumber != nil {
					receipt = xlsxexport.StrValue(strconv.FormatInt(*payment.InvoiceNumber, 10))
				}
			}
			values = append(values, printingDisplay(printingSubjects, payment.Subject), xlsxexport.StrValue(printingOr(payment.Description, "-")),
				xlsxexport.StrValue(payment.Amount), xlsxexport.StrValue(status), xlsxexport.StrValue(printingDate(payment.Created.UTC())), paidOn,
				receipt)
		}
		for c, value := range values {
			data[c] = append(data[c], value)
		}
	}
	return &printingReport{title: "Iscrizioni con tutti i pagamenti", columns: printingAllPaymentColumns, data: data}, nil
}
