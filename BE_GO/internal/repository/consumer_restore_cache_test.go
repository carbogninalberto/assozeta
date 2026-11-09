package repository

import (
	"encoding/hex"
	"testing"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/associationexport"
)

// TestPickleDict compares with CPython 3.13's pickle.dumps(value, 5) for
// values without repeated string objects (no memo references).
func TestPickleDict(t *testing.T) {
	cases := []struct{ value associationexport.Dict }{
		{associationexport.Dict{{Key: "a", Value: 1}, {Key: "b", Value: nil}, {Key: "c", Value: associationexport.Dict{{Key: "d", Value: true}}},
			{Key: "e", Value: "x"}}},
		{associationexport.Dict{{Key: "n", Value: 300}, {Key: "m", Value: 70000}, {Key: "l", Value: []any{1, 2}}}},
	}
	for i, c := range cases {
		data, err := PickleDict(c.value)
		if err != nil {
			t.Fatal(err)
		}
		if want := pickleGolden[i]; hex.EncodeToString(data) != want {
			t.Fatalf("case %d: %x, want %s", i, data, want)
		}
		value, err := unpickleDataRestoreProgress(data)
		if err != nil {
			t.Fatal(err)
		}
		if _, ok := value.(*PickledDict); !ok {
			t.Fatalf("case %d: %T", i, value)
		}
	}
}

var pickleGolden = []string{
	"80059524000000000000007d94288c0161944b018c0162944e8c0163947d948c01649488738c0165948c017894752e",
	"80059521000000000000007d94288c016e944d2c018c016d944a701101008c016c945d94284b014b0265752e",
}
