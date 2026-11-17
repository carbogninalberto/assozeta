package service

import (
	"context"
	"strings"
	"testing"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

func paymentPaidFlowText(value string) *string { return &value }

func TestPaymentPaidFlowMessage(t *testing.T) {
	people := domain.PaymentPaidFlowPeople{AssociateFound: true, AssociateFirst: paymentPaidFlowText("Mario"),
		AssociateLast: paymentPaidFlowText("Rossi"), UserFound: true, UserFirst: paymentPaidFlowText("Message")}
	if got := paymentPaidFlowMessage("15.00", "Quota", people); got != "Pagamento di € 15,00per Quota. Pagamento dell'associato Mario Rossi." {
		t.Fatalf("associate message: %q", got)
	}
	people.AssociateFound = false
	if got := paymentPaidFlowMessage("-1.50", "", people); got != "Pagamento di € -1,50. Pagamento dell'utente Message None." {
		t.Fatalf("user message: %q", got)
	}
	if got := paymentPaidFlowMessage("40.00", "", domain.PaymentPaidFlowPeople{}); got != "Pagamento di € 40,00." {
		t.Fatalf("bare message: %q", got)
	}
}

func TestPaymentPaidFlowStripeFees(t *testing.T) {
	meta := `[{"amount": "4.50"}, {"amount": true}, {"amount": null}]`
	pair, err := paymentPaidFlowStripeFees(context.Background(), 1, domain.InvoiceGeneratePayment{Amount: "30.00", MetaPaymentCategories: &meta})
	if err != nil {
		t.Fatal(err)
	}
	if pair.activity.memory != "5.5" || pair.membership.memory != "24.5" || pair.membership.rendered != "24.50" {
		t.Fatalf("categories: %+v", pair)
	}
	pair, err = paymentPaidFlowStripeFees(context.Background(), 1, domain.InvoiceGeneratePayment{Amount: "12.00"})
	if err != nil {
		t.Fatal(err)
	}
	// Without categories the membership fee stays the stored Decimal.
	if pair.membership.memory != "12.00" || pair.activity.memory != "0.0" {
		t.Fatalf("plain subscription: %+v", pair)
	}
	pair, err = paymentPaidFlowStripeFees(context.Background(), 0, domain.InvoiceGeneratePayment{Amount: "25.00"})
	if err != nil || pair.membership.memory != "0.0" || pair.activity.memory != "25.00" {
		t.Fatalf("other subject: %+v %v", pair, err)
	}
	bad := `[{"amount": [1]}]`
	if _, err = paymentPaidFlowStripeFees(context.Background(), 1, domain.InvoiceGeneratePayment{Amount: "5.00", MetaPaymentCategories: &bad}); err == nil {
		t.Fatal("float() of a list must fail")
	}
}

// The view decodes the payment's columns when it fetches the row: a meta payment
// categories document nested past the reference parser's 1000 levels is read
// there, so with the view's measured edges the fee arithmetic reads it too.
// Without them the reference parser answers as before.
func TestPaymentPaidFlowStripeFeesStoredJSONDepth(t *testing.T) {
	deep := `[{"amount": "4.50", "unused": ` + strings.Repeat("[", 5000) + strings.Repeat("]", 5000) + `}]`
	if _, err := paymentPaidFlowStripeFees(context.Background(), 1, domain.InvoiceGeneratePayment{Amount: "30.00",
		MetaPaymentCategories: &deep}); err == nil {
		t.Fatal("without measured edges the reference parser must refuse")
	}
	ctx := withPaymentJSONEdges(context.Background(), stripeWebhookJSONEdges)
	pair, err := paymentPaidFlowStripeFees(ctx, 1, domain.InvoiceGeneratePayment{Amount: "30.00", MetaPaymentCategories: &deep})
	if err != nil {
		t.Fatal(err)
	}
	if pair.activity.memory != "4.5" || pair.membership.memory != "25.5" {
		t.Fatalf("stored depth: %+v", pair)
	}
}
