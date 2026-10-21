// Package documentkey resolves legacy document keys without performing storage
// operations. A key is not an authorization grant or permission to delete a file.
package documentkey

import (
	"errors"
	"math"
	"math/big"
	"path"
	"strconv"
	"strings"
	"time"
	"unicode/utf8"

	"github.com/google/uuid"
)

var ErrMedicalKey = errors.New("invalid medical document storage key")

// MedicalS3Key matches the timestamp/UUID/filename convention used by Django's
// medical upload and cleanup, followed by django-storages S3 normalization.
// filepath is intentionally not an input: those legacy operations ignore it.
// Names resolving outside this document's directory are rejected, even when the
// legacy cleanup would delete that other key. This does not resolve filesystem
// paths or prove that the object is unreferenced or safe to delete.
func MedicalS3Key(created time.Time, id uuid.UUID, filename, location string) (string, error) {
	created = created.UTC()
	if created.Year() < 1 || created.Year() > 9999 || created.Nanosecond()%1000 != 0 ||
		!utf8.ValidString(filename) || !utf8.ValidString(location) || strings.ContainsRune(filename, 0) || strings.ContainsRune(location, 0) ||
		strings.HasPrefix(filename, "/") || strings.HasPrefix(location, "/") {
		return "", ErrMedicalKey
	}
	// datetime.timestamp() divides the integer timedelta microseconds by 1e6.
	// Converting that integer to float before division can round twice and change
	// the last key digit for old/future records. Round the exact rational once.
	micros := created.Unix()*1_000_000 + int64(created.Nanosecond()/1000)
	seconds, _ := new(big.Rat).SetFrac64(micros, 1_000_000).Float64()
	var timestamp string
	if absolute := math.Abs(seconds); absolute != 0 && absolute < 1e-4 {
		timestamp = strconv.FormatFloat(seconds, 'e', -1, 64)
	} else {
		timestamp = strconv.FormatFloat(seconds, 'f', -1, 64)
		if !strings.Contains(timestamp, ".") {
			timestamp += ".0"
		}
	}
	directory := timestamp + "/" + id.String() + "/"
	// clean_name normalizes POSIX components before replacing backslashes; the
	// subsequent safe_join normalizes again. Preserve meaningful trailing slashes.
	name := directory + filename
	cleaned := strings.ReplaceAll(path.Clean(name), `\`, "/")
	if strings.HasSuffix(name, "/") && !strings.HasSuffix(cleaned, "/") {
		cleaned += "/"
	}
	normalized := path.Clean("/" + cleaned)
	if strings.HasSuffix(cleaned, "/") {
		normalized += "/"
	}
	normalized = strings.TrimPrefix(normalized, "/")
	if !strings.HasPrefix(normalized, directory) {
		return "", ErrMedicalKey
	}
	base := strings.TrimRight(location, "/")
	key := path.Clean(base + "/" + normalized)
	if strings.HasSuffix(normalized, "/") {
		key += "/"
	}
	if !strings.HasPrefix(key, base+"/") {
		return "", ErrMedicalKey
	}
	return strings.TrimLeft(key, "/"), nil
}
