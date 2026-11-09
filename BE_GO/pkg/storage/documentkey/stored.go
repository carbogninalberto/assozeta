package documentkey

import (
	"errors"
	"path"
	"strings"
	"time"
	"unicode/utf8"

	"github.com/google/uuid"
)

// ErrStoredName rejects inputs these helpers do not model (invalid UTF-8, NUL,
// dates outside Python's range or below microsecond precision).
var ErrStoredName = errors.New("unsupported stored document name")

// OutsideLocationError is django-storages' SuspiciousOperation from
// _normalize_name: the cleaned name resolves outside AWS_LOCATION.
type OutsideLocationError struct{ Name string }

func (e *OutsideLocationError) Error() string { return "Attempted access to '" + e.Name + "' denied." }

// StoredName is the name PrintingService.download_file/view_file pass to the
// default storage: os.path.join(STORAGE_DIR, filepath) when filepath is truthy,
// otherwise os.path.join(STORAGE_DIR, str(created.timestamp()), str(id),
// filename). Production core.settings resets STORAGE_DIR to the empty string.
func StoredName(created time.Time, id uuid.UUID, filename string, filepath *string) (string, error) {
	if filepath != nil && *filepath != "" {
		return *filepath, nil
	}
	created = created.UTC()
	if created.Year() < 1 || created.Year() > 9999 || created.Nanosecond()%1000 != 0 {
		return "", ErrStoredName
	}
	// os.path.join keeps the last absolute component.
	if strings.HasPrefix(filename, "/") {
		return filename, nil
	}
	return pythonTimestamp(created) + "/" + id.String() + "/" + filename, nil
}

// S3Key is S3Storage._normalize_name(clean_name(name)) under location: the
// object key django-storages reads for name, or *OutsideLocationError.
func S3Key(name, location string) (string, error) {
	if !utf8.ValidString(name) || !utf8.ValidString(location) || strings.ContainsRune(name, 0) || strings.ContainsRune(location, 0) {
		return "", ErrStoredName
	}
	// storages.utils.clean_name.
	cleaned := strings.ReplaceAll(normpath(name), `\`, "/")
	if strings.HasSuffix(name, "/") && !strings.HasSuffix(cleaned, "/") {
		cleaned += "/"
	}
	if cleaned == "." {
		cleaned = ""
	}
	// storages.utils.safe_join(location, cleaned).
	base := strings.TrimRight(location, "/")
	final := base + "/"
	joined := final + cleaned
	if strings.HasPrefix(cleaned, "/") {
		joined = cleaned
	}
	next := normpath(joined)
	if strings.HasSuffix(cleaned, "/") || next+"/" == final {
		next += "/"
	}
	final = next
	if final == base {
		final += "/"
	}
	if !strings.HasPrefix(final, base) || len(final) <= len(base) || final[len(base)] != '/' {
		return "", &OutsideLocationError{Name: cleaned}
	}
	return strings.TrimLeft(final, "/"), nil
}

// normpath is posixpath.normpath: path.Clean except that exactly two leading
// slashes are kept.
func normpath(p string) string {
	if p == "" {
		return "."
	}
	cleaned := path.Clean(p)
	if strings.HasPrefix(p, "//") && !strings.HasPrefix(p, "///") {
		return "/" + cleaned
	}
	return cleaned
}
