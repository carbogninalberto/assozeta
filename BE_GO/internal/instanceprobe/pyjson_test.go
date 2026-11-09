package instanceprobe

import (
	"errors"
	"math"
	"testing"
)

// TestLoadJSON pins json.loads(bytes) behaviour Go's encoding/json does not
// share: NaN/Infinity, the int digit limit, surrogates, BOM and encodings.
func TestLoadJSON(t *testing.T) {
	for _, tc := range []struct {
		raw  string
		ok   bool
		want any
	}{
		{`NaN`, true, nil}, {`-Infinity`, true, math.Inf(-1)}, {`1e400`, true, math.Inf(1)},
		{"\xef\xbb\xbf[1]", true, nil}, {`"🚀"`, true, "🚀"}, {`"\ud800x"`, true, "\xed\xa0\x80x"},
		{`[1,]`, false, nil}, {`01`, false, nil}, {"\"a\tb\"", false, nil}, {`{"a":1} x`, false, nil},
		{`"\x"`, false, nil}, {"\xff", false, nil}, {`-`, false, nil}, {`1.`, false, nil},
	} {
		value, err := LoadJSON([]byte(tc.raw))
		if (err == nil) != tc.ok {
			t.Errorf("%q: err %v", tc.raw, err)
			continue
		}
		if s, isString := tc.want.(string); isString && value != s {
			t.Errorf("%q: %q", tc.raw, value)
		}
		if f, isFloat := tc.want.(float64); isFloat && value != f {
			t.Errorf("%q: %v", tc.raw, value)
		}
	}
	digits := make([]byte, 4301)
	for i := range digits {
		digits[i] = '7'
	}
	if _, err := LoadJSON(digits); !errors.Is(err, ErrJSON) {
		t.Errorf("4301 digits: %v", err)
	}
	if _, err := LoadJSON(digits[:4300]); err != nil {
		t.Errorf("4300 digits: %v", err)
	}
	if _, err := LoadJSON([]byte("\xff\xfe[\x00]\x00")); !errors.Is(err, ErrJSONEncoding) {
		t.Errorf("UTF-16: %v", err)
	}
	if !HasSurrogate("\xed\xa0\x80") || HasSurrogate("🚀") {
		t.Error("HasSurrogate")
	}
	if tuple, ok := VersionTuple("v1.1٣.0"); !ok || tuple[1].Int64() != 13 {
		t.Error("VersionTuple Unicode digits")
	}
	for _, tag := range []string{"v1.٣.0", "v01.2.3", "1.2", "v1.2.3\n", "V1.2.3"} {
		if _, ok := VersionTuple(tag); ok {
			t.Errorf("VersionTuple(%q) accepted", tag)
		}
	}
}
