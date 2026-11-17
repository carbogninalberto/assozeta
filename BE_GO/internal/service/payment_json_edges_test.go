package service

import (
	"context"
	"errors"
	"strings"
	"testing"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/refusal"
)

const (
	recursionObjectText = "maximum recursion depth exceeded while decoding a JSON object from a unicode string"
	recursionArrayText  = "maximum recursion depth exceeded while decoding a JSON array from a unicode string"
)

func jsonEdgesArray(depth int) *string {
	text := strings.Repeat("[", depth) + strings.Repeat("]", depth)
	return &text
}

func jsonEdgesObject(depth int) *string {
	text := strings.Repeat(`{"a": `, depth) + "0" + strings.Repeat("}", depth)
	return &text
}

func TestPaymentJSONColumnsDepth(t *testing.T) {
	category, name, description := "category", "Category", "Description"
	for _, test := range []struct {
		name                     string
		categories, meta, course *string
		want                     int
	}{
		{name: "nulls", want: 0},
		{name: "categories only", categories: jsonEdgesArray(3), want: 3},
		{name: "deepest wins", categories: jsonEdgesArray(2), meta: jsonEdgesObject(4), course: jsonEdgesArray(1), want: 4},
		{name: "scalars are depth zero", categories: jsonEdgesArray(0), meta: jsonEdgesArray(0), course: jsonEdgesArray(0), want: 0},
	} {
		row := &domain.PaymentSaveRow{ID: "payment", CategoryID: &category, CategoryName: &name, Description: &description,
			MetaPaymentCategories: test.categories, Meta: test.meta, Course: test.course}
		if got := paymentJSONColumnsDepth(row); got != test.want {
			t.Errorf("%s: depth=%d want %d", test.name, got, test.want)
		}
	}
}

func TestPythonJSONRecursionText(t *testing.T) {
	for _, test := range []struct {
		name string
		text string
		edge int
		want string
	}{
		{name: "object", text: `{"a": {"b": 1}}`, edge: 1, want: recursionObjectText},
		{name: "array", text: `[[1]]`, edge: 1, want: recursionArrayText},
		{name: "first level deep object", text: `{"a": 1}`, edge: 0, want: recursionObjectText},
		{name: "bracket inside a string is skipped", text: `{"a": "[", "b": {"c": 1}}`, edge: 1, want: recursionObjectText},
		{name: "escaped quote keeps the string open", text: `{"a": "\"[", "b": [1]}`, edge: 1, want: recursionArrayText},
		{name: "escaped backslash ends the escape", text: `{"a": "\\", "b": [1]}`, edge: 1, want: recursionArrayText},
		{name: "deeper edge", text: `[[[1]]]`, edge: 2, want: recursionArrayText},
	} {
		if got := pythonJSONRecursionText(test.text, test.edge); got != test.want {
			t.Errorf("%s: text %q edge %d: %q want %q", test.name, test.text, test.edge, got, test.want)
		}
	}
}

// paymentJSONEdgesTx is the transaction the save defaults read nothing from;
// every test row already has a category.
type paymentJSONEdgesTx struct{}

func (paymentJSONEdgesTx) OwnerAssociation(context.Context, string) (string, error) { return "", nil }
func (paymentJSONEdgesTx) PaymentForArchive(context.Context, string, string, *string) (domain.PaymentSaveRow, error) {
	return domain.PaymentSaveRow{}, nil
}
func (paymentJSONEdgesTx) DefaultPaymentCategory(context.Context, *string) (*string, *string, error) {
	return nil, nil, errors.New("category lookup unexpected")
}
func (paymentJSONEdgesTx) PaymentLinkedName(context.Context, string) (domain.PaymentLinkedName, error) {
	return domain.PaymentLinkedName{}, nil
}
func (paymentJSONEdgesTx) SavePaymentArchive(context.Context, string, bool, *string, *string) error {
	return nil
}
func (paymentJSONEdgesTx) AppendPaymentAudit(context.Context, domain.PaymentArchiveEvent) error {
	return nil
}
func (paymentJSONEdgesTx) Commit() error   { return nil }
func (paymentJSONEdgesTx) Rollback() error { return nil }

func paymentJSONEdgesSave(ctx context.Context, row *domain.PaymentSaveRow) error {
	return secCategorySaveDefaults(ctx, paymentJSONEdgesTx{}, row, nil, map[string][2]string{})
}

func paymentJSONEdgesRow(meta *string) *domain.PaymentSaveRow {
	category, description := "category", "Description"
	return &domain.PaymentSaveRow{ID: "payment", CategoryID: &category, CategoryName: &category,
		Description: &description, Meta: meta}
}

// A depth one below, at, and one past each edge: a deeper load only fails at
// its own edge, and the failure knows whether the view's earlier writes stay.
func TestPaymentArchiveJSONEdgesOutcomes(t *testing.T) {
	for _, test := range []struct {
		name      string
		edges     paymentJSONEdges
		depth     int
		recursion bool
		after     bool
		fallback  bool
	}{
		{name: "load below", edges: paymentJSONEdges{Load: 5, Save: 5, Index: 5}, depth: 4},
		{name: "load at", edges: paymentJSONEdges{Load: 5, Save: 5, Index: 5}, depth: 5},
		{name: "load past", edges: paymentJSONEdges{Load: 5, Save: 5, Index: 5}, depth: 6, recursion: true},

		{name: "save below", edges: paymentJSONEdges{Load: 9, Save: 5, Index: 5}, depth: 4},
		{name: "save at", edges: paymentJSONEdges{Load: 9, Save: 5, Index: 5}, depth: 5},
		{name: "save past", edges: paymentJSONEdges{Load: 9, Save: 5, Index: 5}, depth: 6, recursion: true, after: true},

		{name: "index below", edges: paymentJSONEdges{Load: 9, Save: 9, Index: 5}, depth: 4},
		{name: "index at", edges: paymentJSONEdges{Load: 9, Save: 9, Index: 5}, depth: 5},
		{name: "index past", edges: paymentJSONEdges{Load: 9, Save: 9, Index: 5}, depth: 6, fallback: true},
	} {
		row := paymentJSONEdgesRow(jsonEdgesArray(test.depth))
		ctx := withPaymentJSONEdges(context.Background(), test.edges)
		err := paymentJSONEdgesSave(ctx, row)
		if row.JSONDepth != test.depth {
			t.Errorf("%s: JSONDepth=%d want %d", test.name, row.JSONDepth, test.depth)
		}
		if !test.recursion {
			if err != nil {
				t.Errorf("%s: err=%v", test.name, err)
			}
			if row.IndexFallback != test.fallback {
				t.Errorf("%s: IndexFallback=%v want %v", test.name, row.IndexFallback, test.fallback)
			}
			continue
		}
		var recursionErr *PaymentStoredJSONRecursionError
		if !errors.As(err, &recursionErr) {
			t.Fatalf("%s: err=%v, want the recursion error", test.name, err)
		}
		if recursionErr.AfterWrites != test.after {
			t.Errorf("%s: AfterWrites=%v want %v", test.name, recursionErr.AfterWrites, test.after)
		}
		if recursionErr.Text != recursionArrayText {
			t.Errorf("%s: Text=%q want %q", test.name, recursionErr.Text, recursionArrayText)
		}
		if !errors.Is(err, refusal.ErrAppState) || errors.Is(err, refusal.ErrNotPorted) {
			t.Errorf("%s: err=%v, want an application-state error", test.name, err)
		}
		if test.fallback || row.IndexFallback {
			t.Errorf("%s: IndexFallback=%v on a failed load", test.name, row.IndexFallback)
		}
	}
}

// The error text names the container at level edge+1 of the first column in
// document order (meta_payment_categories, meta, course) past the edge.
func TestPaymentArchiveJSONRecursionTextFirstColumn(t *testing.T) {
	category, description := "category", "Description"
	row := &domain.PaymentSaveRow{ID: "payment", CategoryID: &category, CategoryName: &category, Description: &description,
		MetaPaymentCategories: jsonEdgesArray(6), Meta: jsonEdgesObject(7), Course: jsonEdgesArray(8)}
	err := paymentJSONEdgesSave(withPaymentJSONEdges(context.Background(), paymentJSONEdges{Load: 5, Save: 5, Index: 5}), row)
	var recursionErr *PaymentStoredJSONRecursionError
	if !errors.As(err, &recursionErr) || recursionErr.Text != recursionArrayText {
		t.Fatalf("err=%v, want the categories' array text", err)
	}

	row = &domain.PaymentSaveRow{ID: "payment", CategoryID: &category, CategoryName: &category, Description: &description,
		MetaPaymentCategories: jsonEdgesArray(4), Meta: jsonEdgesObject(6), Course: jsonEdgesArray(7)}
	err = paymentJSONEdgesSave(withPaymentJSONEdges(context.Background(), paymentJSONEdges{Load: 5, Save: 5, Index: 5}), row)
	if !errors.As(err, &recursionErr) || recursionErr.Text != recursionObjectText {
		t.Fatalf("err=%v, want meta's object text", err)
	}
}

// A depth within the save edge parses through the measured limit, including
// past the former 1000-level reference parser limit.
func TestPaymentArchiveJSONEdgesParsesAtMeasuredDepth(t *testing.T) {
	row := paymentJSONEdgesRow(jsonEdgesArray(2000))
	if err := paymentJSONEdgesSave(withPaymentJSONEdges(context.Background(), paymentJSONEdges{Load: 2001, Save: 2001, Index: 2001}), row); err != nil {
		t.Fatalf("err=%v", err)
	}
	if row.IndexFallback {
		t.Fatal("the depth is not past the index edge")
	}
}

// Without measured edges the save hook keeps the reference parser's handoff.
func TestPaymentArchiveJSONEdgesUnmeasuredKeepsHandoff(t *testing.T) {
	row := paymentJSONEdgesRow(jsonEdgesArray(1002))
	err := paymentJSONEdgesSave(context.Background(), row)
	if !errors.Is(err, errPaymentStoredJSONNested) || errors.Is(err, refusal.ErrAppState) {
		t.Fatalf("err=%v, want the nested handoff", err)
	}
}
