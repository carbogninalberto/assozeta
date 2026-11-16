package service

import (
	"encoding/json"
	"errors"
	"strings"
	"testing"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/xlsxexport"
)

// The module elements band (owner decision X15): psycopg2 loads
// module.elements with json.loads, so 9933 levels answer as today and 9934 are
// the export's unhandled exception (the 500 page). The deep value sits in a
// props key the export does not read, so only the band decides the answer; the
// band is the harness's measured one, not a number this test derives.
func TestModulesExportElementsJSONDepthBand(t *testing.T) {
	const measured = 9933
	if modulesExportElementsJSONDepth != measured {
		t.Fatalf("band: %d, want %d", modulesExportElementsJSONDepth, measured)
	}
	elements := func(levels int) string {
		return `[{"type": "heading", "props": {"label": "Titolo", "unused": ` + strings.Repeat("[", levels) +
			strings.Repeat("]", levels) + `}}, {"type": "text", "props": {"label": "Nome", "name": "nome"}}]`
	}
	// The elements array, its element object and the props object are 3 levels.
	atBand := elements(measured - 3)
	overBand := elements(measured - 2)
	if dto.AuditJSONDepth(atBand) != measured || dto.AuditJSONDepth(overBand) != measured+1 {
		t.Fatalf("authored depths: %d %d", dto.AuditJSONDepth(atBand), dto.AuditJSONDepth(overBand))
	}
	columns, err := modulesExportColumns(&atBand)
	if err != nil || len(columns) != 1 || columns[0].label != "Nome" || columns[0].name != "nome" {
		t.Fatalf("at the band: %#v %v", columns, err)
	}
	if _, err := modulesExportColumns(&overBand); !errors.Is(err, domain.ErrModulesExportServer) {
		t.Fatalf("past the band: %v", err)
	}
	// A nil column (module.elements is NOT NULL) is not iterable, as in Django.
	if _, err := modulesExportColumns(nil); !errors.Is(err, domain.ErrModulesExportServer) {
		t.Fatalf("absent column: %v", err)
	}
}

// The module responses band (owner decision X15): the export loads every
// response column with psycopg2's json.loads, so a response of 9935 levels
// answers as today and 9936 are the export's unhandled exception (the 500
// page). Measured on {"nome": "Anna", "deep": <nested objects>} on the parity
// harness's Django; the band is that measured one, not a number this test
// derives.
func TestModulesExportResponseJSONDepthBand(t *testing.T) {
	const measured = 9935
	if modulesExportResponseJSONDepth != measured {
		t.Fatalf("band: %d, want %d", modulesExportResponseJSONDepth, measured)
	}
	response := func(depth int) string {
		// The outer object is one level; each "deep" object adds one.
		return `{"nome": "Anna", "deep": ` + strings.Repeat(`{"a": `, depth-2) + "{}" + strings.Repeat("}", depth-2) + "}"
	}
	atBand, overBand := response(measured), response(measured+1)
	if dto.AuditJSONDepth(atBand) != measured || dto.AuditJSONDepth(overBand) != measured+1 {
		t.Fatalf("authored depths: %d %d", dto.AuditJSONDepth(atBand), dto.AuditJSONDepth(overBand))
	}
	value, err := modulesExportResponse(&atBand)
	if err != nil {
		t.Fatalf("at the band: %v", err)
	}
	object, isObject := value.(domain.RequestObject)
	if !isObject {
		t.Fatalf("at the band: %#v", value)
	}
	if name, _ := object.Lookup("nome"); name != "Anna" {
		t.Fatalf("at the band: nome=%v", name)
	}
	if _, err := modulesExportResponse(&overBand); !errors.Is(err, domain.ErrModulesExportServer) {
		t.Fatalf("past the band: %v", err)
	}
	// A nil column is a nil value, as Django's JSONField reads SQL NULL.
	if value, err := modulesExportResponse(nil); err != nil || value != nil {
		t.Fatalf("absent column: %#v %v", value, err)
	}
}

// A response value that is a JSON integer: json.loads gives an int of any
// size, and the frame keeps one beyond int64 for xlsxwriter's float(number).
func TestModulesExportValueIntegers(t *testing.T) {
	huge := "1" + strings.Repeat("0", 400)
	for _, item := range []struct {
		text string
		want xlsxexport.Value
	}{
		{"0", xlsxexport.IntValue(0)},
		{"30", xlsxexport.IntValue(30)},
		{"-9223372036854775808", xlsxexport.IntValue(-9223372036854775808)},
		{"9223372036854775808", xlsxexport.BigIntValue("9223372036854775808")},
		{"123456789012345678901234567890", xlsxexport.BigIntValue("123456789012345678901234567890")},
		{huge, xlsxexport.BigIntValue(huge)},
	} {
		got, err := modulesExportValue(json.Number(item.text))
		if err != nil || got != item.want {
			t.Fatalf("%s: got (%+v, %v), want %+v", item.text, got, err, item.want)
		}
	}
}
