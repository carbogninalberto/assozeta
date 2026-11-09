// Package pyzip writes the archive Python 3.13's zipfile produces for
// ZipFile(path, 'w', compression=ZIP_DEFLATED) on a seekable file followed by
// writestr(name, data) calls (PrintingService.download_multiple_files):
// local headers without data descriptors (the header is rewritten in place
// with CRC and sizes), version 20 for extraction and creation, creator system
// 3 (Unix), flag 0x800 only for non-ASCII names, external attributes 0o600
// (0o40775 | MS-DOS directory bit for names ending in '/'), no extra fields,
// the DOS time of time.localtime() at each writestr, and the plain end of
// central directory record.
//
// The deflate stream is compress/flate at zlib's default level 6: it decodes
// to the same bytes with the same CRC, but its compressed bytes (and therefore
// the compressed sizes and offsets) are not zlib's. Archives that would need
// ZIP64 (an entry over ZIP64_LIMIT / 1.05, sizes or offsets over ZIP64_LIMIT,
// or more than 65535 entries) are refused with ErrZip64.
package pyzip

import (
	"bytes"
	"compress/flate"
	"encoding/binary"
	"errors"
	"hash/crc32"
	"strings"
	"time"
	"unicode/utf8"
)

// ErrZip64 is an archive outside the non-ZIP64 subset.
var ErrZip64 = errors.New("pyzip: archive needs ZIP64")

// ErrName is a name zipfile would rewrite (NUL truncation) or one that is not
// valid UTF-8.
var ErrName = errors.New("pyzip: unsupported entry name")

const zip64Limit = (1 << 31) - 1

type entry struct {
	name             []byte
	flags            uint16
	dosTime, dosDate uint16
	crc              uint32
	compressed, size uint32
	external         uint32
	offset           uint32
}

// Writer accumulates the archive in memory.
type Writer struct {
	buf     bytes.Buffer
	entries []entry
}

// DOSDateTime is ZipInfo's date_time packing (seconds halved).
func DOSDateTime(t time.Time) (dosDate, dosTime uint16) {
	dosDate = uint16((t.Year()-1980)<<9 | int(t.Month())<<5 | t.Day())
	dosTime = uint16(t.Hour()<<11 | t.Minute()<<5 | t.Second()/2)
	return dosDate, dosTime
}

// WriteStr is ZipFile.writestr(name, data) at local time at.
func (w *Writer) WriteStr(name string, data []byte, at time.Time) error {
	if !utf8.ValidString(name) || strings.ContainsRune(name, 0) {
		return ErrName
	}
	if at.Year() < 1980 || at.Year() > 2107 {
		return ErrName
	}
	if float64(len(data))*1.05 > zip64Limit || len(w.entries) >= 0xffff {
		return ErrZip64
	}
	var compressed bytes.Buffer
	deflater, err := flate.NewWriter(&compressed, 6)
	if err != nil {
		return err
	}
	if _, err = deflater.Write(data); err != nil {
		return err
	}
	if err = deflater.Close(); err != nil {
		return err
	}
	offset := w.buf.Len()
	if offset > zip64Limit || compressed.Len() > zip64Limit {
		return ErrZip64
	}
	e := entry{name: []byte(name), crc: crc32.ChecksumIEEE(data), compressed: uint32(compressed.Len()), size: uint32(len(data)),
		external: 0o600 << 16, offset: uint32(offset)}
	e.dosDate, e.dosTime = DOSDateTime(at)
	for _, r := range []byte(name) {
		if r >= 0x80 {
			e.flags = 0x800
			break
		}
	}
	if strings.HasSuffix(name, "/") {
		e.external = 0o40775<<16 | 0x10
	}
	header := make([]byte, 30)
	binary.LittleEndian.PutUint32(header[0:], 0x04034b50)
	binary.LittleEndian.PutUint16(header[4:], 20)
	binary.LittleEndian.PutUint16(header[6:], e.flags)
	binary.LittleEndian.PutUint16(header[8:], 8)
	binary.LittleEndian.PutUint16(header[10:], e.dosTime)
	binary.LittleEndian.PutUint16(header[12:], e.dosDate)
	binary.LittleEndian.PutUint32(header[14:], e.crc)
	binary.LittleEndian.PutUint32(header[18:], e.compressed)
	binary.LittleEndian.PutUint32(header[22:], e.size)
	binary.LittleEndian.PutUint16(header[26:], uint16(len(e.name)))
	binary.LittleEndian.PutUint16(header[28:], 0)
	w.buf.Write(header)
	w.buf.Write(e.name)
	w.buf.Write(compressed.Bytes())
	w.entries = append(w.entries, e)
	return nil
}

// Close writes the central directory and returns the archive bytes.
func (w *Writer) Close() ([]byte, error) {
	start := w.buf.Len()
	for _, e := range w.entries {
		header := make([]byte, 46)
		binary.LittleEndian.PutUint32(header[0:], 0x02014b50)
		binary.LittleEndian.PutUint16(header[4:], 3<<8|20)
		binary.LittleEndian.PutUint16(header[6:], 20)
		binary.LittleEndian.PutUint16(header[8:], e.flags)
		binary.LittleEndian.PutUint16(header[10:], 8)
		binary.LittleEndian.PutUint16(header[12:], e.dosTime)
		binary.LittleEndian.PutUint16(header[14:], e.dosDate)
		binary.LittleEndian.PutUint32(header[16:], e.crc)
		binary.LittleEndian.PutUint32(header[20:], e.compressed)
		binary.LittleEndian.PutUint32(header[24:], e.size)
		binary.LittleEndian.PutUint16(header[28:], uint16(len(e.name)))
		binary.LittleEndian.PutUint32(header[38:], e.external)
		binary.LittleEndian.PutUint32(header[42:], e.offset)
		w.buf.Write(header)
		w.buf.Write(e.name)
	}
	size := w.buf.Len() - start
	if start > zip64Limit || size > zip64Limit {
		return nil, ErrZip64
	}
	end := make([]byte, 22)
	binary.LittleEndian.PutUint32(end[0:], 0x06054b50)
	binary.LittleEndian.PutUint16(end[8:], uint16(len(w.entries)))
	binary.LittleEndian.PutUint16(end[10:], uint16(len(w.entries)))
	binary.LittleEndian.PutUint32(end[12:], uint32(size))
	binary.LittleEndian.PutUint32(end[16:], uint32(start))
	w.buf.Write(end)
	return w.buf.Bytes(), nil
}
