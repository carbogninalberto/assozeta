package djtemplate

import (
	"errors"
	"math"
	"math/big"
	"strings"
	"testing"
)

// TestFromJSONStoredNumbers covers the number forms PostgreSQL's jsonb prints
// and Django reads back with json.loads: plain integers of any length (beyond
// int64, up to Python's 4300-digit conversion limit), and decimals read as
// float() (overflow, subnormals and signed zero included).
func TestFromJSONStoredNumbers(t *testing.T) {
	cases := []struct {
		name string
		text string
		// check receives the decoded value.
		check func(t *testing.T, value any)
	}{
		{"int64", "9223372036854775807", func(t *testing.T, value any) {
			if number, ok := value.(int64); !ok || number != math.MaxInt64 {
				t.Fatalf("got %T %v", value, value)
			}
		}},
		{"big positive", "12345678901234567890123", func(t *testing.T, value any) {
			want, _ := new(big.Int).SetString("12345678901234567890123", 10)
			number, ok := value.(*big.Int)
			if !ok || number.Cmp(want) != 0 {
				t.Fatalf("got %T %v", value, value)
			}
		}},
		{"big negative", "-12345678901234567890123", func(t *testing.T, value any) {
			want, _ := new(big.Int).SetString("-12345678901234567890123", 10)
			number, ok := value.(*big.Int)
			if !ok || number.Cmp(want) != 0 {
				t.Fatalf("got %T %v", value, value)
			}
		}},
		{"4300 digits", "1" + strings.Repeat("0", 4299), func(t *testing.T, value any) {
			want, _ := new(big.Int).SetString("1"+strings.Repeat("0", 4299), 10)
			number, ok := value.(*big.Int)
			if !ok || number.Cmp(want) != 0 {
				t.Fatalf("got %T %v", value, value)
			}
		}},
		{"4300 digits negative", "-" + strings.Repeat("9", 4300), func(t *testing.T, value any) {
			want, _ := new(big.Int).SetString("-"+strings.Repeat("9", 4300), 10)
			number, ok := value.(*big.Int)
			if !ok || number.Cmp(want) != 0 {
				t.Fatalf("got %T %v", value, value)
			}
		}},
		{"float", "1.5", func(t *testing.T, value any) {
			if number, ok := value.(float64); !ok || number != 1.5 {
				t.Fatalf("got %T %v", value, value)
			}
		}},
		{"overflowing decimal", "1" + strings.Repeat("0", 400) + ".0", func(t *testing.T, value any) {
			if number, ok := value.(float64); !ok || !math.IsInf(number, 1) {
				t.Fatalf("got %T %v", value, value)
			}
		}},
		{"subnormal decimal", "0." + strings.Repeat("0", 319) + "1", func(t *testing.T, value any) {
			number, ok := value.(float64)
			if !ok || number != 1e-320 {
				t.Fatalf("got %T %v", value, value)
			}
		}},
		{"negative zero", "-0.0", func(t *testing.T, value any) {
			number, ok := value.(float64)
			if !ok || number != 0 || !math.Signbit(number) {
				t.Fatalf("got %T %v", value, value)
			}
		}},
	}
	for _, test := range cases {
		t.Run(test.name, func(t *testing.T) {
			value, err := FromJSON([]byte(test.text))
			if err != nil {
				t.Fatal(err)
			}
			test.check(t, value)
		})
	}
}

// TestFromJSONRefusals keeps json.loads' conversion limit (the 500 page) and
// the texts jsonb cannot store (the refusals) apart.
func TestFromJSONRefusals(t *testing.T) {
	if _, err := FromJSON([]byte("1" + strings.Repeat("0", 4300))); !errors.Is(err, ErrPython) {
		t.Errorf("4301-digit integer: %v", err)
	}
	if _, err := FromJSON([]byte("-" + strings.Repeat("1", 4301))); !errors.Is(err, ErrPython) {
		t.Errorf("negative 4301-digit integer: %v", err)
	}
	// A fraction has no conversion limit: float() reads any length.
	if value, err := FromJSON([]byte("0." + strings.Repeat("0", 5000) + "1")); err != nil || value.(float64) != 0 {
		t.Errorf("long fraction: %T %v, %v", value, value, err)
	}
	// jsonb never keeps a duplicated key (the last one wins on write) and
	// never writes trailing data.
	if _, err := FromJSON([]byte(`{"a": 1, "a": 2}`)); !errors.Is(err, ErrUnsupported) {
		t.Errorf("duplicate key: %v", err)
	}
	if _, err := FromJSON([]byte(`{} {}`)); !errors.Is(err, ErrUnsupported) {
		t.Errorf("trailing data: %v", err)
	}
}

// TestFromJSONKeepsKeyOrderAndNesting is json.loads' dict order and the nested
// values a JSON column can hold.
func TestFromJSONKeepsKeyOrderAndNesting(t *testing.T) {
	value, err := FromJSON([]byte(`{"b": [true, null, -0.0, 12345678901234567890123], "a": {"x": "ß"}}`))
	if err != nil {
		t.Fatal(err)
	}
	dict, ok := value.(*Dict)
	if !ok {
		t.Fatalf("got %T", value)
	}
	if got := strings.Join(dict.Keys(), ","); got != "b,a" {
		t.Errorf("keys %q", got)
	}
	list, ok := mustGet(t, dict, "b").(List)
	if !ok || len(list) != 4 {
		t.Fatalf("list %T", mustGet(t, dict, "b"))
	}
	if _, ok := list[3].(*big.Int); !ok {
		t.Errorf("nested big integer %T", list[3])
	}
	if text, err := PythonStr(dict); err != nil || text != `{'b': [True, None, -0.0, 12345678901234567890123], 'a': {'x': 'ß'}}` {
		t.Errorf("str() %q, %v", text, err)
	}
}

func mustGet(t *testing.T, dict *Dict, key string) any {
	t.Helper()
	value, ok := dict.Get(key)
	if !ok {
		t.Fatalf("missing %q", key)
	}
	return value
}
