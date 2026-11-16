package pycodecs

// This file ports CPython 3.13's 'punycode' decoder, a straight port of
// Lib/encodings/punycode.py (RFC 3492).
//
// The decoder only supports the strict, replace and ignore error handlers;
// any other handler raises UnicodeError("Unsupported error handling: ...")
// before the input is touched. The base (ASCII) part is decoded with the
// asynchronous "ascii" codec and its requested handler, while the extended
// part either raises a UnicodeDecodeError in strict mode or, for replace and
// ignore, silently stops at the first unusable byte. Deltas are Python
// integers: they can exceed 64 bits for long strings, so big.Int is used.
//
// Error positions are byte offsets into the whole input. The ascii errors
// name "ascii"; the rest name "punycode". A few positions can point past the
// end of the input (the generalized-number decoder looks one byte ahead),
// exactly as CPython reports them.

import (
	"bytes"
	"errors"
	"fmt"
	"math"
	"math/big"
)

func init() {
	register("punycode", decodePunycode)
}

// punycodeUpper is bytes.upper(): ASCII letters only.
func punycodeUpper(b []byte) []byte {
	out := make([]byte, len(b))
	for i, c := range b {
		if c >= 'a' && c <= 'z' {
			c -= 'a' - 'A'
		}
		out[i] = c
	}
	return out
}

// punycodeASCII decodes the base part (str(text[:pos], "ascii", errors)).
// The reported data is the whole input so offsets stay absolute.
func punycodeASCII(part, data []byte, h Handler) ([]rune, error) {
	out := make([]rune, 0, len(part))
	for i, c := range part {
		if c < 0x80 {
			out = append(out, rune(c))
			continue
		}
		switch h {
		case Strict:
			// The ASCII codec's only error: one non-ASCII byte.
			return nil, &DecodeError{Codec: "ascii", Data: data, Start: i, End: i + 1, Reason: "ordinal not in range(128)"}
		case Replace:
			out = append(out, 0xFFFD)
		case SurrogateEscape:
			// Unreachable: decodePunycode rejects this handler first.
			out = append(out, 0xDC00+rune(c))
		default: // Ignore
		}
	}
	return out, nil
}

// punycodeT is T(j, bias) with tmin=1 and tmax=26.
func punycodeT(j, bias int) int {
	res := 36*(j+1) - bias
	if res < 1 {
		return 1
	}
	if res > 26 {
		return 26
	}
	return res
}

// punycodeAdapt is adapt(delta, first, numchars): bias adaptation.
func punycodeAdapt(delta *big.Int, first bool, numchars int) int {
	d := new(big.Int).Set(delta)
	if first {
		d.Quo(d, big.NewInt(700))
	} else {
		d.Quo(d, big.NewInt(2))
	}
	d.Add(d, new(big.Int).Quo(new(big.Int).Set(d), big.NewInt(int64(numchars))))

	divisions := 0
	for d.Cmp(big.NewInt(455)) > 0 {
		d.Quo(d, big.NewInt(35))
		divisions += 36
	}
	// divisions + 36 * d // (d + 38)
	num := new(big.Int).Mul(big.NewInt(36), d)
	den := new(big.Int).Add(new(big.Int).Set(d), big.NewInt(38))
	num.Quo(num, den)
	return divisions + int(num.Int64())
}

// punycodeBigToInt converts a Python-integer position to an int, saturating
// at the int range. The generated corpus avoids positions that do not fit
// (they need dozens of maximal base-36 digits); for such input CPython would
// print a larger position than int can hold.
func punycodeBigToInt(b *big.Int) int {
	if b.IsInt64() {
		return int(b.Int64())
	}
	if b.Sign() < 0 {
		return math.MinInt
	}
	return math.MaxInt
}

// punycodePosition is offset + b, saturated like punycodeBigToInt.
func punycodePosition(offset int, b *big.Int) int {
	return punycodeBigToInt(new(big.Int).Add(big.NewInt(int64(offset)), b))
}

// punycodeGeneralizedNumber is decode_generalized_number. ok is false when
// the (non-strict) decoder gives up; strict failures return an error.
func punycodeGeneralizedNumber(extended []byte, extpos, bias int, data []byte, offset int, h Handler) (int, *big.Int, bool, error) {
	result := big.NewInt(0)
	w := big.NewInt(1)
	j := 0
	for {
		if extpos >= len(extended) {
			if h == Strict {
				return 0, nil, false, &DecodeError{
					Codec: "punycode", Data: data,
					Start: offset + extpos, End: offset + extpos + 1,
					Reason: "incomplete punycode string",
				}
			}
			return extpos + 1, nil, false, nil
		}
		c := extended[extpos]
		extpos++
		var digit int
		switch {
		case c >= 'A' && c <= 'Z':
			digit = int(c - 'A')
		case c >= '0' && c <= '9':
			digit = int(c - 22) // '0' is 26
		default:
			if h == Strict {
				return 0, nil, false, &DecodeError{
					Codec: "punycode", Data: data,
					Start: offset + extpos - 1, End: offset + extpos,
					Reason: fmt.Sprintf("Invalid extended code point '%d'", c),
				}
			}
			return extpos, nil, false, nil
		}
		t := punycodeT(j, bias)
		result.Add(result, new(big.Int).Mul(big.NewInt(int64(digit)), w))
		if digit < t {
			return extpos, result, true, nil
		}
		w.Mul(w, big.NewInt(int64(36-t)))
		j++
	}
}

// punycodeInsertionSort is insertion_sort. On a successful decode it appends
// the result to sink.Out.
func punycodeInsertionSort(data []byte, offset int, base []rune, extended []byte, sink *Sink) error {
	char := big.NewInt(0x80)
	pos := big.NewInt(-1)
	bias := 72
	extpos := 0

	for extpos < len(extended) {
		newpos, delta, ok, err := punycodeGeneralizedNumber(extended, extpos, bias, data, offset, sink.Handler())
		if err != nil {
			return err
		}
		if !ok {
			// Synchronization is lost: CPython returns the base decoded so
			// far and ignores the rest of the extended part.
			break
		}
		pos.Add(pos, delta)
		pos.Add(pos, big.NewInt(1))

		denom := int64(len(base) + 1)
		char.Add(char, new(big.Int).Quo(pos, big.NewInt(denom)))
		if char.Cmp(big.NewInt(0x10FFFF)) > 0 {
			if sink.Handler() == Strict {
				return &DecodeError{
					Codec: "punycode", Data: data,
					Start:  punycodePosition(offset, new(big.Int).Sub(pos, big.NewInt(1))),
					End:    punycodePosition(offset, pos),
					Reason: fmt.Sprintf("Invalid character U+%s", char.Text(16)),
				}
			}
			char.SetInt64('?')
		}

		pos.Mod(pos, big.NewInt(denom))
		idx := int(pos.Int64())
		base = append(base, 0)
		copy(base[idx+1:], base[idx:])
		base[idx] = rune(char.Int64())

		bias = punycodeAdapt(delta, extpos == 0, len(base))
		extpos = newpos
	}

	sink.Out = append(sink.Out, base...)
	return nil
}

// decodePunycode is Codec.decode: it rejects unsupported handlers before
// reading the input, splits at the last '-' and runs the insertion sort.
func decodePunycode(data []byte, sink *Sink) error {
	switch sink.Handler() {
	case Strict, Replace, Ignore:
	default:
		return errors.New("Unsupported error handling: " + refusedHandlerName(sink.Handler()))
	}

	pos := bytes.LastIndexByte(data, '-')
	var base []rune
	var extended []byte
	if pos < 0 {
		extended = punycodeUpper(data)
	} else {
		var err error
		base, err = punycodeASCII(data[:pos], data, sink.Handler())
		if err != nil {
			return err
		}
		extended = punycodeUpper(data[pos+1:])
	}
	return punycodeInsertionSort(data, pos+1, base, extended, sink)
}
