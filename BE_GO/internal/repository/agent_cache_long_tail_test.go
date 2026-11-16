package repository

import (
	"encoding/hex"
	"encoding/json"
	"errors"
	"math"
	"math/big"
	"testing"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/refusal"
)

// The golden is pickle.dumps({'save_info': {'s': '\udcff', 'n': 10**199,
// 'f': float('inf')}, 'filename': 'export'}, 4) from CPython 3.13.16: a
// surrogatepass string, a LONG1 of 83 bytes and a BINFLOAT.
const agentSaveInfoGolden = "80049599000000000000007d94288c09736176655f696e666f947d94288c0173948c03edb3bf948c016e948a530000000000000000000000000000000000000000000000008016c545b90b74f34182b9c70a39c50d63da0f34ba53a13d01f795ccc10eea2f35b13deb033ac5753120409213e19955cba1bd155d9d5859c171218c016694477ff0000000000000758c0866696c656e616d65948c066578706f727494752e"

func agentSaveInfoValue(t *testing.T) (domain.RequestObject, *big.Int) {
	t.Helper()
	ten199 := new(big.Int).Exp(big.NewInt(10), big.NewInt(199), nil)
	value := domain.RequestObject{
		{Name: "s", Value: string([]byte{0xed, 0xb3, 0xbf})}, // '\udcff' as WTF-8
		{Name: "n", Value: json.Number(ten199.String())},
		{Name: "f", Value: json.Number("1e400")}, // Python's json.loads float inf
	}
	return value, ten199
}

// Item 9: the pickler writes the same bytes as CPython for the save_info shapes
// its only writer can produce, and the decoder reads them back.
func TestPickleAgentSaveInfoMatchesCPython(t *testing.T) {
	value, ten199 := agentSaveInfoValue(t)
	data, err := PickleAgentSaveInfo(value, "export")
	if err != nil {
		t.Fatal(err)
	}
	if got, want := hex.EncodeToString(data), agentSaveInfoGolden; got != want {
		t.Fatalf("pickle = %s\nwant     %s", got, want)
	}

	decoded, err := decodeAgentSaveInfo(data)
	if err != nil {
		t.Fatal(err)
	}
	outer, ok := decoded.(domain.RequestObject)
	if !ok {
		t.Fatalf("%T", decoded)
	}
	if filename, _ := outer.Lookup("filename"); filename != "export" {
		t.Fatalf("filename = %#v", filename)
	}
	saveInfo, present := outer.Lookup("save_info")
	if !present {
		t.Fatal("save_info missing")
	}
	inner, ok := saveInfo.(domain.RequestObject)
	if !ok {
		t.Fatalf("save_info = %T", saveInfo)
	}
	if text, _ := inner.Lookup("s"); text != string([]byte{0xed, 0xb3, 0xbf}) {
		t.Fatalf("surrogate string = %q", text)
	}
	if number, _ := inner.Lookup("n"); number != json.Number(ten199.String()) {
		t.Fatalf("big integer = %#v", number)
	}
	if number, _ := inner.Lookup("f"); number != math.Inf(1) {
		t.Fatalf("float = %#v", number)
	}

	// The data restore decoder keeps exactly its old subset.
	if _, err := unpickleDataRestoreProgress(data); !errors.Is(err, ErrDataRestoreProgressShape) {
		t.Fatalf("data restore accepted the agent shape: %v", err)
	}
}

// Item 9: values an agent save_info cache key cannot hold are application
// states, and only the decr race stays a refusal.
func TestAgentSaveInfoDecoderAppStates(t *testing.T) {
	if _, err := decodeAgentSaveInfo([]byte("123")); !errors.Is(err, refusal.ErrAppState) {
		t.Fatalf("raw integer: %v", err)
	}
	if _, err := decodeAgentSaveInfo([]byte("not a pickle")); !errors.Is(err, refusal.ErrAppState) {
		t.Fatalf("bad pickle: %v", err)
	}
	if errors.Is(ErrAgentCacheShape, refusal.ErrAppState) {
		t.Fatal("the decr race must stay a refusal")
	}
}

// Item 9: a string that is not WTF-8 cannot come from surrogatepass and is an
// application state, not a refusal.
func TestPickleAgentSaveInfoRejectsNonWTF8(t *testing.T) {
	if _, err := PickleAgentSaveInfo(string([]byte{0xff}), "export"); !errors.Is(err, refusal.ErrAppState) {
		t.Fatalf("invalid WTF-8: %v", err)
	} else if errors.Is(err, refusal.ErrNotPorted) {
		t.Fatalf("refusal leaked: %v", err)
	}
	overflow, err := PickleAgentSaveInfo(json.Number("1e400"), "export")
	if err != nil {
		t.Fatal(err)
	}
	const want = "8004952e000000000000007d94288c09736176655f696e666f94477ff00000000000008c0866696c656e616d65948c066578706f727494752e"
	if got := hex.EncodeToString(overflow); got != want {
		t.Fatalf("overflow float = %s\nwant           %s", got, want)
	}
}
