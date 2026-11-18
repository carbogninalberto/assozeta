// Deflate streams are read the way MuPDF reads them. MuPDF's loaders call
// zlib's inflate() and keep what it produced when the input ends mid-stream (a
// PNG with a cut IDAT opens with the samples decoded so far). compress/flate
// stops one symbol earlier there: readHuffman raises the literal table's
// minimum code length to the end-of-block code's length, so a shorter symbol is
// refused even while its own bits are present. This decoder follows zlib: a
// symbol is decoded once the bits of its own code are there, and the output
// produced when the input runs out is the answer.
package medicalform

// inflateBits reads a deflate stream bit by bit, least significant bit of each
// byte first.
type inflateBits struct {
	data []byte
	pos  int
	bit  int
}

func (b *inflateBits) available() int {
	return (len(b.data)-b.pos)*8 - b.bit
}

// take reads n bits and reports whether the stream still holds them.
func (b *inflateBits) take(n int) (int, bool) {
	if n == 0 {
		return 0, true
	}
	if b.available() < n {
		return 0, false
	}
	value := 0
	for i := 0; i < n; i++ {
		value |= int(b.data[b.pos]>>uint(b.bit)&1) << uint(i)
		b.bit++
		if b.bit == 8 {
			b.bit = 0
			b.pos++
		}
	}
	return value, true
}

// align drops the bits left in the current byte (a stored block's padding).
func (b *inflateBits) align() {
	if b.bit != 0 {
		b.bit = 0
		b.pos++
	}
}

// inflateDecode is the result of one Huffman symbol: the symbol, the input
// ending first, or a code zlib calls invalid.
type inflateDecode int

const (
	inflateSymbol inflateDecode = iota
	inflateShort
	inflateInvalid
)

// inflateHuffman is a canonical Huffman code as zlib's inflate_table builds it:
// the codes of one length are consecutive, and the symbols of a length follow
// each other in symbol order.
type inflateHuffman struct {
	count   [16]int
	base    [16]int
	offset  [16]int
	symbols []int
	max     int
	empty   bool
}

// newInflateHuffman builds the table zlib builds, refusing the sets it
// refuses: an over-subscribed one, an incomplete one (but for a single code of
// one bit in the two data trees), and the code-length tree's own incomplete
// form.
func newInflateHuffman(lengths []int, codeLengths bool) (inflateHuffman, bool) {
	var h inflateHuffman
	for _, length := range lengths {
		if length < 0 || length > 15 {
			return h, false
		}
		h.count[length]++
		if length > h.max {
			h.max = length
		}
	}
	if h.max == 0 {
		// zlib keeps a two-entry invalid table: an empty distance tree is a
		// block without matches, and a symbol from it is an invalid code.
		h.empty = true
		return h, true
	}
	left := 1
	for length := 1; length <= 15; length++ {
		left <<= 1
		left -= h.count[length]
		if left < 0 {
			return h, false
		}
	}
	if left > 0 && (codeLengths || h.max != 1) {
		return h, false
	}
	code, index := 0, 0
	for length := 1; length <= 15; length++ {
		h.base[length] = code
		h.offset[length] = index
		code = (code + h.count[length]) << 1
		index += h.count[length]
	}
	h.symbols = make([]int, 0, index)
	for length := 1; length <= 15; length++ {
		for symbol, n := range lengths {
			if n == length {
				h.symbols = append(h.symbols, symbol)
			}
		}
	}
	return h, true
}

// decode reads one symbol. A code is decoded only when all of its bits are
// present, as zlib's inflate() requires with the bits left at the end of the
// input.
func (h *inflateHuffman) decode(b *inflateBits) (int, inflateDecode) {
	if h.empty {
		return 0, inflateInvalid
	}
	code := 0
	for length := 1; length <= h.max; length++ {
		bit, present := b.take(1)
		if !present {
			return 0, inflateShort
		}
		code = code<<1 | bit
		if index := code - h.base[length]; index >= 0 && index < h.count[length] {
			return h.symbols[h.offset[length]+index], inflateSymbol
		}
	}
	return 0, inflateInvalid
}

var inflateLengthBase = [29]int{3, 4, 5, 6, 7, 8, 9, 10, 11, 13, 15, 17, 19, 23, 27, 31, 35, 43, 51, 59,
	67, 83, 99, 115, 131, 163, 195, 227, 258}
var inflateLengthExtra = [29]int{0, 0, 0, 0, 0, 0, 0, 0, 1, 1, 1, 1, 2, 2, 2, 2, 3, 3, 3, 3, 4, 4, 4, 4,
	5, 5, 5, 5, 0}
var inflateDistanceBase = [30]int{1, 2, 3, 4, 5, 7, 9, 13, 17, 25, 33, 49, 65, 97, 129, 193, 257, 385,
	513, 769, 1025, 1537, 2049, 3073, 4097, 6145, 8193, 12289, 16385, 24577}
var inflateDistanceExtra = [30]int{0, 0, 0, 0, 1, 1, 2, 2, 3, 3, 4, 4, 5, 5, 6, 6, 7, 7, 8, 8, 9, 9,
	10, 10, 11, 11, 12, 12, 13, 13}

// inflateCodeOrder is zlib's order of the code-length code's lengths.
var inflateCodeOrder = [19]int{16, 17, 18, 0, 8, 7, 9, 6, 10, 5, 11, 4, 12, 3, 13, 2, 14, 1, 15}

// fixedInflateLit and fixedInflateDist are the fixed Huffman codes of RFC 1951
// section 3.2.6.
var fixedInflateLit, fixedInflateDist = func() (lit, dist inflateHuffman) {
	litLengths := make([]int, 288)
	for i := range litLengths {
		switch {
		case i < 144:
			litLengths[i] = 8
		case i < 256:
			litLengths[i] = 9
		case i < 280:
			litLengths[i] = 7
		default:
			litLengths[i] = 8
		}
	}
	distLengths := make([]int, 32)
	for i := range distLengths {
		distLengths[i] = 5
	}
	lit, _ = newInflateHuffman(litLengths, false)
	dist, _ = newInflateHuffman(distLengths, false)
	return lit, dist
}()

// inflateZlib is inflate() as MuPDF's loaders call it: it decodes the stream
// until the input ends and returns the bytes produced, at most limit of them.
// ok is false when zlib would report invalid data or the stream carries more
// output than the limit.
func inflateZlib(data []byte, limit int) (out []byte, ok bool) {
	if len(data) < 2 {
		return nil, false
	}
	method, flags := int(data[0]), int(data[1])
	if method&0x0f != 8 || method>>4 > 7 || (method<<8|flags)%31 != 0 || flags&0x20 != 0 {
		return nil, false
	}
	b := &inflateBits{data: data, pos: 2}
	out = make([]byte, 0, limit)
	for {
		finalBit, present := b.take(1)
		if !present {
			return out, true
		}
		final := finalBit != 0
		kind, present := b.take(2)
		if !present {
			return out, true
		}
		var lit, dist inflateHuffman
		switch kind {
		case 0:
			b.align()
			if b.available() < 32 {
				return out, true
			}
			length, _ := b.take(16)
			complement, _ := b.take(16)
			if length != (^complement)&0xffff {
				return nil, false
			}
			for i := 0; i < length; i++ {
				if b.available() < 8 {
					return out, true
				}
				if len(out) == limit {
					// The output buffer is full with the block unfinished:
					// zlib reports it full, and MuPDF refuses only when the
					// input still carries bytes.
					return nil, false
				}
				value, _ := b.take(8)
				out = append(out, byte(value))
			}
			if final {
				return out, true
			}
			continue
		case 1:
			lit, dist = fixedInflateLit, fixedInflateDist
		case 2:
			table, status := inflateDynamicTree(b)
			if status == inflateShort {
				return out, true
			}
			if status == inflateInvalid {
				return nil, false
			}
			lit, dist = table[0], table[1]
		default:
			return nil, false
		}
		for {
			symbol, status := lit.decode(b)
			if status == inflateShort {
				return out, true
			}
			if status == inflateInvalid {
				return nil, false
			}
			if symbol < 256 {
				if room := limit - len(out); room <= 0 {
					// The output buffer is full: MuPDF refuses when the input
					// still carries samples, and accepts the full buffer when
					// the input ends here.
					if b.available() == 0 {
						return out, true
					}
					return nil, false
				}
				out = append(out, byte(symbol))
				continue
			}
			if symbol == 256 {
				break
			}
			if symbol > 285 {
				return nil, false
			}
			index := symbol - 257
			extra, present := b.take(inflateLengthExtra[index])
			if !present {
				return out, true
			}
			length := inflateLengthBase[index] + extra
			symbol, status = dist.decode(b)
			if status == inflateShort {
				return out, true
			}
			if status == inflateInvalid || symbol > 29 {
				return nil, false
			}
			extra, present = b.take(inflateDistanceExtra[symbol])
			if !present {
				return out, true
			}
			distance := inflateDistanceBase[symbol] + extra
			if distance > len(out) {
				return nil, false
			}
			if room := limit - len(out); length > room {
				// zlib copies what fits and reports the buffer full; MuPDF
				// keeps that answer when the input ended with it and refuses
				// otherwise.
				for i := 0; i < room; i++ {
					out = append(out, out[len(out)-distance])
				}
				if b.available() == 0 {
					return out, true
				}
				return nil, false
			}
			for i := 0; i < length; i++ {
				out = append(out, out[len(out)-distance])
			}
		}
		if final {
			return out, true
		}
	}
}

// inflateDynamicTree reads a dynamic block's code lengths and builds its two
// tables (zlib's "code lengths for the code length alphabet" and following
// states).
func inflateDynamicTree(b *inflateBits) (table [2]inflateHuffman, status inflateDecode) {
	if b.available() < 5+5+4 {
		return table, inflateShort
	}
	nlitBits, _ := b.take(5)
	ndistBits, _ := b.take(5)
	nclen, _ := b.take(4)
	nlit, ndist := nlitBits+257, ndistBits+1
	if nlit > 286 || ndist > 30 {
		return table, inflateInvalid
	}
	var codeLengths [19]int
	for i := 0; i < nclen+4; i++ {
		value, present := b.take(3)
		if !present {
			return table, inflateShort
		}
		codeLengths[inflateCodeOrder[i]] = value
	}
	codeTree, valid := newInflateHuffman(codeLengths[:], true)
	if !valid {
		return table, inflateInvalid
	}
	lengths := make([]int, 0, nlit+ndist)
	total := nlit + ndist
	for len(lengths) < total {
		symbol, code := codeTree.decode(b)
		if code != inflateSymbol {
			return table, code
		}
		switch {
		case symbol < 16:
			lengths = append(lengths, symbol)
		case symbol == 16:
			if len(lengths) == 0 {
				return table, inflateInvalid
			}
			extra, present := b.take(2)
			if !present {
				return table, inflateShort
			}
			if len(lengths)+3+extra > total {
				return table, inflateInvalid
			}
			last := lengths[len(lengths)-1]
			for i := 0; i < 3+extra; i++ {
				lengths = append(lengths, last)
			}
		case symbol == 17:
			extra, present := b.take(3)
			if !present {
				return table, inflateShort
			}
			if len(lengths)+3+extra > total {
				return table, inflateInvalid
			}
			for i := 0; i < 3+extra; i++ {
				lengths = append(lengths, 0)
			}
		default:
			extra, present := b.take(7)
			if !present {
				return table, inflateShort
			}
			if len(lengths)+11+extra > total {
				return table, inflateInvalid
			}
			for i := 0; i < 11+extra; i++ {
				lengths = append(lengths, 0)
			}
		}
	}
	table[0], valid = newInflateHuffman(lengths[:nlit], false)
	if !valid {
		return table, inflateInvalid
	}
	// zlib: "invalid code -- missing end-of-block".
	if lengths[256] == 0 {
		return table, inflateInvalid
	}
	table[1], valid = newInflateHuffman(lengths[nlit:], false)
	if !valid {
		return table, inflateInvalid
	}
	return table, inflateSymbol
}
