package service

import (
	"errors"
	"math"
	"strings"
	"testing"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/pyzipfile"
)

// TestRestoreJSONLiteralsAndLimits pins json.loads outcomes measured under
// CPython 3.13.16: literals glued to other characters fail, an escaped
// backslash before a lone surrogate is an invalid escape, integers past 4300
// digits raise ValueError, and nesting below the C band parses.
func TestRestoreJSONLiteralsAndLimits(t *testing.T) {
	for _, text := range []string{"[1NaN]", "[NaN.5]", "[-NaN]", "[NaNx]", "[Infinity0]", "[" + strings.Repeat("1", 4301) + "]",
		"[" + strings.Repeat("[", 9998) + strings.Repeat("]", 9998) + "]"} {
		if _, err := restoreJSON([]byte(text)); err != errRestoreDamaged {
			t.Errorf("%.20q error = %v, want damaged", text, err)
		}
	}
	// "\" followed by U+D800 (surrogatepass UTF-8 bytes).
	if _, err := restoreJSON([]byte("[\"\\\xed\xa0\x80\"]")); err != errRestoreDamaged {
		t.Errorf("escaped surrogate error = %v, want damaged", err)
	}
	value, err := restoreJSON([]byte(`{"a": [NaN, Infinity, -Infinity, 1e999], "b": "` + "\xed\xa0\x80" + `"}`))
	if err != nil {
		t.Fatal(err)
	}
	object := value.(domain.RequestObject)
	items := object[0].Value.([]any)
	if f, _ := items[0].(float64); !math.IsNaN(f) || items[1] != math.Inf(1) || items[2] != math.Inf(-1) || items[3] != math.Inf(1) {
		t.Errorf("literals = %#v", items)
	}
	if object[1].Value != "\xed\xa0\x80" {
		t.Errorf("lone surrogate = %q", object[1].Value)
	}
	if _, err := restoreJSON([]byte("[" + strings.Repeat("1", 4300) + "]")); err != nil {
		t.Errorf("4300 digits: %v", err)
	}
	if _, err := restoreJSON([]byte(strings.Repeat("[", 2000) + strings.Repeat("]", 2000))); err != nil {
		t.Errorf("2000 levels: %v", err)
	}
	band := strings.Repeat("[", 9900) + strings.Repeat("]", 9900)
	if _, err := restoreJSON([]byte(band)); !errors.Is(err, dto.ErrJSONRecursionBand) {
		t.Errorf("9900 levels: %v", err)
	}
	// Each literal keeps its length: a member of NaNs does not grow.
	many := []byte("[" + strings.Repeat("NaN,", 1<<16) + "NaN]")
	if value, err := restoreJSON(many); err != nil || len(value.([]any)) != 1<<16+1 {
		t.Errorf("NaN list: %v", err)
	}
}

// TestRestoreStreamError: iter_json_array's failures in skipped_records.
func TestRestoreStreamError(t *testing.T) {
	cases := []struct {
		err  error
		want error
	}{
		{dto.ErrJSONRecursionBand, dto.ErrJSONRecursionBand},
		{&pyzipfile.Exception{Type: "EOFError", Text: "Compressed file ended before the end-of-stream marker was reached"}, errRestoreReaderRaised},
		{&pyzipfile.Exception{Type: "BadZipFile", Text: "Bad CRC-32 for file 'x'"}, errRestoreDamaged},
		{errors.New("Expecting value"), errRestoreDamaged},
		{errRestoreDamaged, errRestoreDamaged},
	}
	for _, c := range cases {
		if got := restoreStreamError(c.err); !errors.Is(got, c.want) {
			t.Errorf("restoreStreamError(%v) = %v, want %v", c.err, got, c.want)
		}
	}
}
