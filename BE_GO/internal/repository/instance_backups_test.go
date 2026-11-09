package repository

import (
	"encoding/hex"
	"testing"
)

// Goldens from CPython 3.13 pickle.dumps(value) (DEFAULT_PROTOCOL 4), the
// bytes django-redis stores.
func TestInstanceBackupsPickleDict(t *testing.T) {
	i := func(v int64) *int64 { return &v }
	cases := []struct {
		database, objects *int64
		want              string
	}{
		{i(123), nil, "8004951d000000000000007d94288c086461746162617365944b7b8c076f626a65637473944e752e"},
		{i(9000000), i(70000), "80049524000000000000007d94288c086461746162617365944a405489008c076f626a65637473944a70110100752e"},
		{nil, nil, "8004951c000000000000007d94288c086461746162617365944e8c076f626a65637473944e752e"},
		{i(1099511627776), i(255), "80049524000000000000007d94288c086461746162617365948a060000000000018c076f626a65637473944bff752e"},
		{i(256), i(65535), "80049520000000000000007d94288c086461746162617365944d00018c076f626a65637473944dffff752e"},
		{i(65536), i(-1), "80049524000000000000007d94288c086461746162617365944a000001008c076f626a65637473944affffffff752e"},
		{i(2147483648), i(-2147483649), "80049528000000000000007d94288c086461746162617365948a0500000080008c076f626a65637473948a05ffffff7fff752e"},
		{i(9223372036854775807), i(-9223372036854775808), "8004952e000000000000007d94288c086461746162617365948a08ffffffffffffff7f8c076f626a65637473948a080000000000000080752e"},
		{i(128), i(-129), "80049521000000000000007d94288c086461746162617365944b808c076f626a65637473944a7fffffff752e"},
	}
	for _, c := range cases {
		got := hex.EncodeToString(instanceBackupsPickleDict([]string{"database", "objects"}, []*int64{c.database, c.objects}))
		if got != c.want {
			t.Errorf("pickle %v %v = %s, want %s", c.database, c.objects, got, c.want)
		}
		value, err := unpickleDataRestoreProgress(instanceBackupsHex(c.want))
		if err != nil {
			t.Fatalf("golden does not decode: %v", err)
		}
		if _, ok := value.(*PickledDict); !ok {
			t.Fatalf("golden decodes to %T", value)
		}
	}
}

func TestInstanceBackupsInt(t *testing.T) {
	for text, want := range map[string]int64{"0": 0, "5": 5, " 12\n": 12, "-3": -3, "+4": 4, "1_0": 10} {
		if got, ok := instanceBackupsInt([]byte(text)); !ok || got != want {
			t.Errorf("int(%q) = %d %v", text, got, ok)
		}
	}
	for _, text := range []string{"", "x", "1__0", "_1", "\x80\x04\x88.", "99999999999999999999"} {
		if _, ok := instanceBackupsInt([]byte(text)); ok {
			t.Errorf("int(%q) accepted", text)
		}
	}
}

func instanceBackupsHex(text string) []byte {
	out, err := hex.DecodeString(text)
	if err != nil {
		panic(err)
	}
	return out
}
