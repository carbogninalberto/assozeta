package repository

import (
	"context"
	"database/sql/driver"
	"fmt"
	"strings"
	"testing"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

const (
	racePayment    = "a1000000-0000-4000-8000-000000000501"
	racePayment2   = "a1000000-0000-4000-8000-000000000502"
	raceInvoice    = "a1000000-0000-4000-8000-000000000503"
	raceInstalment = "a1000000-0000-4000-8000-000000000504"
)

func subscriptionLifecycleRaceTransaction(t *testing.T, d *rowRaceDriver) *subscriptionLifecycleTransaction {
	t.Helper()
	shared := &paymentCategoryTransaction{ownedTransaction: &ownedTransaction{tx: rowRaceTestTransaction(t, d)}}
	return &subscriptionLifecycleTransaction{courseSubscriptionTransaction: &courseSubscriptionTransaction{paymentCategoryTransaction: shared}}
}

// lifecycleRowGone answers the two reads snapshot() makes: the table's
// columns, then no row.
func lifecycleRowGone(query string, _ []driver.NamedValue) ([]string, [][]driver.Value, error) {
	switch {
	case strings.Contains(query, "FROM pg_attribute"):
		return []string{"name", "type", "foreign_key"}, nil, nil
	case strings.Contains(query, "SELECT to_jsonb(t)"):
		return []string{"to_jsonb"}, nil, nil
	}
	return nil, nil, fmt.Errorf("unexpected query: %s", query)
}

func expectNoExec(t *testing.T, d *rowRaceDriver) {
	t.Helper()
	if _, execs := d.statements(); len(execs) != 0 {
		t.Fatalf("a gone row must not be deleted or audited: %#v", execs)
	}
}

// TestDeletePaymentsSkipsRowsDeletedBeforeTheDelete: the ids come from an
// earlier range lookup; Django's QuerySet.delete() re-runs the query, so a row
// deleted in between is simply not part of the collector (no delete, no entry).
func TestDeletePaymentsSkipsRowsDeletedBeforeTheDelete(t *testing.T) {
	d := &rowRaceDriver{queryFn: lifecycleRowGone}
	tx := subscriptionLifecycleRaceTransaction(t, d)
	if err := tx.DeletePayments(context.Background(), []string{racePayment, racePayment2}, domain.CourseSubscriptionAudit{}); err != nil {
		t.Fatalf("a payment row deleted between the reads is not an error: %v", err)
	}
	expectNoExec(t, d)
	if queries, _ := d.statements(); len(queries) != 4 {
		t.Fatalf("expected one snapshot per id, got %#v", queries)
	}
}

// TestDeletePaymentsContinuesWithPresentRows: one selected payment is gone,
// the other is still stored: the delete and its audit entry happen for the
// present row only.
func TestDeletePaymentsContinuesWithPresentRows(t *testing.T) {
	grantedColumns := []string{"name", "type", "foreign_key"}
	grantedRows := [][]driver.Value{{"payment_id", "uuid", false}, {"sport_association_id", "uuid", true}, {"paid", "boolean", false}}
	row := []byte(`{"payment_id":"` + racePayment2 + `","sport_association_id":null,"paid":false}`)
	d := &rowRaceDriver{
		queryFn: func(query string, args []driver.NamedValue) ([]string, [][]driver.Value, error) {
			switch {
			case strings.Contains(query, "FROM pg_attribute"):
				return grantedColumns, grantedRows, nil
			case strings.Contains(query, "SELECT to_jsonb(t) FROM application_payment"):
				if len(args) == 1 && args[0].Value == racePayment2 {
					return []string{"to_jsonb"}, [][]driver.Value{{row}}, nil
				}
				return []string{"to_jsonb"}, nil, nil
			case strings.Contains(query, "FROM application_coursesubscriptioninstallment"):
				return []string{"course_subscription_installment_id"}, nil, nil
			case strings.Contains(query, "information_schema.referential_constraints"):
				return []string{"table_name", "column_name"}, nil, nil
			case strings.Contains(query, "INSERT INTO auditlog_logentry"):
				return []string{"id"}, [][]driver.Value{{int64(7)}}, nil
			}
			return nil, nil, fmt.Errorf("unexpected query: %s", query)
		},
		execFn: func(query string, _ []driver.NamedValue) (driver.Result, error) {
			if strings.HasPrefix(query, "DELETE FROM application_payment") {
				return driver.RowsAffected(1), nil
			}
			return nil, fmt.Errorf("unexpected exec: %s", query)
		},
	}
	tx := subscriptionLifecycleRaceTransaction(t, d)
	if err := tx.DeletePayments(context.Background(), []string{racePayment, racePayment2}, domain.CourseSubscriptionAudit{}); err != nil {
		t.Fatalf("delete the present payment only: %v", err)
	}
	queries, execs := d.statements()
	if len(execs) != 1 || !strings.HasPrefix(execs[0].query, "DELETE FROM application_payment") ||
		execs[0].args[0].Value != racePayment2 {
		t.Fatalf("the present payment must still be deleted: %#v", execs)
	}
	audited := 0
	for _, query := range queries {
		if strings.Contains(query.query, "INSERT INTO auditlog_logentry") {
			audited++
		}
	}
	if audited != 1 {
		t.Fatalf("one delete entry for the present payment, got %d", audited)
	}
}

// TestDeleteInvoicedPaymentSkipsPaymentDeletedBeforeTheDelete: the payment the
// installment pointed at is gone, so there is no delete and no in-memory
// invoice reference to render.
func TestDeleteInvoicedPaymentSkipsPaymentDeletedBeforeTheDelete(t *testing.T) {
	d := &rowRaceDriver{queryFn: lifecycleRowGone}
	tx := subscriptionLifecycleRaceTransaction(t, d)
	if err := tx.DeleteInvoicedPayment(context.Background(), racePayment, raceInvoice, domain.CourseSubscriptionAudit{}); err != nil {
		t.Fatalf("a payment row deleted between the reads is not an error: %v", err)
	}
	expectNoExec(t, d)
}

// TestDeleteLoopInstallmentSkipsInstallmentDeletedBeforeTheDelete: the
// installment is no longer selected, so the loop does nothing for it.
func TestDeleteLoopInstallmentSkipsInstallmentDeletedBeforeTheDelete(t *testing.T) {
	d := &rowRaceDriver{queryFn: lifecycleRowGone}
	tx := subscriptionLifecycleRaceTransaction(t, d)
	payment := racePayment
	installment := domain.SubscriptionLifecycleInstallment{ID: raceInstalment, PaymentID: &payment}
	if err := tx.DeleteLoopInstallment(context.Background(), installment, nil, domain.CourseSubscriptionAudit{}); err != nil {
		t.Fatalf("an installment row deleted between the reads is not an error: %v", err)
	}
	expectNoExec(t, d)
}

// TestCollectorDeleteSkipsRowDeletedBeforeTheDelete: Model.delete() of a row
// that is gone collects nothing, so nothing is deleted.
func TestCollectorDeleteSkipsRowDeletedBeforeTheDelete(t *testing.T) {
	d := &rowRaceDriver{queryFn: func(query string, _ []driver.NamedValue) ([]string, [][]driver.Value, error) {
		return []string{"key", "row"}, nil, nil
	}}
	tx := subscriptionLifecycleRaceTransaction(t, d)
	if err := tx.DeleteInvoice(context.Background(), raceInvoice, domain.CourseSubscriptionAudit{}); err != nil {
		t.Fatalf("an invoice row deleted between the reads is not an error: %v", err)
	}
	expectNoExec(t, d)
}
