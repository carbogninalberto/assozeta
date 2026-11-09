package domain

import (
	"context"
	"errors"
)

// ErrExportAllDataServer is an exception export_all_data does not catch (the
// 500 page); ErrExportAllDataNotPorted is an output Go does not reproduce,
// refused with the 500 page.
var (
	ErrExportAllDataServer    = errors.New("export all data: unhandled exception")
	ErrExportAllDataNotPorted = errors.New("export all data: output not ported")
)

// ExportAllDataColumn is one exported model field: Kind tells how its value
// becomes str(value) (see repository.exportAllDataExpression).
type ExportAllDataColumn struct {
	Name string
	// Kind is "text" (already str()), "json" (the stored JSON text),
	// "choice-1:<labels>" (LABELS[value - 1][1]), "choice-0:<labels>"
	// (LABELS[value][1]) or "map:<labels>" (dict(CHOICES).get(value, '-')).
	Kind string
	// SQL is the column (or foreign key column) name; FK is the related model
	// name for a foreign key (str() is '<FK> object (<pk>)').
	SQL string
	FK  string
	// Source is the stored type for "text": text, date, datetime, bool,
	// decimal, int or uuid.
	Source string
}

// ExportAllDataSubscription is one 'Iscrizioni' row before str().
type ExportAllDataSubscription struct {
	Subscription []*string
	HasMedical   bool
	Medical      []*string
	AssociateID  *string
	Associate    []*string
}

// ExportAllDataPayment is one 'Pagamenti' row before str(): Related holds
// the payment_category, custom_accounts, associate, supplier, instructor and
// invoice columns (nil without the relation).
type ExportAllDataPayment struct {
	ID      string
	Subject int64
	Course  *string
	Payment []*string
	Related [6][]*string
}

// ExportAllDataCourse is a scoped CourseSubscription or installment .get(payment=...).
type ExportAllDataCourse struct {
	Matches int
	Title   *string
}

type ExportAllDataStore interface {
	BeginExportAllData(ctx context.Context) (ExportAllDataTransaction, error)
}

type ExportAllDataTransaction interface {
	Rollback() error
	// Association is SportAssociation.objects.get(user=...): ErrAssociationNotFound unless exactly one live row.
	Association(ctx context.Context, user string) (*PrintingAssociation, error)
	Subscriptions(ctx context.Context, association string, group, scope *string) ([]ExportAllDataSubscription, error)
	// Tutors are the prefetched associate.tutors (live, group-aware) per associate.
	Tutors(ctx context.Context, associates []string, group *string) (map[string][][]*string, error)
	Payments(ctx context.Context, association string, group, scope *string) ([]ExportAllDataPayment, error)
	CourseSubscription(ctx context.Context, payment string, scope *string) (ExportAllDataCourse, error)
	CourseInstallment(ctx context.Context, payment string, group, scope *string) (ExportAllDataCourse, error)
}

func exportAllDataText(name, source string) ExportAllDataColumn {
	return ExportAllDataColumn{Name: name, Kind: "text", SQL: name, Source: source}
}

func exportAllDataFK(name, model string) ExportAllDataColumn {
	return ExportAllDataColumn{Name: name, Kind: "text", SQL: name + "_id", FK: model}
}

func exportAllDataJSON(name string) ExportAllDataColumn {
	return ExportAllDataColumn{Name: name, Kind: "json", SQL: name}
}

func exportAllDataChoice(name, kind string) ExportAllDataColumn {
	return ExportAllDataColumn{Name: name, Kind: kind, SQL: name, Source: "int"}
}

func exportAllDataTexts(names ...string) []ExportAllDataColumn {
	out := make([]ExportAllDataColumn, len(names))
	for i, name := range names {
		out[i] = exportAllDataText(name, "text")
	}
	return out
}

func exportAllDataJoin(parts ...[]ExportAllDataColumn) []ExportAllDataColumn {
	out := []ExportAllDataColumn{}
	for _, part := range parts {
		out = append(out, part...)
	}
	return out
}

// The exported fields: each model's _meta.fields minus the view's exclusions, in field order.
var (
	ExportAllDataSubscriptionColumns = []ExportAllDataColumn{
		exportAllDataChoice("type", "choice-1:associato|associato e tesserato|tesserato"),
		exportAllDataChoice("role", "choice-1:Socio ordinario|Consigliere|Segretario|Vice Presidente|Presidente|Socio volontario|Socio sostenitore|Tesoriere"),
		exportAllDataText("acceptance_date", "date"), exportAllDataText("resignation_date", "date"),
		exportAllDataChoice("status_flag", "choice-1:non firmata|in attesa|rifiutata|accettata|ritirata"),
		exportAllDataText("creation_date", "datetime"), exportAllDataText("start_date", "date"), exportAllDataText("end_date", "date"),
		exportAllDataText("signature_url", "text"), exportAllDataText("signature_storage_key", "text"),
		exportAllDataText("archived", "bool"), exportAllDataText("draft", "bool"), exportAllDataText("subscription_number", "text"),
		exportAllDataText("subscription_type", "text"), exportAllDataFK("group", "Group"), exportAllDataText("competitive", "bool"),
		exportAllDataText("notes", "text"),
	}
	ExportAllDataMedicalColumns = []ExportAllDataColumn{
		exportAllDataText("creation_date", "datetime"), exportAllDataText("expiration_date", "date"),
		exportAllDataText("competitive_medical_certificate", "bool"), exportAllDataText("notes", "text"),
	}
	ExportAllDataAssociateColumns = exportAllDataJoin(
		exportAllDataTexts("first_name", "last_name", "sex", "tax_code"),
		[]ExportAllDataColumn{exportAllDataText("born_date", "date")},
		exportAllDataTexts("born_city", "address_city", "address", "address_cap", "nationality", "nationality_residence", "email", "phone",
			"phone_2", "phone_3", "phone_4", "phone_label", "phone_2_label", "phone_3_label", "phone_4_label"),
		[]ExportAllDataColumn{exportAllDataText("is_minor", "bool"), exportAllDataText("disabled", "bool"), exportAllDataText("draft", "bool"),
			exportAllDataFK("group", "Group"), exportAllDataFK("family", "Family")},
		exportAllDataTexts("family_role", "id_number"),
		[]ExportAllDataColumn{exportAllDataText("creation_date", "datetime"), exportAllDataText("migrated", "bool"),
			exportAllDataText("notes", "text")},
	)
	ExportAllDataPaymentColumns = []ExportAllDataColumn{
		{Name: "type", Kind: "map:default=DEFAULT|cash=CONTANTI|transfer=BONIFICO|online=ONLINE|sepa-transfer=BONIFICO SEPA|stripe=STRIPE|pos=POS",
			SQL: "type", Source: "text"},
		exportAllDataChoice("subject", "choice-0:Altro|Iscrizione|Corso|Giroconto"),
		exportAllDataText("amount", "decimal"), exportAllDataText("creation_date", "datetime"), exportAllDataText("payment_date", "datetime"),
		exportAllDataText("paid", "bool"), exportAllDataText("expense", "bool"), exportAllDataText("description", "text"),
		exportAllDataJSON("meta_payment_categories"), exportAllDataText("archived", "bool"), exportAllDataJSON("meta"),
		exportAllDataText("notes", "text"), exportAllDataFK("signature", "Signature"), exportAllDataFK("group", "Group"),
		exportAllDataJSON("course"),
	}
	ExportAllDataCategoryColumns = []ExportAllDataColumn{
		exportAllDataText("name", "text"), exportAllDataText("creation_date", "datetime"), exportAllDataText("expense", "bool"),
		exportAllDataText("tax_deductible", "bool"), exportAllDataText("archived", "bool"), exportAllDataText("type", "int"),
		exportAllDataFK("vat_management", "VatManagement"),
	}
	ExportAllDataAccountColumns = []ExportAllDataColumn{
		exportAllDataText("name", "text"), exportAllDataText("initial_balance", "decimal"),
		exportAllDataChoice("account_type", "choice-1:contanti|banca|altro"), exportAllDataText("account_code", "text"),
	}
	ExportAllDataSupplierColumns = exportAllDataJoin(
		exportAllDataTexts("name", "address", "tax_code", "vat_number", "type", "email", "phone_number", "city", "cap", "state", "country",
			"nationality", "note"),
		[]ExportAllDataColumn{exportAllDataFK("group", "Group")},
	)
	ExportAllDataInstructorColumns = exportAllDataJoin(
		exportAllDataTexts("first_name", "last_name", "email"),
		[]ExportAllDataColumn{exportAllDataText("born_date", "date")},
		exportAllDataTexts("born_city", "born_province", "tax_code", "address_city", "address", "address_province", "civic_number",
			"study_title", "role", "phone", "phone_2", "phone_3", "phone_4"),
		[]ExportAllDataColumn{exportAllDataText("draft", "bool"), exportAllDataText("associated_user_id", "uuid"),
			exportAllDataText("is_volunteer", "bool"), exportAllDataText("stipulated_contract_in", "date"),
			exportAllDataText("default_hourly_billing", "decimal"), exportAllDataText("default_percentage_billing", "decimal")},
	)
	ExportAllDataInvoiceColumns = []ExportAllDataColumn{
		exportAllDataText("creation_date", "datetime"), exportAllDataText("membership_fee", "decimal"),
		exportAllDataText("activity_fee", "decimal"), exportAllDataJSON("meta_payment_categories"), exportAllDataText("description", "text"),
		exportAllDataText("number", "int"), exportAllDataText("archived", "bool"), exportAllDataText("cancelled", "bool"),
		exportAllDataFK("selected_tutor", "Associate"), exportAllDataJSON("meta"), exportAllDataFK("group", "Group"),
	}
)
