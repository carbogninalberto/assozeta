package loginimage

import (
	"context"
	"encoding/binary"
	"errors"
)

var ErrGIFHeader = errors.New("login GIF header invalid")

type GIFHeader struct {
	Width, Height, Left, Top, Right, Bottom, Bits, DataOffset int
	Mode                                                      string
	Interlace, PaletteAttached                                bool
	Transparency                                              int
	Comment                                                   []byte
}

func gifSignature(b []byte) bool {
	return len(b) >= 6 && (string(b[:6]) == "GIF87a" || string(b[:6]) == "GIF89a")
}

// ReadGIFHeader inspects only the first frame. Its logical budget covers consumed
// input and retained comments; it does not allocate a raster or read LZW blocks.
func ReadGIFHeader(ctx context.Context, input []byte, maxBytes int) (GIFHeader, error) {
	empty := GIFHeader{}
	if err := ctx.Err(); err != nil {
		return empty, err
	}
	if maxBytes <= 0 {
		return empty, ErrWorkingLimit
	}
	pos := 0
	var comment []byte
	read := func(n int) ([]byte, error) {
		if err := ctx.Err(); err != nil {
			return nil, err
		}
		n = min(n, len(input)-pos)
		if n > maxBytes-pos-len(comment) {
			return nil, ErrWorkingLimit
		}
		b := input[pos : pos+n]
		pos += n
		return b, nil
	}
	block := func() ([]byte, error) {
		b, e := read(1)
		if e != nil || len(b) == 0 {
			return nil, e
		}
		return read(int(b[0]))
	}
	palette := func(n int) (bool, error) {
		p, e := read(n)
		if e != nil {
			return false, e
		}
		for i := 0; i < len(p); i += 3 {
			if p[i] != byte(i/3) {
				return true, nil
			}
			if i+1 >= len(p) {
				return false, ErrGIFHeader
			}
			if p[i+1] != p[i] {
				return true, nil
			}
			if i+2 >= len(p) {
				return false, ErrGIFHeader
			}
			if p[i+2] != p[i] {
				return true, nil
			}
		}
		return false, nil
	}
	b, e := read(13)
	if e != nil {
		return empty, e
	}
	if len(b) < 13 || !gifSignature(b) {
		return empty, ErrGIFHeader
	}
	le := binary.LittleEndian
	h := GIFHeader{Width: int(le.Uint16(b[6:])), Height: int(le.Uint16(b[8:])), Transparency: -1}
	global := false
	if b[10]&128 != 0 {
		global, e = palette(3 << ((b[10] & 7) + 1))
		if e != nil {
			return empty, e
		}
	}
	h.PaletteAttached = global
	for {
		b, e = read(1)
		if e != nil {
			return empty, e
		}
		if len(b) == 0 || b[0] == ';' {
			return empty, ErrGIFHeader
		}
		switch b[0] {
		case '!':
			kind, err := read(1)
			if err != nil {
				return empty, err
			}
			if len(kind) == 0 {
				return empty, ErrGIFHeader
			}
			data, err := block()
			if err != nil {
				return empty, err
			}
			switch kind[0] {
			case 249:
				if len(data) > 0 {
					if len(data) < 3 || data[0]&1 != 0 && len(data) < 4 {
						return empty, ErrGIFHeader
					}
					if data[0]&1 != 0 {
						h.Transparency = int(data[3])
					}
				}
			case 254:
				// Multiple comment extensions are joined by a newline, even when empty.
				if comment != nil {
					if pos+len(comment)+1 > maxBytes {
						return empty, ErrWorkingLimit
					}
					comment = append(comment, '\n')
				} else {
					comment = make([]byte, 0)
				}
				for len(data) > 0 {
					if len(data) > maxBytes-pos-len(comment) {
						return empty, ErrWorkingLimit
					}
					comment = append(comment, data...)
					data, err = block()
					if err != nil {
						return empty, err
					}
				}
				continue
			}
			for {
				data, err = block()
				if err != nil {
					return empty, err
				}
				if len(data) == 0 {
					break
				}
			}
		case ',':
			b, e = read(9)
			if e != nil {
				return empty, e
			}
			if len(b) < 9 {
				return empty, ErrGIFHeader
			}
			h.Left = int(le.Uint16(b))
			h.Top = int(le.Uint16(b[2:]))
			h.Right = h.Left + int(le.Uint16(b[4:]))
			h.Bottom = h.Top + int(le.Uint16(b[6:]))
			h.Interlace = b[8]&64 != 0
			h.Width = max(h.Width, h.Right)
			h.Height = max(h.Height, h.Bottom)
			active := global
			if b[8]&128 != 0 {
				active, e = palette(3 << ((b[8] & 7) + 1))
				if e != nil {
					return empty, e
				}
				if active {
					h.PaletteAttached = true
				}
			}
			h.Mode = "L"
			if active {
				h.Mode = "P"
			}
			b, e = read(1)
			if e != nil {
				return empty, e
			}
			if len(b) == 0 {
				return empty, ErrGIFHeader
			}
			h.Bits = int(b[0])
			h.DataOffset = pos
			if h.Width == 0 || h.Height == 0 || uint64(h.Width)*uint64(h.Height) > 178956970 {
				return empty, ErrGIFHeader
			}
			if len(comment) > 0 {
				h.Comment = comment
			}
			if err := ctx.Err(); err != nil {
				return empty, err
			}
			return h, nil
		}
	}
}
