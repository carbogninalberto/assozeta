package pycodecs

import (
	"encoding/hex"
	"encoding/json"
	"errors"
	"os"
	"slices"
	"testing"
)

// TestSurrogatePass replays bytes.decode(codec, 'surrogatepass') for the
// utf-8/16/32 family from CPython 3.13.16 (testdata/gen_surrogatepass.py).
func TestSurrogatePass(t *testing.T) {
	raw, err := os.ReadFile("testdata/surrogatepass.json")
	if err != nil {
		t.Fatal(err)
	}
	var cases []struct {
		Codec string
		In    string
		Out   []rune
		Err   []any
	}
	if err := json.Unmarshal(raw, &cases); err != nil {
		t.Fatal(err)
	}
	for _, c := range cases {
		data, _ := hex.DecodeString(c.In)
		out, err, ok := Decode(c.Codec, data, SurrogatePass)
		if !ok {
			t.Fatalf("codec %s not registered", c.Codec)
		}
		if c.Err != nil {
			var decodeErr *DecodeError
			if !errors.As(err, &decodeErr) || decodeErr.Start != int(c.Err[0].(float64)) || decodeErr.End != int(c.Err[1].(float64)) ||
				decodeErr.Reason != c.Err[2].(string) {
				t.Errorf("%s %s: got %v %v, want error %v", c.Codec, c.In, out, err, c.Err)
			}
			continue
		}
		if err != nil || !slices.Equal(out, c.Out) {
			t.Errorf("%s %s: got %v %v, want %v", c.Codec, c.In, out, err, c.Out)
		}
	}
}
