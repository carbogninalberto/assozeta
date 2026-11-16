// Package pyzipfile reads ZIP archives exactly as CPython 3.13's zipfile
// module does when a ZipFile is opened in 'r' mode and its members are read.
//
// Every outcome is either the bytes Python returns or the Python exception
// (class name and str(e)) Python raises: BadZipFile, KeyError, RuntimeError,
// NotImplementedError, EOFError, zlib.error, OSError, LZMAError,
// UnicodeDecodeError, ValueError. The end of central directory search, the
// ZIP64 records, the central directory and local header checks, name decoding
// (UTF-8 or cp437), ZipInfo._decodeExtra, the decompressors and ZipExtFile's
// read buffering all follow the Python sources.
package pyzipfile

import (
	"errors"
	"fmt"
	"io"
	"os"
	"strconv"
	"strings"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/pycodecs"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/pystr"
)

// Exception is a Python exception: its class name (BadZipFile, KeyError,
// RuntimeError, NotImplementedError, EOFError, zlib.error, OSError, LZMAError,
// UnicodeDecodeError, ValueError, ...) and str(e).
type Exception struct {
	Type string
	Text string
}

// Error is str(e).
func (e *Exception) Error() string { return e.Text }

func exceptionf(kind, format string, args ...any) *Exception {
	return &Exception{Type: kind, Text: fmt.Sprintf(format, args...)}
}

func badZip(format string, args ...any) *Exception {
	return exceptionf("BadZipFile", format, args...)
}

// IsZipfile is zipfile.is_zipfile(path). Python swallows every OSError and
// BadZipFile while reading and returns False, so this likewise returns False
// and a nil error for unreadable files and non-ZIP content. A non-nil error is
// reserved for a Go-side failure that is not about the file at all (none
// today).
func IsZipfile(path string) (bool, error) {
	file, err := os.Open(path)
	if err != nil {
		return false, nil
	}
	defer file.Close()
	info, err := file.Stat()
	if err != nil {
		return false, nil
	}
	a := &Archive{file: file, size: info.Size()}
	rec, err := a.endRecData()
	if err != nil || rec == nil {
		return false, nil
	}
	return true, nil
}

// Info is the part of ZipInfo callers read. Filename is the name after
// _decodeExtra's Unicode path extra field (and the null-byte sanitising);
// OrigFilename is the name decoded from the central directory bytes.
type Info struct {
	FileSize      uint64
	CompressSize  uint64
	CompressType  uint16
	FlagBits      uint16
	CRC           uint32
	HeaderOffset  int64
	OrigFilename  string
	Filename      string
	ExternalAttrs uint32
	Comment       []byte
}

// Archive is an open zipfile.ZipFile in 'r' mode.
type Archive struct {
	file    *os.File
	size    int64
	closed  bool
	comment []byte
	entries []*entry
	byName  map[string]*entry
}

// entry is the reading part of ZipInfo.
type entry struct {
	origFilename  string
	filename      string
	fileSize      uint64
	compressSize  uint64
	compressType  uint16
	flagBits      uint16
	crc           uint32
	headerOffset  int64
	externalAttrs uint32
	createVersion uint16
	extractVers   uint16
	rawTime       uint16
	endOffset     int64
	hasEndOffset  bool
	comment       []byte
}

func (e *entry) info() Info {
	return Info{
		FileSize:      e.fileSize,
		CompressSize:  e.compressSize,
		CompressType:  e.compressType,
		FlagBits:      e.flagBits,
		CRC:           e.crc,
		HeaderOffset:  e.headerOffset,
		OrigFilename:  e.origFilename,
		Filename:      e.filename,
		ExternalAttrs: e.externalAttrs,
		Comment:       e.comment,
	}
}

// Open is zipfile.ZipFile(path, 'r'). A non-nil error that is not an
// *Exception is an I/O failure of the underlying file (Python would raise
// OSError from io.open before zipfile sees the file).
func Open(path string) (*Archive, error) {
	file, err := os.Open(path)
	if err != nil {
		return nil, err
	}
	info, err := file.Stat()
	if err != nil {
		file.Close()
		return nil, err
	}
	a := &Archive{file: file, size: info.Size(), byName: map[string]*entry{}}
	if err := a.realGetContents(); err != nil {
		file.Close()
		return nil, err
	}
	return a, nil
}

// Close closes the underlying file.
func (a *Archive) Close() error {
	if a.closed {
		return nil
	}
	a.closed = true
	return a.file.Close()
}

// NameList is namelist(): every central directory entry in order, duplicates
// kept.
func (a *Archive) NameList() []string {
	out := make([]string, len(a.entries))
	for i, e := range a.entries {
		out[i] = e.filename
	}
	return out
}

// Has is name in namelist().
func (a *Archive) Has(name string) bool {
	for _, e := range a.entries {
		if e.filename == name {
			return true
		}
	}
	return false
}

// Info returns getinfo(name): the last entry of that name, as NameToInfo keeps
// it.
func (a *Archive) Info(name string) (Info, bool) {
	e := a.byName[name]
	if e == nil {
		return Info{}, false
	}
	return e.info(), true
}

// Read is ZipFile.read(name).
func (a *Archive) Read(name string) ([]byte, error) {
	member, err := a.OpenMember(name)
	if err != nil {
		return nil, err
	}
	defer member.Close()
	return member.Read(-1)
}

// readAt returns the bytes at off, at most n; a short read at end of file is
// not an error (Python's file.read does the same).
func (a *Archive) readAt(off int64, n int) ([]byte, error) {
	if n <= 0 {
		return nil, nil
	}
	if off < 0 {
		return nil, exceptionf("OSError", "[Errno 22] Invalid argument")
	}
	if off >= a.size {
		return nil, nil
	}
	if int64(n) > a.size-off {
		n = int(a.size - off)
	}
	buf := make([]byte, n)
	read, err := a.file.ReadAt(buf, off)
	if err != nil && err != io.EOF {
		return nil, err
	}
	return buf[:read], nil
}

// decodeName decodes a ZIP name: UTF-8 when the flag bit is set, cp437
// otherwise, exactly as the zipfile sources do. The returned string is the
// Python str; it is valid UTF-8 because both codecs only produce scalar
// values.
func decodeName(raw []byte, utf8Flag bool) (string, error) {
	name := "cp437"
	if utf8Flag {
		name = "utf-8"
	}
	runes, err, _ := pycodecs.Decode(name, raw, pycodecs.Strict)
	if err != nil {
		var decodeError *pycodecs.DecodeError
		if errors.As(err, &decodeError) {
			return "", exceptionf("UnicodeDecodeError", "%s", decodeError.Error())
		}
		return "", err
	}
	return pystr.String(runes), nil
}

// sanitizeFilename truncates the name at the first null byte (os.sep is "/"
// on every platform this backend runs on, so no replacement is needed).
func sanitizeFilename(name string) string {
	if at := strings.IndexByte(name, 0); at >= 0 {
		return name[:at]
	}
	return name
}

// pyFloat is "%.1f" of an integer divided by ten, as zipfile's
// "zip file version %.1f" % (extract_version / 10) formats it.
func pyFloat(tenths uint16) string {
	return strconv.FormatFloat(float64(tenths)/10, 'f', 1, 64)
}
