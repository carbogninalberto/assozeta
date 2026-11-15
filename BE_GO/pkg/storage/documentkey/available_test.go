package documentkey

import (
	"context"
	"errors"
	"strings"
	"testing"
)

// fakeExists is S3Storage.exists over a set of taken names.
func fakeExists(taken ...string) func(context.Context, string) (bool, error) {
	set := make(map[string]bool, len(taken))
	for _, name := range taken {
		set[name] = true
	}
	return func(_ context.Context, name string) (bool, error) { return set[name], nil }
}

// fixedRandom is get_random_string(7) with a fixed alphabet run.
func fixedRandom(values ...string) func() string {
	index := 0
	return func() string {
		value := values[index%len(values)]
		index++
		return value
	}
}

// Expected values: Django 5 Storage.get_available_name on S3Storage with
// AWS_S3_FILE_OVERWRITE off (random "AbCdEfG").
func TestAvailableName(t *testing.T) {
	for _, tc := range []struct {
		name   string
		taken  []string
		random func() string
		want   string
	}{
		{"signature_1731686400.123456.png", nil, fixedRandom("AbCdEfG"), "signature_1731686400.123456.png"},
		{"signature_1731686400.123456.png", []string{"signature_1731686400.123456.png"}, fixedRandom("AbCdEfG"),
			"signature_1731686400_AbCdEfG.123456.png"},
		{"picture_1731686400.0.jpg", []string{"picture_1731686400.0.jpg"}, fixedRandom("AbCdEfG"),
			"picture_1731686400_AbCdEfG.0.jpg"},
		{".hidden.txt", []string{".hidden.txt"}, fixedRandom("AbCdEfG"), ".hidden_AbCdEfG.txt"},
		{"a.", []string{"a."}, fixedRandom("AbCdEfG"), "a._AbCdEfG"},
		{"noext", []string{"noext"}, fixedRandom("AbCdEfG"), "noext_AbCdEfG"},
		{"x..y", []string{"x..y"}, fixedRandom("AbCdEfG"), "x_AbCdEfG..y"},
		{"...x", []string{"...x"}, fixedRandom("AbCdEfG"), "...x_AbCdEfG"},
		{"a..b.", []string{"a..b."}, fixedRandom("AbCdEfG"), "a..b._AbCdEfG"},
		{"a/b/c.d.e.f", []string{"a/b/c.d.e.f"}, fixedRandom("AbCdEfG"), "a/b/c_AbCdEfG.d.e.f"},
		// Two alternatives: the first random name is taken as well.
		{"associates/x/picture_1.5.jpg", []string{"associates/x/picture_1.5.jpg", "associates/x/picture_1_AbCdEfG.5.jpg"},
			fixedRandom("AbCdEfG", "hIjKlMn"), "associates/x/picture_1_hIjKlMn.5.jpg"},
		// A directory keeps its shape and the alternative joins into it.
		{"dir/file.tar.gz", []string{"dir/file.tar.gz"}, fixedRandom("AbCdEfG"), "dir/file_AbCdEfG.tar.gz"},
		{"dir//file.txt", []string{"dir/file.txt"}, fixedRandom("AbCdEfG"), "dir/file_AbCdEfG.txt"},
		// clean_name: backslashes and dot segments.
		{`dir\file.txt`, []string{"dir/file.txt"}, fixedRandom("AbCdEfG"), "dir/file_AbCdEfG.txt"},
		{"./file.txt", []string{"file.txt"}, fixedRandom("AbCdEfG"), "file_AbCdEfG.txt"},
	} {
		got, err := AvailableName(context.Background(), tc.name, fakeExists(tc.taken...), tc.random)
		if err != nil || got != tc.want {
			t.Errorf("AvailableName(%q, taken %v) = %q, %v; want %q", tc.name, tc.taken, got, err, tc.want)
		}
	}
}

func TestAvailableNameErrors(t *testing.T) {
	boom := errors.New("head failed")
	if _, err := AvailableName(context.Background(), "a.png", func(context.Context, string) (bool, error) { return false, boom },
		fixedRandom("AbCdEfG")); !errors.Is(err, boom) {
		t.Errorf("exists error = %v, want %v", err, boom)
	}
	for _, tc := range []struct{ name, message string }{
		{"../x.png", "Detected path traversal attempt in '..'"},
		{"../", "Detected path traversal attempt in '..'"},
		{"", "Could not derive file name from ''"},
		{".", "Could not derive file name from ''"},
		{"/", "Could not derive file name from ''"},
		{"a/", "Could not derive file name from ''"},
		{"a/..", "Could not derive file name from ''"},
		{"a/../", "Could not derive file name from ''"},
		{"..", "Could not derive file name from '..'"},
	} {
		_, err := AvailableName(context.Background(), tc.name, fakeExists(), fixedRandom("AbCdEfG"))
		var suspicious *SuspiciousFileError
		if !errors.As(err, &suspicious) || suspicious.Message != tc.message {
			t.Errorf("AvailableName(%q) = %v; want %q", tc.name, err, tc.message)
		}
	}
	if _, err := AvailableName(context.Background(), "a\x00.png", fakeExists(), fixedRandom("AbCdEfG")); !errors.Is(err, ErrStoredName) {
		t.Errorf("NUL name: %v", err)
	}
	if _, err := AvailableName(context.Background(), "a\xff.png", fakeExists(), fixedRandom("AbCdEfG")); !errors.Is(err, ErrStoredName) {
		t.Errorf("invalid UTF-8 name: %v", err)
	}
}

// RandomName7 is get_random_string(7): seven characters of
// django.utils.crypto.RANDOM_STRING_CHARS.
func TestRandomName7(t *testing.T) {
	seen := map[byte]bool{}
	for i := 0; i < 512; i++ {
		name := RandomName7()
		if len(name) != 7 {
			t.Fatalf("RandomName7() = %q, length %d", name, len(name))
		}
		for index := 0; index < len(name); index++ {
			if !strings.ContainsRune(randomNameAlphabet, rune(name[index])) {
				t.Fatalf("RandomName7() = %q, unexpected character %q", name, name[index])
			}
			seen[name[index]] = true
		}
	}
	if len(seen) < 20 {
		t.Errorf("RandomName7 drew only %d distinct characters", len(seen))
	}
}
