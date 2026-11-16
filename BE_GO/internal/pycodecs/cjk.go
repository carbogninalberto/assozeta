// The stateless multibyte codecs: CPython's _multibytecodec decoders from
// Modules/cjkcodecs. They decode one character at a time from a position: a
// character is at most three bytes, gb18030's four-byte forms, or euc_kr's
// eight-byte KS X 1001:1998 make-up form. The tables are generated from
// CPython by testdata/gen_tables.py and embedded in cjk_tables.bin.gz.
package pycodecs

import (
	"bytes"
	"compress/gzip"
	_ "embed"
	"encoding/binary"
	"io"
	"sync"
)

// cjkIllegal and cjkIncomplete are the only two UnicodeDecodeError reasons a
// CPython multibytecodec reports. An error leaf always names a span of
// cjkIllegal bytes; running out of input inside a sequence is cjkIncomplete
// over all the bytes that are left.
const (
	cjkIllegal    = "illegal multibyte sequence"
	cjkIncomplete = "incomplete multibyte sequence"
)

// cjkCodecs are the stateless multibyte codecs, by codecs.lookup(name).name.
var cjkCodecs = []string{
	"big5", "big5hkscs", "cp932", "cp949", "cp950",
	"euc_jis_2004", "euc_jisx0213", "euc_jp", "euc_kr", "gb18030",
	"gb2312", "gbk", "johab", "shift_jis", "shift_jis_2004", "shift_jisx0213",
}

func init() {
	for _, name := range cjkCodecs {
		register(name, func(data []byte, sink *Sink) error {
			return cjkTableOf(name).decode(data, sink)
		})
	}
}

// cjk_tables.bin.gz is written by testdata/gen_tables.py: the byte tries of
// the codecs above, gzip-compressed. The reader below documents the format.
//
//go:embed cjk_tables.bin.gz
var cjkBlob []byte

var (
	cjkOnce   sync.Once
	cjkTables map[string]*cjkTable
)

// Node kinds. Every node is reached by walking bytes from a character start:
// node 0 is the root, an interior node has one entry per byte value, and a
// text leaf is stored in its parent (it is not a node).
const (
	cjkError      uint8 = iota // report span bytes and resume after them
	cjkInterior                // follow the entry for the next byte
	cjkFourByte                // gb18030 4-byte form: index the run table with the next byte
	cjkMakeupNode              // euc_kr KS X 1001:1998 make-up form
)

// Child kinds of an interior node's entries.
const (
	childNode uint8 = iota // another trie node
	childText              // one or two code points, consuming one byte
	childRun               // consecutive code points for consecutive bytes
)

// cjkChild is one run of sibling byte values in an interior node. firstByte
// and count cover [firstByte, firstByte+count). A childText entry consumes one
// byte and appends cps[:ncp]; a childRun entry consumes one byte and appends
// cp+byte-firstByte; a childNode entry continues at that node.
type cjkChild struct {
	firstByte int
	count     int
	kind      uint8
	node      int32
	cps       [2]rune
	ncp       int
	cp        rune
}

// cjkNode is one trie node (never a text leaf).
type cjkNode struct {
	kind     uint8
	span     int // cjkError: the error span's length
	children []cjkChild
}

// child returns the entry covering a byte value. The entries partition 0..255.
func (n *cjkNode) child(b byte) *cjkChild {
	for i := range n.children {
		child := &n.children[i]
		if int(b) < child.firstByte+child.count {
			return child
		}
	}
	// The generated tables always cover every byte value.
	panic("pycodecs: cjk trie node has no entry for a byte")
}

// cjkSpan is a run of identical results in gb18030's four-byte table: count
// consecutive indices that decode to consecutive code points, or to the same
// error span.
type cjkSpan struct {
	start int32
	count int32
	value int32
	err   bool
}

// cjkMakeup is euc_kr's eight-byte make-up form: the fixed bytes, the three
// byte -> index tables, and the syllable composition 0xAC00 + cho*588 +
// jung*28 + jong (see Modules/cjkcodecs/_codecs_kr.c, DECODER(euc_kr)).
type cjkMakeup struct {
	total    int
	required [][2]int // offset, byte value
	fields   [3]int   // offsets of the cho, jung and jong bytes
	base     int
	scale    [2]int
	tables   [3][256]int8 // index, or -1 for a byte the decoder rejects
}

// cjkTable is one codec's trie.
type cjkTable struct {
	nodes  []cjkNode
	four   []cjkSpan  // gb18030 four-byte runs, ordered by start
	makeup *cjkMakeup // euc_kr only
	starts []int32    // four[i].start, for the binary search
}

// cjkTableOf parses the embedded tables once, on the first decode.
func cjkTableOf(name string) *cjkTable {
	cjkOnce.Do(loadCJKTables)
	table, ok := cjkTables[name]
	if !ok {
		panic("pycodecs: no multibyte table for " + name)
	}
	return table
}

// loadCJKTables decodes cjk_tables.bin.gz. The format is:
//
//	gzip of "PYCJK1", uvarint codec count, then per codec, sorted by name:
//	  uvarint name length, name
//	  uvarint node count; node 0 is the root
//	  per node, in id order:
//	    byte kind (cjkError, cjkInterior, cjkFourByte, cjkMakeupNode)
//	    cjkError:    uvarint span
//	    cjkInterior: uvarint entry count, then per entry:
//	                   byte tag: 0 text one code point, 1 text two code points,
//	                             2 code point run, 3 node reference
//	                 0: zigzag(cp - previous cp of the node's entries)
//	                 1: zigzag(first cp - previous), zigzag(second - first)
//	                 2: uvarint count, zigzag(first cp - previous)
//	                 3: zigzag(child id - this node's id)
//	    cjkFourByte: uvarint run count, then per run:
//	                   byte kind: 0 consecutive code points, 1 error span
//	                   uvarint start delta from the previous run's end
//	                   uvarint count, uvarint value (first cp or span)
//	    cjkMakeupNode: uvarint total length, uvarint required count,
//	                   (byte offset, byte value) per requirement,
//	                   three byte field offsets, uvarint base,
//	                   two uvarint scale factors,
//	                   three 256-byte tables holding index+1 (0 = rejected)
//
// A malformed blob can only come from a bad build, so parsing panics.
func loadCJKTables() {
	compressed, err := gzip.NewReader(bytes.NewReader(cjkBlob))
	if err != nil {
		panic("pycodecs: cjk_tables.bin.gz: " + err.Error())
	}
	raw, err := io.ReadAll(compressed)
	if err != nil {
		panic("pycodecs: cjk_tables.bin.gz: " + err.Error())
	}
	if len(raw) < 6 || string(raw[:6]) != "PYCJK1" {
		panic("pycodecs: cjk_tables.bin.gz: bad magic")
	}
	reader := &cjkReader{data: raw, pos: 6}
	tables := make(map[string]*cjkTable)
	for count := reader.uvarint(); count > 0; count-- {
		name := string(reader.take(int(reader.uvarint())))
		table := &cjkTable{}
		nodeCount := int(reader.uvarint())
		table.nodes = make([]cjkNode, nodeCount)
		for i := range table.nodes {
			node := &table.nodes[i]
			node.kind = reader.byte()
			switch node.kind {
			case cjkError:
				node.span = int(reader.uvarint())
			case cjkInterior:
				node.children = make([]cjkChild, reader.uvarint())
				next := 0
				for j := range node.children {
					child := &node.children[j]
					tag := reader.byte()
					child.firstByte = next
					child.count = 1
					switch tag {
					case 0:
						child.kind = childText
						child.cps[0] = child.referenceCp(node, j) + rune(reader.zigzag())
						child.ncp = 1
					case 1:
						child.kind = childText
						child.cps[0] = child.referenceCp(node, j) + rune(reader.zigzag())
						child.cps[1] = child.cps[0] + rune(reader.zigzag())
						child.ncp = 2
					case 2:
						child.kind = childRun
						child.count = int(reader.uvarint())
						child.cp = child.referenceCp(node, j) + rune(reader.zigzag())
					case 3:
						child.kind = childNode
						child.node = int32(i + reader.zigzag())
					default:
						panic("pycodecs: cjk_tables.bin.gz: bad child tag")
					}
					next += child.count
				}
				if next != 256 {
					panic("pycodecs: cjk_tables.bin.gz: children do not cover every byte")
				}
			case cjkFourByte:
				runs := make([]cjkSpan, reader.uvarint())
				var start int32
				for j := range runs {
					errRun := reader.byte()
					start += int32(reader.uvarint())
					runs[j] = cjkSpan{
						start: start,
						count: int32(reader.uvarint()),
						value: int32(reader.uvarint()),
						err:   errRun == 1,
					}
					start += runs[j].count
				}
				table.four = runs
				table.starts = make([]int32, len(runs))
				for j := range runs {
					table.starts[j] = runs[j].start
				}
			case cjkMakeupNode:
				m := &cjkMakeup{total: int(reader.uvarint())}
				required := int(reader.uvarint())
				m.required = make([][2]int, required)
				for j := range m.required {
					m.required[j] = [2]int{int(reader.byte()), int(reader.byte())}
				}
				for j := range m.fields {
					m.fields[j] = int(reader.byte())
				}
				m.base = int(reader.uvarint())
				m.scale[0] = int(reader.uvarint())
				m.scale[1] = int(reader.uvarint())
				for j := range m.tables {
					for k := range m.tables[j] {
						m.tables[j][k] = int8(reader.byte()) - 1
					}
				}
				table.makeup = m
			default:
				panic("pycodecs: cjk_tables.bin.gz: bad node kind")
			}
		}
		for node := range table.nodes {
			for _, child := range table.nodes[node].children {
				if child.kind == childNode && (child.node < 0 || int(child.node) >= nodeCount) {
					panic("pycodecs: cjk_tables.bin.gz: child node out of range")
				}
			}
		}
		tables[name] = table
	}
	if reader.pos != len(reader.data) {
		panic("pycodecs: cjk_tables.bin.gz: trailing bytes")
	}
	cjkTables = tables
}

// referenceCp is the last code point of the entries before index, which text
// entries delta-encode against. A node reference carries no code point, so it
// is skipped.
func (c *cjkChild) referenceCp(node *cjkNode, index int) rune {
	for j := index - 1; j >= 0; j-- {
		previous := &node.children[j]
		switch previous.kind {
		case childText:
			return previous.cps[previous.ncp-1]
		case childRun:
			return previous.cp + rune(previous.count) - 1
		}
	}
	return 0
}

// cjkReader reads the little integers of the table blob.
type cjkReader struct {
	data []byte
	pos  int
}

func (r *cjkReader) byte() byte {
	if r.pos >= len(r.data) {
		panic("pycodecs: cjk_tables.bin.gz: truncated")
	}
	value := r.data[r.pos]
	r.pos++
	return value
}

func (r *cjkReader) take(n int) []byte {
	if n < 0 || r.pos+n > len(r.data) {
		panic("pycodecs: cjk_tables.bin.gz: truncated")
	}
	value := r.data[r.pos : r.pos+n]
	r.pos += n
	return value
}

func (r *cjkReader) uvarint() int {
	value, size := binary.Uvarint(r.data[r.pos:])
	if size <= 0 {
		panic("pycodecs: cjk_tables.bin.gz: bad varint")
	}
	r.pos += size
	return int(value)
}

func (r *cjkReader) zigzag() int {
	value := r.uvarint()
	if value&1 != 0 {
		return -(value + 1) / 2
	}
	return value / 2
}

// decode walks the trie once per character, exactly as CPython's decoder does.
func (t *cjkTable) decode(data []byte, sink *Sink) error {
	for pos := 0; pos < len(data); {
		next, err := t.step(data, pos, sink)
		if err != nil {
			return err
		}
		pos = next
	}
	return nil
}

// step decodes the character at pos and returns the position after it. A text
// leaf appends its code points and consumes the bytes walked so far; an error
// leaf reports its span and decoding resumes at the span's end; the end of the
// data inside a sequence reports every remaining byte as incomplete.
func (t *cjkTable) step(data []byte, pos int, sink *Sink) (int, error) {
	node := &t.nodes[0]
	i := pos
	for {
		switch node.kind {
		case cjkInterior:
			if i == len(data) {
				return failSpan(data, sink, pos, len(data), cjkIncomplete)
			}
			value := data[i]
			child := node.child(value)
			i++
			switch child.kind {
			case childNode:
				node = &t.nodes[child.node]
			case childText:
				sink.Out = append(sink.Out, child.cps[:child.ncp]...)
				return i, nil
			case childRun:
				sink.Out = append(sink.Out, child.cp+rune(int(value)-child.firstByte))
				return i, nil
			}
		case cjkError:
			return failSpan(data, sink, pos, pos+node.span, cjkIllegal)
		case cjkFourByte:
			return t.stepFourByte(data, pos, i, sink)
		case cjkMakeupNode:
			return t.stepMakeup(data, pos, sink)
		}
	}
}

// stepFourByte decodes gb18030's four-byte form at pos; i is pos+3, the
// position of the fourth byte. The decoder accumulates a linear index from the
// bytes and looks it up in the run table. A fourth byte outside 0x30-0x39 is
// an illegal one-byte sequence (CPython reports the error before the index is
// complete).
func (t *cjkTable) stepFourByte(data []byte, pos, i int, sink *Sink) (int, error) {
	if i >= len(data) {
		return failSpan(data, sink, pos, len(data), cjkIncomplete)
	}
	b := data[i]
	if b < 0x30 || b > 0x39 {
		return failSpan(data, sink, pos, pos+1, cjkIllegal)
	}
	index := ((int(data[pos])-0x81)*10+int(data[pos+1])-0x30)*1260 +
		(int(data[pos+2])-0x81)*10 + int(b-0x30)
	span, ok := t.fourByteSpan(index)
	if !ok {
		return failSpan(data, sink, pos, pos+1, cjkIllegal)
	}
	if span.err {
		return failSpan(data, sink, pos, pos+int(span.value), cjkIllegal)
	}
	sink.Out = append(sink.Out, rune(span.value+int32(index)-span.start))
	return i + 1, nil
}

// fourByteSpan finds the run holding a linear four-byte index.
func (t *cjkTable) fourByteSpan(index int) (cjkSpan, bool) {
	low, high := 0, len(t.starts)
	for low < high {
		mid := (low + high) / 2
		if int(t.starts[mid]) <= index {
			low = mid + 1
		} else {
			high = mid
		}
	}
	if low == 0 {
		return cjkSpan{}, false
	}
	span := t.four[low-1]
	if index >= int(span.start+span.count) {
		return cjkSpan{}, false
	}
	return span, true
}

// stepMakeup decodes euc_kr's KS X 1001:1998 make-up form at pos. It needs
// eight bytes; while fewer are left CPython reports all of them as incomplete.
// A byte that does not fit the form is an illegal one-byte sequence.
func (t *cjkTable) stepMakeup(data []byte, pos int, sink *Sink) (int, error) {
	m := t.makeup
	if len(data)-pos < m.total {
		return failSpan(data, sink, pos, len(data), cjkIncomplete)
	}
	ok := true
	for _, requirement := range m.required {
		if data[pos+requirement[0]] != byte(requirement[1]) {
			ok = false
			break
		}
	}
	if ok {
		index := [3]int{
			int(m.tables[0][data[pos+m.fields[0]]]),
			int(m.tables[1][data[pos+m.fields[1]]]),
			int(m.tables[2][data[pos+m.fields[2]]]),
		}
		if index[0] < 0 || index[1] < 0 || index[2] < 0 {
			ok = false
		} else {
			sink.Out = append(sink.Out, rune(m.base+index[0]*m.scale[0]+index[1]*m.scale[1]+index[2]))
			return pos + m.total, nil
		}
	}
	return failSpan(data, sink, pos, pos+1, cjkIllegal)
}

// failSpan reports the undecodable span [pos, end) and returns the position
// to resume at (Sink.Fail: surrogateescape can resume inside the span).
func failSpan(_ []byte, sink *Sink, pos, end int, reason string) (int, error) {
	return sink.Fail(pos, end, reason)
}
