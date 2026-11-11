package pymime

import (
	"encoding/json"
	"errors"
	"os"
	"testing"
)

// Expected values: Python 3.13.16 mimetypes.MimeTypes().guess_type(name)[0]
// (built-in table only, which Load(nil) reproduces).
func TestGuessMatchesPythonDefaults(t *testing.T) {
	table, err := Load(nil)
	if err != nil {
		t.Fatal(err)
	}
	for name, want := range map[string]string{
		"a.pdf": "application/pdf", "a.PDF": "application/pdf", "x/y.tar.gz": "application/x-tar", "b.tgz": "application/x-tar",
		"b.TGZ": "application/x-tar", "c.svgz": "image/svg+xml", ".bashrc": "", "..pdf": "", "a..pdf": "application/pdf",
		"dir.d/file": "", "noext": "", "a.b.c.txt": "text/plain", "f.gz": "", "f.Z": "", "f.z": "",
	} {
		got, ok, err := table.Guess(name)
		if err != nil || ok != (want != "") || got != want {
			t.Errorf("Guess(%q) = %q, %v, %v; want %q", name, got, ok, err, want)
		}
	}
	// Names urlparse reads as a URL: mimetypes.guess_type's own answers (CPython 3.13).
	for _, item := range [][3]string{
		{"data:text/plain,x.pdf", "text/plain", ""},
		{"ab:c.pdf", "application/pdf", ""},
		{" \tab:c.pdf", "application/pdf", ""},
		{"Nota:spese.pdf", "application/pdf", ""},
		{"NOTA:Spese.PDF", "application/pdf", ""},
		{"nota:spese", "", ""},
		{"nota:a.tar.gz", "application/x-tar", "gzip"},
		{"ab:c.pdf#x.txt", "application/pdf", ""},
		{"ab:c.pdf?x.txt", "application/pdf", ""},
		{"ab:c.txt;v.pdf", "application/pdf", ""},
		{"http:c.txt;v.pdf", "text/plain", ""},
		{"http:a;b/c.txt;v.pdf", "text/plain", ""},
		{"http://host/c.png", "image/png", ""},
		{"ab://host", "", ""},
		{"ab://host.pdf", "", ""},
		{"ab://host?x/y.pdf", "", ""},
		{"ab://host/dir.d/f.html", "text/html", ""},
		{"a+b-c.9:x.txt", "text/plain", ""},
		{"a_b:x.txt", "text/plain", ""},
		{"1ab:x.txt", "text/plain", ""},
		{"ab:", "", ""},
		{"data:x.pdf", "", ""},
		{"data:,x.pdf", "text/plain", ""},
		{"data:image/png;base64,AAAA.pdf", "image/png", ""},
		{"data:a=b/c,x", "text/plain", ""},
		{"data:text,x", "text/plain", ""},
		{"DATA:Text/HTML,x", "Text/HTML", ""},
		{"data://host/text/css,x", "/text/css", ""},
		{"data:text/css#,x", "", ""},
		{"ab:\nc.p\tdf", "application/pdf", ""},
		{"ab:x.pdf\n", "application/pdf", ""},
		{"àb:c.pdf", "application/pdf", ""},
		{"ab:ç.pdf", "application/pdf", ""},
		{"tel:x.txt;y.pdf", "text/plain", ""},
		{"ab:.pdf", "", ""},
		{"ab:a/.pdf", "", ""},
		{"ab:a.d/b", "", ""},
	} {
		kind, ok, encoding, err := table.GuessType(item[0])
		if err != nil || ok != (item[1] != "") || kind != item[1] || encoding != item[2] {
			t.Errorf("GuessType(%q) = %q, %v, %q, %v; want %q, %q", item[0], kind, ok, encoding, err, item[1], item[2])
		}
	}
	raw, err := os.ReadFile("testdata/url_vectors.json")
	if err != nil || len(raw) == 0 {
		t.Fatal("url vectors:", err)
	}
	var vectors map[string][]*string
	if err = json.Unmarshal(raw, &vectors); err != nil || len(vectors) < 50 {
		t.Fatal("url vectors:", err, len(vectors))
	}
	for name, want := range vectors {
		kind, ok, _, err := table.GuessType(name)
		if *want[0] == "err" {
			var failure *ValueError
			if !errors.As(err, &failure) || failure.Text != *want[1] {
				t.Errorf("GuessType(%q) error = %v; want ValueError %q", name, err, *want[1])
			}
			continue
		}
		if err != nil || ok != (want[1] != nil) || (ok && kind != *want[1]) {
			t.Errorf("GuessType(%q) = %q, %v, %v; want %v", name, kind, ok, err, want[1])
		}
	}
	if got, ok, err := table.Guess("c:report.pdf"); err != nil || !ok || got != "application/pdf" {
		t.Errorf("one-letter scheme is a path: %q %v %v", got, ok, err)
	}
}
