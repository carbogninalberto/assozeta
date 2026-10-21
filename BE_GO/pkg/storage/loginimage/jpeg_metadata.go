package loginimage

import (
	"bytes"
	"context"
	"encoding/binary"
)

// JPEGInfo describes Image.open's first-frame admission used by compress_base64.
// It does not expose a general EXIF API or implement subsequent MPO frame seeks.
type JPEGInfo struct {
	Header JPEGMarkerHeader
	Format string
	Frames int
}

func InspectJPEG(ctx context.Context, data []byte, maxHeaderBytes int) (JPEGInfo, error) {
	header, err := ReadJPEGMarkerHeader(ctx, data, maxHeaderBytes)
	if err != nil {
		return JPEGInfo{}, err
	}
	base := JPEGInfo{Header: header, Format: "JPEG", Frames: 1}
	if !header.HasDPI && header.EXIF != nil {
		exif := header.EXIF
		for bytes.HasPrefix(exif, []byte("Exif\x00\x00")) {
			exif = exif[6:]
		}
		directory, err := readJPEGDirectory(ctx, exif)
		if ctx.Err() != nil {
			return JPEGInfo{}, ctx.Err()
		}
		// Pillow catches unreadable EXIF and missing DPI tags, using a 72-DPI
		// metadata fallback. IndexError from certain scalar payloads is not caught.
		if err == nil {
			_, unit := directory.values[0x128]
			resolution, present := directory.values[0x11a]
			if unit && present && resolution.invalidDPIIndex() {
				return JPEGInfo{}, ErrJPEGHeader
			}
		}
	}
	if header.MPF == nil {
		return base, nil
	}
	directory, err := readJPEGDirectory(ctx, header.MPF)
	if ctx.Err() != nil {
		return JPEGInfo{}, ctx.Err()
	}
	if err != nil {
		return base, nil
	}
	number, numberPresent := directory.values[0xb001]
	entries, entriesPresent := directory.values[0xb002]
	if !numberPresent || !entriesPresent {
		return base, nil
	}
	count, integer := number.positiveInteger(directory.order)
	if !integer || count == 0 {
		return base, nil
	}
	// struct.unpack_from accepts bytes, not numeric tuples or TIFF ASCII strings.
	if entries.kind != 1 && entries.kind != 7 {
		return base, nil
	}
	var entryOrder binary.ByteOrder = binary.LittleEndian
	if bytes.Equal(header.MPF[:4], []byte{'M', 'M', 0, 42}) {
		entryOrder = binary.BigEndian
	}
	for i := uint64(0); i < count; i++ {
		if err := ctx.Err(); err != nil {
			return JPEGInfo{}, err
		}
		if i >= uint64(len(entries.data)/16) {
			return JPEGInfo{}, ErrJPEGHeader
		}
		attribute := entryOrder.Uint32(entries.data[int(i)*16:])
		if (attribute>>24)&7 != 0 {
			return base, nil
		}
	}
	if count > 1 && !header.UltraHDR {
		base.Format = "MPO"
		base.Frames = int(count)
	}
	if err := ctx.Err(); err != nil {
		return JPEGInfo{}, err
	}
	return base, nil
}

type jpegDirectory struct {
	order  binary.ByteOrder
	values map[uint16]jpegMetadataValue
}
type jpegMetadataValue struct {
	kind uint16
	data []byte
}

// JPEG metadata readers pass only an eight-byte TIFF header to Pillow's IFD
// constructor. Preserve its accepted magic/endian combinations and its handling
// of partially readable first directories; this is not a TIFF image decoder.
func readJPEGDirectory(ctx context.Context, data []byte) (jpegDirectory, error) {
	empty := jpegDirectory{}
	if err := ctx.Err(); err != nil {
		return empty, err
	}
	if len(data) < 8 {
		return empty, ErrJPEGHeader
	}
	var order binary.ByteOrder
	switch string(data[:4]) {
	case "II*\x00", "II\x00*":
		order = binary.LittleEndian
	case "MM\x00*", "MM*\x00", "MM\x00+":
		order = binary.BigEndian
	default:
		return empty, ErrJPEGHeader // Includes II+\0: its 64-bit offset cannot fit the eight-byte header.
	}
	directory := jpegDirectory{order: order, values: make(map[uint16]jpegMetadataValue)}
	offset := uint64(order.Uint32(data[4:8]))
	if offset > uint64(len(data)) || uint64(len(data))-offset < 2 {
		return directory, nil
	}
	count := order.Uint16(data[int(offset):])
	offset += 2
	for i := 0; i < int(count); i++ {
		if err := ctx.Err(); err != nil {
			return empty, err
		}
		if offset > uint64(len(data)) || uint64(len(data))-offset < 12 {
			break
		}
		entry := data[int(offset) : int(offset)+12]
		offset += 12
		tag, kind := order.Uint16(entry), order.Uint16(entry[2:])
		unit := jpegTIFFUnit(kind)
		if unit == 0 {
			continue
		}
		size := uint64(order.Uint32(entry[4:])) * uint64(unit)
		if size == 0 {
			continue
		}
		value := entry[8:]
		if size > 4 {
			location := uint64(order.Uint32(value))
			if location > uint64(len(data)) || size > uint64(len(data))-location {
				break
			}
			value = data[int(location):int(location+size)]
		} else {
			value = value[:int(size)]
		}
		// Other tags still participate in bounds validation, but cannot influence
		// JPEG DPI admission or MP frame selection. Avoid materializing unused data.
		if tag == 0x128 || tag == 0x11a || tag == 0xb001 || tag == 0xb002 {
			directory.values[tag] = jpegMetadataValue{kind, value}
		}
	}
	return directory, nil
}
func jpegTIFFUnit(kind uint16) int {
	switch kind {
	case 1, 2, 6, 7:
		return 1
	case 3, 8:
		return 2
	case 4, 9, 11, 13:
		return 4
	case 5, 10, 12, 16:
		return 8
	}
	return 0
}
func (v jpegMetadataValue) invalidDPIIndex() bool {
	switch v.kind {
	case 1, 7:
		return len(v.data) == 1
	case 2:
		text := v.data
		if len(text) > 0 && text[len(text)-1] == 0 {
			text = text[:len(text)-1]
		}
		return len(text) == 0 || len(text) == 1 && text[0] >= '0' && text[0] <= '9'
	}
	return false
}

// NumberOfImages has TIFF length one: Pillow truncates arrays to their first
// numeric item. Non-integer types fall back to JPEG; zero/negative counts never
// adopt an MPO. Bounds are guaranteed by the IFD parser's unit-size validation.
func (v jpegMetadataValue) positiveInteger(order binary.ByteOrder) (uint64, bool) {
	switch v.kind {
	case 3:
		return uint64(order.Uint16(v.data)), true
	case 4, 13:
		return uint64(order.Uint32(v.data)), true
	case 16:
		return order.Uint64(v.data), true
	case 6:
		if n := int8(v.data[0]); n > 0 {
			return uint64(n), true
		}
		return 0, true
	case 8:
		if n := int16(order.Uint16(v.data)); n > 0 {
			return uint64(n), true
		}
		return 0, true
	case 9:
		if n := int32(order.Uint32(v.data)); n > 0 {
			return uint64(n), true
		}
		return 0, true
	}
	return 0, false
}
