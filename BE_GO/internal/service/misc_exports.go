package service

import (
	"bytes"
	"context"
	"encoding/base64"
	"fmt"
	"strconv"
	"strings"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/celery"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
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
	if strings.Contains(mode, "xls") {
		// The pandas/xlsxwriter workbook is not ported.
		return nil, domain.ErrMiscExportsNotPorted
	}
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
			if promote {
				if err := s.promote(ctx, tx, identity, request.Audit, relation, row.AssociateAssociation); err != nil {
					return nil, err
				}
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
	columns := miscExportFullColumns
	if simplified {
		columns = miscExportSimpleColumns
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
	if mode == "xlsx" {
		// The get_excel_base64 workbook is not ported.
		return nil, domain.ErrMiscExportsNotPorted
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
