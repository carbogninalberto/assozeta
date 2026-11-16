package repository

import (
	"strings"
	"testing"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

// TestPaymentReadsRandomSearchStatement is site 2's SQL: smart_search's
// distinct() with order_by('?') adds RANDOM() to the select list, so the page
// query joins the search rows instead of testing them with EXISTS and keeps
// the repeated payment ids; the count keeps the distinct count.
func TestPaymentReadsRandomSearchStatement(t *testing.T) {
	search := &domain.PaymentReadsSmartSearch{Words: []string{"duevolte"}, Dates: make([][]string, 1)}
	filter := domain.PaymentReadsFilter{AssociationID: "10000000-0000-4000-8000-000000000001", Random: true, Smart: search}
	args := personaArgs{}
	statement, err := paymentReadsPaymentStatement(filter, &args, 10, 0)
	if err != nil {
		t.Fatal(err)
	}
	for _, want := range []string{" FROM application_payment p\n LEFT JOIN application_associate sa ON sa.associate_id=p.associate_id",
		"LEFT JOIN application_associate st ON st.associate_id=sr.tutor_id", "ORDER BY RANDOM()", "LIMIT $", "OFFSET $"} {
		if !strings.Contains(statement, want) {
			t.Errorf("statement misses %q:\n%s", want, statement)
		}
	}
	if strings.Contains(statement, "EXISTS(") || strings.Contains(statement, "DISTINCT") {
		t.Errorf("statement must select the join rows without EXISTS or DISTINCT:\n%s", statement)
	}

	countArgs := personaArgs{}
	count := paymentReadsQuery(filter, &countArgs)
	if !strings.Contains(count, "EXISTS(SELECT 1") || strings.Contains(count, "FROM application_payment p\n LEFT JOIN application_associate sa") {
		t.Errorf("count query must keep the EXISTS search:\n%s", count)
	}

	// A term without a word adds no join: smart_search's Q() adds none either.
	empty := filter
	empty.Smart = &domain.PaymentReadsSmartSearch{}
	statement, err = paymentReadsPaymentStatement(empty, &personaArgs{}, 10, 0)
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(statement, "application_associate") {
		t.Errorf("a wordless search must not join:\n%s", statement)
	}

	// Random without a text search keeps the plain page query.
	plain := domain.PaymentReadsFilter{AssociationID: filter.AssociationID, Random: true}
	statement, err = paymentReadsPaymentStatement(plain, &personaArgs{}, 10, 0)
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(statement, "application_associate") {
		t.Errorf("random without a search must not join:\n%s", statement)
	}
}

// TestPaymentReadsFullNameStatement is site 3's SQL: the annotation's
// expression orders the subscription page when the text search joined ma.
func TestPaymentReadsFullNameStatement(t *testing.T) {
	text := "ordina"
	filter := domain.PaymentReadsFilter{AssociationID: "10000000-0000-4000-8000-000000000001", MemberText: &text,
		Order: []domain.PaymentReadsOrder{{Column: "full_name"}, {Column: "full_name", Descending: true}}}
	statement, err := paymentReadsPaymentStatement(filter, &personaArgs{}, 10, 0)
	if err != nil {
		t.Fatal(err)
	}
	want := "ORDER BY (COALESCE(ma.first_name, '') || ' ' || COALESCE(ma.last_name, ''))" +
		", (COALESCE(ma.first_name, '') || ' ' || COALESCE(ma.last_name, '')) DESC"
	if !strings.Contains(statement, want) {
		t.Errorf("statement misses %q:\n%s", want, statement)
	}

	// Without the text search Django has no annotation: checked_ordering refuses it before the query.
	filter = domain.PaymentReadsFilter{AssociationID: "10000000-0000-4000-8000-000000000001",
		Order: []domain.PaymentReadsOrder{{Column: "full_name"}}}
	if _, err := paymentReadsPaymentStatement(filter, &personaArgs{}, 10, 0); err == nil {
		t.Fatal("full_name without a text search must be refused")
	}
}
