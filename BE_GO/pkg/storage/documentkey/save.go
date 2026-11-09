package documentkey

import (
	"strings"
	"unicode/utf8"
)

// SaveName is the name default_storage.save(name, content) stores on
// S3Storage for a caller-chosen name (docmanager.tasks.save_to_storage):
// Storage.save's validate_file_name(name, allow_relative_path=True), then
// clean_name(name), the name get_available_name checks and _save stores. Its
// key is S3Key(result, location). ErrStoredName rejects inputs these helpers
// do not model (invalid UTF-8, NUL, backslashes, which get_available_name
// re-splits after clean_name).
func SaveName(name string) (string, error) {
	if !utf8.ValidString(name) || strings.ContainsAny(name, "\\\x00") {
		return "", ErrStoredName
	}
	if base := name[strings.LastIndex(name, "/")+1:]; base == "" || base == "." || base == ".." {
		return "", &SuspiciousFileError{Message: "Could not derive file name from '" + name + "'"}
	}
	traversal := strings.HasPrefix(name, "/")
	for _, part := range strings.Split(name, "/") {
		traversal = traversal || part == ".."
	}
	if traversal {
		return "", &SuspiciousFileError{Message: "Detected path traversal attempt in '" + name + "'"}
	}
	// storages.utils.clean_name.
	cleaned := normpath(name)
	if strings.HasSuffix(name, "/") && !strings.HasSuffix(cleaned, "/") {
		cleaned += "/"
	}
	if cleaned == "." {
		cleaned = ""
	}
	return cleaned, nil
}
