package pymime

import "testing"

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
	for _, name := range []string{"data:text/plain,x.pdf", "ab:c.pdf", " \tab:c.pdf"} {
		if _, _, err := table.Guess(name); err != ErrURLName {
			t.Errorf("Guess(%q) error = %v; want ErrURLName", name, err)
		}
	}
	if got, ok, err := table.Guess("c:report.pdf"); err != nil || !ok || got != "application/pdf" {
		t.Errorf("one-letter scheme is a path: %q %v %v", got, ok, err)
	}
}
