package dto

import (
	"encoding/base64"
	"encoding/json"
	"errors"
	"testing"
)

// Vectors produced by CPython 3.13.16 base64.b64decode (decoded bytes re-encoded).
const pythonB64Vectors = `[["YQ==", "YQ==", null], ["YQ==YQ==", "YQYQ", null], ["YQ==x", null, "Incorrect padding"], ["YQ=x=", "YQw=", null], ["YQ=", null, "Incorrect padding"], ["YWI=", "YWI=", null], ["YWI=x", "YWIx", null], ["YWI=YQ==", null, "Invalid base64-encoded string: number of data characters (5) cannot be 1 more than a multiple of 4"], ["Y=Q==", "YQ==", null], ["=YQ==", "YQ==", null], ["YQ===", "YQ==", null], ["YQ==Y", null, "Incorrect padding"], ["YQ==YW", "YQYW", null], ["YQ==YWJj", null, "Incorrect padding"], ["!!YQ==", "YQ==", null], ["abcde", null, "Invalid base64-encoded string: number of data characters (5) cannot be 1 more than a multiple of 4"], ["abc", null, "Incorrect padding"], ["a===", null, "Invalid base64-encoded string: number of data characters (1) cannot be 1 more than a multiple of 4"], ["YQ", null, "Incorrect padding"], ["aGVs\nbG8=", "aGVsbG8=", null], ["YW=Jj", "YWJj", null], ["", "", null], ["====", "", null], ["A", null, "Invalid base64-encoded string: number of data characters (1) cannot be 1 more than a multiple of 4"], ["AB", null, "Incorrect padding"], ["ABC=", "ABA=", null], ["A=B=C=D", "ABCD", null], ["c2Vu\nemE=", "c2VuemE=", null], ["è", null, "string argument should contain only ASCII characters"], ["+/+/", "+/+/", null], ["-_-_", "", null]]`

func TestPythonB64DecodeMatchesCPython(t *testing.T) {
	var vectors [][3]*string
	if err := json.Unmarshal([]byte(pythonB64Vectors), &vectors); err != nil {
		t.Fatal(err)
	}
	for _, vector := range vectors {
		decoded, err := PythonB64Decode(*vector[0])
		var python *PythonBase64Error
		switch {
		case vector[2] != nil:
			if !errors.As(err, &python) || python.Message != *vector[2] {
				t.Errorf("%q: %v, want %q", *vector[0], err, *vector[2])
			}
		case err != nil || base64.StdEncoding.EncodeToString(decoded) != *vector[1]:
			t.Errorf("%q: %x %v, want %s", *vector[0], decoded, err, *vector[1])
		}
	}
	for value, message := range map[any]string{json.Number("5"): "int", json.Number("1.5"): "float", nil: "NoneType", true: "bool"} {
		if _, err := PythonB64Decode(value); err == nil || err.Error() != "argument should be a bytes-like object or ASCII string, not '"+message+"'" {
			t.Errorf("%v: %v", value, err)
		}
	}
}
