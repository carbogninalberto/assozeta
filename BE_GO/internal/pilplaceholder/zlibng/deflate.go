// Package zlibng is a pure-Go port of the zlib-ng 2.3.3 deflate path that
// Pillow 13.0.0 selects when it writes PNG image data (ZipEncode.c):
//
//	deflateInit2(strm, Z_DEFAULT_COMPRESSION, Z_DEFLATED, 15, 9, Z_FILTERED)
//
// That is level 6, which zlib-ng maps to deflate_medium (good=8 lazy=16
// nice=128 chain=128), a 32 KiB window, memLevel 9 (32768 symbols per block)
// and a zlib wrapper. Z_FILTERED does not change zlib-ng's match search at
// this level; it only has to be below Z_HUFFMAN_ONLY.
//
// Unlike classic zlib, the bytes deflate_medium produces depend on how the
// caller splits its input and output across deflate() calls: the function
// keeps a one-match lookahead in local variables that is dropped whenever it
// returns. The port therefore keeps the z_stream calling convention (pending
// buffer, avail_in, avail_out) instead of an io.Writer, so a caller can replay
// the exact call sequence of the C program it mirrors.
//
// The code follows deflate.c, deflate_medium.c, match_tpl.h,
// insert_string_tpl.h and trees.c closely; names mirror the C sources so the
// two can be compared side by side. zlib-ng's hash, match search and block
// splitting are the same on every architecture and byte order (the
// architecture-specific files only vectorise them), so the stream does not
// depend on the platform.
package zlibng

import "hash/adler32"

// Flush values accepted by Deflate.
const (
	NoFlush = 0 // Z_NO_FLUSH
	Finish  = 4 // Z_FINISH
)

// Return codes of Deflate.
const (
	OK        = 0  // Z_OK
	StreamEnd = 1  // Z_STREAM_END
	StreamErr = -2 // Z_STREAM_ERROR
	BufErr    = -5 // Z_BUF_ERROR
)

const (
	wBits        = 15
	wSize        = 1 << wBits
	wMask        = wSize - 1
	windowSize   = 2 * wSize
	hashSize     = 65536
	hashMask     = hashSize - 1
	stdMinMatch  = 3
	stdMaxMatch  = 258
	wantMinMatch = 4
	minLookahead = stdMaxMatch + stdMinMatch + 1
	maxDist      = wSize - minLookahead
	winInit      = stdMaxMatch
	windowPad    = 2 * minLookahead
	litBufsize   = 1 << (9 + 6) // memLevel + 6
	symEnd       = litBufsize - 1

	// configuration_table[6]
	level          = 6
	goodMatch      = 8
	maxLazyMatch   = 16
	niceMatch      = 128
	maxChainLength = 128

	initState   = 1
	busyState   = 2
	finishState = 3
)

type blockState int

const (
	needMore blockState = iota
	blockDone
	finishStarted
	finishDone
)

// Deflater is one zlib-ng deflate stream. The zero value is not usable; call
// NewDeflater.
type Deflater struct {
	trees

	// z_stream
	in    []byte // next_in[:avail_in]
	out   []byte // next_out[:avail_out]
	adler uint32

	status    int
	wrap      int
	lastFlush int

	// The longest-match routines read up to 8 bytes at scan+offset and up to
	// 258 bytes past strstart; strstart <= windowSize-minLookahead keeps all
	// of that inside windowSize. The padding only keeps a read at a stale
	// chain entry in bounds, as zlib-ng's own window padding does.
	window    [windowSize + windowPad]byte
	prev      [wSize]uint16
	head      [hashSize]uint16
	highWater uint32

	blockStart  int
	strstart    uint32
	lookahead   uint32
	insert      uint32
	matchStart  uint32
	prevLength  uint32
	pendingBase []byte
}

// NewDeflater returns a stream initialised as Pillow's PNG encoder does.
func NewDeflater() *Deflater {
	s := &Deflater{}
	s.pendingBase = make([]byte, 0, litBufsize*4)
	s.pendingBuf = s.pendingBase
	s.status = initState
	s.wrap = 1
	s.adler = 1
	s.lastFlush = -2
	s.trInit()
	// lm_init: the zero value already has an empty hash table and counters.
	return s
}

// SetInput sets next_in and avail_in. The slice is read, never modified, and
// must stay unchanged until AvailIn reports zero.
func (s *Deflater) SetInput(p []byte) { s.in = p }

// AvailIn reports avail_in.
func (s *Deflater) AvailIn() int { return len(s.in) }

// Deflate mirrors deflate(strm, flush) with next_out = out and avail_out =
// len(out). It returns the number of bytes written to out and the zlib
// return code.
func (s *Deflater) Deflate(out []byte, flush int) (int, int) {
	s.out = out
	ret := s.deflate(flush)
	n := len(out) - len(s.out)
	s.out = nil
	return n, ret
}

func rank(f int) int {
	if f > 4 {
		return f*2 - 9
	}
	return f * 2
}

func (s *Deflater) deflate(flush int) int {
	if flush != NoFlush && flush != Finish {
		return StreamErr
	}
	if s.status == finishState && flush != Finish {
		return StreamErr
	}
	if len(s.out) == 0 {
		return BufErr
	}

	oldFlush := s.lastFlush
	s.lastFlush = flush

	// Flush as much pending output as possible.
	if len(s.pendingBuf) != 0 {
		s.flushPending()
		if len(s.out) == 0 {
			s.lastFlush = -1
			return OK
		}
	} else if len(s.in) == 0 && rank(flush) <= rank(oldFlush) && flush != Finish {
		return BufErr
	}

	// User must not provide more input after the first FINISH.
	if s.status == finishState && len(s.in) != 0 {
		return BufErr
	}

	// Write the zlib header.
	if s.status == initState {
		header := uint((8 + ((wBits - 8) << 4)) << 8)
		header |= 2 << 6 // level_flags for level 6
		header += 31 - (header % 31)
		s.putShortMSB(uint16(header))
		s.adler = 1
		s.status = busyState

		// Compression must start with an empty pending buffer.
		s.flushPending()
		if len(s.pendingBuf) != 0 {
			s.lastFlush = -1
			return OK
		}
	}

	// Start a new block or continue the current one.
	if len(s.in) != 0 || s.lookahead != 0 || (flush != NoFlush && s.status != finishState) {
		bstate := s.deflateMedium(flush)

		if bstate == finishStarted || bstate == finishDone {
			s.status = finishState
		}
		if bstate == needMore || bstate == finishStarted {
			if len(s.out) == 0 {
				s.lastFlush = -1 // avoid BUF_ERROR next call
			}
			return OK
		}
		// block_done is only reached with a flush value other than
		// Z_NO_FLUSH and Z_FINISH, which Deflate rejects.
	}

	if flush != Finish {
		return OK
	}

	// Write the trailer.
	if s.wrap == 1 {
		s.putUint32MSB(s.adler)
	}
	s.flushPending()
	if s.wrap > 0 {
		s.wrap = -s.wrap // write the trailer only once
	}
	if len(s.pendingBuf) == 0 {
		return StreamEnd
	}
	return OK
}

// flushPending mirrors flush_pending_inline.
func (s *Deflater) flushPending() {
	s.trFlushBits()
	n := copy(s.out, s.pendingBuf)
	if n == 0 {
		return
	}
	s.out = s.out[n:]
	s.pendingBuf = s.pendingBuf[n:]
	if len(s.pendingBuf) == 0 {
		s.pendingBuf = s.pendingBase
	}
}

// readBuf mirrors read_buf for a zlib-wrapped stream.
func (s *Deflater) readBuf(buf []byte) uint32 {
	n := copy(buf, s.in)
	if n == 0 {
		return 0
	}
	s.adler = adler32Update(s.adler, s.in[:n])
	s.in = s.in[n:]
	return uint32(n)
}

func adler32Update(adler uint32, p []byte) uint32 {
	if adler == 1 {
		return adler32.Checksum(p)
	}
	const mod = 65521
	a, b := adler&0xffff, adler>>16
	for len(p) > 0 {
		chunk := p
		if len(chunk) > 5552 {
			chunk = chunk[:5552]
		}
		p = p[len(chunk):]
		for _, c := range chunk {
			a += uint32(c)
			b += a
		}
		a %= mod
		b %= mod
	}
	return b<<16 | a
}

// fillWindow mirrors fill_window.
func (s *Deflater) fillWindow() {
	for {
		more := uint32(windowSize) - s.lookahead - s.strstart

		// If the window is almost full and there is insufficient lookahead,
		// move the upper half to the lower one to make room in the upper half.
		if s.strstart >= wSize+maxDist {
			copy(s.window[:wSize], s.window[wSize:windowSize])
			if s.matchStart >= wSize {
				s.matchStart -= wSize
			} else {
				s.matchStart = 0
				s.prevLength = 0
			}
			s.strstart -= wSize
			s.blockStart -= wSize
			if s.insert > s.strstart {
				s.insert = s.strstart
			}
			s.slideHash()
			more += wSize
		}
		if len(s.in) == 0 {
			break
		}

		at := s.strstart + s.lookahead
		n := s.readBuf(s.window[at : at+more])
		s.lookahead += n

		// Initialize the hash value now that we have some input.
		if s.lookahead+s.insert >= stdMinMatch {
			str := s.strstart - s.insert
			if str >= 1 {
				s.quickInsertString(str + 2 - stdMinMatch)
			}
			count := s.insert
			if s.lookahead == 1 {
				count--
			}
			if count > 0 {
				s.insertString(str, count)
				s.insert -= count
			}
		}
		if !(s.lookahead < minLookahead && len(s.in) != 0) {
			break
		}
	}

	// Zero the WIN_INIT bytes after the current data that were never written.
	if s.highWater < windowSize {
		curr := s.strstart + s.lookahead
		if s.highWater < curr {
			init := uint32(windowSize) - curr
			if init > winInit {
				init = winInit
			}
			clear(s.window[curr : curr+init])
			s.highWater = curr + init
		} else if s.highWater < curr+winInit {
			init := curr + winInit - s.highWater
			if init > windowSize-s.highWater {
				init = windowSize - s.highWater
			}
			clear(s.window[s.highWater : s.highWater+init])
			s.highWater += init
		}
	}
}

// slideHash mirrors slide_hash_c.
func (s *Deflater) slideHash() {
	for i, m := range s.head {
		if m >= wSize {
			s.head[i] = m - wSize
		} else {
			s.head[i] = 0
		}
	}
	for i, m := range s.prev {
		if m >= wSize {
			s.prev[i] = m - wSize
		} else {
			s.prev[i] = 0
		}
	}
}

// hashCalc mirrors HASH_CALC of insert_string.c over the four bytes at str,
// read as a little-endian integer on every platform.
func (s *Deflater) hashCalc(str uint32) uint32 {
	w := s.window[str : str+4 : str+4]
	val := uint32(w[0]) | uint32(w[1])<<8 | uint32(w[2])<<16 | uint32(w[3])<<24
	return ((val * 2654435761) >> 16) & hashMask
}

// quickInsertString mirrors quick_insert_string.
func (s *Deflater) quickInsertString(str uint32) uint16 {
	hm := s.hashCalc(str)
	head := s.head[hm]
	if uint32(head) != str {
		s.prev[str&wMask] = head
		s.head[hm] = uint16(str)
	}
	return head
}

// insertString mirrors insert_string. The position counter is a 16-bit Pos in
// the C source.
func (s *Deflater) insertString(str, count uint32) {
	idx := uint16(str)
	for p := str; p < str+count; p++ {
		hm := s.hashCalc(p)
		head := s.head[hm]
		if head != idx {
			s.prev[uint32(idx)&wMask] = head
			s.head[hm] = idx
		}
		idx++
	}
}

func (s *Deflater) cmp(a, b uint32, n uint32) bool {
	for i := uint32(0); i < n; i++ {
		if s.window[a+i] != s.window[b+i] {
			return false
		}
	}
	return true
}

// longestMatch mirrors longest_match_c (match_tpl.h without
// LONGEST_MATCH_SLOW) for level 6, where early_exit is false.
func (s *Deflater) longestMatch(curMatch uint16) uint32 {
	strstart := s.strstart
	scan := strstart
	lookahead := s.lookahead

	bestLen := s.prevLength
	if bestLen == 0 {
		bestLen = stdMinMatch - 1
	}

	// Read offset which only extends one byte past the best length.
	offset := bestLen - 1
	if bestLen >= 4 {
		offset -= 2
		if bestLen >= 8 {
			offset -= 4
		}
	}

	chainLength := uint32(maxChainLength)
	if bestLen >= goodMatch {
		chainLength >>= 2
	}

	var limit uint16
	if strstart > maxDist {
		limit = uint16(strstart - maxDist)
	}

	for {
		if uint32(curMatch) >= strstart {
			break
		}

		// Skip to the next match if the match length cannot increase or if
		// the match length is less than 2.
		var width uint32 = 4
		if bestLen < 4 {
			width = 2
		} else if bestLen >= 8 {
			width = 8
		}
		found := false
		for {
			cm := uint32(curMatch)
			if s.cmp(cm+offset, scan+offset, width) && s.cmp(cm, scan, width) {
				found = true
				break
			}
			chainLength--
			if chainLength == 0 {
				break
			}
			curMatch = s.prev[uint32(curMatch)&wMask]
			if curMatch <= limit {
				break
			}
		}
		if !found {
			return bestLen
		}

		// compare256(scan+2, match+2) + 2
		cm := uint32(curMatch)
		length := uint32(2)
		for length < 258 && s.window[scan+length] == s.window[cm+length] {
			length++
		}

		if length > bestLen {
			s.matchStart = cm

			// Do not look for better matches if the current match reaches or
			// exceeds the end of the input.
			if length >= lookahead {
				return lookahead
			}
			bestLen = length
			if bestLen >= niceMatch {
				return bestLen
			}

			offset = bestLen - 1
			if bestLen >= 4 {
				offset -= 2
				if bestLen >= 8 {
					offset -= 4
				}
			}
		}

		// GOTO_NEXT_CHAIN
		chainLength--
		if chainLength == 0 {
			return bestLen
		}
		curMatch = s.prev[uint32(curMatch)&wMask]
		if curMatch <= limit {
			return bestLen
		}
	}
	return bestLen
}

// flushBlockOnly mirrors FLUSH_BLOCK_ONLY.
func (s *Deflater) flushBlockOnly(last bool) {
	var buf []byte
	if s.blockStart >= 0 {
		buf = s.window[s.blockStart:s.strstart]
	}
	s.trFlushBlock(buf, s.blockStart >= 0, int(s.strstart)-s.blockStart, last)
	s.blockStart = int(s.strstart)
	s.flushPending()
}
