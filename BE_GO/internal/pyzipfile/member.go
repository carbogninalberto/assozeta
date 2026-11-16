package pyzipfile

import (
	"encoding/binary"
	"hash/crc32"
)

const (
	zipStored   = 0
	zipDeflated = 8
	zipBzip2    = 12
	zipLZMA     = 14

	minReadSize = 4096
	maxN        = (1 << 31) - 1
)

// Member is the reading half of ZipExtFile.
type Member struct {
	archive      *Archive
	entry        *entry
	name         string // zipinfo.filename, as ZipExtFile.name
	compressType uint16
	flagBits     uint16

	compressLeft int64
	left         int64
	eof          bool

	readBuffer []byte
	offset     int

	runningCRC  uint32
	expectedCRC uint32

	position int64
	decomp   decompressor
	closed   bool
}

// decompressor is the reading part of zlib.Decompress, bz2.BZ2Decompressor
// and lzma.LZMADecompressor.
type decompressor interface {
	decompress(data []byte, maxLength int) ([]byte, error)
	flush() ([]byte, error)
	atEOF() bool
	needsInput() bool
	unconsumedTail() []byte
}

// OpenMember is ZipFile.open(name) in 'r' mode: every check open() performs
// before any data is read.
func (a *Archive) OpenMember(name string) (*Member, error) {
	e := a.byName[name]
	if e == nil {
		message := "There is no item named " + pyReprString(name) + " in the archive"
		return nil, exceptionf("KeyError", "%s", pyReprString(message))
	}

	offset := e.headerOffset
	fheader, err := a.readAt(offset, sizeFileHeader)
	if err != nil {
		return nil, err
	}
	if len(fheader) != sizeFileHeader {
		return nil, badZip("Truncated file header")
	}
	if string(fheader[0:4]) != stringFileHdr {
		return nil, badZip("Bad magic number for file header")
	}
	fnameLen := int(binary.LittleEndian.Uint16(fheader[26:28]))
	extraLen := int(binary.LittleEndian.Uint16(fheader[28:30]))
	fname, err := a.readAt(offset+sizeFileHeader, fnameLen)
	if err != nil {
		return nil, err
	}
	position := offset + sizeFileHeader + int64(len(fname)) + int64(extraLen)

	if e.flagBits&0x20 != 0 {
		return nil, exceptionf("NotImplementedError", "compressed patched data (flag bit 5)")
	}
	if e.flagBits&0x40 != 0 {
		return nil, exceptionf("NotImplementedError", "strong encryption (flag bit 6)")
	}

	localFlags := binary.LittleEndian.Uint16(fheader[6:8])
	fnameStr, err := decodeName(fname, localFlags&0x800 != 0)
	if err != nil {
		return nil, err
	}
	if fnameStr != e.origFilename {
		return nil, badZip("File name in directory %s and header %s differ.",
			pyReprString(e.origFilename), pyReprBytes(fname))
	}

	if e.hasEndOffset {
		extent := e.compressSize
		overlaps := false
		if extent > uint64(1)<<62 {
			overlaps = true
		} else if position+int64(extent) > e.endOffset {
			overlaps = true
		}
		if overlaps && e.endOffset != e.headerOffset {
			return nil, badZip("Overlapped entries: %s (possible zip bomb)", pyReprString(e.origFilename))
		}
		// Overlapped entries at the same offset only warn in Python.
	}

	if e.flagBits&0x1 != 0 {
		return nil, exceptionf("RuntimeError", "File %s is encrypted, password required for extraction", pyReprString(name))
	}

	decomp, err := newDecompressor(e.compressType)
	if err != nil {
		return nil, err
	}
	m := &Member{
		archive:      a,
		entry:        e,
		name:         e.filename,
		compressType: e.compressType,
		flagBits:     e.flagBits,
		compressLeft: int64(e.compressSize),
		left:         int64(e.fileSize),
		expectedCRC:  e.crc,
		position:     position,
		decomp:       decomp,
	}
	return m, nil
}

// newDecompressor is _get_decompressor.
func newDecompressor(compressType uint16) (decompressor, error) {
	switch compressType {
	case zipStored:
		return nil, nil
	case zipDeflated:
		return newZlibInflate(), nil
	case zipBzip2:
		return newBzip2(), nil
	case zipLZMA:
		return newLZMA(), nil
	}
	return nil, exceptionf("NotImplementedError", "That compression method is not supported")
}

// Read is ZipExtFile.read(n); n < 0 reads everything.
func (m *Member) Read(n int) ([]byte, error) {
	if m.closed {
		return nil, exceptionf("ValueError", "read from closed file.")
	}
	if n < 0 {
		buf := append([]byte(nil), m.readBuffer[m.offset:]...)
		m.readBuffer = nil
		m.offset = 0
		for !m.eof {
			data, err := m.read1(maxN)
			if err != nil {
				return nil, err
			}
			buf = append(buf, data...)
		}
		return buf, nil
	}

	end := n + m.offset
	if end < len(m.readBuffer) {
		buf := append([]byte(nil), m.readBuffer[m.offset:end]...)
		m.offset = end
		return buf, nil
	}
	n = end - len(m.readBuffer)
	buf := append([]byte(nil), m.readBuffer[m.offset:]...)
	m.readBuffer = nil
	m.offset = 0
	for n > 0 && !m.eof {
		data, err := m.read1(n)
		if err != nil {
			return nil, err
		}
		if n < len(data) {
			m.readBuffer = data
			m.offset = n
			buf = append(buf, data[:n]...)
			break
		}
		buf = append(buf, data...)
		n -= len(data)
	}
	return buf, nil
}

// read1 is ZipExtFile._read1.
func (m *Member) read1(n int) ([]byte, error) {
	if m.eof || n <= 0 {
		return nil, nil
	}
	var data []byte
	var err error
	switch m.compressType {
	case zipDeflated:
		data = m.decomp.unconsumedTail()
		if n > len(data) {
			more, err := m.read2(n - len(data))
			if err != nil {
				return nil, err
			}
			data = append(data, more...)
		}
	case zipStored:
		data, err = m.read2(n)
		if err != nil {
			return nil, err
		}
	default:
		if m.decomp.needsInput() {
			data, err = m.read2(n)
			if err != nil {
				return nil, err
			}
		}
	}

	switch m.compressType {
	case zipStored:
		m.eof = m.compressLeft <= 0
	case zipDeflated:
		if n < minReadSize {
			n = minReadSize
		}
		data, err = m.decomp.decompress(data, n)
		if err != nil {
			return nil, err
		}
		m.eof = m.decomp.atEOF() || (m.compressLeft <= 0 && len(m.decomp.unconsumedTail()) == 0)
		if m.eof {
			extra, err := m.decomp.flush()
			if err != nil {
				return nil, err
			}
			data = append(data, extra...)
		}
	default:
		maxLength := n
		if maxLength < minReadSize {
			maxLength = minReadSize
		}
		data, err = m.decomp.decompress(data, maxLength)
		if err != nil {
			return nil, err
		}
		m.eof = m.decomp.atEOF() || (m.compressLeft <= 0 && m.decomp.needsInput())
	}

	if int64(len(data)) > m.left {
		data = data[:m.left]
	}
	m.left -= int64(len(data))
	if m.left <= 0 {
		m.eof = true
	}
	m.runningCRC = crc32.Update(m.runningCRC, crc32.IEEETable, data)
	if m.eof && m.runningCRC != m.expectedCRC {
		return nil, badZip("Bad CRC-32 for file %s", pyReprString(m.name))
	}
	return data, nil
}

// read2 is ZipExtFile._read2.
func (m *Member) read2(n int) ([]byte, error) {
	if m.compressLeft <= 0 {
		return nil, nil
	}
	if n < minReadSize {
		n = minReadSize
	}
	if int64(n) > m.compressLeft {
		n = int(m.compressLeft)
	}
	data, err := m.archive.readAt(m.position, n)
	if err != nil {
		return nil, err
	}
	m.position += int64(len(data))
	m.compressLeft -= int64(len(data))
	if len(data) == 0 {
		return nil, exceptionf("EOFError", "")
	}
	return data, nil
}

// Close is ZipExtFile.close.
func (m *Member) Close() error {
	m.closed = true
	return nil
}

// unconsumedTail is the DEFLATE branch's use of unconsumed_tail.
func (m *Member) unconsumedTail() []byte {
	if m.decomp == nil {
		return nil
	}
	return m.decomp.unconsumedTail()
}
