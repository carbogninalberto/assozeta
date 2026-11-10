package zlibng

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

// The golden PNGs are written by TESTS/adapters/go/pil_placeholder_golden.py.
// Their IDAT data is a zlib-ng stream of filtered lines that Pillow compressed
// one line per deflate call, so inflating it gives the exact input and call
// pattern to replay.
func TestDeflateMatchesPillowIDAT(t *testing.T) {
	file, err := os.Open("../testdata/png_golden_zlibng.json.gz")
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
		width := int(binary.BigEndian.Uint32(png[16:]))
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

		d := NewDeflater()
		out := make([]byte, len(want)+64)
		var got []byte
		line := width*3 + 1
		for len(lines) > 0 {
			d.SetInput(lines[:line])
			lines = lines[line:]
			n, ret := d.Deflate(out, NoFlush)
			if ret != OK || d.AvailIn() != 0 {
				t.Fatalf("case %d: deflate returned %d with %d bytes unread", index, ret, d.AvailIn())
			}
			got = append(got, out[:n]...)
		}
		n, ret := d.Deflate(out, Finish)
		if ret != StreamEnd {
			t.Fatalf("case %d: finish returned %d", index, ret)
		}
		got = append(got, out[:n]...)
		if !bytes.Equal(got, want) {
			t.Fatalf("case %d: stream differs (%d bytes, want %d)", index, len(got), len(want))
		}
		checked++
	}
	if checked < 30 {
		t.Fatalf("only %d streams checked", checked)
	}
}

// A one-byte output buffer exercises every early return of deflate(); the
// result must still be a valid stream of the same data.
func TestDeflateTinyOutputRoundTrips(t *testing.T) {
	input := make([]byte, 200000)
	state := uint32(1)
	for i := range input {
		state = state*1664525 + 1013904223
		input[i] = byte(state>>24) & 0x1f
	}
	d := NewDeflater()
	d.SetInput(input)
	var stream []byte
	out := make([]byte, 1)
	flush := NoFlush
	for {
		if d.AvailIn() == 0 {
			flush = Finish
		}
		n, ret := d.Deflate(out, flush)
		stream = append(stream, out[:n]...)
		if ret == StreamEnd {
			break
		}
		if ret != OK {
			t.Fatalf("deflate returned %d", ret)
		}
	}
	r, err := zlib.NewReader(bytes.NewReader(stream))
	if err != nil {
		t.Fatal(err)
	}
	back, err := io.ReadAll(r)
	if err != nil {
		t.Fatal(err)
	}
	if !bytes.Equal(back, input) {
		t.Fatal("round trip differs")
	}
}

func TestDeflateRejectsOtherFlushModes(t *testing.T) {
	d := NewDeflater()
	d.SetInput([]byte("x"))
	if _, ret := d.Deflate(make([]byte, 16), 2); ret != StreamErr {
		t.Fatalf("got %d", ret)
	}
	if _, ret := d.Deflate(nil, NoFlush); ret != BufErr {
		t.Fatalf("got %d", ret)
	}
}
