package pyzipfile

import (
	"encoding/binary"
	"hash/crc32"
	"sort"
)

const (
	sizeCentralDir = 46
	sizeFileHeader = 30
	asUint32Max    = 0xFFFFFFFF
	asUint64Max    = 0xFFFFFFFFFFFFFFFF
	stringCentral  = "PK\x01\x02"
	stringFileHdr  = "PK\x03\x04"
)

// realGetContents is ZipFile._RealGetContents.
func (a *Archive) realGetContents() error {
	rec, err := a.endRecData()
	if err != nil {
		if e, ok := err.(*Exception); ok && e.Type == "OSError" {
			return badZip("File is not a zip file")
		}
		return err
	}
	if rec == nil {
		return badZip("File is not a zip file")
	}
	sizeCD := rec.size
	offsetCD := rec.offset
	a.comment = rec.comment

	// "concat" is zero unless the zip was concatenated to another file.
	concat := rec.location - sizeCD - offsetCD
	startDir := offsetCD + concat
	if startDir < 0 {
		return badZip("Bad offset for central directory")
	}

	// Python reads size_cd bytes (a short read at end of file is fine and the
	// parse loop then reports a truncated directory).
	count := sizeCD
	if count < 0 {
		count = 0
	}
	if available := a.size - startDir; startDir >= 0 && count > available {
		count = available
	}
	data := []byte(nil)
	if count > 0 {
		var err error
		data, err = a.readAt(startDir, int(count))
		if err != nil {
			return err
		}
	}

	position := 0
	total := int64(0)
	for total < sizeCD {
		if position+sizeCentralDir > len(data) {
			return badZip("Truncated central directory")
		}
		header := data[position : position+sizeCentralDir]
		if string(header[0:4]) != stringCentral {
			return badZip("Bad magic number for central directory")
		}
		nameLen := int(binary.LittleEndian.Uint16(header[28:30]))
		extraLen := int(binary.LittleEndian.Uint16(header[30:32]))
		commentLen := int(binary.LittleEndian.Uint16(header[32:34]))
		rawName := sliceClamped(data, position+sizeCentralDir, nameLen)
		rawExtra := sliceClamped(data, position+sizeCentralDir+nameLen, extraLen)
		rawComment := sliceClamped(data, position+sizeCentralDir+nameLen+extraLen, commentLen)

		flagBits := binary.LittleEndian.Uint16(header[8:10])
		name, err := decodeName(rawName, flagBits&0x800 != 0)
		if err != nil {
			return err
		}
		e := &entry{
			origFilename:  name,
			filename:      sanitizeFilename(name),
			comment:       append([]byte(nil), rawComment...),
			headerOffset:  int64(binary.LittleEndian.Uint32(header[42:46])),
			createVersion: uint16(header[4]),
			extractVers:   uint16(header[6]),
			flagBits:      flagBits,
			compressType:  binary.LittleEndian.Uint16(header[10:12]),
			rawTime:       binary.LittleEndian.Uint16(header[12:14]),
			crc:           binary.LittleEndian.Uint32(header[16:20]),
			compressSize:  uint64(binary.LittleEndian.Uint32(header[20:24])),
			fileSize:      uint64(binary.LittleEndian.Uint32(header[24:28])),
			externalAttrs: binary.LittleEndian.Uint32(header[38:42]),
		}
		if e.extractVers > 63 {
			return exceptionf("NotImplementedError", "zip file version %s", pyFloat(e.extractVers))
		}
		if err := e.decodeExtra(rawExtra, crc32.ChecksumIEEE(rawName)); err != nil {
			return err
		}
		e.headerOffset += concat

		a.entries = append(a.entries, e)
		a.byName[e.filename] = e

		total += int64(sizeCentralDir + nameLen + extraLen + commentLen)
		position += sizeCentralDir + nameLen + extraLen + commentLen
	}

	// end_offset: the next local header (or the central directory) after each
	// member, for the overlapped-entry check in open().
	order := make([]int, len(a.entries))
	for i := range order {
		order[i] = i
	}
	sort.SliceStable(order, func(i, j int) bool {
		return a.entries[order[i]].headerOffset < a.entries[order[j]].headerOffset
	})
	endOffset := startDir
	for i := len(order) - 1; i >= 0; i-- {
		e := a.entries[order[i]]
		e.endOffset = endOffset
		e.hasEndOffset = true
		endOffset = e.headerOffset
	}
	return nil
}

func sliceClamped(data []byte, start, n int) []byte {
	if start > len(data) {
		return nil
	}
	end := start + n
	if end > len(data) {
		end = len(data)
	}
	return data[start:end]
}

// decodeExtra is ZipInfo._decodeExtra.
func (e *entry) decodeExtra(extra []byte, filenameCRC uint32) error {
	for len(extra) >= 4 {
		tp := binary.LittleEndian.Uint16(extra[0:2])
		ln := binary.LittleEndian.Uint16(extra[2:4])
		if int(ln)+4 > len(extra) {
			return badZip("Corrupt extra field %04x (size=%d)", tp, ln)
		}
		data := extra[4 : 4+int(ln)]
		if tp == 0x0001 {
			// ZIP64 extension (large files and/or large archives).
			field := ""
			if e.fileSize == asUint64Max || e.fileSize == asUint32Max {
				field = "File size"
				v, ok := readU64(data)
				if !ok {
					return badZip("Corrupt zip64 extra field. %s not found.", field)
				}
				e.fileSize = v
				data = data[8:]
			}
			if e.compressSize == asUint32Max {
				field = "Compress size"
				v, ok := readU64(data)
				if !ok {
					return badZip("Corrupt zip64 extra field. %s not found.", field)
				}
				e.compressSize = v
				data = data[8:]
			}
			if uint64(e.headerOffset) == asUint32Max {
				field = "Header offset"
				v, ok := readU64(data)
				if !ok {
					return badZip("Corrupt zip64 extra field. %s not found.", field)
				}
				e.headerOffset = int64(v)
			}
		} else if tp == 0x7075 {
			// Unicode Path Extra Field.
			if len(data) < 5 {
				return badZip("Corrupt unicode path extra field (0x7075)")
			}
			version := data[0]
			nameCRC := binary.LittleEndian.Uint32(data[1:5])
			if version == 1 && nameCRC == filenameCRC {
				name, err := decodeName(data[5:], true)
				if err != nil {
					return badZip("Corrupt unicode path extra field (0x7075): invalid utf-8 bytes")
				}
				if name != "" {
					e.filename = sanitizeFilename(name)
				}
			}
		}
		extra = extra[4+int(ln):]
	}
	return nil
}

func readU64(data []byte) (uint64, bool) {
	if len(data) < 8 {
		return 0, false
	}
	return binary.LittleEndian.Uint64(data), true
}
