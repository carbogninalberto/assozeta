package zlibclassic

import (
	"bytes"
	"compress/gzip"
	"compress/zlib"
	"encoding/base64"
	"encoding/binary"
	"encoding/json"
	"io"
	"os"
	"testing"
)

// The golden PNGs are written by TESTS/adapters/go/pil_placeholder_golden.py
// in the production image, where Pillow compresses with the system zlib.
// Inflating their IDAT data gives the filtered lines that went in.
func TestDeflateMatchesPillowIDAT(t *testing.T) {
	file, err := os.Open("../testdata/png_golden_zlib.json.gz")
	if err != nil {
		t.Fatal(err)
	}
	defer file.Close()
	zr, err := gzip.NewReader(file)
	if err != nil {
		t.Fatal(err)
	}
	var golden struct {
		Cases []struct {
			PNG string `json:"png"`
		} `json:"cases"`
	}
	if err := json.NewDecoder(zr).Decode(&golden); err != nil {
		t.Fatal(err)
	}
	checked := 0
	for index, c := range golden.Cases {
		if c.PNG == "" {
			continue
		}
		png, err := base64.StdEncoding.DecodeString(c.PNG)
		if err != nil {
			t.Fatal(err)
		}
		var want []byte
		for p := 8; p < len(png); {
			n := int(binary.BigEndian.Uint32(png[p:]))
			if string(png[p+4:p+8]) == "IDAT" {
				want = append(want, png[p+8:p+8+n]...)
			}
			p += 12 + n
		}
		r, err := zlib.NewReader(bytes.NewReader(want))
		if err != nil {
			t.Fatal(err)
		}
		lines, err := io.ReadAll(r)
		if err != nil {
			t.Fatal(err)
		}
		if got := Deflate(lines); !bytes.Equal(got, want) {
			t.Fatalf("case %d: stream differs (%d bytes, want %d)", index, len(got), len(want))
		}

		// The stream must not depend on how the input is split.
		var split bytes.Buffer
		w := Compress(&split)
		for i := 0; i < len(lines); i += 7 {
			if _, err := w.Write(lines[i:min(i+7, len(lines))]); err != nil {
				t.Fatal(err)
			}
		}
		if err := w.Close(); err != nil {
			t.Fatal(err)
		}
		if !bytes.Equal(split.Bytes(), want) {
			t.Fatalf("case %d: stream depends on the write sizes", index)
		}
		checked++
	}
	if checked < 30 {
		t.Fatalf("only %d streams checked", checked)
	}
}

func TestWriteAfterClose(t *testing.T) {
	var b bytes.Buffer
	w := Compress(&b)
	if err := w.Close(); err != nil {
		t.Fatal(err)
	}
	if _, err := w.Write([]byte("x")); err == nil {
		t.Fatal("write after close succeeded")
	}
	r, err := zlib.NewReader(&b)
	if err != nil {
		t.Fatal(err)
	}
	if data, err := io.ReadAll(r); err != nil || len(data) != 0 {
		t.Fatalf("empty stream: %q %v", data, err)
	}
}
