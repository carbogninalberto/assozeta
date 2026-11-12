package loginimage

import (
	"bytes"
	"context"
	"encoding/binary"
	"errors"
)

var ErrJPEGHeader = errors.New("invalid login JPEG marker header")

// JPEGMarkerHeader is the lazy marker stage, not completed Image.open admission.
// EXIF DPI parsing and MPO adoption must still interpret the retained metadata.
// HeaderBytes ends after the first SOS segment or at EOF between markers; entropy
// data is not consumed. An EOF-admitted header need not have decodable pixels.
type JPEGMarkerHeader struct {
	Width, Height      int
	Mode               string
	HeaderBytes        int
	Comment, EXIF, MPF []byte
	MPFOffset          int
	UltraHDR           bool
	HasDPI             bool
	PixelWarning       bool
}

// ReadJPEGMarkerHeader preserves Pillow 13's marker-level acceptance, including
// missing pixel tables, repeated frames, ignored junk and empty SOS payloads.
// maxHeaderBytes bounds consumed input and retained metadata, not total RSS.
func ReadJPEGMarkerHeader(ctx context.Context, data []byte, maxHeaderBytes int) (JPEGMarkerHeader, error) {
	empty := JPEGMarkerHeader{}
	if err := ctx.Err(); err != nil {
		return empty, err
	}
	if maxHeaderBytes < 3 {
		return empty, ErrWorkingLimit
	}
	if len(data) < 3 || !bytes.Equal(data[:3], []byte{255, 216, 255}) {
		return empty, ErrJPEGHeader
	}
	cursor := jpegHeaderCursor{ctx: ctx, data: data, position: 2, limit: maxHeaderBytes}
	header := JPEGMarkerHeader{}
	var minimumICC []byte
markers:
	for {
		if cursor.position == len(data) {
			break
		}
		prefix, err := cursor.take(1)
		if err != nil {
			return empty, err
		}
		if prefix[0] != 255 {
			continue
		}
		var marker byte
		for {
			if cursor.position == len(data) {
				break markers
			}
			value, err := cursor.take(1)
			if err != nil {
				return empty, err
			}
			marker = value[0]
			if marker != 255 {
				break
			}
		}
		if marker == 0 {
			continue
		}
		if marker == 0xc8 || marker >= 0xd0 && marker <= 0xd9 || marker >= 0xf0 && marker <= 0xfd {
			continue
		}
		frame := marker >= 0xc0 && marker <= 0xcf && marker != 0xc4 && marker != 0xc8 && marker != 0xcc || marker == 0xde
		skip := marker == 0xc4 || marker == 0xcc || marker >= 0xda && marker <= 0xdf
		app := marker >= 0xe0 && marker <= 0xef
		if !frame && !skip && !app && marker != 0xfe {
			return empty, ErrJPEGHeader
		}
		length, err := cursor.take(2)
		if err != nil {
			return empty, err
		}
		size := int(binary.BigEndian.Uint16(length)) - 2
		// Pillow _safe_read returns an empty payload for non-positive lengths.
		if size < 0 {
			size = 0
		}
		body, err := cursor.take(size)
		if err != nil {
			return empty, err
		}
		switch {
		case frame:
			if len(body) < 6 || body[0] != 8 || (len(body)-6)%3 != 0 {
				return empty, ErrJPEGHeader
			}
			header.Height = int(binary.BigEndian.Uint16(body[1:3]))
			header.Width = int(binary.BigEndian.Uint16(body[3:5]))
			switch body[5] {
			case 1:
				header.Mode = "L"
			case 3:
				header.Mode = "RGB"
			case 4:
				header.Mode = "CMYK"
			default:
				return empty, ErrJPEGHeader
			}
			if minimumICC != nil && len(minimumICC) < 14 {
				return empty, ErrJPEGHeader
			}
			minimumICC = nil
		case marker == 0xdb:
			for len(body) > 0 {
				size := 65
				if body[0]>>4 != 0 {
					size = 129
				}
				if len(body) < size {
					return empty, ErrJPEGHeader
				}
				body = body[size:]
			}
		case marker == 0xfe:
			header.Comment = bytes.Clone(body)
		case app:
			switch {
			case marker == 0xe0 && bytes.HasPrefix(body, []byte("JFIF")):
				if len(body) < 7 {
					return empty, ErrJPEGHeader
				}
				if len(body) >= 12 && (body[7] == 1 || body[7] == 2) {
					header.HasDPI = true
				}
			case marker == 0xee && bytes.HasPrefix(body, []byte("Adobe")):
				if len(body) < 7 {
					return empty, ErrJPEGHeader
				}
			case marker == 0xe1 && bytes.HasPrefix(body, []byte("Exif\x00\x00")):
				if header.EXIF == nil {
					header.EXIF = bytes.Clone(body)
				} else {
					header.EXIF = append(header.EXIF, body[6:]...)
				}
			case marker == 0xe2 && bytes.HasPrefix(body, []byte("ICC_PROFILE\x00")):
				if minimumICC == nil || bytes.Compare(body, minimumICC) < 0 {
					minimumICC = body
				}
			case marker == 0xe2 && bytes.HasPrefix(body, []byte("MPF\x00")):
				header.MPF = bytes.Clone(body[4:])
				header.MPFOffset = cursor.position - len(body) + 4
			case marker == 0xed && bytes.HasPrefix(body, []byte("Photoshop 3.0\x00")):
				if !validPhotoshopHeader(body) {
					return empty, ErrJPEGHeader
				}
			}
			if marker == 0xe1 && bytes.Contains(body, []byte(" hdrgm:Version=\"")) {
				header.UltraHDR = true
			}
		}
		if marker == 0xda {
			break
		}
	}
	if header.Width <= 0 || header.Height <= 0 || header.Mode == "" {
		return empty, ErrJPEGHeader
	}
	pixels := int64(header.Width) * int64(header.Height)
	if pixels > 2*89478485 {
		return empty, ErrJPEGHeader
	}
	header.PixelWarning = pixels > 89478485
	header.HeaderBytes = cursor.position
	if err := ctx.Err(); err != nil {
		return empty, err
	}
	return header, nil
}

type jpegHeaderCursor struct {
	ctx             context.Context
	data            []byte
	position, limit int
}

func (c *jpegHeaderCursor) take(n int) ([]byte, error) {
	if err := c.ctx.Err(); err != nil {
		return nil, err
	}
	if n > c.limit-c.position {
		return nil, ErrWorkingLimit
	}
	if n > len(c.data)-c.position {
		return nil, ErrJPEGHeader
	}
	result := c.data[c.position : c.position+n]
	c.position += n
	return result, nil
}

// Pillow catches truncated numeric fields in Photoshop resources but a missing
// Pascal-name length is an IndexError that invalidates ImageFile construction.
func validPhotoshopHeader(body []byte) bool {
	offset := int64(14)
	length := int64(len(body))
	for offset+4 <= length && string(body[offset:offset+4]) == "8BIM" {
		offset += 4
		if offset+2 > length {
			return true
		}
		code := binary.BigEndian.Uint16(body[offset : offset+2])
		offset += 2
		if offset >= length {
			return false
		}
		offset += 1 + int64(body[offset])
		offset += offset & 1
		if offset+4 > length {
			return true
		}
		size := int64(binary.BigEndian.Uint32(body[offset : offset+4]))
		offset += 4
		if code == 0x03ed && min(size, length-offset) < 14 {
			return true
		}
		offset += size
		offset += offset & 1
	}
	return true
}
