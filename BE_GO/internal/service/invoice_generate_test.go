package service

import (
	"context"
	"strconv"
	"testing"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/celery"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

// invoiceGenerateFake records the transaction calls in order; methods the
// subject-0 path does not reach are left to the embedded nil interface.
type invoiceGenerateFake struct {
	domain.InvoiceGenerateTransaction
	events *[]string
}

func (f invoiceGenerateFake) log(event string) { *f.events = append(*f.events, event) }

func (f invoiceGenerateFake) ScopedAssociation(context.Context, string, *string) (string, error) {
	return "a1000000-0000-4000-8000-000000701001", nil
}
func (f invoiceGenerateFake) PaymentState(context.Context, domain.PaymentStateQuery) (*domain.PaymentStateRow, error) {
	category, name, description := "c1", "Quote", "Quota"
	return &domain.PaymentStateRow{PaymentSaveRow: domain.PaymentSaveRow{ID: "p1", AssociationID: "a1000000-0000-4000-8000-000000701001",
		CategoryID: &category, CategoryName: &name, Description: &description}}, nil
}
func (f invoiceGenerateFake) GenerateSubscription(context.Context, string, *string, *string) (bool, *string, error) {
	associate := "s1"
	return true, &associate, nil
}
func (f invoiceGenerateFake) InvoiceGeneratePayment(context.Context, string) (domain.InvoiceGeneratePayment, error) {
	return domain.InvoiceGeneratePayment{Amount: "25.00", CreationDate: time.Date(2026, 10, 1, 8, 0, 0, 0, time.UTC)}, nil
}
func (f invoiceGenerateFake) InvoiceGenerateOwner(context.Context, string) (domain.InvoiceGenerateOwner, error) {
	return domain.InvoiceGenerateOwner{StartDay: 1, StartMonth: 1, StartingNumber: 3}, nil
}
func (f invoiceGenerateFake) LockInvoiceNumbering(context.Context, string) error {
	f.log("lock")
	return nil
}
func (f invoiceGenerateFake) LastInvoiceNumber(_ context.Context, query domain.InvoiceGenerateNumberQuery) (*int64, error) {
	f.log("last-number")
	number := int64(7)
	return &number, nil
}
func (f invoiceGenerateFake) AssociationDenomination(context.Context, string) (*string, error) {
	name := "Den"
	return &name, nil
}
func (f invoiceGenerateFake) AssociateNames(context.Context, string) (*string, *string, error) {
	first, last := "Anna", "Bianchi"
	return &first, &last, nil
}
func (f invoiceGenerateFake) CreateInvoice(_ context.Context, create domain.InvoiceGenerateCreate, overrides map[string]string,
	_ domain.LoginAuditContext, _ time.Time) error {
	f.log("create " + strconv.FormatInt(create.Number, 10))
	return nil
}
func (f invoiceGenerateFake) SaveGeneratedPayment(context.Context, domain.InvoiceGenerateSave) error {
	f.log("save")
	return nil
}
func (f invoiceGenerateFake) AppendPaymentAudit(context.Context, domain.PaymentArchiveEvent) error {
	return nil
}
func (f invoiceGenerateFake) RenderPayment(context.Context, string, *string) (domain.PaymentStateRender, error) {
	return domain.PaymentStateRender{}, nil
}
func (f invoiceGenerateFake) Commit() error   { f.log("commit"); return nil }
func (f invoiceGenerateFake) Rollback() error { return nil }

type invoiceGenerateFakeStore struct{ tx invoiceGenerateFake }

func (s invoiceGenerateFakeStore) BeginInvoiceGenerate(context.Context) (domain.InvoiceGenerateTransaction, error) {
	return s.tx, nil
}

type invoiceOrderedPublisher struct {
	events *[]string
	tasks  []celery.Task
}

func (p *invoiceOrderedPublisher) Publish(_ context.Context, task celery.Task) (string, error) {
	*p.events = append(*p.events, "publish")
	p.tasks = append(p.tasks, task)
	return "id", nil
}

func TestInvoiceGenerateLocksBeforeNumberingAndPublishesAfterCommit(t *testing.T) {
	events := []string{}
	publisher := &invoiceOrderedPublisher{events: &events}
	service := NewInvoiceGenerations(invoiceGenerateFakeStore{tx: invoiceGenerateFake{events: &events}}, publisher,
		func() time.Time { return time.Date(2026, 11, 9, 10, 0, 0, 0, time.UTC) })
	service.newID = func() string { return "i1" }
	owner := &domain.Account{ID: "owner"}
	token := "Bearer x"
	body := domain.RequestObject{{Name: "subscription_id", Value: "a1000000-0000-4000-8000-000000995009"}}
	if _, err := service.Generate(context.Background(), domain.ResolvedIdentity{Actor: owner, Effective: owner, ScopeUser: owner},
		InvoiceGenerateRequest{ID: "a1000000-0000-4000-8000-000000995010", Authorization: &token}, body); err != nil {
		t.Fatal(err)
	}
	want := []string{"lock", "last-number", "create 8", "save", "commit", "publish"}
	if len(events) != len(want) {
		t.Fatalf("events %v, want %v", events, want)
	}
	for i := range want {
		if events[i] != want[i] {
			t.Fatalf("events %v, want %v", events, want)
		}
	}
	task := publisher.tasks[0]
	if task.Name != "print_document_invoice" || !task.ArgsList || len(task.Args) != 2 || task.Args[0] != "i1" || task.Args[1] != token {
		t.Fatalf("task %+v", task)
	}
}

func TestInvoiceFees(t *testing.T) {
	categories := `[{"amount": 10.5}, {"amount": 0}, {"amount": "2"}]`
	fees, err := invoiceFees(1, domain.InvoiceGeneratePayment{Amount: "30.00", MetaPaymentCategories: &categories})
	if err != nil || fees.membership != (invoiceFee{"17.5", "1.75000000e+01", "17.50"}) || fees.activity.memory != "12.5" {
		t.Fatalf("%+v %v", fees, err)
	}
	small := `[{"amount": 0.1}]`
	fees, err = invoiceFees(1, domain.InvoiceGeneratePayment{Amount: "0.30", MetaPaymentCategories: &small})
	if err != nil || fees.membership.memory != "0.19999999999999998" || fees.membership.rendered != "0.20" {
		t.Fatalf("%+v %v", fees, err)
	}
	fees, err = invoiceFees(0, domain.InvoiceGeneratePayment{Amount: "25.00"})
	if err != nil || fees.membership.memory != "0.0" || fees.membership.rendered != "0.00" || fees.activity != decimalFee("25.00") {
		t.Fatalf("%+v %v", fees, err)
	}
	tie := `[{"amount": 9.875}]`
	if _, err = invoiceFees(1, domain.InvoiceGeneratePayment{Amount: "10.00", MetaPaymentCategories: &tie}); err != domain.ErrPaymentStateNotPorted {
		t.Fatalf("exact tie: %v", err)
	}
	bad := `[{"amount": true}]`
	if _, err = invoiceFees(1, domain.InvoiceGeneratePayment{Amount: "10.00", MetaPaymentCategories: &bad}); err != domain.ErrPaymentStateServer {
		t.Fatalf("float('True'): %v", err)
	}
}

func TestInvoicePeriodAndEscape(t *testing.T) {
	day := func(y int) *time.Time { v := time.Date(y, 1, 1, 0, 0, 0, 0, time.UTC); return &v }
	if got := invoicePeriod(domain.InvoiceGeneratePeriod{Start: day(2025), End: day(2026)}); got != "25/26" {
		t.Fatal(got)
	}
	if got := invoicePeriod(domain.InvoiceGeneratePeriod{End: day(2026)}); got != "2026" {
		t.Fatal(got)
	}
	if got := formatHTML(invoiceDescriptionTemplates[0], `<a href="x">'&`, "Den"); got !=
		"Ricevuta di pagamento con causale <b>&lt;a href=&quot;x&quot;&gt;&#x27;&amp;</b> per l'associazione <b>Den</b>." {
		t.Fatal(got)
	}
}
