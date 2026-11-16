package dto

import (
	"errors"
	"strings"
	"testing"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/refusal"
)

// The X15 bands are measured on the parity harness's Django (CPython 3.13.16):
// detail answers up to frame 954, the full list up to 953; a stored column is
// read up to depth 9935 (detail) or 9934 (list). The edges below are written as
// measured (not through the endpoint constants) so that a changed constant is
// caught by TestAuditBandConstants and a changed frame or depth rule by the
// edges themselves.

var (
	testDetailBand = AuditBand{Frames: 954, Depth: 9935}
	testListBand   = AuditBand{Frames: 953, Depth: 9934}
)

// auditDeepLists is n nested lists whose innermost list is empty.
func auditDeepLists(n int) []any {
	value := []any{}
	for i := 1; i < n; i++ {
		value = []any{value}
	}
	return value
}

func auditDeepText(n int) string {
	return strings.Repeat("[", n) + strings.Repeat("]", n)
}

func auditDeepLeafText(n int) string {
	return strings.Repeat("[", n) + `"x"` + strings.Repeat("]", n)
}

// auditBandOutOfRange reports the band's refusal: the plain 500 page, never the
// handoff sentinel.
func auditBandOutOfRange(t *testing.T, name string, err error) {
	t.Helper()
	if !errors.Is(err, ErrAuditUnhandled) || errors.Is(err, refusal.ErrNotPorted) {
		t.Fatalf("%s: want ErrAuditUnhandled, got %v", name, err)
	}
}

func TestAuditBandConstants(t *testing.T) {
	detail, list := AuditDetailBand(), AuditListBand()
	if detail.Frames != 954 || detail.Depth != 9935 || list.Frames != 953 || list.Depth != 9934 {
		t.Fatalf("bands: %+v %+v", detail, list)
	}
	if detail != testDetailBand || list != testListBand {
		t.Fatalf("the endpoint bands moved from the measured values: %+v %+v", detail, list)
	}
}

func TestAuditEnrichFrameBandEdges(t *testing.T) {
	cases := []struct {
		band AuditBand
		name string
		text int // nesting of a list stored as a JSON string value
		raw  int // nesting of a raw list
		leaf int // nesting of a string leaf
	}{
		// Detail: measured string-encoded n <= 953, raw n <= 954, leaf n <= 951.
		{testDetailBand, "detail", 953, 954, 951},
		// List (full serializer): one frame shallower, so one level less each.
		{testListBand, "list", 952, 953, 950},
	}
	for _, test := range cases {
		if resolved, err := AuditEnrich(auditDeepText(test.text), "deep", noResolver{}, test.band); err != nil || resolved == nil {
			t.Errorf("%s: string-encoded %d levels: %v", test.name, test.text, err)
		}
		_, err := AuditEnrich(auditDeepText(test.text+1), "deep", noResolver{}, test.band)
		auditBandOutOfRange(t, test.name+": string-encoded +1", err)

		if _, err := AuditEnrich(auditDeepLists(test.raw), "deep", noResolver{}, test.band); err != nil {
			t.Errorf("%s: raw %d levels: %v", test.name, test.raw, err)
		}
		if _, err := AuditEnrich(auditDeepLists(test.raw+1), "deep", noResolver{}, test.band); !errors.Is(err, ErrAuditUnhandled) {
			t.Errorf("%s: raw %d levels: want ErrAuditUnhandled, got %v", test.name, test.raw+1, err)
		}

		if _, err := AuditEnrich(auditDeepLeafText(test.leaf), "deep", noResolver{}, test.band); err != nil {
			t.Errorf("%s: leaf %d levels: %v", test.name, test.leaf, err)
		}
		_, err = AuditEnrich(auditDeepLeafText(test.leaf+1), "deep", noResolver{}, test.band)
		auditBandOutOfRange(t, test.name+": leaf +1", err)
	}
}

func TestAuditEnrichBooleanFieldDoesNotRecurse(t *testing.T) {
	// format_boolean is one frame and never walks the value: a raw list at the
	// column-load band answers "Sì" for either band.
	value := auditDeepLists(testDetailBand.Depth - 2)
	for _, band := range []AuditBand{testDetailBand, testListBand} {
		enriched, err := AuditEnrich(value, "paid", noResolver{}, band)
		if err != nil || enriched != "Sì" {
			t.Fatalf("paid: %v %v", enriched, err)
		}
	}
}

func TestAuditEnrichFormatterJSONLoadsDepthBand(t *testing.T) {
	// The events formatter's json.loads reads at the endpoint's total-depth band
	// and its result is a shallow string, so the band edges are visible.
	if formatted, err := AuditEnrich(auditDeepText(9935), "events", noResolver{}, testDetailBand); err != nil || formatted != "1 eventi" {
		t.Fatalf("detail depth band: %v %v", formatted, err)
	}
	_, err := AuditEnrich(auditDeepText(9936), "events", noResolver{}, testDetailBand)
	auditBandOutOfRange(t, "detail depth band +1", err)

	if _, err := AuditEnrich(auditDeepText(9934), "events", noResolver{}, testListBand); err != nil {
		t.Fatalf("list depth band: %v", err)
	}
	_, err = AuditEnrich(auditDeepText(9935), "events", noResolver{}, testListBand)
	auditBandOutOfRange(t, "list depth band +1", err)
}

func TestAuditJSONLoadsAtDepthBand(t *testing.T) {
	value, decoded, err := AuditJSONLoadsAt(auditDeepText(9935), 9935)
	if err != nil || !decoded || value == nil {
		t.Fatalf("detail depth band: %v %v", decoded, err)
	}
	_, _, err = AuditJSONLoadsAt(auditDeepText(9936), 9935)
	auditBandOutOfRange(t, "detail depth band +1", err)

	if _, decoded, err := AuditJSONLoadsAt(auditDeepText(9934), 9934); err != nil || !decoded {
		t.Fatalf("list depth band: %v %v", decoded, err)
	}
	_, _, err = AuditJSONLoadsAt(auditDeepText(9935), 9934)
	auditBandOutOfRange(t, "list depth band +1", err)

	// The 500-level readers of the other families are unchanged.
	if _, _, err := AuditJSONLoads(auditDeepText(501)); !errors.Is(err, ErrAuditNotPorted) {
		t.Fatalf("AuditJSONLoads must keep the handoff: %v", err)
	}
}

func TestAuditStoredJSONAtDepthBand(t *testing.T) {
	within := auditDeepText(9935)
	if value, err := AuditStoredJSONAt(&within, 9935); err != nil || value == nil {
		t.Fatalf("detail depth band: %v", err)
	}
	beyond := auditDeepText(9936)
	_, err := AuditStoredJSONAt(&beyond, 9935)
	auditBandOutOfRange(t, "detail depth band +1", err)

	if value, err := AuditStoredJSONAt(nil, 9935); err != nil || value != nil {
		t.Fatalf("NULL column: %v %v", value, err)
	}
}

// TestAuditResponseRendersColumnBand pins that the response path itself has no
// nesting limit below the column band (the renderer writes the raw changes and
// the enriched values).
func TestAuditResponseRendersColumnBand(t *testing.T) {
	value := auditDeepLists(9935)
	if got := string(PythonJSONDumpsCompact(value)); got != auditDeepText(9935) {
		t.Fatalf("compact dumps of 9935 levels: %d bytes", len(got))
	}
	if !AuditResponseEncodable(value) {
		t.Fatal("a column-band value must be encodable")
	}
}
