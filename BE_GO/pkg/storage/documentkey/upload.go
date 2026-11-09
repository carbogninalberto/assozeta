package documentkey

import (
	"strings"
	"time"

	"github.com/google/uuid"
)

// SuspiciousFileError is Django's SuspiciousFileOperation from
// validate_file_name, with its exact message.
type SuspiciousFileError struct{ Message string }

func (e *SuspiciousFileError) Error() string { return e.Message }

// UploadName is the name the upload views pass to default_storage.save:
// os.path.join(STORAGE_DIR, str(created.timestamp()), str(id), filename)
// (STORAGE_DIR is "": an absolute filename replaces the directory), checked
// by Storage.save's validate_file_name(name, allow_relative_path=True).
// The result is clean_name(name), the name S3Storage stores and returns; its
// key is S3Key(result, location). Names that pass this check cannot fail
// get_available_name's own path checks.
func UploadName(created time.Time, id uuid.UUID, filename string) (string, error) {
	created = created.UTC()
	if created.Year() < 1 || created.Year() > 9999 || created.Nanosecond()%1000 != 0 {
		return "", ErrStoredName
	}
	name := filename
	if !strings.HasPrefix(filename, "/") {
		name = pythonTimestamp(created) + "/" + id.String() + "/" + filename
	}
	if base := name[strings.LastIndex(name, "/")+1:]; base == "" || base == "." || base == ".." {
		return "", &SuspiciousFileError{Message: "Could not derive file name from '" + name + "'"}
	}
	posix := strings.ReplaceAll(name, `\`, "/")
	traversal := strings.HasPrefix(posix, "/")
	for _, part := range strings.Split(posix, "/") {
		traversal = traversal || part == ".."
	}
	if traversal {
		return "", &SuspiciousFileError{Message: "Detected path traversal attempt in '" + name + "'"}
	}
	// storages.utils.clean_name.
	cleaned := strings.ReplaceAll(normpath(name), `\`, "/")
	if strings.HasSuffix(name, "/") && !strings.HasSuffix(cleaned, "/") {
		cleaned += "/"
	}
	if cleaned == "." {
		cleaned = ""
	}
	return cleaned, nil
}
