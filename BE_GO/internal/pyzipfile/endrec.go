package pyzipfile

import (
	"bytes"
	"encoding/binary"
)

// endRec is the "end of central directory" record plus its location, as
// _EndRecData returns it.
type endRec struct {
	diskNumber      uint32
	diskStart       uint32
	entriesThisDisk uint32
	entriesTotal    uint32
	size            int64
	offset          int64
	comment         []byte
	commentSize     uint16
	location        int64
	zip64           bool
}

const (
	sizeEndCentDir        = 22
	sizeEndCentDir64      = 56
	sizeEndCentDir64Local = 20
	zipMaxComment         = 1<<16 - 1
)

var (
	stringEndArchive        = []byte("PK\x05\x06")
	stringEndArchive64      = []byte("PK\x06\x06")
	stringEndArchive64Local = []byte("PK\x06\x07")
)

// endRecData is _EndRecData. A returned *Exception of type OSError stands for
// the OSError("Unknown I/O error") the Python function raises (the caller
// turns it into BadZipFile("File is not a zip file") or False).
func (a *Archive) endRecData() (*endRec, error) {
	size := a.size

	if size >= sizeEndCentDir {
		data, err := a.readAt(size-sizeEndCentDir, sizeEndCentDir)
		if err != nil {
			return nil, err
		}
		if len(data) == sizeEndCentDir && bytes.Equal(data[0:4], stringEndArchive) &&
			data[sizeEndCentDir-2] == 0 && data[sizeEndCentDir-1] == 0 {
			rec, err := unpackEndRec(data, nil)
			if err != nil {
				return nil, err
			}
			rec.location = size - sizeEndCentDir
			return a.endRecData64(size-sizeEndCentDir, rec)
		}
	}

	maxCommentStart := size - zipMaxComment - sizeEndCentDir
	if maxCommentStart < 0 {
		maxCommentStart = 0
	}
	data, err := a.readAt(maxCommentStart, zipMaxComment+sizeEndCentDir)
	if err != nil {
		return nil, err
	}
	start := bytes.LastIndex(data, stringEndArchive)
	if start < 0 {
		return nil, nil
	}
	recData := data[start:]
	if len(recData) > sizeEndCentDir {
		recData = recData[:sizeEndCentDir]
	}
	if len(recData) != sizeEndCentDir {
		return nil, nil
	}
	rec, err := unpackEndRec(recData, nil)
	if err != nil {
		return nil, err
	}
	commentStart := start + sizeEndCentDir
	commentEnd := commentStart + int(rec.commentSize)
	if commentEnd > len(data) {
		commentEnd = len(data)
	}
	comment := append([]byte(nil), data[commentStart:commentEnd]...)
	rec.comment = comment
	rec.location = maxCommentStart + int64(start)
	return a.endRecData64(maxCommentStart+int64(start), rec)
}

// unpackEndRec unpacks structEndArchive ("<4s4H2LH").
func unpackEndRec(data []byte, rec *endRec) (*endRec, error) {
	if rec == nil {
		rec = &endRec{}
	}
	rec.diskNumber = uint32(binary.LittleEndian.Uint16(data[4:6]))
	rec.diskStart = uint32(binary.LittleEndian.Uint16(data[6:8]))
	rec.entriesThisDisk = uint32(binary.LittleEndian.Uint16(data[8:10]))
	rec.entriesTotal = uint32(binary.LittleEndian.Uint16(data[10:12]))
	rec.size = int64(binary.LittleEndian.Uint32(data[12:16]))
	rec.offset = int64(binary.LittleEndian.Uint32(data[16:20]))
	rec.commentSize = binary.LittleEndian.Uint16(data[20:22])
	return rec, nil
}

// endRecData64 is _EndRecData64.
func (a *Archive) endRecData64(offset int64, rec *endRec) (*endRec, error) {
	offset -= sizeEndCentDir64Local
	if offset < 0 {
		return rec, nil
	}
	data, err := a.readAt(offset, sizeEndCentDir64Local)
	if err != nil {
		return nil, err
	}
	if len(data) != sizeEndCentDir64Local {
		return nil, exceptionf("OSError", "Unknown I/O error")
	}
	if !bytes.Equal(data[0:4], stringEndArchive64Local) {
		return rec, nil
	}
	diskno := binary.LittleEndian.Uint32(data[4:8])
	reloff := binary.LittleEndian.Uint64(data[8:16])
	disks := binary.LittleEndian.Uint32(data[16:20])
	if diskno != 0 || disks > 1 {
		return nil, badZip("zipfiles that span multiple disks are not supported")
	}

	offset -= sizeEndCentDir64
	if offset < 0 || !uint64LE(reloff) {
		return nil, badZip("Corrupt zip64 end of central directory locator")
	}
	if int64(reloff) > offset {
		return nil, badZip("Corrupt zip64 end of central directory locator")
	}
	extrasz := offset - int64(reloff)
	data, err = a.readAt(int64(reloff), sizeEndCentDir64)
	if err != nil {
		return nil, err
	}
	if len(data) != sizeEndCentDir64 {
		return nil, exceptionf("OSError", "Unknown I/O error")
	}
	if !bytes.HasPrefix(data, stringEndArchive64) && int64(reloff) != offset {
		extrasz = 0
		data, err = a.readAt(offset, sizeEndCentDir64)
		if err != nil {
			return nil, err
		}
		if len(data) != sizeEndCentDir64 {
			return nil, exceptionf("OSError", "Unknown I/O error")
		}
	}
	if !bytes.HasPrefix(data, stringEndArchive64) {
		return nil, badZip("Zip64 end of central directory record not found")
	}
	sz := binary.LittleEndian.Uint64(data[4:12])
	diskNum := binary.LittleEndian.Uint32(data[16:20])
	diskDir := binary.LittleEndian.Uint32(data[20:24])
	dircount := binary.LittleEndian.Uint64(data[24:32])
	dircount2 := binary.LittleEndian.Uint64(data[32:40])
	dirsize := binary.LittleEndian.Uint64(data[40:48])
	diroffset := binary.LittleEndian.Uint64(data[48:56])
	if !uint64LE(diroffset) || int64(diroffset)+int64(dirsize) != int64(reloff) ||
		!uint64LE(sz) || int64(sz)+12 != sizeEndCentDir64+extrasz {
		return nil, badZip("Corrupt zip64 end of central directory record")
	}

	rec.diskNumber = diskNum
	rec.diskStart = diskDir
	rec.entriesThisDisk = uint32(dircount)
	rec.entriesTotal = uint32(dircount2)
	rec.size = int64(dirsize)
	rec.offset = int64(diroffset)
	rec.location = offset - extrasz
	rec.zip64 = true
	return rec, nil
}

// uint64LE reports whether v fits a signed 64-bit offset. Python's ints are
// unbounded; every value this package reads from a real file is far below it.
func uint64LE(v uint64) bool { return v <= 1<<63-1 }
