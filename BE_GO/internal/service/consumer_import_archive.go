package service

import (
	"bytes"
	"encoding/json"
	"errors"
	"strconv"
	"strings"
	"unicode/utf8"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/importeffects"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/pyzipfile"
)

// errConsumerImportUnsupported marks archive content outside the reproduced
// subset (the message is handed to Django).
var errConsumerImportUnsupported = importeffects.ErrUnsupported

// archiveError is a Python exception from reading the archive. It still
// matches errConsumerImportUnsupported for callers that do not reproduce
// Django's handling of it yet; callers that do read the exception with
// errors.As.
type archiveError struct{ exc *pyzipfile.Exception }

func (e *archiveError) Error() string { return e.exc.Text }

// Is makes every Python exception match the old refusal sentinel.
func (e *archiveError) Is(target error) bool { return target == errConsumerImportUnsupported }

// As exposes the Python exception to callers that reproduce Django's
// handling of a particular class or message.
func (e *archiveError) As(target any) bool {
	if p, ok := target.(**pyzipfile.Exception); ok {
		*p = e.exc
		return true
	}
	return false
}

// archiveWrap converts a pyzipfile Python exception to the service's error.
func archiveWrap(err error) error {
	if err == nil {
		return nil
	}
	var exc *pyzipfile.Exception
	if errors.As(err, &exc) {
		return &archiveError{exc: exc}
	}
	return err
}

// consumerImportIsZipFile is zipfile.is_zipfile(path). Python swallows every
// read error and returns False, so this returns a nil error for content and
// I/O failures alike.
func consumerImportIsZipFile(path string) (bool, error) {
	return pyzipfile.IsZipfile(path)
}

// consumerImportArchive is zipfile.ZipFile(path) in read mode.
type consumerImportArchive struct {
	archive *pyzipfile.Archive
	names   []string
	byName  map[string]pyzipfile.Info
}

func consumerImportOpenArchive(path string) (*consumerImportArchive, error) {
	archive, err := pyzipfile.Open(path)
	if err != nil {
		return nil, archiveWrap(err)
	}
	a := &consumerImportArchive{
		archive: archive,
		names:   archive.NameList(),
	}
	a.byName = make(map[string]pyzipfile.Info, len(a.names))
	for _, name := range a.names {
		info, _ := archive.Info(name)
		a.byName[name] = info
	}
	return a, nil
}

func (a *consumerImportArchive) Close() { a.archive.Close() }

func (a *consumerImportArchive) has(name string) bool { return a.archive.Has(name) }

// read is ZipFile.read(name): the decompressed member, CRC checked.
func (a *consumerImportArchive) read(name string) ([]byte, error) {
	data, err := a.archive.Read(name)
	if err != nil {
		return nil, archiveWrap(err)
	}
	return data, nil
}

// verify reads a member through and checks its CRC without keeping it.
func (a *consumerImportArchive) verify(name string) (int64, error) {
	member, err := a.archive.OpenMember(name)
	if err != nil {
		return 0, archiveWrap(err)
	}
	defer member.Close()
	var total int64
	for {
		data, err := member.Read(1 << 20)
		if err != nil {
			return total, archiveWrap(err)
		}
		total += int64(len(data))
		if len(data) == 0 {
			return total, nil
		}
	}
}

// openMember is ZipFile.open(name).
func (a *consumerImportArchive) openMember(name string) (*pyzipfile.Member, error) {
	member, err := a.archive.OpenMember(name)
	if err != nil {
		return nil, archiveWrap(err)
	}
	return member, nil
}

// archiveBadCRC reports the BadZipFile("Bad CRC-32 for file ...") exception.
func archiveBadCRC(err error) bool {
	var exc *pyzipfile.Exception
	return errors.As(err, &exc) && exc.Type == "BadZipFile" &&
		strings.HasPrefix(exc.Text, "Bad CRC-32 for file ")
}

// consumerImportJSON is json.loads(data) (and iter_json_array for an array)
// for UTF-8 input without a byte order mark; other input is unsupported.
func consumerImportJSON(data []byte) (any, error) {
	if bytes.HasPrefix(data, []byte("\xef\xbb\xbf")) || !utf8.Valid(data) || !json.Valid(data) {
		return nil, errConsumerImportUnsupported
	}
	value, err := dto.ParseProfileTablesJSON(data)
	if err != nil {
		return nil, errConsumerImportUnsupported
	}
	return value, nil
}

// importGet is dict.get(key) on a decoded JSON object.
func importGet(object domain.RequestObject, key string) (any, bool) {
	for _, field := range object {
		if field.Name == key {
			return field.Value, true
		}
	}
	return nil, false
}

func importSet(object domain.RequestObject, key string, value any) domain.RequestObject {
	for i, field := range object {
		if field.Name == key {
			object[i].Value = value
			return object
		}
	}
	return append(object, domain.RequestField{Name: key, Value: value})
}

func importPop(object domain.RequestObject, key string) (domain.RequestObject, any, bool) {
	for i, field := range object {
		if field.Name == key {
			return append(object[:i:i], object[i+1:]...), field.Value, true
		}
	}
	return object, nil, false
}

// importTypeName is type(value).__name__ for decoded JSON.
func importTypeName(value any) string {
	switch v := value.(type) {
	case nil:
		return "NoneType"
	case string:
		return "str"
	case bool:
		return "bool"
	case json.Number:
		if strings.ContainsAny(string(v), ".eE") {
			return "float"
		}
		return "int"
	case domain.RequestObject:
		return "dict"
	case []any:
		return "list"
	}
	return "object"
}

// importTruthy is bool(value) for decoded JSON.
func importTruthy(value any) bool {
	switch v := value.(type) {
	case nil:
		return false
	case string:
		return v != ""
	case bool:
		return v
	case json.Number:
		f, err := strconv.ParseFloat(string(v), 64)
		return err != nil || f != 0
	case domain.RequestObject:
		return len(v) > 0
	case []any:
		return len(v) > 0
	case importeffects.Value:
		return !v.IsNone()
	}
	return true
}

// importStr is str(value) for decoded JSON scalars (containers are unsupported).
func importStr(value any) (string, error) {
	switch v := value.(type) {
	case nil:
		return "None", nil
	case string:
		return v, nil
	case bool:
		if v {
			return "True", nil
		}
		return "False", nil
	case json.Number:
		parsed, err := importeffects.FromJSON(v)
		if err != nil {
			return "", err
		}
		return parsed.S, nil
	case importeffects.Value:
		return v.Text()
	}
	return "", errConsumerImportUnsupported
}
