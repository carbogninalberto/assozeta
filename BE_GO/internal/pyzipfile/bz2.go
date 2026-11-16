package pyzipfile

// This file is a port of libbzip2 1.0.8's decompressor (decompress.c, the
// output stage of bzlib.c and huffman.c) as used by CPython's _bz2 module:
// BZ2Decompressor with small=0, verbosity=0. The incremental state machine
// keeps every loop counter that libbzip2 saves between calls, so output
// chunks, needs_input and the point where corrupt data is reported match the
// reference Python.

const (
	bzMaxAlphaSize = 258
	bzNGroups      = 6
	bzGSize        = 50
	bzMaxSelectors = 2 + (900000 / bzGSize)
	bzMaxCodeLen   = 23
	bzRunA         = 0
	bzRunB         = 1
	bzMTFASize     = 4096
	bzMTFLSize     = 16
)

// BZ2_rNums from randtable.c.
var bzRNums = [512]int32{
	619, 720, 127, 481, 931, 816, 813, 233, 566, 247,
	985, 724, 205, 454, 863, 491, 741, 242, 949, 214,
	733, 859, 335, 708, 621, 574, 73, 654, 730, 472,
	419, 436, 278, 496, 867, 210, 399, 680, 480, 51,
	878, 465, 811, 169, 869, 675, 611, 697, 867, 561,
	862, 687, 507, 283, 482, 129, 807, 591, 733, 623,
	150, 238, 59, 379, 684, 877, 625, 169, 643, 105,
	170, 607, 520, 932, 727, 476, 693, 425, 174, 647,
	73, 122, 335, 530, 442, 853, 695, 249, 445, 515,
	909, 545, 703, 919, 874, 474, 882, 500, 594, 612,
	641, 801, 220, 162, 819, 984, 589, 513, 495, 799,
	161, 604, 958, 533, 221, 400, 386, 867, 600, 782,
	382, 596, 414, 171, 516, 375, 682, 485, 911, 276,
	98, 553, 163, 354, 666, 933, 424, 341, 533, 870,
	227, 730, 475, 186, 263, 647, 537, 686, 600, 224,
	469, 68, 770, 919, 190, 373, 294, 822, 808, 206,
	184, 943, 795, 384, 383, 461, 404, 758, 839, 887,
	715, 67, 618, 276, 204, 918, 873, 777, 604, 560,
	951, 160, 578, 722, 79, 804, 96, 409, 713, 940,
	652, 934, 970, 447, 318, 353, 859, 672, 112, 785,
	645, 863, 803, 350, 139, 93, 354, 99, 820, 908,
	609, 772, 154, 274, 580, 184, 79, 626, 630, 742,
	653, 282, 762, 623, 680, 81, 927, 626, 789, 125,
	411, 521, 938, 300, 821, 78, 343, 175, 128, 250,
	170, 774, 972, 275, 999, 639, 495, 78, 352, 126,
	857, 956, 358, 619, 580, 124, 737, 594, 701, 612,
	669, 112, 134, 694, 363, 992, 809, 743, 168, 974,
	944, 375, 748, 52, 600, 747, 642, 182, 862, 81,
	344, 805, 988, 739, 511, 655, 814, 334, 249, 515,
	897, 955, 664, 981, 649, 113, 974, 459, 893, 228,
	433, 837, 553, 268, 926, 240, 102, 654, 459, 51,
	686, 754, 806, 760, 493, 403, 415, 394, 687, 700,
	946, 670, 656, 610, 738, 392, 760, 799, 887, 653,
	978, 321, 576, 617, 626, 502, 894, 679, 243, 440,
	680, 879, 194, 572, 640, 724, 926, 56, 204, 700,
	707, 151, 457, 449, 797, 195, 791, 558, 945, 679,
	297, 59, 87, 824, 713, 663, 412, 693, 342, 606,
	134, 108, 571, 364, 631, 212, 174, 643, 304, 329,
	343, 97, 430, 751, 497, 314, 983, 374, 822, 928,
	140, 206, 73, 263, 980, 736, 876, 478, 430, 305,
	170, 514, 364, 692, 829, 82, 855, 953, 676, 246,
	369, 970, 294, 750, 807, 827, 150, 790, 288, 923,
	804, 378, 215, 828, 592, 281, 565, 555, 710, 82,
	896, 831, 547, 261, 524, 462, 293, 465, 502, 56,
	661, 821, 976, 991, 658, 869, 905, 758, 745, 193,
	768, 550, 608, 933, 378, 286, 215, 979, 792, 961,
	61, 688, 793, 644, 986, 403, 106, 366, 905, 644,
	372, 567, 466, 434, 645, 210, 389, 550, 919, 135,
	780, 773, 635, 389, 707, 100, 626, 958, 165, 504,
	920, 176, 193, 713, 857, 265, 203, 50, 668, 108,
	645, 990, 626, 197, 510, 357, 358, 850, 858, 364,
	936, 638,
}

var bzCRCTable [256]uint32

func init() {
	for i := 0; i < 256; i++ {
		crc := uint32(i) << 24
		for j := 0; j < 8; j++ {
			if crc&0x80000000 != 0 {
				crc = crc<<1 ^ 0x04C11DB7
			} else {
				crc <<= 1
			}
		}
		bzCRCTable[i] = crc
	}
}

const (
	bzPhMagic = iota
	bzPhBlockMagic
	bzPhBlockCRC
	bzPhBlockRand
	bzPhOrigPtr
	bzPhMap16
	bzPhMap256
	bzPhSelectors
	bzPhSelectorList
	bzPhCoding
	bzPhTables
	bzPhMTF
	bzPhBlockSetup
	bzPhOutput
	bzPhEndMagic
	bzPhEndCRC
	bzPhDone
	bzPhFailed
)

type bzip2Decompressor struct {
	input  []byte
	pos    int
	bsBuff uint64
	bsLive int

	phase  int
	eof    bool
	failed bool

	blockSize100k   int
	blockRandomised bool
	origPtr         int

	storedBlockCRC        uint32
	calculatedBlockCRC    uint32
	storedCombinedCRC     uint32
	calculatedCombinedCRC uint32

	inUse16     [16]bool
	inUse       [256]bool
	seqToUnseq  [256]byte
	nInUse      int
	alphaSize   int
	nGroups     int
	nSelectors  int
	selectorMtf [bzMaxSelectors]byte
	selector    [bzMaxSelectors]byte
	length      [bzNGroups][bzMaxAlphaSize]int32
	limit       [bzNGroups][bzMaxCodeLen]int32
	base        [bzNGroups][bzMaxCodeLen]int32
	perm        [bzNGroups][bzMaxAlphaSize]int32
	minLens     [bzNGroups]int32

	mtfa      [bzMTFASize]byte
	mtfbase   [256 / bzMTFLSize]int32
	unzftab   [256]int32
	cftab     [257]int32
	tt        []uint32
	nblock    int
	nblockMAX int

	tPos        uint32
	nblockUsed  int
	stateOutLen int
	stateOutCh  byte
	k0          byte
	rNToGo      int32
	rTPos       int32

	hdrIndex int
	magic    [6]byte
	i, j, t  int
	groupNo  int
	groupPos int
	curr     int

	mtfSymbolStarted bool
	mtfInitialRead   bool
	mtfNeedExtBit    bool
	zn               int32
	zvec             int32
	es               int
	runN             int
	inRun            bool
	runNeedSym       bool
	nextSym          int32
	haveSym          bool
	codingHaveCurr   bool
	codingNeedSecond bool
}

func newBzip2() *bzip2Decompressor {
	return &bzip2Decompressor{phase: bzPhMagic}
}

func (d *bzip2Decompressor) available() int { return len(d.input) - d.pos }

func (d *bzip2Decompressor) appendInput(data []byte) {
	if len(data) == 0 {
		return
	}
	if d.pos >= len(d.input) {
		d.input = append(d.input[:0], data...)
		d.pos = 0
		return
	}
	d.input = append(d.input[:d.pos], data...)
	d.pos = 0
}

// getBits is GET_BITS: it consumes n bits, adding input bytes as needed. It
// returns false (leaving the bit buffer untouched) when no input is
// available.
func (d *bzip2Decompressor) getBits(n int) (uint32, bool) {
	for d.bsLive < n {
		if d.available() == 0 {
			return 0, false
		}
		d.bsBuff = d.bsBuff<<8 | uint64(d.input[d.pos])
		d.pos++
		d.bsLive += 8
	}
	d.bsLive -= n
	return uint32((d.bsBuff >> uint(d.bsLive)) & (1<<uint(n) - 1)), true
}

func (d *bzip2Decompressor) dataError() {
	d.failed = true
	d.phase = bzPhFailed
}

func (d *bzip2Decompressor) updateCRC(c byte) {
	d.calculatedBlockCRC = d.calculatedBlockCRC<<8 ^ bzCRCTable[byte(d.calculatedBlockCRC>>24)^c]
}

func (d *bzip2Decompressor) getFast() (byte, bool) {
	if d.tPos >= uint32(100000*d.blockSize100k) {
		return 0, true
	}
	d.tPos = d.tt[d.tPos]
	c := byte(d.tPos & 0xff)
	d.tPos >>= 8
	return c, false
}

// createDecodeTables is BZ2_hbCreateDecodeTables.
func (d *bzip2Decompressor) createDecodeTables(g, minLen, maxLen, alphaSize int) {
	pp := 0
	for i := minLen; i <= maxLen; i++ {
		for j := 0; j < alphaSize; j++ {
			if int(d.length[g][j]) == i {
				d.perm[g][pp] = int32(j)
				pp++
			}
		}
	}
	for i := range d.base[g] {
		d.base[g][i] = 0
	}
	for i := 0; i < alphaSize; i++ {
		d.base[g][d.length[g][i]+1]++
	}
	for i := 1; i < bzMaxCodeLen; i++ {
		d.base[g][i] += d.base[g][i-1]
	}
	for i := range d.limit[g] {
		d.limit[g][i] = 0
	}
	vec := int32(0)
	for i := minLen; i <= maxLen; i++ {
		vec += d.base[g][i+1] - d.base[g][i]
		d.limit[g][i] = vec - 1
		vec <<= 1
	}
	for i := minLen + 1; i <= maxLen; i++ {
		d.base[g][i] = ((d.limit[g][i-1] + 1) << 1) - d.base[g][i]
	}
}

// getMTFVal is GET_MTF_VAL, resumable across input exhaustion.
func (d *bzip2Decompressor) getMTFVal() (int32, bool) {
	for {
		if !d.mtfSymbolStarted {
			if d.groupPos == 0 {
				d.groupNo++
				if d.groupNo >= d.nSelectors {
					d.dataError()
					return 0, false
				}
				d.groupPos = bzGSize
				gSel := int(d.selector[d.groupNo])
				d.i = gSel // gSel is remembered in i while the symbol decodes
				d.zn = d.minLens[gSel]
			} else {
				d.i = int(d.selector[d.groupNo])
			}
			d.groupPos--
			d.zn = d.minLens[d.i]
			d.mtfSymbolStarted = true
			d.mtfInitialRead = false
			d.mtfNeedExtBit = false
		}
		g := d.i
		if !d.mtfInitialRead {
			v, ok := d.getBits(int(d.zn))
			if !ok {
				return 0, false
			}
			d.zvec = int32(v)
			d.mtfInitialRead = true
		}
		for {
			if d.mtfNeedExtBit {
				v, ok := d.getBits(1)
				if !ok {
					return 0, false
				}
				d.zvec = d.zvec<<1 | int32(v)
				d.mtfNeedExtBit = false
			}
			if d.zn > 20 {
				d.dataError()
				return 0, false
			}
			if d.zvec <= d.limit[g][d.zn] {
				break
			}
			d.zn++
			d.mtfNeedExtBit = true
		}
		index := d.zvec - d.base[g][d.zn]
		if index < 0 || index >= bzMaxAlphaSize {
			d.dataError()
			return 0, false
		}
		lval := d.perm[g][index]
		d.mtfSymbolStarted = false
		return lval, true
	}
}

func (d *bzip2Decompressor) decompress(data []byte, maxLength int) ([]byte, error) {
	if d.eof {
		return nil, exceptionf("EOFError", "End of stream already reached")
	}
	if d.failed {
		return nil, exceptionf("ValueError", "Decompressor is unusable after a previous error")
	}
	d.appendInput(data)
	out := make([]byte, 0, 4096)
	for d.phase != bzPhDone && d.phase != bzPhFailed {
		if maxLength >= 0 && len(out) >= maxLength {
			break
		}
		if !d.step(&out, maxLength) {
			break
		}
	}
	if d.failed {
		return nil, exceptionf("OSError", "Invalid data stream")
	}
	if d.phase == bzPhDone {
		d.eof = true
	}
	return out, nil
}

// step runs phases until it needs input (returns false) or produces output.
func (d *bzip2Decompressor) step(out *[]byte, maxLength int) bool {
	switch d.phase {
	case bzPhMagic:
		for d.hdrIndex < 4 {
			v, ok := d.getBits(8)
			if !ok {
				return false
			}
			switch d.hdrIndex {
			case 0:
				if v != 'B' {
					d.dataError()
					return false
				}
			case 1:
				if v != 'Z' {
					d.dataError()
					return false
				}
			case 2:
				if v != 'h' {
					d.dataError()
					return false
				}
			case 3:
				if v < '1' || v > '9' {
					d.dataError()
					return false
				}
				d.blockSize100k = int(v - '0')
			}
			d.hdrIndex++
		}
		d.hdrIndex = 0
		d.calculatedCombinedCRC = 0
		d.phase = bzPhBlockMagic
		d.magic = [6]byte{}
		return true

	case bzPhBlockMagic, bzPhEndMagic:
		for d.hdrIndex < 6 {
			v, ok := d.getBits(8)
			if !ok {
				return false
			}
			if d.hdrIndex == 0 {
				if d.phase == bzPhBlockMagic {
					if v == 0x17 {
						d.phase = bzPhEndMagic
						d.hdrIndex++
						d.magic = [6]byte{0x17, 0x72, 0x45, 0x38, 0x50, 0x90}
						return true
					}
					d.magic = [6]byte{0x31, 0x41, 0x59, 0x26, 0x53, 0x59}
					if v != 0x31 {
						d.dataError()
						return false
					}
					d.hdrIndex++
					continue
				}
			}
			if d.magic[d.hdrIndex] != byte(v) {
				d.dataError()
				return false
			}
			d.hdrIndex++
		}
		if d.phase == bzPhEndMagic {
			d.hdrIndex = 0
			d.phase = bzPhEndCRC
			return true
		}
		d.hdrIndex = 0
		d.storedBlockCRC = 0
		d.phase = bzPhBlockCRC
		return true

	case bzPhBlockCRC:
		for d.hdrIndex < 4 {
			v, ok := d.getBits(8)
			if !ok {
				return false
			}
			d.storedBlockCRC = d.storedBlockCRC<<8 | v
			d.hdrIndex++
		}
		d.hdrIndex = 0
		d.phase = bzPhBlockRand
		return true

	case bzPhBlockRand:
		v, ok := d.getBits(1)
		if !ok {
			return false
		}
		d.blockRandomised = v == 1
		d.phase = bzPhOrigPtr
		d.hdrIndex = 0
		return true

	case bzPhOrigPtr:
		for d.hdrIndex < 3 {
			v, ok := d.getBits(8)
			if !ok {
				return false
			}
			d.origPtr = d.origPtr<<8 | int(v)
			d.hdrIndex++
		}
		d.hdrIndex = 0
		if d.origPtr < 0 || d.origPtr > 10+100000*d.blockSize100k {
			d.dataError()
			return false
		}
		d.phase = bzPhMap16
		return true

	case bzPhMap16:
		for d.hdrIndex < 16 {
			v, ok := d.getBits(1)
			if !ok {
				return false
			}
			d.inUse16[d.hdrIndex] = v == 1
			d.hdrIndex++
		}
		for i := range d.inUse {
			d.inUse[i] = false
		}
		d.i, d.j = 0, 0
		d.phase = bzPhMap256
		return true

	case bzPhMap256:
		for d.i < 16 {
			if d.inUse16[d.i] {
				for d.j < 16 {
					v, ok := d.getBits(1)
					if !ok {
						return false
					}
					if v == 1 {
						d.inUse[d.i*16+d.j] = true
					}
					d.j++
				}
			}
			d.i++
			d.j = 0
		}
		d.nInUse = 0
		for i := 0; i < 256; i++ {
			if d.inUse[i] {
				d.seqToUnseq[d.nInUse] = byte(i)
				d.nInUse++
			}
		}
		if d.nInUse == 0 {
			d.dataError()
			return false
		}
		d.alphaSize = d.nInUse + 2
		d.hdrIndex = 0
		d.phase = bzPhSelectors
		return true

	case bzPhSelectors:
		if d.hdrIndex == 0 {
			v, ok := d.getBits(3)
			if !ok {
				return false
			}
			d.nGroups = int(v)
			if d.nGroups < 2 || d.nGroups > bzNGroups {
				d.dataError()
				return false
			}
			d.hdrIndex = 1
		}
		if d.hdrIndex == 1 {
			v, ok := d.getBits(15)
			if !ok {
				return false
			}
			d.nSelectors = int(v)
			if d.nSelectors < 1 {
				d.dataError()
				return false
			}
			d.hdrIndex = 0
			d.i, d.j = 0, 0
			d.phase = bzPhSelectorList
		}
		return true

	case bzPhSelectorList:
		for d.i < d.nSelectors {
			v, ok := d.getBits(1)
			if !ok {
				return false
			}
			if v == 0 {
				if d.i < bzMaxSelectors {
					d.selectorMtf[d.i] = byte(d.j)
				}
				d.i++
				d.j = 0
				continue
			}
			d.j++
			if d.j >= d.nGroups {
				d.dataError()
				return false
			}
		}
		if d.nSelectors > bzMaxSelectors {
			d.nSelectors = bzMaxSelectors
		}
		var pos [bzNGroups]byte
		for v := 0; v < d.nGroups; v++ {
			pos[v] = byte(v)
		}
		for i := 0; i < d.nSelectors; i++ {
			v := d.selectorMtf[i]
			tmp := pos[v]
			for v > 0 {
				pos[v] = pos[v-1]
				v--
			}
			pos[0] = tmp
			d.selector[i] = tmp
		}
		d.t, d.i = 0, 0
		d.codingHaveCurr = false
		d.phase = bzPhCoding
		return true

	case bzPhCoding:
		for d.t < d.nGroups {
			if !d.codingHaveCurr {
				v, ok := d.getBits(5)
				if !ok {
					return false
				}
				d.curr = int(v)
				d.codingHaveCurr = true
				d.i = 0
			}
			for d.i < d.alphaSize {
				if d.curr < 1 || d.curr > 20 {
					d.dataError()
					return false
				}
				if d.codingNeedSecond {
					v, ok := d.getBits(1)
					if !ok {
						return false
					}
					if v == 0 {
						d.curr++
					} else {
						d.curr--
					}
					d.codingNeedSecond = false
					continue
				}
				v, ok := d.getBits(1)
				if !ok {
					return false
				}
				if v == 0 {
					d.length[d.t][d.i] = int32(d.curr)
					d.i++
					continue
				}
				d.codingNeedSecond = true
			}
			d.t++
			d.codingHaveCurr = false
		}
		d.phase = bzPhTables
		return true

	case bzPhTables:
		for t := 0; t < d.nGroups; t++ {
			minLen, maxLen := int32(32), int32(0)
			for i := 0; i < d.alphaSize; i++ {
				if d.length[t][i] > maxLen {
					maxLen = d.length[t][i]
				}
				if d.length[t][i] < minLen {
					minLen = d.length[t][i]
				}
			}
			d.createDecodeTables(t, int(minLen), int(maxLen), d.alphaSize)
			d.minLens[t] = minLen
		}
		d.nblockMAX = 100000 * d.blockSize100k
		if cap(d.tt) < d.nblockMAX {
			d.tt = make([]uint32, d.nblockMAX)
		} else {
			d.tt = d.tt[:d.nblockMAX]
		}
		d.nblock = 0
		for i := range d.unzftab {
			d.unzftab[i] = 0
		}
		// MTF init
		kk := bzMTFASize - 1
		for ii := 256/bzMTFLSize - 1; ii >= 0; ii-- {
			for jj := bzMTFLSize - 1; jj >= 0; jj-- {
				d.mtfa[kk] = byte(ii*bzMTFLSize + jj)
				kk--
			}
			d.mtfbase[ii] = int32(kk + 1)
		}
		d.groupNo = -1
		d.groupPos = 0
		d.inRun = false
		d.haveSym = false
		d.phase = bzPhMTF
		return true

	case bzPhMTF:
		for {
			if d.inRun {
				for {
					if d.runNeedSym {
						sym, ok := d.getMTFVal()
						if !ok {
							return false
						}
						d.nextSym = sym
						d.runNeedSym = false
					}
					if d.nextSym != bzRunA && d.nextSym != bzRunB {
						break
					}
					if d.runN >= 2*1024*1024 {
						d.dataError()
						return false
					}
					if d.nextSym == bzRunA {
						d.es += d.runN
					} else {
						d.es += 2 * d.runN
					}
					d.runN *= 2
					d.runNeedSym = true
				}
				d.inRun = false
				d.es++
				uc := d.seqToUnseq[d.mtfa[d.mtfbase[0]]]
				d.unzftab[uc] += int32(d.es)
				for e := d.es; e > 0; e-- {
					if d.nblock >= d.nblockMAX {
						d.dataError()
						return false
					}
					d.tt[d.nblock] = uint32(uc)
					d.nblock++
				}
			} else {
				sym, ok := d.getMTFVal()
				if !ok {
					return false
				}
				d.nextSym = sym
			}
			sym := d.nextSym
			if sym == int32(d.nInUse+1) {
				d.phase = bzPhBlockSetup
				return true
			}
			if sym == bzRunA || sym == bzRunB {
				d.es = -1
				d.runN = 1
				d.inRun = true
				d.runNeedSym = false
				continue
			}
			if d.nblock >= d.nblockMAX {
				d.dataError()
				return false
			}
			nn := int32(sym - 1)
			var uc byte
			if nn < bzMTFLSize {
				pp := d.mtfbase[0]
				uc = d.mtfa[pp+nn]
				for nn > 3 {
					z := pp + nn
					d.mtfa[z] = d.mtfa[z-1]
					d.mtfa[z-1] = d.mtfa[z-2]
					d.mtfa[z-2] = d.mtfa[z-3]
					d.mtfa[z-3] = d.mtfa[z-4]
					nn -= 4
				}
				for nn > 0 {
					d.mtfa[pp+nn] = d.mtfa[pp+nn-1]
					nn--
				}
				d.mtfa[pp] = uc
			} else {
				lno := nn / bzMTFLSize
				off := nn % bzMTFLSize
				pp := d.mtfbase[lno] + off
				uc = d.mtfa[pp]
				for pp > d.mtfbase[lno] {
					d.mtfa[pp] = d.mtfa[pp-1]
					pp--
				}
				d.mtfbase[lno]++
				for lno > 0 {
					d.mtfbase[lno]--
					d.mtfa[d.mtfbase[lno]] = d.mtfa[d.mtfbase[lno-1]+bzMTFLSize-1]
					lno--
				}
				d.mtfbase[0]--
				d.mtfa[d.mtfbase[0]] = uc
				if d.mtfbase[0] == 0 {
					kk := bzMTFASize - 1
					for ii := 256/bzMTFLSize - 1; ii >= 0; ii-- {
						for jj := bzMTFLSize - 1; jj >= 0; jj-- {
							d.mtfa[kk] = d.mtfa[d.mtfbase[ii]+int32(jj)]
							kk--
						}
						d.mtfbase[ii] = int32(kk + 1)
					}
				}
			}
			d.unzftab[d.seqToUnseq[uc]]++
			d.tt[d.nblock] = uint32(d.seqToUnseq[uc])
			d.nblock++
		}

	case bzPhBlockSetup:
		if d.origPtr < 0 || d.origPtr >= d.nblock {
			d.dataError()
			return false
		}
		for i := 0; i <= 255; i++ {
			if d.unzftab[i] < 0 || d.unzftab[i] > int32(d.nblock) {
				d.dataError()
				return false
			}
		}
		d.cftab[0] = 0
		for i := 1; i <= 256; i++ {
			d.cftab[i] = d.unzftab[i-1]
		}
		for i := 1; i <= 256; i++ {
			d.cftab[i] += d.cftab[i-1]
		}
		for i := 0; i <= 256; i++ {
			if d.cftab[i] < 0 || d.cftab[i] > int32(d.nblock) {
				d.dataError()
				return false
			}
		}
		for i := 1; i <= 256; i++ {
			if d.cftab[i-1] > d.cftab[i] {
				d.dataError()
				return false
			}
		}
		d.stateOutLen = 0
		d.stateOutCh = 0
		d.calculatedBlockCRC = 0xFFFFFFFF
		for i := 0; i < d.nblock; i++ {
			uc := byte(d.tt[i])
			d.tt[d.cftab[uc]] |= uint32(i) << 8
			d.cftab[uc]++
		}
		d.tPos = d.tt[d.origPtr] >> 8
		d.nblockUsed = 0
		if d.blockRandomised {
			d.rNToGo = 0
			d.rTPos = 0
		}
		c, corrupt := d.getFast()
		if corrupt {
			d.dataError()
			return false
		}
		if d.blockRandomised {
			d.randUpdateMask()
			c ^= d.randMask()
		}
		d.k0 = c
		d.nblockUsed++
		d.phase = bzPhOutput
		return true

	case bzPhOutput:
		for {
			if d.blockRandomised {
				if !d.outputRandomised(out, maxLength) {
					return false
				}
				if d.phase != bzPhOutput {
					return true
				}
				if maxLength >= 0 && len(*out) >= maxLength {
					return true
				}
				continue
			}
			if d.stateOutLen > 0 {
				for d.stateOutLen > 1 {
					if maxLength >= 0 && len(*out) >= maxLength {
						return true
					}
					*out = append(*out, d.stateOutCh)
					d.updateCRC(d.stateOutCh)
					d.stateOutLen--
				}
				if maxLength >= 0 && len(*out) >= maxLength {
					return true
				}
				*out = append(*out, d.stateOutCh)
				d.updateCRC(d.stateOutCh)
				d.stateOutLen = 0
			}
			if d.nblockUsed > d.nblock+1 {
				d.dataError()
				return false
			}
			if d.nblockUsed == d.nblock+1 {
				d.stateOutLen = 0
				if !d.finishBlock() {
					return false
				}
				return true
			}
			d.stateOutCh = d.k0
			c, corrupt := d.getFast()
			d.nblockUsed++
			if corrupt {
				d.dataError()
				return false
			}
			if c != d.k0 {
				d.k0 = c
				d.stateOutLen = 1
				continue
			}
			if d.nblockUsed == d.nblock+1 {
				d.stateOutLen = 1
				continue
			}
			d.stateOutLen = 2
			c, corrupt = d.getFast()
			d.nblockUsed++
			if corrupt {
				d.dataError()
				return false
			}
			if d.nblockUsed == d.nblock+1 {
				continue
			}
			if c != d.k0 {
				d.k0 = c
				continue
			}
			d.stateOutLen = 3
			c, corrupt = d.getFast()
			d.nblockUsed++
			if corrupt {
				d.dataError()
				return false
			}
			if d.nblockUsed == d.nblock+1 {
				continue
			}
			if c != d.k0 {
				d.k0 = c
				continue
			}
			c, corrupt = d.getFast()
			d.nblockUsed++
			if corrupt {
				d.dataError()
				return false
			}
			d.stateOutLen = int(c) + 4
			c, corrupt = d.getFast()
			d.nblockUsed++
			if corrupt {
				d.dataError()
				return false
			}
			d.k0 = c
		}

	case bzPhEndCRC:
		for d.hdrIndex < 4 {
			v, ok := d.getBits(8)
			if !ok {
				return false
			}
			d.storedCombinedCRC = d.storedCombinedCRC<<8 | v
			d.hdrIndex++
		}
		d.hdrIndex = 0
		if d.calculatedCombinedCRC != d.storedCombinedCRC {
			d.dataError()
			return false
		}
		d.phase = bzPhDone
		return true
	}
	return false
}

func (d *bzip2Decompressor) finishBlock() bool {
	d.calculatedBlockCRC = ^d.calculatedBlockCRC
	if d.calculatedBlockCRC != d.storedBlockCRC {
		d.dataError()
		return false
	}
	d.calculatedCombinedCRC = d.calculatedCombinedCRC<<1 | d.calculatedCombinedCRC>>31
	d.calculatedCombinedCRC ^= d.calculatedBlockCRC
	d.phase = bzPhBlockMagic
	d.hdrIndex = 0
	return true
}

func (d *bzip2Decompressor) randMask() byte {
	if d.rNToGo == 1 {
		return 1
	}
	return 0
}

func (d *bzip2Decompressor) randUpdateMask() {
	if d.rNToGo == 0 {
		d.rNToGo = bzRNums[d.rTPos]
		d.rTPos++
		if d.rTPos == 512 {
			d.rTPos = 0
		}
	}
	d.rNToGo--
}

func (d *bzip2Decompressor) outputRandomised(out *[]byte, maxLength int) bool {
	for {
		// finish existing run
		for {
			if maxLength >= 0 && len(*out) >= maxLength {
				return true
			}
			if d.stateOutLen == 0 {
				break
			}
			*out = append(*out, d.stateOutCh)
			d.updateCRC(d.stateOutCh)
			d.stateOutLen--
		}
		if d.nblockUsed == d.nblock+1 {
			d.stateOutLen = 0
			return d.finishBlock()
		}
		if d.nblockUsed > d.nblock+1 {
			d.dataError()
			return false
		}
		d.stateOutLen = 1
		d.stateOutCh = d.k0
		c, corrupt := d.getFast()
		d.nblockUsed++
		if corrupt {
			d.dataError()
			return false
		}
		d.randUpdateMask()
		c ^= d.randMask()
		if d.nblockUsed == d.nblock+1 {
			continue
		}
		if c != d.k0 {
			d.k0 = c
			continue
		}
		d.stateOutLen = 2
		c, corrupt = d.getFast()
		d.nblockUsed++
		if corrupt {
			d.dataError()
			return false
		}
		d.randUpdateMask()
		c ^= d.randMask()
		if d.nblockUsed == d.nblock+1 {
			continue
		}
		if c != d.k0 {
			d.k0 = c
			continue
		}
		d.stateOutLen = 3
		c, corrupt = d.getFast()
		d.nblockUsed++
		if corrupt {
			d.dataError()
			return false
		}
		d.randUpdateMask()
		c ^= d.randMask()
		if d.nblockUsed == d.nblock+1 {
			continue
		}
		if c != d.k0 {
			d.k0 = c
			continue
		}
		c, corrupt = d.getFast()
		d.nblockUsed++
		if corrupt {
			d.dataError()
			return false
		}
		d.randUpdateMask()
		c ^= d.randMask()
		d.stateOutLen = int(c) + 4
		c, corrupt = d.getFast()
		d.nblockUsed++
		if corrupt {
			d.dataError()
			return false
		}
		d.randUpdateMask()
		c ^= d.randMask()
		d.k0 = c
	}
}

func (d *bzip2Decompressor) flush() ([]byte, error) { return nil, nil }

func (d *bzip2Decompressor) atEOF() bool { return d.eof }

func (d *bzip2Decompressor) needsInput() bool { return d.available() == 0 }

func (d *bzip2Decompressor) unconsumedTail() []byte { return nil }
