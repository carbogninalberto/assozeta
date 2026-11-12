package documentkey

import (
	"errors"
	"testing"
	"time"

	"github.com/google/uuid"
)

// Expected values from Django 5.2.18 validate_file_name(name, allow_relative_path=True)
// and django-storages 1.14.5 clean_name/safe_join on os.path.join("", ts, id, filename).
func TestUploadNameMatchesDjango(t *testing.T) {
	created := time.Date(2026, 11, 9, 2, 36, 22, 42060000, time.UTC)
	id := uuid.MustParse("b49d9c75-1603-44f8-a1bc-07bef3194c57")
	dir := "1794191782.04206/b49d9c75-1603-44f8-a1bc-07bef3194c57/"
	for _, item := range []struct{ filename, message, cleaned string }{
		{"statuto è.pdf", "", dir + "statuto è.pdf"},
		{"/etc/x.pdf", "Detected path traversal attempt in '/etc/x.pdf'", ""},
		{"../x.pdf", "Detected path traversal attempt in '" + dir + "../x.pdf'", ""},
		{"a/../b.pdf", "Detected path traversal attempt in '" + dir + "a/../b.pdf'", ""},
		{`a\..\b.pdf`, "Detected path traversal attempt in '" + dir + `a\..\b.pdf'`, ""},
		{"a//b.pdf", "", dir + "a/b.pdf"},
		{"./c.pdf", "", dir + "c.pdf"},
		{"", "Could not derive file name from '" + dir + "'", ""},
		{".", "Could not derive file name from '" + dir + ".'", ""},
		{"x/.", "Could not derive file name from '" + dir + "x/.'", ""},
		{"dir/", "Could not derive file name from '" + dir + "dir/'", ""},
		{`a\b.txt`, "", dir + "a/b.txt"},
		{"x..y", "", dir + "x..y"},
		{"...", "", dir + "..."},
	} {
		cleaned, err := UploadName(created, id, item.filename)
		var suspicious *SuspiciousFileError
		if item.message != "" {
			if !errors.As(err, &suspicious) || suspicious.Message != item.message {
				t.Errorf("%q: error %v, want %q", item.filename, err, item.message)
			}
			continue
		}
		if err != nil || cleaned != item.cleaned {
			t.Errorf("%q: %q %v, want %q", item.filename, cleaned, err, item.cleaned)
		}
		if key, err := S3Key(cleaned, "loc"); err != nil || key != "loc/"+item.cleaned {
			t.Errorf("%q: key %q %v", item.filename, key, err)
		}
	}
}
