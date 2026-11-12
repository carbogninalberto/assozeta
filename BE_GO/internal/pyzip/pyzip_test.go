package pyzip

import (
	"bytes"
	"compress/flate"
	"encoding/base64"
	"encoding/binary"
	"encoding/json"
	"hash/crc32"
	"io"
	"os"
	"testing"
	"time"
)

type goldenCase struct {
	Name    string `json:"name"`
	At      []int  `json:"at"`
	Entries []struct {
		Name string `json:"name"`
		Data string `json:"data"`
	} `json:"entries"`
	Zip string `json:"zip"`
}

// records splits an archive into its headers with the deflate-dependent
// fields (compressed sizes, offsets, the central directory offset) zeroed and
// the compressed payloads removed.
func records(t *testing.T, archive []byte) [][]byte {
	t.Helper()
	var out [][]byte
	position := 0
	for position < len(archive) {
		signature := binary.LittleEndian.Uint32(archive[position:])
		switch signature {
		case 0x04034b50:
			names := int(binary.LittleEndian.Uint16(archive[position+26:]))
			extra := int(binary.LittleEndian.Uint16(archive[position+28:]))
			compressed := int(binary.LittleEndian.Uint32(archive[position+18:]))
			record := append([]byte(nil), archive[position:position+30+names+extra]...)
			binary.LittleEndian.PutUint32(record[18:], 0)
			out = append(out, record)
			position += 30 + names + extra + compressed
		case 0x02014b50:
			names := int(binary.LittleEndian.Uint16(archive[position+28:]))
			extra := int(binary.LittleEndian.Uint16(archive[position+30:]))
			comment := int(binary.LittleEndian.Uint16(archive[position+32:]))
			record := append([]byte(nil), archive[position:position+46+names+extra+comment]...)
			binary.LittleEndian.PutUint32(record[20:], 0)
			binary.LittleEndian.PutUint32(record[42:], 0)
			out = append(out, record)
			position += len(record)
		case 0x06054b50:
			record := append([]byte(nil), archive[position:]...)
			binary.LittleEndian.PutUint32(record[16:], 0)
			out = append(out, record)
			position = len(archive)
		default:
			t.Fatalf("unexpected record %x at %d", signature, position)
		}
	}
	return out
}

// contents inflates every local entry in order (archive/zip refuses a
// directory entry that carries data, which zipfile writes).
func contents(t *testing.T, archive []byte) []string {
	t.Helper()
	var out []string
	position := 0
	for binary.LittleEndian.Uint32(archive[position:]) == 0x04034b50 {
		names := int(binary.LittleEndian.Uint16(archive[position+26:]))
		extra := int(binary.LittleEndian.Uint16(archive[position+28:]))
		compressed := int(binary.LittleEndian.Uint32(archive[position+18:]))
		name := string(archive[position+30 : position+30+names])
		body := archive[position+30+names+extra : position+30+names+extra+compressed]
		data, err := io.ReadAll(flate.NewReader(bytes.NewReader(body)))
		if err != nil {
			t.Fatal(err)
		}
		if crc32.ChecksumIEEE(data) != binary.LittleEndian.Uint32(archive[position+14:]) {
			t.Fatalf("CRC of %q", name)
		}
		out = append(out, name+"\x00"+string(data))
		position += 30 + names + extra + compressed
	}
	return out
}

func TestPythonZipGolden(t *testing.T) {
	raw, err := os.ReadFile("testdata/python_zip.json")
	if err != nil {
		t.Fatal(err)
	}
	var golden struct {
		Cases []goldenCase `json:"cases"`
	}
	if err = json.Unmarshal(raw, &golden); err != nil {
		t.Fatal(err)
	}
	if len(golden.Cases) == 0 {
		t.Fatal("no golden cases")
	}
	for _, c := range golden.Cases {
		t.Run(c.Name, func(t *testing.T) {
			at := time.Date(c.At[0], time.Month(c.At[1]), c.At[2], c.At[3], c.At[4], c.At[5], 0, time.UTC)
			var w Writer
			for _, e := range c.Entries {
				data, _ := base64.StdEncoding.DecodeString(e.Data)
				if err := w.WriteStr(e.Name, data, at); err != nil {
					t.Fatal(err)
				}
			}
			actual, err := w.Close()
			if err != nil {
				t.Fatal(err)
			}
			expected, _ := base64.StdEncoding.DecodeString(c.Zip)
			want, got := records(t, expected), records(t, actual)
			if len(want) != len(got) {
				t.Fatalf("records: want %d, got %d", len(want), len(got))
			}
			for i := range want {
				if !bytes.Equal(want[i], got[i]) {
					t.Fatalf("record %d differs:\nwant %x\ngot  %x", i, want[i], got[i])
				}
			}
			wantData, gotData := contents(t, expected), contents(t, actual)
			if len(wantData) != len(gotData) {
				t.Fatalf("entries differ")
			}
			for i := range wantData {
				if wantData[i] != gotData[i] {
					t.Fatalf("entry %d content differs", i)
				}
			}
		})
	}
}

func TestRefusals(t *testing.T) {
	var w Writer
	at := time.Date(2026, 1, 1, 0, 0, 0, 0, time.UTC)
	if err := w.WriteStr("a\x00b", nil, at); err != ErrName {
		t.Fatalf("NUL name: %v", err)
	}
	if err := w.WriteStr(string([]byte{0xff}), nil, at); err != ErrName {
		t.Fatalf("invalid UTF-8: %v", err)
	}
}
