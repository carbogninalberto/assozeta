package medicalform

import (
	"bytes"
	"compress/zlib"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"io"
	"testing"
)

// zlibSample is a compressible picture-like buffer: a row filter byte followed
// by a gradient, the shape a PNG IDAT carries.
func zlibSample(width, height int) []byte {
	out := make([]byte, 0, height*(1+width*3))
	for y := 0; y < height; y++ {
		out = append(out, 0)
		for x := 0; x < width; x++ {
			out = append(out, byte(x*5), byte(y*9), byte(x*y*3))
		}
	}
	return out
}

// TestInflateZlibComplete checks the decoder against compress/zlib on whole
// streams: the samples must be identical.
func TestInflateZlibComplete(t *testing.T) {
	for _, level := range []int{0, 1, 6, 9} {
		for _, size := range [][2]int{{1, 1}, {7, 5}, {61, 37}} {
			source := zlibSample(size[0], size[1])
			var buffer bytes.Buffer
			writer, err := zlib.NewWriterLevel(&buffer, level)
			if err != nil {
				t.Fatal(err)
			}
			if _, err := writer.Write(source); err != nil {
				t.Fatal(err)
			}
			if err := writer.Close(); err != nil {
				t.Fatal(err)
			}
			out, ok := inflateZlib(buffer.Bytes(), len(source))
			if !ok || !bytes.Equal(out, source) {
				t.Fatalf("level %d %dx%d: ok=%v len=%d want %d", level, size[0], size[1], ok, len(out), len(source))
			}
		}
	}
}

// TestInflateZlibTruncated checks every cut of a stream: zlib returns the
// samples it could decode, so the decoder must return a prefix of the source
// that is never shorter than compress/flate's answer.
func TestInflateZlibTruncated(t *testing.T) {
	source := zlibSample(61, 37)
	var buffer bytes.Buffer
	writer, _ := zlib.NewWriterLevel(&buffer, 9)
	writer.Write(source)
	writer.Close()
	stream := buffer.Bytes()
	for cut := 2; cut < len(stream); cut++ {
		out, ok := inflateZlib(stream[:cut], len(source))
		if !ok {
			t.Fatalf("cut %d: refused", cut)
		}
		if !bytes.Equal(out, source[:len(out)]) {
			t.Fatalf("cut %d: samples are not a prefix of the image (%d bytes)", cut, len(out))
		}
		reader, err := zlib.NewReader(bytes.NewReader(stream[:cut]))
		if err != nil {
			continue
		}
		decoded, err := io.ReadAll(reader)
		if err != nil && err != io.ErrUnexpectedEOF {
			t.Fatalf("cut %d: %v", cut, err)
		}
		if len(out) < len(decoded) {
			t.Fatalf("cut %d: %d samples, compress/flate decoded %d", cut, len(out), len(decoded))
		}
	}
}

// corpusIDAT is the IDAT payload of png_idat_truncated_zlib.png in the
// authoring corpus: zlib level 9 of the 61x37 RGB gradient, cut to two
// thirds of its length.
const corpusIDAT = "" +
	"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" +
	"J3PQxax4mDHJzHiZvX5mOcBcCjG6MOOPMtkFphtnpBSzNcHQSeZsitmQYexZZjHPnCwwV0vM7Qyzu8wcrzIXa4ymwcw1maNt" +
	"hu0w4i7j4phEj2msMbwBM7vOHBgxJybMZR4zDTNBhFkSMRekDKpgdqiYI1rmvIGZAqACoIqVX4kJKhVxZSyrmJWV/epKWVe5" +
	"ilVMeGWfqfKmpXLZVrnNUdnjrLzuqgw8le+SlZC38qq/cilQuSVUeSxc+Zdo5eJCRRuv7KIq+UTlQrKiTlV2Ziq/yVZW85Vv" +
	"FyqPlCq/ZirdckVerZC1SrpRYZsVSbuyrVN5sVs5z1VEvcqWtUpyUDm3XhGMKvdPKr/kVc7ClW8hlXtFlaellYai8k1VxaGt" +
	"/IOhchqAOoDqd/HrBwX1uri+QVa/V1lPqOvv6epTWH0LXj9sqn9gqUttddJRP+qsf+Sqo576TrJ+zFvv+eu6QP2xUP14uP5p" +
	"tD69UN8TrzNU/UqiPpOs70/VT2bqX2brs/l6vFA/Xapfx9SJcv2Zav1srY406q5m/fl2/XynfmO3vp2rv9Srd9fqykF9br2e" +
	"G9UvTOo38+rzcL2A1C+J6rdK67sV9TdU9cvautFQjwLQAlDrbn7rGUGrJW6JZa0Hla2X1a3/0rV0WOvHeOsNU+vPlpbF1nrC" +
	"0ao7W99ytVye1otk6/fe1rf9LX+g9S+h1qfh1m3RVnShVYm3rqFa30+0EsnW+6nWjZnWQ9lWNt/6uNDaXGr9hGkx5dbn1db3" +
	"aq0nG61Gs3V9u7Wl0/pVt9XlWt/ptXattYqD1mC99Tej1v5J63e8Fg9u3YO0nhW1/o+0JVW0Hla1ctrWJ4aWAQAWQCzBZ2kB" +
	"y4pZVMbOK9llNXtVx85i7CGcPWdipRZ2zsYuOdiBk51xsYsetkGyU152u5/NBtiLIXY6zEaj7MoCuyHOuij2SIJdTbK6FLs7" +
	"ww=="

// TestInflateZlibCorpusCut is the measured case: the IDAT of a PNG whose zlib
// stream is cut in the middle of a symbol. PyMuPDF 1.25.5 opens it and stores
// these 4511 samples (the rest of the image is the 0xff fill); zlib stops one
// symbol after compress/flate.
func TestInflateZlibCorpusCut(t *testing.T) {
	data, err := base64.StdEncoding.DecodeString(corpusIDAT)
	if err != nil {
		t.Fatal(err)
	}
	out, ok := inflateZlib(data, 61*37*3)
	if !ok {
		t.Fatal("refused")
	}
	if len(out) != 4511 {
		t.Fatalf("%d samples, want 4511", len(out))
	}
	sum := sha256.Sum256(out)
	if got := hex.EncodeToString(sum[:]); got != "965b240900b8bef1d9e60ea57329f892873a08e6f705fec5914a0f6efd69902a" {
		t.Fatalf("samples hash %s", got)
	}
	if out[len(out)-1] != 0x9b {
		t.Fatalf("last sample %#x, want 0x9b", out[len(out)-1])
	}
}

// TestInflateZlibInvalid checks the streams zlib reports as invalid data.
func TestInflateZlibInvalid(t *testing.T) {
	valid := func() []byte {
		var buffer bytes.Buffer
		writer, _ := zlib.NewWriterLevel(&buffer, 6)
		writer.Write(zlibSample(9, 9))
		writer.Close()
		return buffer.Bytes()
	}()
	badHeader := append([]byte(nil), valid...)
	badHeader[0] = 0x79 // compression method 9
	badCheck := append([]byte(nil), valid...)
	badCheck[0] += 1                        // FCHECK no longer divisible by 31
	fixed := []byte{0x78, 0x9c, 0x03, 0x00} // one empty fixed block
	for name, data := range map[string][]byte{
		"header":  badHeader,
		"check":   badCheck,
		"empty":   {},
		"onebyte": {0x78},
	} {
		if _, ok := inflateZlib(data, 4096); ok {
			t.Fatalf("%s: accepted", name)
		}
	}
	if out, ok := inflateZlib(fixed, 4096); !ok || len(out) != 0 {
		t.Fatalf("empty block: ok=%v len=%d", ok, len(out))
	}
}
