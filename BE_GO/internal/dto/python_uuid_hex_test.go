package dto

import (
	"encoding/json"
	"strings"
	"testing"
)

// TestPythonUUIDLookup is UUIDField.to_python for stored JSON values, with the
// golden pairs recorded from CPython 3.13: uuid.UUID(hex=) reads
// int(text, 16) after the 32-code-point length check.
func TestPythonUUIDLookup(t *testing.T) {
	const base = "a1b2c3d4e5f60718293a4b5c6d7e8f90"
	const canonical = "a1b2c3d4-e5f6-0718-293a-4b5c6d7e8f90"
	const shifted = "01b2c3d4-e5f6-0718-293a-4b5c6d7e8f90"
	inputs := []struct {
		name  string
		input string
		want  string
	}{
		{"plain", base, canonical},
		{"uppercase", strings.ToUpper(base), canonical},
		{"hyphenated", "a1b2c3d4-e5f6-0718-293a-4b5c6d7e8f90", canonical},
		{"braced", "{a1b2c3d4-e5f6-0718-293a-4b5c6d7e8f90}", canonical},
		{"urn", "urn:uuid:a1b2c3d4-e5f6-0718-293a-4b5c6d7e8f90", canonical},
		{"arabic-digit-in-place", "a1b2c\u0663d4-e5f6-0718-293a-4b5c6d7e8f90", canonical},
		{"leading-space", " " + base[1:], shifted},
		{"trailing-space", base[1:] + " ", shifted},
		{"leading-tab", "\t" + base[1:], shifted},
		{"leading-information-separator", "\x1c" + base[1:], ""},
		{"leading-no-break-space", "\u00a0" + base[1:], shifted},
		{"leading-ideographic-space", "\u3000" + base[1:], shifted},
		{"leading-next-line", "\u0085" + base[1:], shifted},
		{"leading-line-separator", "\u2028" + base[1:], shifted},
		{"leading-vertical-tab", "\v" + base[1:], shifted},
		{"plus", "+" + base[1:], shifted},
		{"prefix-lower", "0x" + base[2:], "00b2c3d4-e5f6-0718-293a-4b5c6d7e8f90"},
		{"prefix-upper", "0X" + base[2:], "00b2c3d4-e5f6-0718-293a-4b5c6d7e8f90"},
		{"prefix-underscore", "0x_" + base[3:], "0002c3d4-e5f6-0718-293a-4b5c6d7e8f90"},
		{"plus-prefix", "+0x" + base[3:], "0002c3d4-e5f6-0718-293a-4b5c6d7e8f90"},
		{"leading-underscore", "_" + base[1:], ""},
		{"trailing-underscore", base[:31] + "_", ""},
		{"inner-underscore", base[:10] + "_" + base[11:], "0a1b2c3d-4e56-0718-293a-4b5c6d7e8f90"},
		{"doubled-underscore", base[:10] + "__" + base[12:], ""},
		{"inner-space", base[:10] + " " + base[11:], ""},
		{"fullwidth-letter", "\uFF21" + base[1:], ""},
		{"fullwidth-digit", "\uFF11" + base[1:], "11b2c3d4-e5f6-0718-293a-4b5c6d7e8f90"},
		{"arabic-indic-digits", strings.Repeat("\u0661", 32), "11111111-1111-1111-1111-111111111111"},
		{"zeros", strings.Repeat("0", 32), "00000000-0000-0000-0000-000000000000"},
		{"effs", strings.Repeat("f", 32), "ffffffff-ffff-ffff-ffff-ffffffffffff"},
		{"not-hex", "g" + base[1:], ""},
		{"too-long", base + "a", ""},
		{"too-short", base[1:], ""},
		{"space-around-plus-prefix-underscore", " +0x_" + base[5:] + " ", ""},
		{"doubled-braces", "{{" + base + "}}", canonical},
		{"leading-brace", "{" + base, canonical},
		{"uuid-urn", "uuid:urn:" + base, canonical},
		{"broken-prefix", "u-uid:" + base, ""},
		{"dashes", base[:16] + "-----" + base[16:], canonical},
		{"mathematical-bold-zero", "\U0001D7CE" + base[1:], shifted},
		{"thai-zero", "\u0e50" + base[1:], shifted},
		{"letter-looking-digit", "\u1d7f" + base[1:], ""},
		{"superscript-two", "\u00b2" + base[1:], ""},
		{"b-is-a-digit", "0b" + base[2:], "0bb2c3d4-e5f6-0718-293a-4b5c6d7e8f90"},
		{"o-is-not-a-digit", "0o" + base[2:], ""},
		{"doubled-prefix", "0x0x" + base[4:], ""},
		{"doubled-plus", "++" + base[2:], ""},
		{"plus-then-underscore", "+_" + base[2:], ""},
		{"underscore-then-x", "0_x" + base[3:], ""},
	}
	for _, testCase := range inputs {
		got, ok := PythonUUIDLookup(testCase.input)
		if !ok {
			got = ""
		}
		if got != testCase.want {
			t.Errorf("%s: PythonUUIDLookup(%q) = %q, %v; want %q", testCase.name, testCase.input, got, ok, testCase.want)
		}
	}
	values := []struct {
		name  string
		input any
		want  string
	}{
		{"json-number", json.Number("1"), "00000000-0000-0000-0000-000000000001"},
		{"true", true, "00000000-0000-0000-0000-000000000001"},
		{"float", 1.5, ""},
		{"nil", nil, ""},
	}
	for _, testCase := range values {
		got, ok := PythonUUIDLookup(testCase.input)
		if !ok {
			got = ""
		}
		if got != testCase.want {
			t.Errorf("%s: PythonUUIDLookup(%v) = %q, %v; want %q", testCase.name, testCase.input, got, ok, testCase.want)
		}
	}
}
