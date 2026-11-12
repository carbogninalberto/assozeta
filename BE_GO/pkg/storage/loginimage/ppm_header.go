package loginimage

import (
	"context"
	"errors"
	"math"
	"strconv"
	"strings"
)

var ErrPPMHeader = errors.New("login PPM header invalid")

// PPMHeader covers Pillow's PBM/PGM/PPM, grayscale PFM and PIL extensions.
// Scale retains its sign (PFM byte order); sample values are not multiplied by it.
type PPMHeader struct {
	Width, Height, MaxValue, DataOffset int
	Magic, Mode                         string
	Scale                               float64
	PixelWarning                        bool
}

func ppmSpace(c byte) bool {
	return c == ' ' || c == '\t' || c == '\n' || c == '\v' || c == '\f' || c == '\r'
}
func ppmSignature(data []byte) bool {
	return len(data) >= 2 && data[0] == 'P' && strings.ContainsRune("0123456fy", rune(data[1]))
}
func ppmMode(magic string, maxValue int) string {
	switch magic {
	case "P1", "P4":
		return "1"
	case "P2", "P5":
		if maxValue > 255 {
			return "I"
		}
		return "L"
	case "P3", "P6":
		return "RGB"
	case "P0CMYK", "PyCMYK":
		return "CMYK"
	case "PyRGBA":
		return "RGBA"
	case "PyP":
		return "P"
	case "Pf":
		return "F"
	}
	return ""
}

// ReadPPMHeader consumes only the lazy header. Its byte limit includes skipped
// comments/whitespace; each number is limited to Pillow's ten-byte token length.
func ReadPPMHeader(ctx context.Context, data []byte, maxBytes int) (PPMHeader, error) {
	empty := PPMHeader{}
	if err := ctx.Err(); err != nil {
		return empty, err
	}
	if maxBytes <= 0 {
		return empty, ErrWorkingLimit
	}
	pos := 0
	read := func() (byte, bool, error) {
		if err := ctx.Err(); err != nil {
			return 0, false, err
		}
		if pos == len(data) {
			return 0, false, nil
		}
		if pos >= maxBytes {
			return 0, false, ErrWorkingLimit
		}
		c := data[pos]
		pos++
		return c, true, nil
	}
	magic := make([]byte, 0, 6)
	for i := 0; i < 6; i++ {
		c, ok, err := read()
		if err != nil {
			return empty, err
		}
		if !ok || ppmSpace(c) {
			break
		}
		magic = append(magic, c)
	}
	h := PPMHeader{Magic: string(magic)}
	if ppmMode(h.Magic, 0) == "" {
		return empty, ErrPPMHeader
	}
	token := func() (string, error) {
		var buf [11]byte
		n := 0
		for n <= 10 {
			c, ok, err := read()
			if err != nil {
				return "", err
			}
			if !ok {
				break
			}
			if ppmSpace(c) {
				if n == 0 {
					continue
				}
				break
			}
			if c == '#' {
				for {
					c, ok, err = read()
					if err != nil {
						return "", err
					}
					if !ok || c == '\r' || c == '\n' {
						break
					}
				}
				continue
			}
			buf[n] = c
			n++
		}
		if n == 0 || n > 10 {
			return "", ErrPPMHeader
		}
		return string(buf[:n]), nil
	}
	values := [2]int{}
	for i := range values {
		s, err := token()
		if err != nil {
			return empty, err
		}
		v, ok := ppmInteger(s)
		if !ok || v <= 0 || v > 178956970 {
			return empty, ErrPPMHeader
		}
		values[i] = int(v)
	}
	h.Width, h.Height = values[0], values[1]
	if uint64(h.Width)*uint64(h.Height) > 178956970 {
		return empty, ErrPPMHeader
	}
	h.PixelWarning = uint64(h.Width)*uint64(h.Height) > 89478485
	if h.Magic == "Pf" {
		s, err := token()
		if err != nil {
			return empty, err
		}
		if !ppmUnderscores(s) || strings.ContainsAny(s, "xXpP") {
			return empty, ErrPPMHeader
		}
		v, err := strconv.ParseFloat(strings.ReplaceAll(s, "_", ""), 64)
		if err != nil || v == 0 || math.IsNaN(v) || math.IsInf(v, 0) {
			return empty, ErrPPMHeader
		}
		h.Scale = v
	} else if h.Magic != "P1" && h.Magic != "P4" {
		s, err := token()
		if err != nil {
			return empty, err
		}
		v, ok := ppmInteger(s)
		if !ok || v <= 0 || v >= 65536 {
			return empty, ErrPPMHeader
		}
		h.MaxValue = int(v)
	}
	h.Mode = ppmMode(h.Magic, h.MaxValue)
	h.DataOffset = pos
	if err := ctx.Err(); err != nil {
		return empty, err
	}
	return h, nil
}
func ppmUnderscores(s string) bool {
	for i := 0; i < len(s); i++ {
		if s[i] == '_' && (i == 0 || i == len(s)-1 || s[i-1] < '0' || s[i-1] > '9' || s[i+1] < '0' || s[i+1] > '9') {
			return false
		}
	}
	return true
}
func ppmInteger(s string) (int64, bool) {
	if len(s) == 0 || len(s) > 10 || !ppmUnderscores(s) {
		return 0, false
	}
	v, err := strconv.ParseInt(strings.ReplaceAll(s, "_", ""), 10, 64)
	return v, err == nil
}
