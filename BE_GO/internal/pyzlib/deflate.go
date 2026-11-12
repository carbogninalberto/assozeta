// Package pyzlib is a pure-Go port of zlib's raw deflate encoder at the
// settings CPython's zipfile uses for ZIP_DEFLATED members:
//
//	zlib.compressobj(zlib.Z_DEFAULT_COMPRESSION, zlib.DEFLATED, -15)
//
// That is level 6 (deflate_slow, good=8 lazy=16 nice=128 chain=128),
// memLevel 8, a 32 KiB window, no zlib header and the default strategy.
// The output is byte-for-byte identical to zlib 1.2.12 through 1.3.x fed the
// same data with Z_NO_FLUSH and finished with Z_FINISH, independent of how
// the input is split across Write calls.
//
// The code follows deflate.c and trees.c closely; names mirror the C sources
// so the two can be compared side by side.
package pyzlib

import (
	"bytes"
	"errors"
	"io"
)

const (
	wBits        = 15
	wSize        = 1 << wBits
	wMask        = wSize - 1
	windowSize   = 2 * wSize
	hashBits     = 8 + 7 // memLevel + 7
	hashSize     = 1 << hashBits
	hashMask     = hashSize - 1
	hashShift    = (hashBits + minMatch - 1) / minMatch
	minMatch     = 3
	maxMatch     = 258
	minLookahead = maxMatch + minMatch + 1
	maxDist      = wSize - minLookahead
	tooFar       = 4096
	litBufsize   = 1 << (8 + 6) // memLevel + 6
	symEnd       = litBufsize - 1

	// configuration_table[6]
	goodMatch      = 8
	maxLazyMatch   = 16
	niceMatchLimit = 128
	maxChainLength = 128

	outFlushSize = 64 << 10
)

var errClosed = errors.New("pyzlib: write to closed Writer")

// Writer compresses data written to it into a raw deflate stream identical
// to CPython's zlib at level 6, wbits -15.
type Writer struct {
	w      io.Writer
	err    error
	closed bool

	// Pending input not yet copied into the window. in[inPos:] is unread.
	in    []byte
	inPos int
	buf   []byte

	window [windowSize]byte
	prev   [wSize]uint16
	head   [hashSize]uint16

	insH           uint32
	strstart       int
	blockStart     int
	lookahead      int
	insert         int
	matchStart     int
	prevMatch      int
	matchLength    int
	prevLength     int
	matchAvailable bool

	trees
}

// Compress returns a Writer that writes the raw deflate stream to w.
// Close must be called to finish the stream; it does not close w.
func Compress(w io.Writer) *Writer {
	d := &Writer{w: w}
	d.matchLength = minMatch - 1
	d.prevLength = minMatch - 1
	d.trInit()
	return d
}

// Deflate returns the raw deflate encoding of data.
func Deflate(data []byte) []byte {
	var b bytes.Buffer
	d := Compress(&b)
	_, _ = d.Write(data)
	_ = d.Close()
	return b.Bytes()
}

// Write compresses p. Output is emitted as complete blocks become available.
func (d *Writer) Write(p []byte) (int, error) {
	if d.closed {
		return 0, errClosed
	}
	if d.err != nil {
		return 0, d.err
	}
	if len(p) == 0 {
		return 0, nil
	}
	if d.inPos == len(d.in) {
		// Borrow p for the duration of the call; keep only the leftover.
		d.in, d.inPos = p, 0
	} else {
		d.buf = append(d.buf[:0], d.in[d.inPos:]...)
		d.buf = append(d.buf, p...)
		d.in, d.inPos = d.buf, 0
	}
	d.deflateSlow(false)
	rest := d.in[d.inPos:]
	d.buf = append(d.buf[:0], rest...)
	d.in, d.inPos = d.buf, 0
	d.flushOut(true)
	if d.err != nil {
		return 0, d.err
	}
	return len(p), nil
}

// Close finishes the stream (Z_FINISH) and writes all remaining output.
func (d *Writer) Close() error {
	if d.closed {
		return d.err
	}
	d.closed = true
	if d.err != nil {
		return d.err
	}
	d.deflateSlow(true)
	d.in, d.buf = nil, nil
	d.flushOut(true)
	return d.err
}

func (d *Writer) flushOut(force bool) {
	if d.err != nil {
		d.pending = d.pending[:0]
		return
	}
	if len(d.pending) == 0 || (!force && len(d.pending) < outFlushSize) {
		return
	}
	_, d.err = d.w.Write(d.pending)
	d.pending = d.pending[:0]
}

func (d *Writer) insertString(str int) int {
	d.insH = ((d.insH << hashShift) ^ uint32(d.window[str+minMatch-1])) & hashMask
	h := int(d.head[d.insH])
	d.prev[str&wMask] = uint16(h)
	d.head[d.insH] = uint16(str)
	return h
}

func (d *Writer) slideHash() {
	for i, m := range d.head {
		if m >= wSize {
			d.head[i] = m - wSize
		} else {
			d.head[i] = 0
		}
	}
	for i, m := range d.prev {
		if m >= wSize {
			d.prev[i] = m - wSize
		} else {
			d.prev[i] = 0
		}
	}
}

// fillWindow mirrors zlib's fill_window. When not finishing it only reads
// once it can fill the whole free space, exactly as zlib would with all the
// input available at once; this makes output independent of Write sizes.
// The window starts zeroed, so zlib's high_water zeroing is a no-op here.
func (d *Writer) fillWindow(finish bool) {
	for {
		more := windowSize - d.lookahead - d.strstart
		if d.strstart >= wSize+maxDist {
			copy(d.window[:wSize-more], d.window[wSize:windowSize-more])
			d.matchStart -= wSize
			d.strstart -= wSize
			d.blockStart -= wSize
			if d.insert > d.strstart {
				d.insert = d.strstart
			}
			d.slideHash()
			more += wSize
		}
		avail := len(d.in) - d.inPos
		if avail == 0 || (!finish && avail < more) {
			break
		}
		n := min(avail, more)
		copy(d.window[d.strstart+d.lookahead:], d.in[d.inPos:d.inPos+n])
		d.inPos += n
		d.lookahead += n

		if d.lookahead+d.insert >= minMatch {
			str := d.strstart - d.insert
			d.insH = uint32(d.window[str])
			d.insH = ((d.insH << hashShift) ^ uint32(d.window[str+1])) & hashMask
			for d.insert > 0 {
				d.insH = ((d.insH << hashShift) ^ uint32(d.window[str+minMatch-1])) & hashMask
				d.prev[str&wMask] = d.head[d.insH]
				d.head[d.insH] = uint16(str)
				str++
				d.insert--
				if d.lookahead+d.insert < minMatch {
					break
				}
			}
		}
		if d.lookahead >= minLookahead || d.inPos == len(d.in) {
			break
		}
	}
}

// longestMatch mirrors zlib's longest_match (non-UNALIGNED_OK variant).
func (d *Writer) longestMatch(curMatch int) int {
	chainLength := maxChainLength
	win := d.window[:]
	scan := d.strstart
	bestLen := d.prevLength
	niceMatch := niceMatchLimit
	limit := 0
	if d.strstart > maxDist {
		limit = d.strstart - maxDist
	}
	scanEnd1 := win[scan+bestLen-1]
	scanEnd := win[scan+bestLen]
	if d.prevLength >= goodMatch {
		chainLength >>= 2
	}
	if niceMatch > d.lookahead {
		niceMatch = d.lookahead
	}
	s := win[scan : scan+maxMatch : scan+maxMatch]
	for {
		match := curMatch
		if win[match+bestLen] == scanEnd && win[match+bestLen-1] == scanEnd1 &&
			win[match] == win[scan] && win[match+1] == win[scan+1] {
			// scan[2] and match[2] are equal by the hash; compare from 3.
			m := win[match : match+maxMatch : match+maxMatch]
			l := 3
			for l < maxMatch && s[l] == m[l] {
				l++
			}
			if l > bestLen {
				d.matchStart = curMatch
				bestLen = l
				if l >= niceMatch {
					break
				}
				scanEnd1 = win[scan+bestLen-1]
				scanEnd = win[scan+bestLen]
			}
		}
		curMatch = int(d.prev[curMatch&wMask])
		if curMatch <= limit {
			break
		}
		chainLength--
		if chainLength == 0 {
			break
		}
	}
	if bestLen <= d.lookahead {
		return bestLen
	}
	return d.lookahead
}

func (d *Writer) flushBlock(last bool) {
	var buf []byte
	if d.blockStart >= 0 {
		buf = d.window[d.blockStart:d.strstart]
	}
	d.trFlushBlock(buf, d.blockStart >= 0, d.strstart-d.blockStart, last)
	d.blockStart = d.strstart
	d.flushOut(false)
}

// deflateSlow mirrors zlib's deflate_slow. With finish=false it stops when
// more input is needed (Z_NO_FLUSH); with finish=true it ends the stream.
func (d *Writer) deflateSlow(finish bool) {
	for {
		if d.lookahead < minLookahead {
			d.fillWindow(finish)
			if d.lookahead < minLookahead && !finish {
				return
			}
			if d.lookahead == 0 {
				break
			}
		}

		hashHead := 0
		if d.lookahead >= minMatch {
			hashHead = d.insertString(d.strstart)
		}

		d.prevLength, d.prevMatch = d.matchLength, d.matchStart
		d.matchLength = minMatch - 1

		if hashHead != 0 && d.prevLength < maxLazyMatch && d.strstart-hashHead <= maxDist {
			d.matchLength = d.longestMatch(hashHead)
			if d.matchLength <= 5 && d.matchLength == minMatch && d.strstart-d.matchStart > tooFar {
				d.matchLength = minMatch - 1
			}
		}

		if d.prevLength >= minMatch && d.matchLength <= d.prevLength {
			maxInsert := d.strstart + d.lookahead - minMatch
			bflush := d.tallyDist(d.strstart-1-d.prevMatch, d.prevLength-minMatch)
			d.lookahead -= d.prevLength - 1
			d.prevLength -= 2
			for {
				d.strstart++
				if d.strstart <= maxInsert {
					d.insertString(d.strstart)
				}
				d.prevLength--
				if d.prevLength == 0 {
					break
				}
			}
			d.matchAvailable = false
			d.matchLength = minMatch - 1
			d.strstart++
			if bflush {
				d.flushBlock(false)
			}
		} else if d.matchAvailable {
			if d.tallyLit(d.window[d.strstart-1]) {
				d.flushBlock(false)
			}
			d.strstart++
			d.lookahead--
		} else {
			d.matchAvailable = true
			d.strstart++
			d.lookahead--
		}
	}
	if d.matchAvailable {
		d.tallyLit(d.window[d.strstart-1])
		d.matchAvailable = false
	}
	d.insert = min(d.strstart, minMatch-1)
	d.flushBlock(true)
}
