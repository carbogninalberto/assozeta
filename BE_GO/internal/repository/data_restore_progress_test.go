package repository

import (
	"encoding/hex"
	"errors"
	"reflect"
	"testing"
)

// Vectors are pickle.dumps(value, protocol) from CPython 3.13.
func TestUnpickleDataRestoreProgressMatchesCPython(t *testing.T) {
	payloadKeys := []string{"operation_id", "attempt", "sequence", "phase", "updated_at", "completed", "total", "unit", "detail",
		"percent", "elapsed_seconds", "eta_seconds"}
	payload := map[string]any{"operation_id": "d7000000-0000-4000-8000-000000000106", "attempt": int64(3), "sequence": int64(12),
		"phase": "files", "updated_at": "2026-04-02T10:00:00.123456+00:00", "completed": int64(512), "total": int64(2048),
		"unit": "bytes", "detail": "1 / 4 allegati", "percent": int64(25), "elapsed_seconds": int64(9), "eta_seconds": nil}
	vectors := map[string]string{
		"payload4": "80049506010000000000007d94288c0c6f7065726174696f6e5f6964948c2464373030303030302d303030302d343030302d383030302d303030303030303030313036948c07617474656d7074944b038c0873657175656e6365944b0c8c057068617365948c0566696c6573948c0a757064617465645f6174948c20323032362d30342d30325431303a30303a30302e3132333435362b30303a3030948c09636f6d706c65746564944d00028c05746f74616c944d00088c04756e6974948c056279746573948c0664657461696c948c0e31202f203420616c6c6567617469948c0770657263656e74944b198c0f656c61707365645f7365636f6e6473944b098c0b6574615f7365636f6e6473944e752e",
		"payload5": "80059506010000000000007d94288c0c6f7065726174696f6e5f6964948c2464373030303030302d303030302d343030302d383030302d303030303030303030313036948c07617474656d7074944b038c0873657175656e6365944b0c8c057068617365948c0566696c6573948c0a757064617465645f6174948c20323032362d30342d30325431303a30303a30302e3132333435362b30303a3030948c09636f6d706c65746564944d00028c05746f74616c944d00088c04756e6974948c056279746573948c0664657461696c948c0e31202f203420616c6c6567617469948c0770657263656e74944b198c0f656c61707365645f7365636f6e6473944b098c0b6574615f7365636f6e6473944e752e",
		"payload2": "80027d710028580c0000006f7065726174696f6e5f69647101582400000064373030303030302d303030302d343030302d383030302d30303030303030303031303671025807000000617474656d707471034b03580800000073657175656e636571044b0c580500000070686173657105580500000066696c65737106580a000000757064617465645f617471075820000000323032362d30342d30325431303a30303a30302e3132333435362b30303a303071085809000000636f6d706c6574656471094d00025805000000746f74616c710a4d00085804000000756e6974710b58050000006279746573710c580600000064657461696c710d580e00000031202f203420616c6c6567617469710e580700000070657263656e74710f4b19580f000000656c61707365645f7365636f6e647371104b09580b0000006574615f7365636f6e647371114e752e",
	}
	for name, vector := range vectors {
		raw, _ := hex.DecodeString(vector)
		value, err := unpickleDataRestoreProgress(raw)
		if err != nil {
			t.Fatalf("%s: %v", name, err)
		}
		dict, ok := value.(*PickledDict)
		if !ok || !reflect.DeepEqual(dict.Keys, payloadKeys) || !reflect.DeepEqual(dict.Values, payload) {
			t.Fatalf("%s: %#v", name, value)
		}
	}
	// {'a': [1, -1, 300, -70000, 2**40, True, False, None, 1.5, 'àè€😀'], 'b': ('x', 'x'), 'c': {}, 'attempt': 1}
	raw, _ := hex.DecodeString("8004955c000000000000007d94288c0161945d94284b014affffffff4d2c014a90eefeff8a0600000000000188894e473ff80000000000008c0bc3a0c3a8e282acf09f988094658c0162948c017894680586948c0163947d948c07617474656d7074944b01752e")
	value, err := unpickleDataRestoreProgress(raw)
	if err != nil {
		t.Fatal(err)
	}
	dict := value.(*PickledDict)
	want := map[string]any{"a": []any{int64(1), int64(-1), int64(300), int64(-70000), int64(1 << 40), true, false, nil, 1.5, "àè€😀"},
		"b": []any{"x", "x"}, "c": &PickledDict{Keys: nil, Values: map[string]any{}}, "attempt": int64(1)}
	if !reflect.DeepEqual(dict.Keys, []string{"a", "b", "c", "attempt"}) || !reflect.DeepEqual(dict.Values, want) {
		t.Fatalf("nested: %#v", dict.Values)
	}
	raw, _ = hex.DecodeString("80049511000000000000005d94288c07617474656d7074944b01652e")
	if value, err := unpickleDataRestoreProgress(raw); err != nil || !reflect.DeepEqual(value, []any{"attempt", int64(1)}) {
		t.Fatalf("list: %#v %v", value, err)
	}
	raw, _ = hex.DecodeString("80047d942e")
	if value, err := unpickleDataRestoreProgress(raw); err != nil || len(value.(*PickledDict).Keys) != 0 {
		t.Fatalf("empty: %#v %v", value, err)
	}
}

func TestUnpickleDataRestoreProgressRefusesOtherOpcodes(t *testing.T) {
	for _, vector := range []string{
		"80048c08627569746c696e73948c05706f77948693942e", // STACK_GLOBAL
		"8004430161942e",       // SHORT_BINBYTES
		"80047d948c0161944b01", // no STOP
		"28dp0\n.",             // protocol 0
	} {
		raw, err := hex.DecodeString(vector)
		if err != nil {
			raw = []byte(vector)
		}
		if _, err := unpickleDataRestoreProgress(raw); !errors.Is(err, ErrDataRestoreProgressShape) {
			t.Fatalf("%s: %v", vector, err)
		}
	}
}

func TestPythonIntBytes(t *testing.T) {
	for text, want := range map[string]bool{"7": true, " -7\n": true, "+1_000": true, "0": true, "1__0": false, "_1": false, "": false,
		"1.0": false, "0x10": false, "\x80\x04}.": false} {
		if _, ok := pythonIntBytes([]byte(text)); ok != want {
			t.Fatalf("%q: %v", text, ok)
		}
	}
}
