package loginimage

import (
	"bytes"
	"context"
	"errors"
	"math/big"
)

var ErrXBMHeader = errors.New("login XBM header invalid")

type XBMHeader struct {
	Width, Height, DataOffset, HeaderBytes int
	Mode                                   string
	PixelWarning                           bool
}

func xbmSignature(data []byte) bool {
	return bytes.HasPrefix(data, []byte("#define")) || bytes.HasPrefix(data, []byte("\n#define")) || bytes.HasPrefix(data, []byte("/*"))
}

// ReadXBMHeader follows Pillow 13's 75-byte readline parser. The logical budget
// charges every consumed byte, including comments, but stops at the first bits
// declaration. Long comment sequences are bounded by the caller's header budget.
func ReadXBMHeader(ctx context.Context, data []byte, maxBytes int) (XBMHeader, error) {
	empty := XBMHeader{}
	if err := ctx.Err(); err != nil {
		return empty, err
	}
	if maxBytes <= 0 {
		return empty, ErrWorkingLimit
	}
	if !xbmSignature(data) {
		return empty, ErrXBMHeader
	}
	var width, height *big.Int
	starting, comment := true, false
	used, offset := 0, 0
	for used < len(data) {
		if err := ctx.Err(); err != nil {
			return empty, err
		}
		n := min(75, len(data)-used)
		if newline := bytes.IndexByte(data[used:used+n], '\n'); newline >= 0 {
			n = newline + 1
		}
		if n > maxBytes-used {
			return empty, ErrWorkingLimit
		}
		line := bytes.TrimRight(data[used:used+n], " \t\n\r\v\f")
		used += n
		if comment {
			if bytes.HasSuffix(line, []byte("*/")) {
				comment = false
			}
			continue
		}
		if bytes.HasPrefix(line, []byte("/*")) {
			comment = !bytes.HasSuffix(line, []byte("*/"))
			continue
		}
		if starting && len(line) == 0 {
			starting = false
			continue
		}
		if !bytes.HasPrefix(line, []byte("#define ")) {
			if bytes.HasSuffix(line, []byte("_bits[] = {")) {
				offset = used
			}
			break
		}
		fields := bytes.FieldsFunc(line, func(r rune) bool { return r == ' ' || r >= '\t' && r <= '\r' })
		value, ok := xbmInteger(fields[len(fields)-1])
		if !ok {
			return empty, ErrXBMHeader
		}
		// Unknown/hotspot declarations still validate their integer. Hotspots
		// do not affect JPEG output; width/height substring order is significant.
		if bytes.Contains(line, []byte("width ")) {
			width = value
		} else if bytes.Contains(line, []byte("height ")) {
			height = value
		}
	}
	if width == nil || height == nil || offset == 0 || !width.IsInt64() || !height.IsInt64() {
		return empty, ErrXBMHeader
	}
	w, h := width.Int64(), height.Int64()
	if w <= 0 || h <= 0 || w > 178956970/h {
		return empty, ErrXBMHeader
	}
	if err := ctx.Err(); err != nil {
		return empty, err
	}
	return XBMHeader{int(w), int(h), offset, used, "1", w*h > 89478485}, nil
}

// Python int(bytes) accepts a sign and single underscores between ASCII digits.
// Inputs are at most one 75-byte header line, including unused declarations.
func xbmInteger(value []byte) (*big.Int, bool) {
	digits := value
	if len(digits) > 0 && (digits[0] == '+' || digits[0] == '-') {
		digits = digits[1:]
	}
	if len(digits) == 0 {
		return nil, false
	}
	for i, c := range digits {
		if c >= '0' && c <= '9' {
			continue
		}
		if c != '_' || i == 0 || i == len(digits)-1 || digits[i-1] < '0' || digits[i-1] > '9' || digits[i+1] < '0' || digits[i+1] > '9' {
			return nil, false
		}
	}
	return new(big.Int).SetString(string(bytes.ReplaceAll(value, []byte("_"), nil)), 10)
}
