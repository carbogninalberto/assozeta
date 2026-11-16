package pyzipfile

// This file is a port of zlib 1.2.12's inflate.c, inffast.c and inftrees.c
// for raw deflate streams (windowBits -15, wrap 0), which is what
// zlib.decompressobj(-15) uses in CPython 3.13. The state machine, its
// decisions about how much input must be consumed before each check and the
// error messages are kept as close to the C as the language allows, so a
// corrupt stream fails at the same point and with the same zlib message the
// reference Python reports.

const (
	zOK        = 0
	zStreamEnd = 1
	zDataError = -3
	zBufError  = -5
	zStreamErr = -2
)

// zlib messages (zlib 1.2.12 inflate.c).
const (
	msgInvalidBlockType       = "invalid block type"
	msgInvalidStoredLengths   = "invalid stored block lengths"
	msgTooManySymbols         = "too many length or distance symbols"
	msgInvalidCodeLengths     = "invalid code lengths set"
	msgInvalidBitLengthRepeat = "invalid bit length repeat"
	msgMissingEndOfBlock      = "invalid code -- missing end-of-block"
	msgInvalidLiterals        = "invalid literal/lengths set"
	msgInvalidDistances       = "invalid distances set"
	msgInvalidLiteralCode     = "invalid literal/length code"
	msgInvalidDistanceCode    = "invalid distance code"
	msgInvalidDistanceTooFar  = "invalid distance too far back"
)

type code struct {
	op   uint8
	bits uint8
	val  uint16
}

const (
	codesType = iota
	lensType
	distsType
)

const (
	mType = iota
	mTypedo
	mStored
	mCopy_
	mCopy
	mTable
	mLenlens
	mCodelens
	mLen_
	mLen
	mLenext
	mDist
	mDistent
	mMatch
	mLit
	mCheck
	mLength
	mDone
	mBad
	mMem
	mSync
)

const (
	enoughLens  = 852
	enoughDists = 592
	enough      = 1444
	maxBits     = 15
)

var codeLengthOrder = [19]int{16, 17, 18, 0, 8, 7, 9, 6, 10, 5, 11, 4, 12, 3, 13, 2, 14, 1, 15}

var (
	lbase = [31]uint16{3, 4, 5, 6, 7, 8, 9, 10, 11, 13, 15, 17, 19, 23, 27, 31,
		35, 43, 51, 59, 67, 83, 99, 115, 131, 163, 195, 227, 258, 0, 0}
	lext = [31]uint16{16, 16, 16, 16, 16, 16, 16, 16, 17, 17, 17, 17, 18, 18, 18, 18,
		19, 19, 19, 19, 20, 20, 20, 20, 21, 21, 21, 21, 16, 199, 202}
	dbase = [32]uint16{1, 2, 3, 4, 5, 7, 9, 13, 17, 25, 33, 49, 65, 97, 129, 193,
		257, 385, 513, 769, 1025, 1537, 2049, 3073, 4097, 6145,
		8193, 12289, 16385, 24577, 0, 0}
	dext = [32]uint16{16, 16, 16, 16, 17, 17, 18, 18, 19, 19, 20, 20, 21, 21, 22, 22,
		23, 23, 24, 24, 25, 25, 26, 26, 27, 27,
		28, 28, 29, 29, 64, 64}
)

type inflater struct {
	mode int

	last uint32

	wsize  uint32
	whave  uint32
	wnext  uint32
	window []byte

	hold uint64
	bits uint32

	length uint32
	offset uint32
	extra  uint32
	was    uint32

	lencode  []code
	distcode []code
	lenbits  uint32
	distbits uint32

	ncode uint32
	nlen  uint32
	ndist uint32
	have  uint32

	next  int
	lens  [320]uint16
	work  [288]uint16
	codes [enough]code

	sane bool
	back int

	msg string
}

func newInflater() *inflater {
	s := &inflater{}
	s.reset()
	return s
}

func (s *inflater) reset() {
	s.mode = mTypedo
	s.last = 0
	s.wsize, s.whave, s.wnext = 0, 0, 0
	s.window = nil
	s.hold, s.bits = 0, 0
	s.length, s.offset, s.extra, s.was = 0, 0, 0, 0
	s.lencode, s.distcode = nil, nil
	s.lenbits, s.distbits = 0, 0
	s.ncode, s.nlen, s.ndist, s.have = 0, 0, 0, 0
	s.next = 0
	s.sane = true
	s.back = -1
	s.msg = ""
}

// fixedtables builds the fixed code tables once (inffixed.h in zlib).
var fixedLen, fixedDist []code

func init() {
	lens := make([]uint16, 288)
	for i := 0; i < 144; i++ {
		lens[i] = 8
	}
	for i := 144; i < 256; i++ {
		lens[i] = 9
	}
	for i := 256; i < 280; i++ {
		lens[i] = 7
	}
	for i := 280; i < 288; i++ {
		lens[i] = 8
	}
	codes := make([]code, enough)
	work := make([]uint16, 288)
	nxt, bits, ret := inflateTable(lensType, lens, 288, codes, 0, 9, work)
	if ret != 0 || bits != 9 {
		panic("pyzipfile: fixed length table")
	}
	fixedLen = append([]code(nil), codes[:nxt]...)

	for i := range lens {
		lens[i] = 0
	}
	for i := 0; i < 32; i++ {
		lens[i] = 5
	}
	nxt, bits, ret = inflateTable(distsType, lens, 32, codes, 0, 5, work)
	if ret != 0 || bits != 5 {
		panic("pyzipfile: fixed distance table")
	}
	fixedDist = append([]code(nil), codes[:nxt]...)
}

// inflateTable is inftrees.c's inflate_table.
func inflateTable(typ int, lens []uint16, n int, codes []code, start int, bits uint32, work []uint16) (int, uint32, int) {
	var count [maxBits + 1]uint16
	for i := 0; i < n; i++ {
		if int(lens[i]) > maxBits {
			return start, bits, -1
		}
		count[lens[i]]++
	}

	root := bits
	max := maxBits
	for ; max >= 1; max-- {
		if count[max] != 0 {
			break
		}
	}
	if int(root) > max {
		root = uint32(max)
	}
	if max == 0 {
		here := code{op: 64, bits: 1, val: 0}
		codes[start] = here
		codes[start+1] = here
		return start + 2, 1, 0
	}
	min := 1
	for ; min < max; min++ {
		if count[min] != 0 {
			break
		}
	}
	if int(root) < min {
		root = uint32(min)
	}

	left := 1
	for length := 1; length <= maxBits; length++ {
		left <<= 1
		left -= int(count[length])
		if left < 0 {
			return start, bits, -1
		}
	}
	if left > 0 && (typ == codesType || max != 1) {
		return start, bits, -1
	}

	var offs [maxBits + 1]uint16
	offs[1] = 0
	for length := 1; length < maxBits; length++ {
		offs[length+1] = offs[length] + count[length]
	}
	for sym := 0; sym < n; sym++ {
		if lens[sym] != 0 {
			work[offs[lens[sym]]] = uint16(sym)
			offs[lens[sym]]++
		}
	}

	var base, extra []uint16
	match := 0
	switch typ {
	case codesType:
		match = 20
	case lensType:
		base, extra, match = lbase[:], lext[:], 257
	default:
		base, extra, match = dbase[:], dext[:], 0
	}

	huff := 0
	sym := 0
	length := min
	next := start
	curr := int(root)
	drop := 0
	low := ^uint32(0)
	used := 1 << root
	mask := uint32(used - 1)

	if (typ == lensType && used > enoughLens) || (typ == distsType && used > enoughDists) {
		return start, bits, 1
	}

	for {
		here := code{bits: uint8(length - drop)}
		if int(work[sym])+1 < match {
			here.op = 0
			here.val = work[sym]
		} else if int(work[sym]) >= match {
			here.op = uint8(extra[int(work[sym])-match])
			here.val = base[int(work[sym])-match]
		} else {
			here.op = 32 + 64
			here.val = 0
		}

		incr := 1 << uint(length-drop)
		fill := 1 << uint(curr)
		minFill := fill
		for {
			fill -= incr
			codes[next+(huff>>uint(drop))+fill] = here
			if fill == 0 {
				break
			}
		}

		incr = 1 << uint(length-1)
		for huff&incr != 0 {
			incr >>= 1
		}
		if incr != 0 {
			huff &= incr - 1
			huff += incr
		} else {
			huff = 0
		}

		sym++
		count[length]--
		if count[length] == 0 {
			if length == max {
				break
			}
			length = int(lens[work[sym]])
		}

		if length > int(root) && (uint32(huff)&mask) != low {
			if drop == 0 {
				drop = int(root)
			}
			next += minFill
			curr = length - drop
			left := 1 << uint(curr)
			for curr+drop < max {
				left -= int(count[curr+drop])
				if left <= 0 {
					break
				}
				curr++
				left <<= 1
			}
			used += 1 << uint(curr)
			if (typ == lensType && used > enoughLens) || (typ == distsType && used > enoughDists) {
				return start, bits, 1
			}
			low = uint32(huff) & mask
			codes[start+int(low)].op = uint8(curr)
			codes[start+int(low)].bits = uint8(root)
			codes[start+int(low)].val = uint16(next - start)
		}
	}

	if huff != 0 {
		codes[next+huff] = code{op: 64, bits: uint8(length - drop), val: 0}
	}
	return start + int(used), root, 0
}

// inflate is inflate(): it consumes input and writes output, returning the
// bytes used, the bytes produced and a zlib return code.
func (s *inflater) inflate(input, output []byte, finish bool) (int, int, int) {
	next, have := 0, len(input)
	put, left := 0, len(output)
	hold, bits := s.hold, s.bits
	in, out := have, left
	put0 := put
	ret := zOK

	if s.mode == mType {
		s.mode = mTypedo
	}

	for {
		switch s.mode {
		case mType:
			s.mode = mTypedo
			fallthrough
		case mTypedo:
			if s.last != 0 {
				hold >>= bits & 7
				bits -= bits & 7
				s.mode = mCheck
				break
			}
			for bits < 3 {
				if have == 0 {
					goto infLeave
				}
				have--
				hold += uint64(input[next]) << bits
				next++
				bits += 8
			}
			s.last = uint32(hold & 1)
			hold >>= 1
			bits--
			switch hold & 3 {
			case 0:
				s.mode = mStored
			case 1:
				s.lencode, s.lenbits = fixedLen, 9
				s.distcode, s.distbits = fixedDist, 5
				s.mode = mLen_
			case 2:
				s.mode = mTable
			case 3:
				s.msg = msgInvalidBlockType
				s.mode = mBad
			}
			hold >>= 2
			bits -= 2
			break

		case mStored:
			hold >>= bits & 7
			bits -= bits & 7
			for bits < 32 {
				if have == 0 {
					goto infLeave
				}
				have--
				hold += uint64(input[next]) << bits
				next++
				bits += 8
			}
			if uint32(hold&0xffff) != (uint32(hold>>16) ^ 0xffff) {
				s.msg = msgInvalidStoredLengths
				s.mode = mBad
				break
			}
			s.length = uint32(hold) & 0xffff
			hold, bits = 0, 0
			s.mode = mCopy_
			fallthrough
		case mCopy_:
			s.mode = mCopy
			fallthrough
		case mCopy:
			copyN := s.length
			if copyN != 0 {
				if copyN > uint32(have) {
					copyN = uint32(have)
				}
				if copyN > uint32(left) {
					copyN = uint32(left)
				}
				if copyN == 0 {
					goto infLeave
				}
				copy(output[put:put+int(copyN)], input[next:next+int(copyN)])
				have -= int(copyN)
				next += int(copyN)
				left -= int(copyN)
				put += int(copyN)
				s.length -= copyN
				break
			}
			s.mode = mType
			break

		case mTable:
			for bits < 14 {
				if have == 0 {
					goto infLeave
				}
				have--
				hold += uint64(input[next]) << bits
				next++
				bits += 8
			}
			s.nlen = uint32(hold&0x1f) + 257
			hold >>= 5
			bits -= 5
			s.ndist = uint32(hold&0x1f) + 1
			hold >>= 5
			bits -= 5
			s.ncode = uint32(hold&0xf) + 4
			hold >>= 4
			bits -= 4
			if s.nlen > 286 || s.ndist > 30 {
				s.msg = msgTooManySymbols
				s.mode = mBad
				break
			}
			s.have = 0
			s.mode = mLenlens
			fallthrough
		case mLenlens:
			for s.have < s.ncode {
				for bits < 3 {
					if have == 0 {
						goto infLeave
					}
					have--
					hold += uint64(input[next]) << bits
					next++
					bits += 8
				}
				s.lens[codeLengthOrder[s.have]] = uint16(hold & 7)
				s.have++
				hold >>= 3
				bits -= 3
			}
			for s.have < 19 {
				s.lens[codeLengthOrder[s.have]] = 0
				s.have++
			}
			s.next = 0
			s.lencode = s.codes[:]
			s.lenbits = 7
			nxt, newBits, rc := inflateTable(codesType, s.lens[:], 19, s.codes[:], s.next, s.lenbits, s.work[:])
			if rc != 0 {
				s.msg = msgInvalidCodeLengths
				s.mode = mBad
				break
			}
			s.next, s.lenbits = nxt, newBits
			s.have = 0
			s.mode = mCodelens
			fallthrough
		case mCodelens:
			{
			codelens:
				for s.have < s.nlen+s.ndist {
					var here code
					for {
						here = s.lencode[bitsMask(hold, s.lenbits)]
						if uint32(here.bits) <= bits {
							break
						}
						if have == 0 {
							goto infLeave
						}
						have--
						hold += uint64(input[next]) << bits
						next++
						bits += 8
					}
					if here.val < 16 {
						hold >>= here.bits
						bits -= uint32(here.bits)
						s.lens[s.have] = here.val
						s.have++
					} else {
						var lengthValue uint32
						var copyN uint32
						if here.val == 16 {
							for bits < uint32(here.bits)+2 {
								if have == 0 {
									goto infLeave
								}
								have--
								hold += uint64(input[next]) << bits
								next++
								bits += 8
							}
							hold >>= here.bits
							bits -= uint32(here.bits)
							if s.have == 0 {
								s.msg = msgInvalidBitLengthRepeat
								s.mode = mBad
								break codelens
							}
							lengthValue = uint32(s.lens[s.have-1])
							copyN = 3 + uint32(hold&3)
							hold >>= 2
							bits -= 2
						} else if here.val == 17 {
							for bits < uint32(here.bits)+3 {
								if have == 0 {
									goto infLeave
								}
								have--
								hold += uint64(input[next]) << bits
								next++
								bits += 8
							}
							hold >>= here.bits
							bits -= uint32(here.bits)
							lengthValue = 0
							copyN = 3 + uint32(hold&7)
							hold >>= 3
							bits -= 3
						} else {
							for bits < uint32(here.bits)+7 {
								if have == 0 {
									goto infLeave
								}
								have--
								hold += uint64(input[next]) << bits
								next++
								bits += 8
							}
							hold >>= here.bits
							bits -= uint32(here.bits)
							lengthValue = 0
							copyN = 11 + uint32(hold&0x7f)
							hold >>= 7
							bits -= 7
						}
						if s.have+copyN > s.nlen+s.ndist {
							s.msg = msgInvalidBitLengthRepeat
							s.mode = mBad
							break codelens
						}
						for ; copyN > 0; copyN-- {
							s.lens[s.have] = uint16(lengthValue)
							s.have++
						}
					}
				}
			}
			if s.mode == mBad {
				break
			}
			if s.lens[256] == 0 {
				s.msg = msgMissingEndOfBlock
				s.mode = mBad
				break
			}
			s.next = 0
			s.lencode = s.codes[:]
			s.lenbits = 9
			nxt, newBits, rc := inflateTable(lensType, s.lens[:], int(s.nlen), s.codes[:], s.next, s.lenbits, s.work[:])
			if rc != 0 {
				s.msg = msgInvalidLiterals
				s.mode = mBad
				break
			}
			s.next, s.lenbits = nxt, newBits
			s.distcode = s.codes[s.next:]
			s.distbits = 6
			nxt, newBits, rc = inflateTable(distsType, s.lens[s.nlen:], int(s.ndist), s.codes[:], s.next, s.distbits, s.work[:])
			if rc != 0 {
				s.msg = msgInvalidDistances
				s.mode = mBad
				break
			}
			s.next, s.distbits = nxt, newBits
			s.mode = mLen_
			fallthrough
		case mLen_:
			s.mode = mLen
			fallthrough
		case mLen:
			if have >= 6 && left >= 258 {
				s.hold, s.bits = hold, bits
				next, have, put, left = s.inflateFast(input, next, have, output, put, left, put0)
				hold, bits = s.hold, s.bits
				if s.mode == mType {
					s.back = -1
				}
				break
			}
			s.back = 0
			var here code
			for {
				here = s.lencode[bitsMask(hold, s.lenbits)]
				if uint32(here.bits) <= bits {
					break
				}
				if have == 0 {
					goto infLeave
				}
				have--
				hold += uint64(input[next]) << bits
				next++
				bits += 8
			}
			if here.op != 0 && here.op&0xf0 == 0 {
				lastm := here
				for {
					here = s.lencode[int(lastm.val)+int((hold>>lastm.bits)&((1<<lastm.op)-1))]
					if uint32(lastm.bits)+uint32(here.bits) <= bits {
						break
					}
					if have == 0 {
						goto infLeave
					}
					have--
					hold += uint64(input[next]) << bits
					next++
					bits += 8
				}
				hold >>= lastm.bits
				bits -= uint32(lastm.bits)
				s.back += int(lastm.bits)
			}
			hold >>= here.bits
			bits -= uint32(here.bits)
			s.back += int(here.bits)
			s.length = uint32(here.val)
			if here.op == 0 {
				s.mode = mLit
				break
			}
			if here.op&32 != 0 {
				s.back = -1
				s.mode = mType
				break
			}
			if here.op&64 != 0 {
				s.msg = msgInvalidLiteralCode
				s.mode = mBad
				break
			}
			s.extra = uint32(here.op) & 15
			s.mode = mLenext
			fallthrough
		case mLenext:
			if s.extra != 0 {
				for bits < s.extra {
					if have == 0 {
						goto infLeave
					}
					have--
					hold += uint64(input[next]) << bits
					next++
					bits += 8
				}
				s.length += uint32(hold) & (1<<s.extra - 1)
				hold >>= s.extra
				bits -= s.extra
				s.back += int(s.extra)
			}
			s.was = s.length
			s.mode = mDist
			fallthrough
		case mDist:
			var here code
			for {
				here = s.distcode[bitsMask(hold, s.distbits)]
				if uint32(here.bits) <= bits {
					break
				}
				if have == 0 {
					goto infLeave
				}
				have--
				hold += uint64(input[next]) << bits
				next++
				bits += 8
			}
			if here.op&0xf0 == 0 {
				lastm := here
				for {
					here = s.distcode[int(lastm.val)+int((hold>>lastm.bits)&((1<<lastm.op)-1))]
					if uint32(lastm.bits)+uint32(here.bits) <= bits {
						break
					}
					if have == 0 {
						goto infLeave
					}
					have--
					hold += uint64(input[next]) << bits
					next++
					bits += 8
				}
				hold >>= lastm.bits
				bits -= uint32(lastm.bits)
				s.back += int(lastm.bits)
			}
			hold >>= here.bits
			bits -= uint32(here.bits)
			s.back += int(here.bits)
			if here.op&64 != 0 {
				s.msg = msgInvalidDistanceCode
				s.mode = mBad
				break
			}
			s.offset = uint32(here.val)
			s.extra = uint32(here.op) & 15
			s.mode = mDistent
			fallthrough
		case mDistent:
			if s.extra != 0 {
				for bits < s.extra {
					if have == 0 {
						goto infLeave
					}
					have--
					hold += uint64(input[next]) << bits
					next++
					bits += 8
				}
				s.offset += uint32(hold) & (1<<s.extra - 1)
				hold >>= s.extra
				bits -= s.extra
				s.back += int(s.extra)
			}
			s.mode = mMatch
			fallthrough
		case mMatch:
			if left == 0 {
				goto infLeave
			}
			copyN := uint32(put - put0)
			from := 0
			useWindow := false
			if s.offset > copyN {
				copyN = s.offset - copyN
				if copyN > s.whave {
					s.msg = msgInvalidDistanceTooFar
					s.mode = mBad
					break
				}
				if copyN > s.wnext {
					copyN -= s.wnext
					from = int(s.wsize - copyN)
				} else {
					from = int(s.wnext - copyN)
				}
				useWindow = true
				if copyN > s.length {
					copyN = s.length
				}
			} else {
				from = put - int(s.offset)
				copyN = s.length
			}
			if copyN > uint32(left) {
				copyN = uint32(left)
			}
			left -= int(copyN)
			s.length -= copyN
			for ; copyN > 0; copyN-- {
				if useWindow {
					output[put] = s.window[from]
				} else {
					output[put] = output[from]
				}
				put++
				from++
			}
			if s.length == 0 {
				s.mode = mLen
			}
			break

		case mLit:
			if left == 0 {
				goto infLeave
			}
			output[put] = byte(s.length)
			put++
			left--
			s.mode = mLen
			break

		case mCheck:
			s.mode = mDone
			fallthrough
		case mDone:
			ret = zStreamEnd
			goto infLeave
		case mBad:
			ret = zDataError
			goto infLeave
		default:
			return in, out, zStreamErr
		}
	}

infLeave:
	s.hold, s.bits = hold, bits
	if s.wsize != 0 || (out != left && s.mode < mBad && (s.mode < mCheck || !finish)) {
		s.updateWindow(output[:put], out-left)
	}
	used := in - have
	produced := out - left
	if ((used == 0 && produced == 0) || finish) && ret == zOK {
		ret = zBufError
	}
	return used, produced, ret
}

func bitsMask(hold uint64, bits uint32) int {
	return int(hold & ((uint64(1) << bits) - 1))
}

// inflateFast is inffast.c's inflate_fast. Output and input are the whole
// buffers; in/put are the current positions and have/left the remaining
// counts. put0 is inflate()'s initial output position (C's "beg"). It returns
// the updated positions and remaining counts.
func (s *inflater) inflateFast(input []byte, in, have int, output []byte, put, left, put0 int) (int, int, int, int) {
	last := in + (have - 5)
	end := put + (left - 257)
	wsize := s.wsize
	whave := s.whave
	wnext := s.wnext
	window := s.window
	hold := s.hold
	bits := s.bits
	lcode := s.lencode
	dcode := s.distcode
	lmask := uint64(1)<<s.lenbits - 1
	dmask := uint64(1)<<s.distbits - 1

	var here code
	var op, length, dist uint32

fast:
	for {
		if bits < 15 {
			hold += uint64(input[in]) << bits
			in++
			bits += 8
			hold += uint64(input[in]) << bits
			in++
			bits += 8
		}
		here = lcode[hold&lmask]
	dolen:
		op = uint32(here.bits)
		hold >>= here.bits
		bits -= uint32(here.bits)
		op = uint32(here.op)
		if op == 0 {
			output[put] = byte(here.val)
			put++
		} else if op&16 != 0 {
			length = uint32(here.val)
			op &= 15
			if op != 0 {
				if bits < op {
					hold += uint64(input[in]) << bits
					in++
					bits += 8
				}
				length += uint32(hold) & (1<<op - 1)
				hold >>= op
				bits -= op
			}
			if bits < 15 {
				hold += uint64(input[in]) << bits
				in++
				bits += 8
				hold += uint64(input[in]) << bits
				in++
				bits += 8
			}
			here = dcode[hold&dmask]
		dodist:
			op = uint32(here.bits)
			hold >>= here.bits
			bits -= uint32(here.bits)
			op = uint32(here.op)
			if op&16 != 0 {
				dist = uint32(here.val)
				op &= 15
				if bits < op {
					hold += uint64(input[in]) << bits
					in++
					bits += 8
					if bits < op {
						hold += uint64(input[in]) << bits
						in++
						bits += 8
					}
				}
				dist += uint32(hold) & (1<<op - 1)
				hold >>= op
				bits -= op
				op = uint32(put - put0)
				if dist > op {
					op = dist - op
					if op > whave {
						s.msg = msgInvalidDistanceTooFar
						s.mode = mBad
						break fast
					}
					from := 0
					// useWindow records whether from still indexes the
					// window, as in inffast.c's pointer arithmetic.
					useWindow := true
					if wnext == 0 {
						from = int(wsize - op)
						if op < length {
							length -= op
							for ; op > 0; op-- {
								output[put] = window[from]
								put++
								from++
							}
							from = put - int(dist)
							useWindow = false
						}
					} else if wnext < op {
						from = int(wsize + wnext - op)
						op -= wnext
						if op < length {
							length -= op
							for ; op > 0; op-- {
								output[put] = window[from]
								put++
								from++
							}
							from = 0
							if wnext < length {
								op = wnext
								length -= op
								for ; op > 0; op-- {
									output[put] = window[from]
									put++
									from++
								}
								from = put - int(dist)
								useWindow = false
							}
						}
					} else {
						from = int(wnext - op)
						if op < length {
							length -= op
							for ; op > 0; op-- {
								output[put] = window[from]
								put++
								from++
							}
							from = put - int(dist)
							useWindow = false
						}
					}
					for length > 2 {
						if useWindow {
							output[put] = window[from]
						} else {
							output[put] = output[from]
						}
						put++
						from++
						if useWindow {
							output[put] = window[from]
						} else {
							output[put] = output[from]
						}
						put++
						from++
						if useWindow {
							output[put] = window[from]
						} else {
							output[put] = output[from]
						}
						put++
						from++
						length -= 3
					}
					if length != 0 {
						if useWindow {
							output[put] = window[from]
						} else {
							output[put] = output[from]
						}
						put++
						from++
						if length > 1 {
							if useWindow {
								output[put] = window[from]
							} else {
								output[put] = output[from]
							}
							put++
							from++
						}
					}
				} else {
					from := put - int(dist)
					for {
						output[put] = output[from]
						put++
						from++
						output[put] = output[from]
						put++
						from++
						output[put] = output[from]
						put++
						from++
						length -= 3
						if length <= 2 {
							break
						}
					}
					if length != 0 {
						output[put] = output[from]
						put++
						from++
						if length > 1 {
							output[put] = output[from]
							put++
							from++
						}
					}
				}
			} else if op&64 == 0 {
				here = dcode[int(here.val)+int(hold&(1<<op-1))]
				goto dodist
			} else {
				s.msg = msgInvalidDistanceCode
				s.mode = mBad
				break fast
			}
		} else if op&64 == 0 {
			here = lcode[int(here.val)+int(hold&(1<<op-1))]
			goto dolen
		} else if op&32 != 0 {
			s.mode = mType
			break fast
		} else {
			s.msg = msgInvalidLiteralCode
			s.mode = mBad
			break fast
		}
		if in < last && put < end {
			continue
		}
		break
	}

	// Return unused bytes: on entry bits < 8, so in won't go too far back.
	skip := bits >> 3
	in -= int(skip)
	bits -= skip << 3
	hold &= (1 << bits) - 1

	s.hold, s.bits = hold, bits
	// The remaining counts are measured from the end of the buffers.
	return in, len(input) - in, put, len(output) - put
}

func (s *inflater) updateWindow(written []byte, copyN int) {
	if s.window == nil {
		s.window = make([]byte, 1<<15)
	}
	if s.wsize == 0 {
		s.wsize = 1 << 15
		s.wnext = 0
		s.whave = 0
	}
	end := written[len(written)-copyN:]
	if copyN >= int(s.wsize) {
		copy(s.window, end[len(end)-int(s.wsize):])
		s.wnext = 0
		s.whave = s.wsize
		return
	}
	dist := int(s.wsize - s.wnext)
	if dist > copyN {
		dist = copyN
	}
	copy(s.window[s.wnext:], end[len(end)-copyN:len(end)-copyN+dist])
	copyN -= dist
	if copyN != 0 {
		copy(s.window, end[len(end)-copyN:])
		s.wnext = uint32(copyN)
		s.whave = s.wsize
	} else {
		s.wnext += uint32(dist)
		if s.wnext == s.wsize {
			s.wnext = 0
		}
		if s.whave < s.wsize {
			s.whave += uint32(dist)
		}
	}
}

// zlibInflate is zlib.decompressobj(-15) as zipfile uses it: decompress(data,
// max_length) with unconsumed_tail, eof and flush().
type zlibInflate struct {
	s          *inflater
	unconsumed []byte
	eof        bool
}

func newZlibInflate() *zlibInflate {
	return &zlibInflate{s: newInflater()}
}

func (z *zlibInflate) decompress(data []byte, maxLength int) ([]byte, error) {
	if maxLength == 0 {
		maxLength = -1
	}
	var out []byte
	pos := 0
	for {
		remaining := -1
		if maxLength >= 0 {
			remaining = maxLength - len(out)
			if remaining == 0 {
				break
			}
		}
		chunk := remaining
		if chunk < 0 || chunk > 1<<20 {
			chunk = 1 << 20
		}
		buf := make([]byte, chunk)
		used, produced, ret := z.s.inflate(data[pos:], buf, false)
		out = append(out, buf[:produced]...)
		pos += used
		if ret == zStreamEnd {
			z.eof = true
			z.unconsumed = nil
			return out, nil
		}
		if ret == zDataError {
			return nil, zlibDataError(z.s.msg)
		}
		if ret != zOK && ret != zBufError {
			return nil, zlibDataError(z.s.msg)
		}
		if used == 0 && produced == 0 {
			break
		}
		if maxLength >= 0 && len(out) >= maxLength {
			break
		}
		if pos >= len(data) {
			break
		}
	}
	if pos < len(data) {
		z.unconsumed = append([]byte(nil), data[pos:]...)
	} else {
		z.unconsumed = nil
	}
	return out, nil
}

func (z *zlibInflate) flush() ([]byte, error) {
	var out []byte
	// flush() first drains unconsumed_tail with Z_NO_FLUSH.
	for len(z.unconsumed) > 0 {
		buf := make([]byte, 1<<20)
		used, produced, ret := z.s.inflate(z.unconsumed, buf, false)
		out = append(out, buf[:produced]...)
		z.unconsumed = z.unconsumed[used:]
		if ret == zStreamEnd {
			z.eof = true
			z.unconsumed = nil
			return out, nil
		}
		if ret == zDataError {
			return nil, zlibDataError(z.s.msg)
		}
		if used == 0 && produced == 0 {
			break
		}
	}
	for {
		buf := make([]byte, 1<<20)
		used, produced, ret := z.s.inflate(nil, buf, true)
		out = append(out, buf[:produced]...)
		if ret == zStreamEnd {
			z.eof = true
			z.unconsumed = nil
			return out, nil
		}
		if ret == zDataError {
			return nil, zlibDataError(z.s.msg)
		}
		if used == 0 && produced == 0 {
			return out, nil
		}
	}
}

func (z *zlibInflate) atEOF() bool { return z.eof }

func (z *zlibInflate) needsInput() bool { return len(z.unconsumed) == 0 }

func (z *zlibInflate) unconsumedTail() []byte { return z.unconsumed }

func zlibDataError(msg string) *Exception {
	if msg == "" {
		msg = "invalid input data"
	}
	return exceptionf("zlib.error", "Error -3 while decompressing data: %s", msg)
}
