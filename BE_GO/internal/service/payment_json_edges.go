package service

import (
	"context"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/refusal"
)

// paymentJSONEdges are the three nesting edges a view that saves a payment
// meets while Django loads the row's JSON columns with json.loads.
//
// The order is the one the request passes through them: Load is the view's own
// load of the row (deeper: RecursionError before the view writes anything),
// Save is the deepest load between that load and the row's UPDATE (auditlog's
// pre_save refetch of the old row, get_course_carnet_name, ...; deeper: the
// view's earlier writes stay and the save raises), and Index is the audit
// index resolver's reload of the row after auditlog's LogEntry INSERT, which
// runs inside except Exception (deeper: the save succeeds and the resolver
// falls back). Nesting is dto.AuditJSONDepth's total nesting ('[]' is 1).
//
// CPython's C JSON scanner raises RecursionError at a depth that depends on
// the C calls already on the stack, so there is no portable constant: Go
// answers what Django answers at the measured depths (decision X15). Each edge
// is measured on the parity harness's Django (CPython 3.13.16), per view; the
// measured value moves by up to two levels between requests, so parity cases
// sit at least 4 levels away from an edge.
type paymentJSONEdges struct{ Load, Save, Index int }

type paymentJSONEdgesKey struct{}

// withPaymentJSONEdges marks the request's view with its measured edges. The
// save hook reads them; a context without them keeps the former handoff.
func withPaymentJSONEdges(ctx context.Context, edges paymentJSONEdges) context.Context {
	return context.WithValue(ctx, paymentJSONEdgesKey{}, edges)
}

// paymentJSONLoadEdge is the view's measured load edge, 0 without edges (the
// callers then keep the 1000-level reference parser).
func paymentJSONLoadEdge(ctx context.Context) int {
	if edges, measured := paymentJSONEdgesOf(ctx); measured {
		return edges.Load
	}
	return 0
}

func paymentJSONEdgesOf(ctx context.Context) (paymentJSONEdges, bool) {
	edges, ok := ctx.Value(paymentJSONEdgesKey{}).(paymentJSONEdges)
	return edges, ok
}

// errPaymentStoredJSONRecursion is the recursion edge's application-state
// error: Django raises RecursionError here, which its production deployment
// answers with the 500 page, so Go answers the same page itself.
var errPaymentStoredJSONRecursion = refusal.AppState("payment save: stored JSON nested past the view's measured recursion edge")

// PaymentStoredJSONRecursionError is the RecursionError Django raises while
// loading a payment's stored JSON at the view's measured depth. AfterWrites is
// whether the view's writes made before payment.save() stay committed. Text is
// Python's str(RecursionError).
type PaymentStoredJSONRecursionError struct {
	AfterWrites bool
	Text        string
}

func (e *PaymentStoredJSONRecursionError) Error() string {
	if e.Text == "" {
		return errPaymentStoredJSONRecursion.Error()
	}
	return errPaymentStoredJSONRecursion.Error() + ": " + e.Text
}

// Unwrap keeps errors.Is(err, refusal.ErrAppState) true, so handlers answer
// Django's 500 page.
func (e *PaymentStoredJSONRecursionError) Unwrap() error { return errPaymentStoredJSONRecursion }

// paymentJSONColumnsDepth is the deepest nesting of the payment's three JSON
// columns, in the order the columns are loaded, 0 when all are NULL.
func paymentJSONColumnsDepth(row *domain.PaymentSaveRow) int {
	return paymentJSONTextsDepth(row.MetaPaymentCategories, row.Meta, row.Course)
}

// paymentJSONTextsDepth is paymentJSONColumnsDepth for callers that hold the
// three texts without a save row (a payment another query loaded).
func paymentJSONTextsDepth(metaPaymentCategories, meta, course *string) int {
	depth := 0
	for _, text := range [3]*string{metaPaymentCategories, meta, course} {
		if text == nil {
			continue
		}
		if column := dto.AuditJSONDepth(*text); column > depth {
			depth = column
		}
	}
	return depth
}

// paymentJSONColumns is the three JSON columns in document order.
func paymentJSONColumns(row *domain.PaymentSaveRow) [3]*string {
	return [3]*string{row.MetaPaymentCategories, row.Meta, row.Course}
}

// paymentJSONRecursionTextAt is Python's str(RecursionError) for the column
// the scanner reaches first whose depth exceeds edge.
func paymentJSONRecursionTextAt(row *domain.PaymentSaveRow, edge int) string {
	for _, text := range paymentJSONColumns(row) {
		if text != nil && dto.AuditJSONDepth(*text) > edge {
			return pythonJSONRecursionText(*text, edge)
		}
	}
	return ""
}

// pythonJSONRecursionText is Python's str(RecursionError) for a scanner that
// fails at nesting level edge+1: the message names the container ('{' or '[',
// outside strings) at that level that the scanner reaches first. A column
// deeper than edge always holds one.
func pythonJSONRecursionText(text string, edge int) string {
	const objectText = "maximum recursion depth exceeded while decoding a JSON object from a unicode string"
	const arrayText = "maximum recursion depth exceeded while decoding a JSON array from a unicode string"
	depth, quoted, escaped := 0, false, false
	for i := 0; i < len(text); i++ {
		c := text[i]
		switch {
		case escaped:
			escaped = false
		case quoted && c == '\\':
			escaped = true
		case c == '"':
			quoted = !quoted
		case !quoted && (c == '{' || c == '['):
			depth++
			if depth == edge+1 {
				if c == '{' {
					return objectText
				}
				return arrayText
			}
		case !quoted && (c == '}' || c == ']'):
			depth--
		}
	}
	return objectText
}
