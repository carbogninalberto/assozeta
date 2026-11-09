package xlsxexport

import (
	"archive/zip"
	"bytes"
	"io"
	"testing"
	"time"
)

// TestOpenpyxlRowsResponse checks the streamed workbook (wb.save(HttpResponse))
// against OpenpyxlRows: the same entries and parts, every entry with flag 0x08.
func TestOpenpyxlRowsResponse(t *testing.T) {
	now := time.Date(2026, 3, 1, 10, 0, 0, 0, time.UTC)
	titles := []string{"Iscrizioni", "Pagamenti"}
	sheets := [][][]string{{{"a", "b"}, {"1", "-"}}, {{"c"}, {"x\ny"}}}
	plain, err := OpenpyxlRows(titles, sheets, now)
	if err != nil {
		t.Fatal(err)
	}
	streamed, err := OpenpyxlRowsResponse(titles, sheets, now)
	if err != nil {
		t.Fatal(err)
	}
	read := func(data []byte) *zip.Reader {
		reader, err := zip.NewReader(bytes.NewReader(data), int64(len(data)))
		if err != nil {
			t.Fatal(err)
		}
		return reader
	}
	want, got := read(plain), read(streamed)
	if len(want.File) != len(got.File) {
		t.Fatalf("entries %d != %d", len(got.File), len(want.File))
	}
	for i, file := range got.File {
		if file.Name != want.File[i].Name || file.Flags != zipDataDescriptor || want.File[i].Flags != 0 ||
			file.ExternalAttrs != want.File[i].ExternalAttrs {
			t.Fatalf("entry %d: %s flags %#x", i, file.Name, file.Flags)
		}
		open := func(f *zip.File) []byte {
			reader, err := f.Open()
			if err != nil {
				t.Fatal(err)
			}
			defer reader.Close()
			data, err := io.ReadAll(reader)
			if err != nil {
				t.Fatal(err)
			}
			return data
		}
		if !bytes.Equal(open(file), open(want.File[i])) {
			t.Fatalf("entry %s differs", file.Name)
		}
	}
	// The local header carries zero sizes and the signed descriptor follows the data.
	if !bytes.Contains(streamed, []byte{0x50, 0x4b, 0x07, 0x08}) {
		t.Fatal("no data descriptor")
	}
}
