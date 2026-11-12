package repository

import (
	"encoding/hex"
	"strings"
	"testing"
)

// Expected bytes are pickle.dumps(value, 4) from CPython 3.13.16, the
// django-redis default serializer protocol.
func TestPickledBroadcastNames(t *testing.T) {
	name := func(value string) *string { return &value }
	cases := []struct {
		names   []*string
		encoded string
	}{
		{[]*string{name("broadcast_x"), name("ws-club-news"), name("all_notifications")},
			"80049536000000000000005d94288c0b62726f6164636173745f78948c0c77732d636c75622d6e657773948c11616c6c5f6e6f74696669636174696f6e7394652e"},
		{[]*string{name("all_notifications")}, "80049518000000000000005d948c11616c6c5f6e6f74696669636174696f6e7394612e"},
		{[]*string{nil, name("all_notifications")}, "8004951a000000000000005d94284e8c11616c6c5f6e6f74696669636174696f6e7394652e"},
	}
	for _, test := range cases {
		encoded, ok := encodePickledNames(test.names)
		if !ok || hex.EncodeToString(encoded) != test.encoded {
			t.Fatalf("encode: %x", encoded)
		}
		decoded, err := decodePickledNames(encoded)
		if err != nil || len(decoded) != len(test.names) {
			t.Fatalf("decode: %v %v", decoded, err)
		}
		for i := range decoded {
			if (decoded[i] == nil) != (test.names[i] == nil) || (decoded[i] != nil && *decoded[i] != *test.names[i]) {
				t.Fatalf("decode item %d", i)
			}
		}
	}
	long, _ := encodePickledNames([]*string{name(strings.Repeat("x", 300)), name("all_notifications")})
	if !strings.HasPrefix(hex.EncodeToString(long), "8004954b010000000000005d9428582c01000078") {
		t.Fatalf("long name: %x", long[:20])
	}
	if _, err := decodePickledNames([]byte("\x80\x04\x95\x05\x00\x00\x00\x00\x00\x00\x00}\x94.")); err == nil {
		t.Fatal("a dictionary is not a broadcast list")
	}
}
