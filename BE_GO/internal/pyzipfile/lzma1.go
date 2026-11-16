package pyzipfile

// A raw LZMA1 decoder for zipfile's ZIP_LZMA members: the 4-byte zip
// properties header is consumed by the zipfile wrapper (lzmaDecompressor),
// the 5 property bytes give lc/lp/pb and the dictionary size, and the stream
// is the LZMA1 range-coded data with an end-of-stream marker.
//
// The decoder follows liblzma's lzma_decoder.c (which CPython's _lzmamodule
// wraps): probability updates happen bit by bit, the range coder reads input
// lazily, and a decompress() call stops at the requested output limit (or
// when it needs input) without losing progress. To keep the Go code simple
// the decoder rewinds a partial symbol to the symbol boundary using an undo
// log of the probability updates, which reproduces liblzma's resumable
// state machine exactly at the granularity liblzma observes (a symbol is
// atomic: it either produced all of its output or none).

const (
	lzmaNumStates    = 12
	lzmaNumLitStates = 7
	lzmaNumPosBits   = 4
	lzmaNumPosStates = 1 << lzmaNumPosBits
	lzmaLenLowBits   = 3
	lzmaLenLow       = 8
	lzmaLenHighBits  = 8
	lzmaKMatchMinLen = 2
	lzmaLitSize      = 0x300
	lzmaNumTopValue  = 1 << 24
	lzmaBitModel     = 1 << 11
	lzmaMoveBits     = 5

	lzmaSpecPos       = 0
	lzmaIsRep0Long    = lzmaSpecPos + 128
	lzmaLenProbs      = 2*(lzmaNumPosStates<<lzmaLenLowBits) + (1 << lzmaLenHighBits)
	lzmaRepLenCoder   = lzmaIsRep0Long + (16 << lzmaNumPosBits)
	lzmaLenCoder      = lzmaRepLenCoder + lzmaLenProbs
	lzmaIsMatch       = lzmaLenCoder + lzmaLenProbs
	lzmaAlign         = lzmaIsMatch + (16 << lzmaNumPosBits)
	lzmaIsRep         = lzmaAlign + 16
	lzmaIsRepG0       = lzmaIsRep + lzmaNumStates
	lzmaIsRepG1       = lzmaIsRepG0 + lzmaNumStates
	lzmaIsRepG2       = lzmaIsRepG1 + lzmaNumStates
	lzmaPosSlot       = lzmaIsRepG2 + lzmaNumStates
	lzmaLiteral       = lzmaPosSlot + (4 << 6)
	lzmaNumBaseProbs  = lzmaLiteral
	lzmaStartPosModel = 4
	lzmaEndPosModel   = 14
)

type lzmaOptions struct {
	lc, lp, pb int
	dictSize   uint32
}

// parseLZMAProps is lzma_lzma_props_decode: exactly five property bytes, lc
// and lp at most 4 and lc+lp at most 4, pb at most 4.
func parseLZMAProps(props []byte) (lzmaOptions, bool) {
	var opts lzmaOptions
	if len(props) != 5 {
		return opts, false
	}
	prop := int(props[0])
	if prop >= 9*5*5 {
		return opts, false
	}
	opts.lc = prop % 9
	prop /= 9
	opts.lp = prop % 5
	opts.pb = prop / 5
	if opts.lc > 4 || opts.lp > 4 || opts.lc+opts.lp > 4 || opts.pb > 4 {
		return opts, false
	}
	opts.dictSize = uint32(props[1]) | uint32(props[2])<<8 | uint32(props[3])<<16 | uint32(props[4])<<24
	return opts, true
}

const (
	lzmaNeedMore = iota
	lzmaDone
	lzmaDataError
	lzmaOutputFull
)

// probUndo records one probability update so a partial symbol can be rewound
// to its first bit.
type probUndo struct {
	index int
	value uint16
}

type lzma1Decoder struct {
	opts  lzmaOptions
	probs []uint16

	started      bool
	range_       uint32
	code         uint32
	pos          int
	state        int
	reps         [4]uint32
	processedPos uint32

	out []byte

	// start of the symbol currently being decoded
	symStarted   bool
	symOutLen    int
	symRange     uint32
	symCode      uint32
	symPos       int
	symState     int
	symReps      [4]uint32
	symProcessed uint32
	undo         []probUndo
}

func newLZMA1(opts lzmaOptions) *lzma1Decoder {
	d := &lzma1Decoder{opts: opts}
	n := lzmaNumBaseProbs + lzmaLitSize<<uint(opts.lc+opts.lp)
	d.probs = make([]uint16, n)
	for i := range d.probs {
		d.probs[i] = lzmaBitModel >> 1
	}
	return d
}

// distanceLimit is liblzma's dict.full: the number of bytes a match may look
// back, capped by the (rounded up, at least 4 KiB) dictionary size.
func (d *lzma1Decoder) distanceLimit() uint32 {
	size := uint64(d.opts.dictSize)
	if size < 4096 {
		size = 4096
	} else {
		size = (size + 15) &^ 15
	}
	full := uint64(d.processedPos)
	if size < full {
		full = size
	}
	return uint32(full)
}

// beginSymbol records the state at the first bit of a symbol.
func (d *lzma1Decoder) beginSymbol() {
	d.symStarted = true
	d.symOutLen = len(d.out)
	d.symRange = d.range_
	d.symCode = d.code
	d.symPos = d.pos
	d.symState = d.state
	d.symReps = d.reps
	d.symProcessed = d.processedPos
	d.undo = d.undo[:0]
}

// rewindSymbol restores the state recorded by beginSymbol: the probability
// updates of the partially decoded symbol are undone and the range coder,
// the LZMA state and the output are rewound to the symbol boundary. This is
// what makes "needs more input" resumable without liblzma's sequence enum.
func (d *lzma1Decoder) rewindSymbol() {
	for i := len(d.undo) - 1; i >= 0; i-- {
		d.probs[d.undo[i].index] = d.undo[i].value
	}
	d.undo = d.undo[:0]
	if !d.symStarted {
		return
	}
	d.range_ = d.symRange
	d.code = d.symCode
	d.pos = d.symPos
	d.state = d.symState
	d.reps = d.symReps
	d.processedPos = d.symProcessed
	d.out = d.out[:d.symOutLen]
	d.symStarted = false
}

// needInput rewinds the partial symbol and reports that more compressed input
// is required.
func (d *lzma1Decoder) needInput() int {
	d.rewindSymbol()
	return lzmaNeedMore
}

// bit decodes one range-coded bit with the given model.
func (d *lzma1Decoder) bit(data []byte, index int) (int, bool) {
	if d.range_ < lzmaNumTopValue {
		if d.pos >= len(data) {
			return 0, false
		}
		d.range_ <<= 8
		d.code = d.code<<8 | uint32(data[d.pos])
		d.pos++
	}
	prob := uint32(d.probs[index])
	d.undo = append(d.undo, probUndo{index: index, value: d.probs[index]})
	bound := (d.range_ >> 11) * prob
	if d.code < bound {
		d.range_ = bound
		d.probs[index] = uint16(prob + ((lzmaBitModel - prob) >> lzmaMoveBits))
		return 0, true
	}
	d.range_ -= bound
	d.code -= bound
	d.probs[index] = uint16(prob - (prob >> lzmaMoveBits))
	return 1, true
}

// decode advances the stream using data (the whole compressed member so far),
// stopping when it has produced outLimit bytes (outLimit < 0 means no limit),
// when it needs more input, or at the end of the stream.
func (d *lzma1Decoder) decode(data []byte, outLimit int) int {
	if !d.started {
		if len(data) < 5 {
			return lzmaNeedMore
		}
		if data[0] != 0 {
			return lzmaDataError
		}
		d.range_ = 0xFFFFFFFF
		// LZMA1's range coder starts with a zero byte followed by the
		// four-byte code word.
		d.code = uint32(data[1])<<24 | uint32(data[2])<<16 | uint32(data[3])<<8 | uint32(data[4])
		d.pos = 5
		d.state = 0
		d.reps = [4]uint32{1, 1, 1, 1}
		d.processedPos = 0
		d.started = true
	}

	pbMask := (1 << uint(d.opts.pb)) - 1
	for {
		if outLimit >= 0 && len(d.out) >= outLimit {
			return lzmaOutputFull
		}
		d.beginSymbol()
		posState := (int(d.processedPos) & pbMask) << 4
		b, ok := d.bit(data, lzmaIsMatch+posState+d.state)
		if !ok {
			return d.needInput()
		}
		if b == 0 {
			switch d.literal(data, posState) {
			case lzmaNeedMore:
				return d.needInput()
			case lzmaDataError:
				return lzmaDataError
			}
			continue
		}
		switch d.match(data, posState) {
		case lzmaNeedMore:
			return d.needInput()
		case lzmaDataError:
			return lzmaDataError
		case lzmaDone:
			return lzmaDone
		}
	}
}

func (d *lzma1Decoder) literal(data []byte, posState int) int {
	var prev byte
	if len(d.out) != 0 {
		prev = d.out[len(d.out)-1]
	}
	context := ((int(d.processedPos) & ((1 << uint(d.opts.lp)) - 1)) << uint(d.opts.lc)) +
		int(prev>>uint(8-d.opts.lc))
	base := lzmaLiteral + lzmaLitSize*context
	d.processedPos++
	symbol := 1
	if d.state < lzmaNumLitStates {
		for symbol < 0x100 {
			b, ok := d.bit(data, base+symbol)
			if !ok {
				return lzmaNeedMore
			}
			symbol = symbol<<1 | b
		}
		if d.state < 4 {
			d.state = 0
		} else {
			d.state -= 3
		}
	} else {
		distance := int(d.reps[0])
		if distance > len(d.out) || distance <= 0 {
			return lzmaDataError
		}
		matchByte := uint32(d.out[len(d.out)-distance])
		offs := 0x100
		for symbol < 0x100 {
			matchByte += matchByte
			bitv := offs
			offs &= int(matchByte)
			b, ok := d.bit(data, base+offs+bitv+symbol)
			if !ok {
				return lzmaNeedMore
			}
			symbol = symbol<<1 | b
			if b == 0 {
				offs ^= bitv
			}
		}
		if d.state < 10 {
			d.state -= 3
		} else {
			d.state -= 6
		}
	}
	d.out = append(d.out, byte(symbol))
	return -1
}

// match decodes a match or rep symbol.
func (d *lzma1Decoder) match(data []byte, posState int) int {
	repState := d.state
	b, ok := d.bit(data, lzmaIsRep+repState)
	if !ok {
		return lzmaNeedMore
	}
	prob := 0
	if b == 0 {
		d.state += lzmaNumStates
		prob = lzmaLenCoder
	} else {
		g0, ok := d.bit(data, lzmaIsRepG0+repState)
		if !ok {
			return lzmaNeedMore
		}
		if g0 == 0 {
			b0, ok := d.bit(data, lzmaIsRep0Long+posState+repState)
			if !ok {
				return lzmaNeedMore
			}
			if b0 == 0 {
				distance := int(d.reps[0])
				if distance > len(d.out) || distance <= 0 {
					return lzmaDataError
				}
				d.out = append(d.out, d.out[len(d.out)-distance])
				d.processedPos++
				if d.state < lzmaNumLitStates {
					d.state = 9
				} else {
					d.state = 11
				}
				return -1
			}
		} else {
			g1, ok := d.bit(data, lzmaIsRepG1+repState)
			if !ok {
				return lzmaNeedMore
			}
			var distance uint32
			if g1 == 0 {
				distance = d.reps[1]
			} else {
				g2, ok := d.bit(data, lzmaIsRepG2+repState)
				if !ok {
					return lzmaNeedMore
				}
				if g2 == 0 {
					distance = d.reps[2]
				} else {
					distance = d.reps[3]
					d.reps[3] = d.reps[2]
				}
				d.reps[2] = d.reps[1]
			}
			d.reps[1] = d.reps[0]
			d.reps[0] = distance
		}
		if d.state < lzmaNumLitStates {
			d.state = 8
		} else {
			d.state = 11
		}
		prob = lzmaRepLenCoder
	}

	// Length.
	b, ok = d.bit(data, prob)
	if !ok {
		return lzmaNeedMore
	}
	length := 0
	if b == 0 {
		probLen := prob + lzmaLenLow + posState
		length = 1
		for i := 0; i < lzmaLenLowBits; i++ {
			bitv, ok := d.bit(data, probLen+length)
			if !ok {
				return lzmaNeedMore
			}
			length = length<<1 | bitv
		}
		length -= 8
	} else {
		b, ok = d.bit(data, prob+8)
		if !ok {
			return lzmaNeedMore
		}
		if b == 0 {
			probLen := prob + lzmaLenLow + posState + 8
			length = 1
			for i := 0; i < lzmaLenLowBits; i++ {
				bitv, ok := d.bit(data, probLen+length)
				if !ok {
					return lzmaNeedMore
				}
				length = length<<1 | bitv
			}
		} else {
			probLen := prob + 2*(lzmaNumPosStates<<lzmaLenLowBits)
			length = 1
			for i := 0; i < lzmaLenHighBits; i++ {
				bitv, ok := d.bit(data, probLen+length)
				if !ok {
					return lzmaNeedMore
				}
				length = length<<1 | bitv
			}
			length -= 1 << lzmaLenHighBits
			length += 16
		}
	}

	if d.state >= lzmaNumStates {
		lenState := length
		if lenState > 3 {
			lenState = 3
		}
		probSlot := lzmaPosSlot + (lenState << 6)
		distance := uint32(1)
		for i := 0; i < 6; i++ {
			bitv, ok := d.bit(data, probSlot+int(distance))
			if !ok {
				return lzmaNeedMore
			}
			distance = distance<<1 | uint32(bitv)
		}
		distance -= 0x40
		if distance >= lzmaStartPosModel {
			posSlot := distance
			numDirectBits := (distance >> 1) - 1
			distance = 2 | (distance & 1)
			if posSlot < lzmaEndPosModel {
				distance <<= uint(numDirectBits)
				distance++
				m := uint32(1)
				for ; numDirectBits != 0; numDirectBits-- {
					bitv, ok := d.bit(data, lzmaSpecPos+int(distance))
					if !ok {
						return lzmaNeedMore
					}
					if bitv == 0 {
						distance += m
						m += m
					} else {
						m += m
						distance += m
					}
				}
				distance -= m
			} else {
				numDirectBits -= 4
				for ; numDirectBits != 0; numDirectBits-- {
					if d.range_ < lzmaNumTopValue {
						if d.pos >= len(data) {
							return lzmaNeedMore
						}
						d.range_ <<= 8
						d.code = d.code<<8 | uint32(data[d.pos])
						d.pos++
					}
					d.range_ >>= 1
					d.code -= d.range_
					t := uint32(0) - (d.code >> 31)
					distance = distance<<1 + (t + 1)
					d.code += d.range_ & t
				}
				distance <<= 4
				i := 1
				for _, m := range [3]int{1, 2, 4} {
					bitv, ok := d.bit(data, lzmaAlign+i)
					if !ok {
						return lzmaNeedMore
					}
					if bitv == 0 {
						i += m
					} else {
						i += m * 2
					}
				}
				bitv, ok := d.bit(data, lzmaAlign+i)
				if !ok {
					return lzmaNeedMore
				}
				if bitv == 0 {
					i -= 8
				}
				distance |= uint32(i)
				if distance == 0xFFFFFFFF {
					// End-of-payload marker: liblzma normalises the range
					// decoder once more and requires the code word to be
					// zero.
					if d.range_ < lzmaNumTopValue {
						if d.pos >= len(data) {
							return lzmaNeedMore
						}
						d.range_ <<= 8
						d.code = d.code<<8 | uint32(data[d.pos])
						d.pos++
					}
					if d.code != 0 {
						return lzmaDataError
					}
					return lzmaDone
				}
			}
		}
		if distance >= d.distanceLimit() {
			return lzmaDataError
		}
		d.reps[3] = d.reps[2]
		d.reps[2] = d.reps[1]
		d.reps[1] = d.reps[0]
		d.reps[0] = distance + 1
		if d.state < lzmaNumStates+lzmaNumLitStates {
			d.state = lzmaNumLitStates
		} else {
			d.state = lzmaNumLitStates + 3
		}
	}

	length += lzmaKMatchMinLen
	dist := int(d.reps[0])
	if dist <= 0 || dist > len(d.out) || uint32(dist-1) >= d.distanceLimit() {
		return lzmaDataError
	}
	for i := 0; i < length; i++ {
		d.out = append(d.out, d.out[len(d.out)-dist])
	}
	d.processedPos += uint32(length)
	return -1
}

// lzmaDecompressor is zipfile's LZMADecompressor wrapper around
// _lzma.LZMADecompressor(FORMAT_RAW, FILTER_LZMA1): it buffers the zip
// properties header, bounds each call's output like decompress(data,
// max_length), and tracks needs_input and eof.
type lzmaDecompressor struct {
	header  []byte
	inner   *lzma1Decoder
	stream  []byte
	emitted int
	done    bool
	eof     bool
	// needsInputState is _lzma's needs_input attribute after the last call.
	needsInputState bool
	failed          bool
}

func newLZMA() *lzmaDecompressor { return &lzmaDecompressor{needsInputState: true} }

func (l *lzmaDecompressor) decompress(data []byte, maxLength int) ([]byte, error) {
	if l.eof {
		return nil, exceptionf("EOFError", "Already at end of stream")
	}
	if l.failed {
		return nil, exceptionf("ValueError", "Decompressor is unusable after a previous error")
	}
	if l.inner == nil {
		l.header = append(l.header, data...)
		if len(l.header) <= 4 {
			return nil, nil
		}
		psize := int(uint16(l.header[2]) | uint16(l.header[3])<<8)
		if len(l.header) <= 4+psize {
			return nil, nil
		}
		opts, ok := parseLZMAProps(l.header[4 : 4+psize])
		if !ok {
			l.failed = true
			return nil, exceptionf("LZMAError", "Invalid or unsupported options")
		}
		l.inner = newLZMA1(opts)
		data = l.header[4+psize:]
		l.header = nil
	}
	l.stream = append(l.stream, data...)

	limit := -1
	if maxLength >= 0 {
		if maxLength <= maxN-l.emitted {
			limit = l.emitted + maxLength
		}
	}

	var out []byte
	status := -1
	for {
		if l.emitted < len(l.inner.out) {
			n := len(l.inner.out) - l.emitted
			if limit >= 0 && l.emitted+n > limit {
				n = limit - l.emitted
			}
			out = append(out, l.inner.out[l.emitted:l.emitted+n]...)
			l.emitted += n
			if limit >= 0 && l.emitted >= limit {
				status = lzmaOutputFull
				break
			}
		}
		if status == lzmaNeedMore || status == lzmaDone {
			break
		}
		status = l.inner.decode(l.stream, limit)
		if status == lzmaDataError {
			l.failed = true
			return nil, exceptionf("LZMAError", "Corrupt input data")
		}
	}

	if status == lzmaDone {
		l.done = true
		l.eof = l.emitted == len(l.inner.out)
	}
	l.needsInputState = status == lzmaNeedMore
	return out, nil
}

func (l *lzmaDecompressor) flush() ([]byte, error) { return nil, nil }

func (l *lzmaDecompressor) atEOF() bool { return l.eof }

func (l *lzmaDecompressor) needsInput() bool { return l.needsInputState }

func (l *lzmaDecompressor) unconsumedTail() []byte { return nil }
