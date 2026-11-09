package dto

import (
	"encoding/hex"
	"encoding/json"
	"testing"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

// json.dumps({'operation_id': 'x', 'missing_relations': [], 'tutor_reassignments':
// [{'name': 'àè', 'n': None, 'f': 1.5, 'list': [1, {}]}]}, indent=2) in CPython 3.13.
func TestPythonJSONDumpsIndentMatchesCPython(t *testing.T) {
	want, _ := hex.DecodeString("7b0a2020226f7065726174696f6e5f6964223a202278222c0a2020226d697373696e675f72656c6174696f6e73223a205b5d2c0a2020227475746f725f726561737369676e6d656e7473223a205b0a202020207b0a202020202020226e616d65223a20225c75303065305c7530306538222c0a202020202020226e223a206e756c6c2c0a2020202020202266223a20312e352c0a202020202020226c697374223a205b0a2020202020202020312c0a20202020202020207b7d0a2020202020205d0a202020207d0a20205d0a7d")
	value := domain.RequestObject{{Name: "operation_id", Value: "x"}, {Name: "missing_relations", Value: []any{}},
		{Name: "tutor_reassignments", Value: []any{domain.RequestObject{{Name: "name", Value: "àè"}, {Name: "n", Value: nil},
			{Name: "f", Value: json.Number("1.5")}, {Name: "list", Value: []any{json.Number("1"), domain.RequestObject{}}}}}}}
	if got := PythonJSONDumpsIndent(value, 2); string(got) != string(want) {
		t.Fatalf("got\n%s\nwant\n%s", got, want)
	}
}
