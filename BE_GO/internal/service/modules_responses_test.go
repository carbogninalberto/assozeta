package service

import (
	"bytes"
	"testing"
)

// The expected values are the view's decode_base64 results on Python 3.13.
func TestPythonB64Decode(t *testing.T) {
	for _, item := range []struct {
		input string
		want  []byte
		fails bool
	}{
		{input: "Y2lhbw==", want: []byte("ciao")},
		{input: "Y2lhbw", want: []byte("ciao")},
		{input: "Y2l!hbw==", want: []byte("ciao")},
		{input: "data:text/plain;base64,Y2lhbw", want: []byte("ciao")},
		{input: "Y2lhbw==Zm", want: []byte("ciao\x06f")},
		{input: "YQ=a=", want: []byte("a\x06")},
		{input: "YW=Jj", want: []byte("abc")},
		{input: "Y2lhbw==Zm9v", fails: true},
		{input: "Y2lhbw==Z", want: []byte("ciao\x06")},
		{input: "abcde", fails: true},
		{input: "Y2l hb w", fails: true},
		{input: "çiao", fails: true},
	} {
		got, err := pythonB64Decode(item.input)
		if item.fails != (err != nil) || !item.fails && !bytes.Equal(got, item.want) {
			t.Fatalf("%q: got %q, %v", item.input, got, err)
		}
	}
}

func TestModuleContains(t *testing.T) {
	if present, _, err := moduleContains([]any{"filename"}, "filename"); !present || err == nil {
		t.Fatal("a list answers membership and raises when indexed")
	}
	if present, _, err := moduleContains([]any{"x"}, "filename"); present || err != nil {
		t.Fatal("a list without the key")
	}
	if _, _, err := moduleContains(nil, "filename"); err == nil {
		t.Fatal("None is not iterable")
	}
}
