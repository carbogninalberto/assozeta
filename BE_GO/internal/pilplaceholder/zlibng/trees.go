package zlibng

// Port of zlib-ng's trees.c and trees_emit.h: Huffman tree construction and
// block emission. The tree construction is the classic zlib algorithm; the
// bit buffer is zlib-ng's 64-bit one, kept exact because the number of bytes
// sitting in the pending buffer decides when a deflate call runs out of
// output space.

const (
	maxBits    = 15
	maxBLBits  = 7
	lengthCode = 29
	literals   = 256
	lCodes     = literals + 1 + lengthCode
	dCodes     = 30
	blCodes    = 19
	heapSize   = 2*lCodes + 1
	endBlock   = 256
	rep3_6     = 16
	repz3_10   = 17
	repz11_138 = 18

	storedBlock = 0
	staticTrees = 1
	dynTrees    = 2
)

var extraLbits = [lengthCode]int{0, 0, 0, 0, 0, 0, 0, 0, 1, 1, 1, 1, 2, 2, 2, 2, 3, 3, 3, 3, 4, 4, 4, 4, 5, 5, 5, 5, 0}

var extraDbits = [dCodes]int{0, 0, 0, 0, 1, 1, 2, 2, 3, 3, 4, 4, 5, 5, 6, 6, 7, 7, 8, 8, 9, 9, 10, 10, 11, 11, 12, 12, 13, 13}

var extraBlbits = [blCodes]int{0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 2, 3, 7}

var blOrder = [blCodes]int{16, 17, 18, 0, 8, 7, 9, 6, 10, 5, 11, 4, 12, 3, 13, 2, 14, 1, 15}

// ctData mirrors zlib's ct_data unions: fc is Freq or Code, dl is Dad or Len.
type ctData struct {
	fc uint16
	dl uint16
}

type staticTreeDesc struct {
	staticTree []ctData
	extraBits  []int
	extraBase  int
	elems      int
	maxLength  int
}

var (
	staticLtree  [lCodes + 2]ctData
	staticDtree  [dCodes]ctData
	distCode     [512]uint8
	lengthCodeTb [stdMaxMatch - stdMinMatch + 1]uint8
	baseLength   [lengthCode]int
	baseDist     [dCodes]int

	staticLDesc  staticTreeDesc
	staticDDesc  staticTreeDesc
	staticBLDesc staticTreeDesc
)

func init() {
	// tr_static_init
	length := 0
	var code int
	for code = 0; code < lengthCode-1; code++ {
		baseLength[code] = length
		for n := 0; n < 1<<extraLbits[code]; n++ {
			lengthCodeTb[length] = uint8(code)
			length++
		}
	}
	lengthCodeTb[length-1] = uint8(code)

	dist := 0
	for code = 0; code < 16; code++ {
		baseDist[code] = dist
		for n := 0; n < 1<<extraDbits[code]; n++ {
			distCode[dist] = uint8(code)
			dist++
		}
	}
	dist >>= 7
	for ; code < dCodes; code++ {
		baseDist[code] = dist << 7
		for n := 0; n < 1<<(extraDbits[code]-7); n++ {
			distCode[256+dist] = uint8(code)
			dist++
		}
	}

	var blCount [maxBits + 1]uint16
	n := 0
	for n <= 143 {
		staticLtree[n].dl = 8
		n++
		blCount[8]++
	}
	for n <= 255 {
		staticLtree[n].dl = 9
		n++
		blCount[9]++
	}
	for n <= 279 {
		staticLtree[n].dl = 7
		n++
		blCount[7]++
	}
	for n <= 287 {
		staticLtree[n].dl = 8
		n++
		blCount[8]++
	}
	genCodes(staticLtree[:], lCodes+1, &blCount)
	for n := 0; n < dCodes; n++ {
		staticDtree[n].dl = 5
		staticDtree[n].fc = uint16(biReverse(uint(n), 5))
	}

	staticLDesc = staticTreeDesc{staticLtree[:], extraLbits[:], literals + 1, lCodes, maxBits}
	staticDDesc = staticTreeDesc{staticDtree[:], extraDbits[:], 0, dCodes, maxBits}
	staticBLDesc = staticTreeDesc{nil, extraBlbits[:], 0, blCodes, maxBLBits}
}

func dCode(dist int) int {
	if dist < 256 {
		return int(distCode[dist])
	}
	return int(distCode[256+(dist>>7)])
}

func biReverse(code uint, length int) uint {
	var res uint
	for {
		res |= code & 1
		code >>= 1
		res <<= 1
		length--
		if length <= 0 {
			break
		}
	}
	return res >> 1
}

// genCodes mirrors gen_codes.
func genCodes(tree []ctData, maxCode int, blCount *[maxBits + 1]uint16) {
	var nextCode [maxBits + 1]uint16
	var code uint
	for bits := 1; bits <= maxBits; bits++ {
		code = (code + uint(blCount[bits-1])) << 1
		nextCode[bits] = uint16(code)
	}
	for n := 0; n <= maxCode; n++ {
		l := int(tree[n].dl)
		if l == 0 {
			continue
		}
		tree[n].fc = uint16(biReverse(uint(nextCode[l]), l))
		nextCode[l]++
	}
}

type treeDesc struct {
	dynTree  []ctData
	maxCode  int
	statDesc *staticTreeDesc
}

type trees struct {
	dynLtree [heapSize]ctData
	dynDtree [2*dCodes + 1]ctData
	blTree   [2*blCodes + 1]ctData

	lDesc, dDesc, blDesc treeDesc

	blCount [maxBits + 1]uint16
	heap    [2*lCodes + 1]int
	heapLen int
	heapMax int
	depth   [2*lCodes + 1]uint8

	symDist [symEnd]uint16
	symLc   [symEnd]uint8
	symNext int

	optLen    uint64
	staticLen uint64

	biBuf   uint64
	biValid uint

	// pending_out[:pending]; Deflater owns the backing array.
	pendingBuf []byte
}

func (s *trees) trInit() {
	s.lDesc = treeDesc{dynTree: s.dynLtree[:], statDesc: &staticLDesc}
	s.dDesc = treeDesc{dynTree: s.dynDtree[:], statDesc: &staticDDesc}
	s.blDesc = treeDesc{dynTree: s.blTree[:], statDesc: &staticBLDesc}
	s.biBuf, s.biValid = 0, 0
	s.initBlock()
}

func (s *trees) initBlock() {
	for n := 0; n < lCodes; n++ {
		s.dynLtree[n].fc = 0
	}
	for n := 0; n < dCodes; n++ {
		s.dynDtree[n].fc = 0
	}
	for n := 0; n < blCodes; n++ {
		s.blTree[n].fc = 0
	}
	s.dynLtree[endBlock].fc = 1
	s.optLen, s.staticLen = 0, 0
	s.symNext = 0
}

// --- bit output ---

func (s *trees) putByte(c byte) { s.pendingBuf = append(s.pendingBuf, c) }

func (s *trees) putShort(w uint16) { s.pendingBuf = append(s.pendingBuf, byte(w), byte(w>>8)) }

func (s *trees) putShortMSB(w uint16) { s.pendingBuf = append(s.pendingBuf, byte(w>>8), byte(w)) }

func (s *trees) putUint32(w uint32) {
	s.pendingBuf = append(s.pendingBuf, byte(w), byte(w>>8), byte(w>>16), byte(w>>24))
}

func (s *trees) putUint32MSB(w uint32) {
	s.pendingBuf = append(s.pendingBuf, byte(w>>24), byte(w>>16), byte(w>>8), byte(w))
}

func (s *trees) putUint64(w uint64) {
	s.putUint32(uint32(w))
	s.putUint32(uint32(w >> 32))
}

// sendBits mirrors the send_bits macro.
func (s *trees) sendBits(value uint64, length uint) {
	total := s.biValid + length
	if total < 64 && s.biValid < 64 {
		s.biBuf |= value << s.biValid
		s.biValid = total
	} else if s.biValid >= 64 {
		s.putUint64(s.biBuf)
		s.biBuf = value
		s.biValid = length
	} else {
		s.biBuf |= value << s.biValid
		s.putUint64(s.biBuf)
		s.biBuf = value >> (64 - s.biValid)
		s.biValid = total - 64
	}
}

func (s *trees) sendCode(c int, tree []ctData) {
	s.sendBits(uint64(tree[c].fc), uint(tree[c].dl))
}

// biWindup mirrors bi_windup.
func (s *trees) biWindup() {
	if s.biValid > 56 {
		s.putUint64(s.biBuf)
	} else {
		if s.biValid > 24 {
			s.putUint32(uint32(s.biBuf))
			s.biBuf >>= 32
			s.biValid -= 32
		}
		if int(s.biValid) > 8 {
			s.putShort(uint16(s.biBuf))
			s.biBuf >>= 16
			s.biValid -= 16
		}
		if int(s.biValid) > 0 {
			s.putByte(byte(s.biBuf))
		}
	}
	s.biBuf = 0
	s.biValid = 0
}

// trFlushBits mirrors zng_tr_flush_bits.
func (s *trees) trFlushBits() {
	if s.biValid >= 48 {
		s.putUint32(uint32(s.biBuf))
		s.putShort(uint16(s.biBuf >> 32))
		s.biBuf >>= 48
		s.biValid -= 48
	} else if s.biValid >= 32 {
		s.putUint32(uint32(s.biBuf))
		s.biBuf >>= 32
		s.biValid -= 32
	}
	if s.biValid >= 16 {
		s.putShort(uint16(s.biBuf))
		s.biBuf >>= 16
		s.biValid -= 16
	}
	if s.biValid >= 8 {
		s.putByte(byte(s.biBuf))
		s.biBuf >>= 8
		s.biValid -= 8
	}
}

// --- heap / tree construction ---

func smaller(tree []ctData, n, m int, depth []uint8) bool {
	return tree[n].fc < tree[m].fc || (tree[n].fc == tree[m].fc && depth[n] <= depth[m])
}

func (s *trees) pqdownheap(tree []ctData, k int) {
	v := s.heap[k]
	j := k << 1
	for j <= s.heapLen {
		if j < s.heapLen && smaller(tree, s.heap[j+1], s.heap[j], s.depth[:]) {
			j++
		}
		if smaller(tree, v, s.heap[j], s.depth[:]) {
			break
		}
		s.heap[k] = s.heap[j]
		k = j
		j <<= 1
	}
	s.heap[k] = v
}

func (s *trees) genBitlen(desc *treeDesc) {
	tree := desc.dynTree
	maxCode := desc.maxCode
	stree := desc.statDesc.staticTree
	extra := desc.statDesc.extraBits
	base := desc.statDesc.extraBase
	maxLength := desc.statDesc.maxLength
	overflow := 0

	for bits := 0; bits <= maxBits; bits++ {
		s.blCount[bits] = 0
	}
	tree[s.heap[s.heapMax]].dl = 0

	h := s.heapMax + 1
	for ; h < heapSize; h++ {
		n := s.heap[h]
		bits := int(tree[tree[n].dl].dl) + 1
		if bits > maxLength {
			bits = maxLength
			overflow++
		}
		tree[n].dl = uint16(bits)
		if n > maxCode {
			continue
		}
		s.blCount[bits]++
		xbits := 0
		if n >= base {
			xbits = extra[n-base]
		}
		f := uint64(tree[n].fc)
		s.optLen += f * uint64(bits+xbits)
		if stree != nil {
			s.staticLen += f * uint64(int(stree[n].dl)+xbits)
		}
	}
	if overflow == 0 {
		return
	}
	for {
		bits := maxLength - 1
		for s.blCount[bits] == 0 {
			bits--
		}
		s.blCount[bits]--
		s.blCount[bits+1] += 2
		s.blCount[maxLength]--
		overflow -= 2
		if overflow <= 0 {
			break
		}
	}
	for bits := maxLength; bits != 0; bits-- {
		n := int(s.blCount[bits])
		for n != 0 {
			h--
			m := s.heap[h]
			if m > maxCode {
				continue
			}
			if int(tree[m].dl) != bits {
				s.optLen += (uint64(bits) - uint64(tree[m].dl)) * uint64(tree[m].fc)
				tree[m].dl = uint16(bits)
			}
			n--
		}
	}
}

func (s *trees) buildTree(desc *treeDesc) {
	tree := desc.dynTree
	stree := desc.statDesc.staticTree
	elems := desc.statDesc.elems
	maxCode := -1

	s.heapLen = 0
	s.heapMax = heapSize
	for n := 0; n < elems; n++ {
		if tree[n].fc != 0 {
			s.heapLen++
			s.heap[s.heapLen] = n
			maxCode = n
			s.depth[n] = 0
		} else {
			tree[n].dl = 0
		}
	}
	for s.heapLen < 2 {
		node := 0
		if maxCode < 2 {
			maxCode++
			node = maxCode
		}
		s.heapLen++
		s.heap[s.heapLen] = node
		tree[node].fc = 1
		s.depth[node] = 0
		s.optLen--
		if stree != nil {
			s.staticLen -= uint64(stree[node].dl)
		}
	}
	desc.maxCode = maxCode

	for n := s.heapLen / 2; n >= 1; n-- {
		s.pqdownheap(tree, n)
	}

	node := elems
	for {
		// pqremove
		n := s.heap[1]
		s.heap[1] = s.heap[s.heapLen]
		s.heapLen--
		s.pqdownheap(tree, 1)

		m := s.heap[1]
		s.heapMax--
		s.heap[s.heapMax] = n
		s.heapMax--
		s.heap[s.heapMax] = m

		tree[node].fc = tree[n].fc + tree[m].fc
		dn, dm := s.depth[n], s.depth[m]
		if dn >= dm {
			s.depth[node] = dn + 1
		} else {
			s.depth[node] = dm + 1
		}
		tree[n].dl = uint16(node)
		tree[m].dl = uint16(node)
		s.heap[1] = node
		node++
		s.pqdownheap(tree, 1)
		if s.heapLen < 2 {
			break
		}
	}
	s.heapMax--
	s.heap[s.heapMax] = s.heap[1]

	s.genBitlen(desc)
	genCodes(tree, maxCode, &s.blCount)
}

func (s *trees) scanTree(tree []ctData, maxCode int) {
	prevlen := -1
	nextlen := int(tree[0].dl)
	count := 0
	maxCount, minCount := 7, 4
	if nextlen == 0 {
		maxCount, minCount = 138, 3
	}
	tree[maxCode+1].dl = 0xffff // guard
	for n := 0; n <= maxCode; n++ {
		curlen := nextlen
		nextlen = int(tree[n+1].dl)
		count++
		if count < maxCount && curlen == nextlen {
			continue
		} else if count < minCount {
			s.blTree[curlen].fc += uint16(count)
		} else if curlen != 0 {
			if curlen != prevlen {
				s.blTree[curlen].fc++
			}
			s.blTree[rep3_6].fc++
		} else if count <= 10 {
			s.blTree[repz3_10].fc++
		} else {
			s.blTree[repz11_138].fc++
		}
		count = 0
		prevlen = curlen
		if nextlen == 0 {
			maxCount, minCount = 138, 3
		} else if curlen == nextlen {
			maxCount, minCount = 6, 3
		} else {
			maxCount, minCount = 7, 4
		}
	}
}

func (s *trees) sendTree(tree []ctData, maxCode int) {
	prevlen := -1
	nextlen := int(tree[0].dl)
	count := 0
	maxCount, minCount := 7, 4
	if nextlen == 0 {
		maxCount, minCount = 138, 3
	}
	bl := s.blTree[:]
	for n := 0; n <= maxCode; n++ {
		curlen := nextlen
		nextlen = int(tree[n+1].dl)
		count++
		if count < maxCount && curlen == nextlen {
			continue
		} else if count < minCount {
			for {
				s.sendCode(curlen, bl)
				count--
				if count == 0 {
					break
				}
			}
		} else if curlen != 0 {
			if curlen != prevlen {
				s.sendCode(curlen, bl)
				count--
			}
			s.sendCode(rep3_6, bl)
			s.sendBits(uint64(count-3), 2)
		} else if count <= 10 {
			s.sendCode(repz3_10, bl)
			s.sendBits(uint64(count-3), 3)
		} else {
			s.sendCode(repz11_138, bl)
			s.sendBits(uint64(count-11), 7)
		}
		count = 0
		prevlen = curlen
		if nextlen == 0 {
			maxCount, minCount = 138, 3
		} else if curlen == nextlen {
			maxCount, minCount = 6, 3
		} else {
			maxCount, minCount = 7, 4
		}
	}
}

func (s *trees) buildBLTree() int {
	s.scanTree(s.dynLtree[:], s.lDesc.maxCode)
	s.scanTree(s.dynDtree[:], s.dDesc.maxCode)
	s.buildTree(&s.blDesc)
	maxBlindex := blCodes - 1
	for ; maxBlindex >= 3; maxBlindex-- {
		if s.blTree[blOrder[maxBlindex]].dl != 0 {
			break
		}
	}
	s.optLen += 3*(uint64(maxBlindex)+1) + 5 + 5 + 4
	return maxBlindex
}

func (s *trees) sendAllTrees(lcodes, dcodes, blcodes int) {
	s.sendBits(uint64(lcodes-257), 5)
	s.sendBits(uint64(dcodes-1), 5)
	s.sendBits(uint64(blcodes-4), 4)
	for rank := 0; rank < blcodes; rank++ {
		s.sendBits(uint64(s.blTree[blOrder[rank]].dl), 3)
	}
	s.sendTree(s.dynLtree[:], lcodes-1)
	s.sendTree(s.dynDtree[:], dcodes-1)
}

func (s *trees) trStoredBlock(buf []byte, last bool) {
	s.sendBits(uint64(storedBlock<<1)+b2u(last), 3)
	s.biWindup()
	n := len(buf)
	s.putShort(uint16(n))
	s.putShort(^uint16(n))
	s.pendingBuf = append(s.pendingBuf, buf...)
}

// trFlushBlock mirrors zng_tr_flush_block for level > 0 and a strategy other
// than Z_FIXED. Deflate streams are data_type-agnostic, so detect_data_type is
// not ported.
func (s *trees) trFlushBlock(buf []byte, haveBuf bool, storedLen int, last bool) {
	var optLenb, staticLenb uint64
	maxBlindex := 0

	if s.symNext == 0 {
		// Emit an empty static tree block with no codes.
		s.staticLen = 7
	} else {
		s.buildTree(&s.lDesc)
		s.buildTree(&s.dDesc)
		maxBlindex = s.buildBLTree()

		optLenb = (s.optLen + 3 + 7) >> 3
		staticLenb = (s.staticLen + 3 + 7) >> 3
		if staticLenb <= optLenb {
			optLenb = staticLenb
		}
	}

	if uint64(storedLen)+4 <= optLenb && haveBuf {
		s.trStoredBlock(buf, last)
	} else if staticLenb == optLenb {
		s.sendBits(uint64(staticTrees<<1)+b2u(last), 3)
		s.compressBlock(staticLtree[:], staticDtree[:])
	} else {
		s.sendBits(uint64(dynTrees<<1)+b2u(last), 3)
		s.sendAllTrees(s.lDesc.maxCode+1, s.dDesc.maxCode+1, maxBlindex+1)
		s.compressBlock(s.dynLtree[:], s.dynDtree[:])
	}
	s.initBlock()
	if last {
		s.biWindup()
	}
}

func b2u(b bool) uint64 {
	if b {
		return 1
	}
	return 0
}

func (s *trees) tallyLit(c byte) bool {
	s.symDist[s.symNext] = 0
	s.symLc[s.symNext] = c
	s.symNext++
	s.dynLtree[c].fc++
	return s.symNext == symEnd
}

func (s *trees) tallyDist(dist, length int) bool {
	s.symDist[s.symNext] = uint16(dist)
	s.symLc[s.symNext] = uint8(length)
	s.symNext++
	dist--
	s.dynLtree[int(lengthCodeTb[length])+literals+1].fc++
	s.dynDtree[dCode(dist)].fc++
	return s.symNext == symEnd
}

// compressBlock mirrors compress_block with zng_emit_lit and zng_emit_dist: a
// match is sent as one send_bits call of the combined length and distance
// codes.
func (s *trees) compressBlock(ltree, dtree []ctData) {
	for i := 0; i < s.symNext; i++ {
		dist := int(s.symDist[i])
		lc := int(s.symLc[i])
		if dist == 0 {
			s.sendCode(lc, ltree)
			continue
		}
		code := int(lengthCodeTb[lc])
		c := code + literals + 1
		matchBits := uint64(ltree[c].fc)
		matchBitsLen := uint(ltree[c].dl)
		if extra := extraLbits[code]; extra != 0 {
			matchBits |= uint64(lc-baseLength[code]) << matchBitsLen
			matchBitsLen += uint(extra)
		}
		dist--
		code = dCode(dist)
		matchBits |= uint64(dtree[code].fc) << matchBitsLen
		matchBitsLen += uint(dtree[code].dl)
		if extra := extraDbits[code]; extra != 0 {
			matchBits |= uint64(dist-baseDist[code]) << matchBitsLen
			matchBitsLen += uint(extra)
		}
		s.sendBits(matchBits, matchBitsLen)
	}
	s.sendCode(endBlock, ltree)
}
