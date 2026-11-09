package repository

import (
	"bytes"
	"context"
	"testing"
)

// The expected bytes are pickle.dumps(value, 5) from CPython 3.13 (django-redis
// PickleSerializer with pickle.DEFAULT_PROTOCOL).
func TestPicklePythonFloatsMatchesCPython(t *testing.T) {
	for _, item := range []struct {
		values []float64
		want   []byte
	}{
		{[]float64{1.5}, []byte("\x80\x05\x95\r\x00\x00\x00\x00\x00\x00\x00]\x94G?\xf8\x00\x00\x00\x00\x00\x00a.")},
		{[]float64{1.5, 2.5}, []byte("\x80\x05\x95\x17\x00\x00\x00\x00\x00\x00\x00]\x94(G?\xf8\x00\x00\x00\x00\x00\x00G@\x04\x00\x00\x00\x00\x00\x00e.")},
	} {
		got := picklePythonFloats(item.values)
		if !bytes.Equal(got, item.want) {
			t.Fatalf("pickle(%v) = %q", item.values, got)
		}
		decoded, err := decodeDjangoSession(context.Background(), got)
		if err != nil {
			t.Fatal(err)
		}
		list, ok := decoded.([]any)
		if !ok || len(list) != len(item.values) || list[0] != item.values[0] {
			t.Fatalf("round trip = %#v", decoded)
		}
	}
}
