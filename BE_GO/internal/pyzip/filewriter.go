package pyzip

import (
	"encoding/binary"
	"hash/crc32"
	"io"
	"os"
	"strings"
	"time"
	"unicode/utf8"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/pyzlib"
)

// FileWriter writes the archive of ZipFile(path, 'w', ZIP_DEFLATED) on a
// seekable file followed by ZipFile.write(file_path, arcname) calls
// (AssociationExportService.create_zip): ZipInfo.from_file takes the date
// from the file's mtime (time.localtime, given here) and the external
// attributes from st_mode; each local header is written first and rewritten
// with the CRC and sizes after the member's data. Unlike Writer, the deflate
// stream is zlib's own (internal/pyzlib reproduces zlib 1.2.12–1.3.x output
// byte for byte), so the whole archive matches CPython's.
type FileWriter struct {
	out     *os.File
	entries []entry
}

// NewFileWriter starts an archive at the beginning of out.
func NewFileWriter(out *os.File) *FileWriter { return &FileWriter{out: out} }

// countingWriter counts the compressed bytes written to the archive.
type countingWriter struct {
	w io.Writer
	n int64
}

func (c *countingWriter) Write(p []byte) (int, error) {
	n, err := c.w.Write(p)
	c.n += int64(n)
	return n, err
}

// WriteFile is ZipFile.write(path, name): the member's mode bits and local
// modification time (mtime as time.localtime returns it) come from the file.
func (w *FileWriter) WriteFile(name, path string, mode os.FileMode, modified time.Time) error {
	if !utf8.ValidString(name) || strings.ContainsRune(name, 0) || modified.Year() < 1980 || modified.Year() > 2107 {
		return ErrName
	}
	source, err := os.Open(path)
	if err != nil {
		return err
	}
	defer source.Close()
	info, err := source.Stat()
	if err != nil {
		return err
	}
	if float64(info.Size())*1.05 > zip64Limit || len(w.entries) >= 0xffff {
		return ErrZip64
	}
	offset, err := w.out.Seek(0, io.SeekCurrent)
	if err != nil {
		return err
	}
	if offset > zip64Limit {
		return ErrZip64
	}
	e := entry{name: []byte(name), external: uint32(unixMode(mode)&0xFFFF) << 16, offset: uint32(offset)}
	e.dosDate, e.dosTime = DOSDateTime(modified)
	for _, r := range []byte(name) {
		if r >= 0x80 {
			e.flags = 0x800
			break
		}
	}
	if _, err = w.out.Write(localHeader(e)); err != nil {
		return err
	}
	counter := &countingWriter{w: w.out}
	deflater := pyzlib.Compress(counter)
	checksum := crc32.NewIEEE()
	buffer := make([]byte, 8192)
	var size int64
	for {
		n, readErr := source.Read(buffer)
		if n > 0 {
			checksum.Write(buffer[:n])
			size += int64(n)
			if _, err = deflater.Write(buffer[:n]); err != nil {
				return err
			}
		}
		if readErr == io.EOF {
			break
		}
		if readErr != nil {
			return readErr
		}
	}
	if err = deflater.Close(); err != nil {
		return err
	}
	if size > zip64Limit || counter.n > zip64Limit {
		return ErrZip64
	}
	e.crc, e.size, e.compressed = checksum.Sum32(), uint32(size), uint32(counter.n)
	end, err := w.out.Seek(0, io.SeekCurrent)
	if err != nil {
		return err
	}
	if _, err = w.out.WriteAt(localHeader(e), offset); err != nil {
		return err
	}
	if _, err = w.out.Seek(end, io.SeekStart); err != nil {
		return err
	}
	w.entries = append(w.entries, e)
	return nil
}

// unixMode is st_mode for a regular file or directory.
func unixMode(mode os.FileMode) uint32 {
	bits := uint32(mode.Perm())
	if mode&os.ModeSetuid != 0 {
		bits |= 0o4000
	}
	if mode&os.ModeSetgid != 0 {
		bits |= 0o2000
	}
	if mode&os.ModeSticky != 0 {
		bits |= 0o1000
	}
	if mode.IsDir() {
		return bits | 0o040000
	}
	return bits | 0o100000
}

func localHeader(e entry) []byte {
	header := make([]byte, 30, 30+len(e.name))
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
	return append(header, e.name...)
}

// Close writes the central directory and the end record.
func (w *FileWriter) Close() error {
	start, err := w.out.Seek(0, io.SeekCurrent)
	if err != nil {
		return err
	}
	var directory []byte
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
		directory = append(append(directory, header...), e.name...)
	}
	if start > zip64Limit || int64(len(directory)) > zip64Limit {
		return ErrZip64
	}
	end := make([]byte, 22)
	binary.LittleEndian.PutUint32(end[0:], 0x06054b50)
	binary.LittleEndian.PutUint16(end[8:], uint16(len(w.entries)))
	binary.LittleEndian.PutUint16(end[10:], uint16(len(w.entries)))
	binary.LittleEndian.PutUint32(end[12:], uint32(len(directory)))
	binary.LittleEndian.PutUint32(end[16:], uint32(start))
	if _, err = w.out.Write(append(directory, end...)); err != nil {
		return err
	}
	return nil
}
