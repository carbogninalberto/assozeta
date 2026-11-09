package repository

import (
	"bytes"
	"testing"
)

func TestGoogleSessionPickleMatchesDjangoRedis(t *testing.T) {
	// pickle.dumps({'state': 'A' * 30}, 4)
	want := append([]byte("\x80\x04\x95-\x00\x00\x00\x00\x00\x00\x00}\x94\x8c\x05state\x94\x8c\x1e"), bytes.Repeat([]byte("A"), 30)...)
	want = append(want, []byte("\x94s.")...)
	if got := googleSessionPickle(string(bytes.Repeat([]byte("A"), 30))); !bytes.Equal(got, want) {
		t.Fatalf("%q", got)
	}
}
