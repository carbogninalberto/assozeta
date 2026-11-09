package documentkey

import (
	"errors"
	"testing"
	"time"

	"github.com/google/uuid"
)

// Expected values: django-storages 1.14.5 safe_join(location, clean_name(name)).
func TestS3KeyMatchesDjangoStorages(t *testing.T) {
	for _, tc := range []struct{ name, location, key, outside string }{
		{"a/b.pdf", "loc", "loc/a/b.pdf", ""}, {"../x", "loc", "", "../x"}, {"a/../../x", "loc", "", "../x"},
		{`a\b.pdf`, "loc", "loc/a/b.pdf", ""}, {"/abs", "loc", "", "/abs"}, {"a/b/", "loc", "loc/a/b/", ""},
		{"x", "loc/", "loc/x", ""}, {"x", "", "x", ""}, {"//x", "", "x", ""}, {"./a", "loc", "loc/a", ""},
		{"a/./b", "loc/sub", "loc/sub/a/b", ""},
	} {
		key, err := S3Key(tc.name, tc.location)
		var outside *OutsideLocationError
		if tc.outside != "" {
			if !errors.As(err, &outside) || outside.Name != tc.outside {
				t.Errorf("S3Key(%q, %q) = %q, %v; want outside %q", tc.name, tc.location, key, err, tc.outside)
			}
			continue
		}
		if err != nil || key != tc.key {
			t.Errorf("S3Key(%q, %q) = %q, %v; want %q", tc.name, tc.location, key, err, tc.key)
		}
	}
}

func TestStoredName(t *testing.T) {
	id := uuid.MustParse("a1000000-0000-4000-8000-000000983001")
	created := time.Date(2026, 2, 3, 4, 5, 6, 123456000, time.UTC)
	if name, err := StoredName(created, id, "report.pdf", nil); err != nil || name != "1770091506.123456/"+id.String()+"/report.pdf" {
		t.Fatalf("timestamp name = %q, %v", name, err)
	}
	empty, path := "", "archive/x.zip"
	if name, _ := StoredName(created, id, "report.pdf", &empty); name != "1770091506.123456/"+id.String()+"/report.pdf" {
		t.Fatalf("empty filepath = %q", name)
	}
	if name, _ := StoredName(created, id, "report.pdf", &path); name != path {
		t.Fatalf("filepath = %q", name)
	}
	if name, _ := StoredName(created, id, "/etc/x", nil); name != "/etc/x" {
		t.Fatalf("absolute filename = %q", name)
	}
}
