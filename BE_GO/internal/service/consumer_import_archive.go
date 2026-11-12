package service

import (
	"archive/zip"
	"bytes"
	"encoding/binary"
	"encoding/json"
	"errors"
	"io"
	"os"
	"strconv"
	"strings"
	"unicode/utf8"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/importeffects"
)

// errConsumerImportUnsupported marks archive content outside the reproduced
// subset (the message is handed to Django).
var errConsumerImportUnsupported = importeffects.ErrUnsupported

// consumerImportIsZipFile is zipfile.is_zipfile: an end of central directory
// record found at the end of the file or within the last 64 KiB + 22 bytes
// (the archive comment). A ZIP64 locator before it is unsupported here.
func consumerImportIsZipFile(path string) (bool, error) {
	file, err := os.Open(path)
	if err != nil {
		return false, err
	}
	defer file.Close()
	info, err := file.Stat()
	if err != nil {
		return false, err
	}
	size := info.Size()
	const record = 22
	if size < record {
		return false, nil
	}
	start := max(size-(1<<16)-record, 0)
	data := make([]byte, size-start)
	if _, err := file.ReadAt(data, start); err != nil {
		return false, err
	}
	signature := []byte("PK\x05\x06")
	at := -1
	tail := data[len(data)-record:]
	if bytes.HasPrefix(tail, signature) && tail[record-2] == 0 && tail[record-1] == 0 {
		at = len(data) - record
	} else {
		at = bytes.LastIndex(data, signature)
		if at < 0 || at+record > len(data) {
			return false, nil
		}
	}
	// _EndRecData64: a ZIP64 locator 20 bytes before the record.
	if offset := start + int64(at) - 20; offset >= 0 {
		locator := make([]byte, 4)
		if _, err := file.ReadAt(locator, offset); err != nil {
			return false, err
		}
		if string(locator) == "PK\x06\x07" {
			return false, errConsumerImportUnsupported
		}
	}
	return true, nil
}

// consumerImportArchive is zipfile.ZipFile(path) for the archives the port
// reads: unique member names, UTF-8 (flagged) or ASCII names, stored or
// deflated members whose local header names match the central directory.
type consumerImportArchive struct {
	reader *zip.ReadCloser
	file   *os.File
	names  []string
	byName map[string]*zip.File
}

func consumerImportOpenArchive(path string) (*consumerImportArchive, error) {
	reader, err := zip.OpenReader(path)
	if err != nil {
		return nil, errConsumerImportUnsupported
	}
	file, err := os.Open(path)
	if err != nil {
		reader.Close()
		return nil, err
	}
	archive := &consumerImportArchive{reader: reader, file: file, byName: map[string]*zip.File{}}
	headers, err := consumerImportLocalHeaders(file, len(reader.File))
	if err != nil {
		archive.Close()
		return nil, errConsumerImportUnsupported
	}
	fail := func() (*consumerImportArchive, error) {
		archive.Close()
		return nil, errConsumerImportUnsupported
	}
	for _, member := range reader.File {
		name := member.Name
		if !utf8.ValidString(name) || strings.ContainsAny(name, "\x00\\") {
			return fail()
		}
		if member.NonUTF8 {
			for i := 0; i < len(name); i++ {
				if name[i] >= 0x80 {
					return fail()
				}
			}
		}
		if _, duplicate := archive.byName[name]; duplicate {
			return fail()
		}
		if member.Method != zip.Store && member.Method != zip.Deflate || member.Flags&0x1 != 0 {
			return fail()
		}
		// ZipFile.open compares the local header's name with the directory's.
		offset, ok := headers[len(archive.names)]
		if !ok {
			return fail()
		}
		header := make([]byte, 30+len(name))
		if _, err := file.ReadAt(header, offset); err != nil || string(header[:4]) != "PK\x03\x04" ||
			int(binary.LittleEndian.Uint16(header[26:28])) != len(name) || string(header[30:]) != name {
			return fail()
		}
		archive.names = append(archive.names, name)
		archive.byName[name] = member
	}
	return archive, nil
}

// consumerImportLocalHeaders lists the local header offset of every central
// directory entry, in directory order (archives without ZIP64 or a prefix).
func consumerImportLocalHeaders(file *os.File, count int) (map[int]int64, error) {
	info, err := file.Stat()
	if err != nil {
		return nil, err
	}
	size := info.Size()
	start := max(size-(1<<16)-22, 0)
	data := make([]byte, size-start)
	if _, err := file.ReadAt(data, start); err != nil {
		return nil, err
	}
	at := bytes.LastIndex(data, []byte("PK\x05\x06"))
	if at < 0 || at+22 > len(data) {
		return nil, errConsumerImportUnsupported
	}
	end := data[at : at+22]
	entries := int(binary.LittleEndian.Uint16(end[10:12]))
	cdSize := int64(binary.LittleEndian.Uint32(end[12:16]))
	cdOffset := int64(binary.LittleEndian.Uint32(end[16:20]))
	if entries != count || cdOffset+cdSize != start+int64(at) {
		return nil, errConsumerImportUnsupported
	}
	directory := make([]byte, cdSize)
	if _, err := file.ReadAt(directory, cdOffset); err != nil {
		return nil, err
	}
	offsets := map[int]int64{}
	for i, position := 0, 0; i < entries; i++ {
		if position+46 > len(directory) || string(directory[position:position+4]) != "PK\x01\x02" {
			return nil, errConsumerImportUnsupported
		}
		entry := directory[position:]
		names, extra, comment := int(binary.LittleEndian.Uint16(entry[28:30])), int(binary.LittleEndian.Uint16(entry[30:32])),
			int(binary.LittleEndian.Uint16(entry[32:34]))
		offsets[i] = int64(binary.LittleEndian.Uint32(entry[42:46]))
		position += 46 + names + extra + comment
	}
	return offsets, nil
}

func (a *consumerImportArchive) Close() {
	a.reader.Close()
	a.file.Close()
}

func (a *consumerImportArchive) has(name string) bool { return a.byName[name] != nil }

// read is ZipFile.read(name): the decompressed member, CRC checked.
func (a *consumerImportArchive) read(name string) ([]byte, error) {
	member := a.byName[name]
	if member == nil {
		return nil, errors.New("missing member")
	}
	reader, err := member.Open()
	if err != nil {
		return nil, errConsumerImportUnsupported
	}
	defer reader.Close()
	data, err := io.ReadAll(reader)
	if err != nil {
		return nil, errConsumerImportUnsupported
	}
	return data, nil
}

// verify reads a member through and checks its CRC without keeping it.
func (a *consumerImportArchive) verify(name string) (int64, error) {
	member := a.byName[name]
	if member == nil {
		return 0, errors.New("missing member")
	}
	reader, err := member.Open()
	if err != nil {
		return 0, errConsumerImportUnsupported
	}
	defer reader.Close()
	n, err := io.Copy(io.Discard, reader)
	if err != nil {
		return 0, errConsumerImportUnsupported
	}
	return n, nil
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
