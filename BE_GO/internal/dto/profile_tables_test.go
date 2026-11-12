package dto

import (
	"encoding/json"
	"errors"
	"testing"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

func TestProfileTablesInput(t *testing.T) {
	for _, value := range []any{domain.RequestObject{{Name: "columns", Value: []any{"name", false}}}, []any{json.Number("9007199254740993")}, "plain", false, json.Number("1.5")} {
		got, err := ParseProfileTables(domain.RequestObject{{Name: "tables_settings", Value: value}}, false)
		if err != nil || !got.Present {
			t.Fatal(got, err)
		}
	}
	for _, body := range []any{domain.RequestObject{}, domain.RequestObject{{Name: "password", Value: "ignored"}}} {
		got, err := ParseProfileTables(body, false)
		if err != nil || got.Present {
			t.Fatal(got, err)
		}
	}
	for _, tc := range []struct {
		body any
		form bool
		code string
	}{
		{[]any{}, false, "object"}, {"text", false, "object"}, {nil, false, "null-body"},
		{domain.RequestObject{{Name: "tables_settings"}}, false, "null"},
		{domain.RequestObject{{Name: "tables_settings", Value: json.Number("1e309")}}, false, "json"},
		{domain.RequestObject{{Name: "tables_settings", Value: domain.RequestObject{{Name: "x", Value: json.Number("1e309")}}}}, false, "json"},
		{domain.RequestObject{{Name: "tables_settings", Value: "invalid"}}, true, "json"},
		{domain.RequestObject{{Name: "tables_settings", Value: "NaN"}}, true, "json"},
	} {
		_, err := ParseProfileTables(tc.body, tc.form)
		var validation *ProfileTablesValidationError
		if !errors.As(err, &validation) || validation.Code != tc.code {
			t.Fatal(tc, err)
		}
	}
	for _, raw := range []string{`{"columns":[true]}`, `"literal"`, `false`, `1.5`, `null`, `1e309`} {
		got, err := ParseProfileTables(domain.RequestObject{{Name: "tables_settings", Value: raw}}, true)
		if err != nil || !got.Present {
			t.Fatal(raw, got, err)
		}
		if (got.Value == nil) != (raw == "null") {
			t.Fatal(raw, got)
		}
	}
}
