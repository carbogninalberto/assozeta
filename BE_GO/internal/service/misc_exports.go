package service

import (
	"bytes"
	"context"
	"encoding/base64"
	"errors"
	"fmt"
	"strconv"
	"strings"
	"time"
	"unicode/utf8"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/celery"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/xlsxexport"
	"github.com/google/uuid"
)

// MiscExports ports subscription_list_export (subscriptions_views.py) and
// invoice_list_export (invoice_views.py) for their CSV outputs and the
// invoice zip dispatch.
type MiscExports struct {
	store     domain.MiscExportStore
	publisher celery.Publisher
	now       func() time.Time
}

func NewMiscExports(store domain.MiscExportStore, publisher celery.Publisher, now func() time.Time) *MiscExports {
	return &MiscExports{store: store, publisher: publisher, now: now}
}

// MiscExportRequest is one export request.
type MiscExportRequest struct {
	Identity    domain.ResolvedIdentity
	Query       domain.RequestObject
	GroupHeader string
	Audit       domain.LoginAuditContext
}

const miscExportPublishTimeout = 10 * time.Second

var miscExportStatus = map[int64]string{1: "non firmata", 2: "in attesa", 3: "rifiutata", 4: "accettata", 5: "archiviata"}

var miscExportSimpleColumns = []string{"data", "nome", "cognome", "data di nascita", "codice fiscale", "minore", "stato",
	"certificato medico", "scadenza certificato medico", "validità certificato medico", "firmata", "email", "iscritto il",
	"scadenza iscrizione", "numero tessera", "tipo tessera", "tags"}

var miscExportFullColumns = []string{"stato iscrizione", "certificato medico", "scadenza certificato medico",
	"validità certificato medico", "firma", "nome", "cognome", "sesso", "codice fiscale", "data di nascita", "città di nascita",
	"città di residenza", "indirizzo di residenza", "cap di residenza", "email", "nome contatto telefono", "telefono",
	"nome contatto telefono 2", "telefono 2", "nome contatto telefono 3", "telefono 3", "nome contatto telefono 4", "telefono 4",
	"nome tutore", "cognome tutore", "sesso tutore", "codice fiscale tutore", "data di nascita tutore", "città di nascita tutore",
	"città di residenza tutore", "indirizzo di residenza tutore", "cap di residenza tutore", "email tutore",
	"nome contatto telefono tutore", "telefono tutore", "nome contatto telefono 2 tutore", "telefono 2 tutore",
	"nome contatto telefono 3 tutore", "telefono 3 tutore", "nome contatto telefono 4 tutore", "telefono 4 tutore", "iscritto il",
	"scadenza iscrizione", "numero tessera", "tipo tessera", "tags"}

var miscExportInvoiceColumns = []string{"data", "nome", "cognome", "quota associativa", "quota attivita", "numero", "modalità di pagamento"}

func miscExportDate(value time.Time) string {
	return fmt.Sprintf("%04d-%02d-%02d", value.Year(), int(value.Month()), value.Day())
}

func miscExportOptionalDate(value *time.Time) string {
	if value == nil {
		return ""
	}
	return miscExportDate(*value)
}

// miscExportOr is `value if value else ”` of a text column.
func miscExportOr(value *string) string {
	if value == nil {
		return ""
	}
	return *value
}

func miscExportYesNo(value bool) string {
	if value {
		return "si"
	}
	return "no"
}

func (s *MiscExports) today() time.Time {
	now := s.now().UTC()
	return time.Date(now.Year(), now.Month(), now.Day(), 0, 0, 0, 0, time.UTC)
}

// medicalStatus is _get_medical_status.
func (s *MiscExports) medicalStatus(row domain.MiscExportSubscription) string {
	if !row.HasMedical || row.MedicalExpiration == nil {
		return ""
	}
	expiration := *row.MedicalExpiration
	if !time.Date(expiration.Year(), expiration.Month(), expiration.Day(), 0, 0, 0, 0, time.UTC).Before(s.today()) {
		return "valido"
	}
	if row.IsMinor {
		return "esente"
	}
	return "scaduto"
}

func miscExportPersonFields(person *domain.MiscExportPerson) []string {
	if person == nil {
		return make([]string, 18)
	}
	return []string{miscExportOr(person.FirstName), miscExportOr(person.LastName), miscExportOr(person.Sex), miscExportOr(person.TaxCode),
		miscExportOptionalDate(person.BornDate), miscExportOr(person.BornCity), miscExportOr(person.AddressCity), miscExportOr(person.Address),
		miscExportOr(person.AddressCap), miscExportOr(person.Email), miscExportOr(person.PhoneLabel), miscExportOr(person.Phone),
		miscExportOr(person.Phone2Label), miscExportOr(person.Phone2), miscExportOr(person.Phone3Label), miscExportOr(person.Phone3),
		miscExportOr(person.Phone4Label), miscExportOr(person.Phone4)}
}

// tutorState is get_main_tutor per associate: the primary relation, else the
// first relation (which the view promotes and saves).
type miscExportTutors struct {
	byAssociate map[string][]*domain.MiscExportRelation
}

func (t miscExportTutors) main(associate string) (tutor *domain.MiscExportRelation, promote bool) {
	relations := t.byAssociate[associate]
	for _, relation := range relations {
		if relation.Primary {
			return relation, false
		}
	}
	if len(relations) == 0 {
		return nil, false
	}
	return relations[0], true
}

// SubscriptionExport follows subscription_list_export.
func (s *MiscExports) SubscriptionExport(ctx context.Context, request MiscExportRequest) (any, error) {
	identity := request.Identity
	if identity.ScopeUser.Role == domain.RoleAthlete {
		return nil, domain.ErrMiscExportsServer // raise Exception("Cannot export info for athlete")
	}
	mode := "csv"
	if value, present := paymentReadsParam(request.Query, "filetype"); present {
		mode = value
	}
	xls := strings.Contains(mode, "xls")
	simplified := strings.Contains(mode, "simplified")
	tx, err := s.store.BeginMiscExports(ctx)
	if err != nil {
		return nil, err
	}
	defer tx.Rollback()
	scope := paymentReadsScope(identity)
	association, err := tx.ScopedAssociation(ctx, identity.ScopeUser.ID, scope)
	if err != nil {
		return nil, err
	}
	if association == nil {
		return nil, domain.ErrMiscExportsServer
	}
	subscriptions, err := tx.Subscriptions(ctx, association.ID, supplierGroup(request.GroupHeader), scope)
	if err != nil {
		return nil, err
	}
	ids := make([]string, len(subscriptions))
	associates := []string{}
	for i, row := range subscriptions {
		ids[i] = row.ID
		if row.AssociateID != nil {
			associates = append(associates, *row.AssociateID)
		}
	}
	tags, err := tx.Tags(ctx, ids)
	if err != nil {
		return nil, err
	}
	tutors := miscExportTutors{byAssociate: map[string][]*domain.MiscExportRelation{}}
	if !simplified {
		relations, err := tx.Relations(ctx, associates)
		if err != nil {
			return nil, err
		}
		for i := range relations {
			relation := &relations[i]
			tutors.byAssociate[relation.AssociateID] = append(tutors.byAssociate[relation.AssociateID], relation)
		}
	}
	columns := miscExportFullColumns
	if simplified {
		columns = miscExportSimpleColumns
	}
	var workbook []byte
	if xls {
		// The workbook's cells do not depend on the promotions: build it from
		// a dry pass first, so an input Go does not reproduce is refused
		// before any write.
		preview, failure, err := s.subscriptionRows(ctx, tx, request, subscriptions, tags, tutors.copy(), simplified, true)
		if err != nil {
			return nil, err
		}
		if failure == nil && len(preview) > 0 {
			if workbook, err = miscExportsWorkbook(columns, preview, s.now()); err != nil {
				return nil, err
			}
		}
	}
	rows, failure, err := s.subscriptionRows(ctx, tx, request, subscriptions, tags, tutors, simplified, false)
	if err != nil {
		return nil, err
	}
	// Each promotion was autocommitted before the failing row.
	if err := tx.Commit(); err != nil {
		return nil, err
	}
	if failure != nil {
		return nil, failure
	}
	if len(rows) == 0 {
		return nil, domain.ErrMiscExportsServer // pandas: df.columns = cols on an empty frame
	}
	if xls {
		filename := fmt.Sprintf("[%s] ISCRIZIONI a %s.xls", miscExportDate(s.today()), miscExportDenomination(association))
		return domain.RequestObject{{Name: "data", Value: domain.RequestObject{{Name: "filename", Value: filename}, {Name: "type", Value: "xls"},
			{Name: "file", Value: base64.StdEncoding.EncodeToString(workbook)}}}}, nil
	}
	var out bytes.Buffer
	paymentExportWriteRow(&out, columns)
	for _, row := range rows {
		paymentExportWriteRow(&out, row)
	}
	filename := fmt.Sprintf("[%s] ISCRIZIONI a %s.csv", miscExportDate(s.today()), miscExportDenomination(association))
	return domain.RequestObject{{Name: "data", Value: domain.RequestObject{{Name: "filename", Value: filename}, {Name: "type", Value: "csv"},
		{Name: "file", Value: base64.StdEncoding.EncodeToString(out.Bytes())}}}}, nil
}

// subscriptionRows builds the export rows in queryset order; a row that
// raises ends the rows with its failure. Unless dry, get_main_tutor's
// promotions are saved as the rows are built.
func (s *MiscExports) subscriptionRows(ctx context.Context, tx domain.MiscExportTransaction, request MiscExportRequest,
	subscriptions []domain.MiscExportSubscription, tags map[string][]string, tutors miscExportTutors, simplified, dry bool) ([][]string, error, error) {
	identity := request.Identity
	rows := make([][]string, 0, len(subscriptions))
	var failure error
	for _, row := range subscriptions {
		if row.AssociateID == nil {
			failure = domain.ErrMiscExportsServer // subscription.associate.<field>: AttributeError
			break
		}
		var tutor *domain.MiscExportPerson
		if !simplified {
			relation, promote := tutors.main(*row.AssociateID)
			if promote && !dry {
				if err := s.promote(ctx, tx, identity, request.Audit, relation, row.AssociateAssociation); err != nil {
					return nil, nil, err
				}
			}
			if promote {
				relation.Primary = true
			}
			if relation != nil {
				tutor = &relation.MiscExportPerson
			}
		}
		status, known := miscExportStatus[row.Status]
		if !known {
			failure = domain.ErrMiscExportsServer // STATUS_DICT KeyError
			break
		}
		expiration := ""
		if row.HasMedical && row.MedicalExpiration != nil {
			expiration = miscExportDate(*row.MedicalExpiration)
		}
		signed := miscExportYesNo(miscExportOr(row.SignatureKey) != "" || miscExportOr(row.SignatureURL) != "")
		tail := []string{miscExportOptionalDate(row.Start), miscExportOptionalDate(row.End), miscExportOr(row.Number), miscExportOr(row.Kind),
			strings.Join(tags[row.ID], ", ")}
		var values []string
		if simplified {
			minor := "maggiorenne"
			if row.IsMinor {
				minor = "minorenne"
			}
			values = []string{miscExportDate(row.Created.UTC()), miscExportOr(row.FirstName), miscExportOr(row.LastName),
				miscExportOptionalDate(row.BornDate), miscExportOr(row.TaxCode), minor, status, miscExportYesNo(row.HasMedical), expiration,
				s.medicalStatus(row), signed, miscExportOr(row.Email)}
		} else {
			values = append([]string{status, miscExportYesNo(row.HasMedical), expiration, s.medicalStatus(row), signed},
				miscExportPersonFields(&row.MiscExportPerson)...)
			values = append(values, miscExportPersonFields(tutor)...)
		}
		rows = append(rows, append(values, tail...))
	}
	return rows, failure, nil
}

// copy is the tutor relations with their primary flags copied (for a dry pass).
func (t miscExportTutors) copy() miscExportTutors {
	out := miscExportTutors{byAssociate: map[string][]*domain.MiscExportRelation{}}
	for associate, relations := range t.byAssociate {
		for _, relation := range relations {
			clone := *relation
			out.byAssociate[associate] = append(out.byAssociate[associate], &clone)
		}
	}
	return out
}

// miscExportsWorkbook is _export_to_excel(DataFrame(rows) with cols): sheet
// 'Libro Soci' with the index column, set_column(i, i, len(col) + 10) for
// i over the columns (the index column takes the first width). Every value is
// a str or None (written blank, as ”).
func miscExportsWorkbook(columns []string, rows [][]string, now time.Time) ([]byte, error) {
	frame := xlsxexport.Frame{Sheet: "Libro Soci", Columns: columns, Index: true}
	for _, column := range columns {
		frame.Widths = append(frame.Widths, float64(utf8.RuneCountInString(column)+10))
	}
	for _, row := range rows {
		values := make([]xlsxexport.Value, len(row))
		for i, text := range row {
			values[i] = xlsxexport.StrValue(text)
		}
		frame.Rows = append(frame.Rows, values)
	}
	workbook, err := xlsxexport.PandasXlsxwriter(frame, now)
	switch {
	case errors.Is(err, xlsxexport.ErrNotPorted):
		return nil, domain.ErrMiscExportsNotPorted
	case err != nil:
		return nil, domain.ErrMiscExportsServer
	}
	return workbook, nil
}

func miscExportDenomination(association *domain.PaymentExportAssociation) string {
	if association.Denomination == nil {
		return "None"
	}
	return *association.Denomination
}

// promote is get_main_tutor's save, audited through the associate's
// association (or, without one, the actor's).
func (s *MiscExports) promote(ctx context.Context, tx domain.MiscExportTransaction, identity domain.ResolvedIdentity,
	audit domain.LoginAuditContext, relation *domain.MiscExportRelation, association *string) error {
	promotion := domain.MiscExportPromotion{RelationID: relation.ID, Context: paymentCategoryAuditContext(identity, audit),
		At: s.now().UTC().Truncate(time.Microsecond)}
	path := "associate.sport_association"
	if association == nil {
		var err error
		if association, err = tx.ActiveAssociationOf(ctx, identity.Actor.ID); err != nil {
			return err
		}
		path = "actor.sport_association"
	}
	if association != nil {
		promotion.AssociationID, promotion.Path = association, &path
	}
	return tx.PromoteRelation(ctx, promotion)
}

// InvoiceExport follows invoice_list_export.
func (s *MiscExports) InvoiceExport(ctx context.Context, request MiscExportRequest) (any, error) {
	identity := request.Identity
	if identity.ScopeUser.Role == domain.RoleAthlete {
		return nil, domain.ErrMiscExportsServer // raise Exception("Cannot export info for athlete")
	}
	mode := "csv"
	if value, present := paymentReadsParam(request.Query, "m"); present {
		mode = value
	}
	tx, err := s.store.BeginMiscExports(ctx)
	if err != nil {
		return nil, err
	}
	defer tx.Rollback()
	scope := paymentReadsScope(identity)
	association, err := tx.ScopedAssociation(ctx, identity.ScopeUser.ID, scope)
	if err != nil {
		return nil, err
	}
	if association == nil {
		return nil, domain.ErrMiscExportsServer
	}
	archived := false
	if raw, present := paymentReadsParam(request.Query, "archived"); present {
		if archived, err = paymentReadsBoolean(raw); err != nil {
			return nil, domain.ErrMiscExportsServer // BooleanField.to_python's ValidationError
		}
	}
	group := supplierGroup(request.GroupHeader)
	invoices, err := tx.Invoices(ctx, association.ID, archived, group, scope)
	if err != nil {
		return nil, err
	}
	ids := make([]string, len(invoices))
	for i, invoice := range invoices {
		ids[i] = invoice.ID
	}
	payments, err := tx.FirstPayments(ctx, ids, group, scope)
	if err != nil {
		return nil, err
	}
	if err := tx.Rollback(); err != nil {
		return nil, err
	}
	data := domain.RequestObject{{Name: "file", Value: ""}}
	switch mode {
	case "csv":
		var out bytes.Buffer
		paymentExportWriteRow(&out, miscExportInvoiceColumns)
		for _, invoice := range invoices {
			payment, found := payments[invoice.ID]
			first, last, kind := "", "", "-"
			if found {
				if payment.HasAssociate {
					first, last = miscExportOr(payment.AssociateFirst), miscExportOr(payment.AssociateLast)
				}
				if payment.Type != "default" {
					kind = payment.Type
				}
			}
			number := ""
			if invoice.Number != nil {
				number = strconv.FormatInt(*invoice.Number, 10)
			}
			paymentExportWriteRow(&out, []string{miscExportDate(invoice.Created.UTC()), first, last, invoice.Membership, invoice.Activity,
				number, kind})
		}
		data = domain.RequestObject{{Name: "file", Value: base64.StdEncoding.EncodeToString(out.Bytes())}, {Name: "type", Value: "csv"},
			{Name: "filename", Value: fmt.Sprintf("[%s] RICEVUTE a %s.csv", miscExportDate(s.today()), miscExportDenomination(association))}}
	case "xlsx":
		workbook, err := miscExportsInvoiceWorkbook(invoices, payments, s.now())
		if err != nil {
			return nil, err
		}
		data = domain.RequestObject{{Name: "file", Value: base64.StdEncoding.EncodeToString(workbook)}, {Name: "type", Value: "xlsx"},
			{Name: "filename", Value: fmt.Sprintf("[%s] RICEVUTE a %s.xlsx", miscExportDate(s.today()), miscExportDenomination(association))}}
	case "files":
		files := []any{}
		for _, invoice := range invoices {
			if invoice.DocumentID != nil {
				files = append(files, *invoice.DocumentID)
			}
		}
		// export_invoices_to_zip.delay(files_to_zip, sport_association.sport_association_id): the view
		// publishes after its reads; a publish failure is a 500.
		publish, cancel := context.WithTimeout(context.WithoutCancel(ctx), miscExportPublishTimeout)
		defer cancel()
		if _, err := s.publisher.Publish(publish, celery.Task{Name: "export_invoices_to_zip",
			Args: []any{files, uuid.MustParse(association.ID)}}); err != nil {
			return nil, err
		}
		data = append(data, domain.RequestField{Name: "message",
			Value: "Export dei file in corso, controlla la sezione Documenti e Modelli tra qualche minuto."})
	}
	return domain.RequestObject{{Name: "data", Value: data}}, nil
}

// miscExportsInvoiceWorkbook is invoice_list_export's get_excel_base64 call:
// the creation date, the payment associate's first and last name (None kept,
// ” without a payment or an associate), the fees (Decimals), the number
// (None kept) and the payment type ('-' for default or without a payment).
func miscExportsInvoiceWorkbook(invoices []domain.MiscExportInvoice, payments map[string]domain.MiscExportPayment,
	now time.Time) ([]byte, error) {
	data := make([][]xlsxexport.Value, len(miscExportInvoiceColumns))
	for c := range data {
		data[c] = []xlsxexport.Value{}
	}
	for _, invoice := range invoices {
		payment, found := payments[invoice.ID]
		first, last, kind := xlsxexport.StrValue(""), xlsxexport.StrValue(""), "-"
		if found {
			if payment.HasAssociate {
				first, last = xlsxexport.OptionalStr(payment.AssociateFirst), xlsxexport.OptionalStr(payment.AssociateLast)
			}
			if payment.Type != "default" {
				kind = payment.Type
			}
		}
		number := xlsxexport.NoneValue()
		if invoice.Number != nil {
			number = xlsxexport.IntValue(*invoice.Number)
		}
		values := []xlsxexport.Value{xlsxexport.StrValue(miscExportDate(invoice.Created.UTC())), first, last,
			xlsxexport.DecimalValue(invoice.Membership), xlsxexport.DecimalValue(invoice.Activity), number, xlsxexport.StrValue(kind)}
		for c, value := range values {
			data[c] = append(data[c], value)
		}
	}
	workbook, err := xlsxexport.PandasOpenpyxl(data, miscExportInvoiceColumns, now)
	switch {
	case errors.Is(err, xlsxexport.ErrNotPorted):
		return nil, domain.ErrMiscExportsNotPorted
	case err != nil:
		return nil, domain.ErrMiscExportsServer // get_excel_base64 returned None: raise Exception
	}
	return workbook, nil
}
