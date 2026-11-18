package service

import (
	"encoding/binary"
	"errors"
	"hash/crc32"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/importeffects"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/pyzipfile"
)

// TestConsumerImportJSONRecursionEdge: the Celery import worker parses with
// json.loads up to the edge measured for the same worker and parsers in the
// restore task; one level deeper is Django's RecursionError, which the task's
// except reports. A data file Django streams with iter_json_array passes one
// level more, since the file's array level is not parsed recursively.
func TestConsumerImportJSONRecursionEdge(t *testing.T) {
	nested := func(depth int) []byte {
		return []byte(strings.Repeat("[", depth) + strings.Repeat("]", depth))
	}
	for _, edge := range []int{consumerImportJSONEdge, consumerImportJSONEdge + 1} {
		for _, depth := range []int{edge, edge - 1, 2000} {
			if _, err := consumerImportJSON(nested(depth), edge); err != nil {
				t.Fatalf("edge %d depth %d: %v", edge, depth, err)
			}
		}
		_, err := consumerImportJSON(nested(edge+1), edge)
		var django *importeffects.DjangoError
		if !errors.As(err, &django) || django.Class != "RecursionError" || django.Message != recursionArrayText {
			t.Fatalf("edge %d: err=%v, want Django's RecursionError", edge, err)
		}
		if errors.Is(err, errImportArchive) {
			t.Fatalf("edge %d: %v, want the Django error, not the archive hand-off", edge, err)
		}
	}
}

// storedZip builds one stored member with the given central/local flags and
// CRC, so the service tests can exercise crafted archives without depending
// on archive/zip's reading.
func storedZip(name string, data []byte, flags uint16, crc uint32) []byte {
	nameBytes := []byte(name)
	out := make([]byte, 0, 30+len(nameBytes)+len(data)+46+len(nameBytes)+22)
	out = append(out, []byte("PK\x03\x04")...)
	out = append(out, 20, 0) // extract_version, extract_system
	out = binary.LittleEndian.AppendUint16(out, flags)
	out = binary.LittleEndian.AppendUint16(out, 0) // stored
	out = binary.LittleEndian.AppendUint16(out, 0)
	out = binary.LittleEndian.AppendUint16(out, 0)
	out = binary.LittleEndian.AppendUint32(out, crc)
	out = binary.LittleEndian.AppendUint32(out, uint32(len(data)))
	out = binary.LittleEndian.AppendUint32(out, uint32(len(data)))
	out = binary.LittleEndian.AppendUint16(out, uint16(len(nameBytes)))
	out = binary.LittleEndian.AppendUint16(out, 0)
	out = append(out, nameBytes...)
	out = append(out, data...)
	cd := len(out)
	out = append(out, []byte("PK\x01\x02")...)
	out = append(out, 20, 3, 20, 0)
	out = binary.LittleEndian.AppendUint16(out, flags)
	out = binary.LittleEndian.AppendUint16(out, 0)
	out = binary.LittleEndian.AppendUint16(out, 0)
	out = binary.LittleEndian.AppendUint16(out, 0)
	out = binary.LittleEndian.AppendUint32(out, crc)
	out = binary.LittleEndian.AppendUint32(out, uint32(len(data)))
	out = binary.LittleEndian.AppendUint32(out, uint32(len(data)))
	out = binary.LittleEndian.AppendUint16(out, uint16(len(nameBytes)))
	out = binary.LittleEndian.AppendUint16(out, 0)
	out = binary.LittleEndian.AppendUint16(out, 0)
	out = binary.LittleEndian.AppendUint16(out, 0)
	out = binary.LittleEndian.AppendUint16(out, 0)
	out = binary.LittleEndian.AppendUint32(out, 0o600<<16)
	out = binary.LittleEndian.AppendUint32(out, 0)
	out = append(out, nameBytes...)
	cdSize := len(out) - cd
	out = append(out, []byte("PK\x05\x06")...)
	out = append(out, 0, 0, 0, 0)
	out = binary.LittleEndian.AppendUint16(out, 1)
	out = binary.LittleEndian.AppendUint16(out, 1)
	out = binary.LittleEndian.AppendUint32(out, uint32(cdSize))
	out = binary.LittleEndian.AppendUint32(out, uint32(cd))
	out = binary.LittleEndian.AppendUint16(out, 0)
	return out
}

func writeTemp(t *testing.T, data []byte) string {
	t.Helper()
	path := filepath.Join(t.TempDir(), "upload.zip")
	if err := os.WriteFile(path, data, 0o600); err != nil {
		t.Fatal(err)
	}
	return path
}

func TestConsumerImportArchiveReadsPythonZipfileOutcomes(t *testing.T) {
	data := []byte("manifest")
	crc := crc32.ChecksumIEEE(data)
	path := writeTemp(t, storedZip("manifest.json", data, 0, crc))

	isZip, err := consumerImportIsZipFile(path)
	if err != nil || !isZip {
		t.Fatalf("is_zipfile = %v, %v", isZip, err)
	}
	archive, err := consumerImportOpenArchive(path)
	if err != nil {
		t.Fatal(err)
	}
	defer archive.Close()
	if got := archive.names; len(got) != 1 || got[0] != "manifest.json" {
		t.Fatalf("names = %q", got)
	}
	if !archive.has("manifest.json") || archive.has("other") {
		t.Fatal("has() mismatch")
	}
	body, err := archive.read("manifest.json")
	if err != nil || string(body) != "manifest" {
		t.Fatalf("read = %q, %v", body, err)
	}
	if n, err := archive.verify("manifest.json"); err != nil || n != 8 {
		t.Fatalf("verify = %d, %v", n, err)
	}
	info := archive.byName["manifest.json"]
	if info.Filename != "manifest.json" || info.FileSize != 8 || info.CompressSize != 8 {
		t.Fatalf("info = %+v", info)
	}
}

func TestConsumerImportArchiveNotAZip(t *testing.T) {
	path := writeTemp(t, []byte("not a zip"))
	isZip, err := consumerImportIsZipFile(path)
	if err != nil || isZip {
		t.Fatalf("is_zipfile = %v, %v", isZip, err)
	}
	_, err = consumerImportOpenArchive(path)
	if !errors.Is(err, errImportArchive) {
		t.Fatalf("error %v does not match the refusal sentinel", err)
	}
	var exc *pyzipfile.Exception
	if !errors.As(err, &exc) || exc.Type != "BadZipFile" || exc.Text != "File is not a zip file" {
		t.Fatalf("exception = %+v", exc)
	}
}

func TestConsumerImportArchiveMissingMember(t *testing.T) {
	path := writeTemp(t, storedZip("a.txt", []byte("a"), 0, crc32.ChecksumIEEE([]byte("a"))))
	archive, err := consumerImportOpenArchive(path)
	if err != nil {
		t.Fatal(err)
	}
	defer archive.Close()
	_, err = archive.read("nope")
	var exc *pyzipfile.Exception
	if !errors.As(err, &exc) || exc.Type != "KeyError" {
		t.Fatalf("error = %v", err)
	}
	if exc.Text != `"There is no item named 'nope' in the archive"` {
		t.Fatalf("text = %q", exc.Text)
	}
	if !errors.Is(err, errImportArchive) {
		t.Fatal("missing member must still match the refusal sentinel")
	}
}

func TestConsumerImportArchiveEncryptedAndCRC(t *testing.T) {
	data := []byte("secret")
	crc := crc32.ChecksumIEEE(data)
	archive, err := consumerImportOpenArchive(writeTemp(t, storedZip("x.txt", data, 1, crc)))
	if err != nil {
		t.Fatal(err)
	}
	_, err = archive.read("x.txt")
	archive.Close()
	var exc *pyzipfile.Exception
	if !errors.As(err, &exc) || exc.Type != "RuntimeError" ||
		exc.Text != "File 'x.txt' is encrypted, password required for extraction" {
		t.Fatalf("encrypted error = %+v", exc)
	}

	archive, err = consumerImportOpenArchive(writeTemp(t, storedZip("x.txt", data, 0, crc^1)))
	if err != nil {
		t.Fatal(err)
	}
	_, err = archive.verify("x.txt")
	archive.Close()
	if !archiveBadCRC(err) {
		t.Fatalf("bad CRC error = %v", err)
	}
}
