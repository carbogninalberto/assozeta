package dto

import (
	"bytes"
	"fmt"
	"strings"
	"unicode/utf8"
)

// JSONParseDetail diagnoses invalid input using shipped Python 3.13.2 JSONDecoder's
// grammar/positions. It is not a second value decoder: successful values use the
// standard Go decoder. Positions count Unicode code points, not UTF-8 bytes.
func JSONParseDetail(raw []byte) string {
	if bytes.HasPrefix(raw, []byte{0xef, 0xbb, 0xbf}) {
		return "JSON parse error - Unexpected UTF-8 BOM (decode using utf-8-sig): line 1 column 1 (char 0)"
	}
	p := jsonDiagnostic{raw: raw}
	p.space()
	p.value(0)
	p.space()
	if p.reason == "" && p.pos < len(raw) {
		p.fail("Extra data", p.pos)
	}
	if p.special != "" {
		return "JSON parse error - " + p.special
	}
	line := 1 + strings.Count(string(raw[:p.at]), "\n")
	last := strings.LastIndexByte(string(raw[:p.at]), '\n')
	column := utf8.RuneCount(raw[last+1:p.at]) + 1
	return fmt.Sprintf("JSON parse error - %s: line %d column %d (char %d)", p.reason, line, column, utf8.RuneCount(raw[:p.at]))
}

// UTF8ParseDetail retains Python's byte-oriented decode error before JSON parsing.
func UTF8ParseDetail(raw []byte) string {
	for i := 0; i < len(raw); {
		_, size := utf8.DecodeRune(raw[i:])
		if size > 1 || raw[i] < 0x80 {
			i += size
			continue
		}
		first := raw[i]
		length := 0
		switch {
		case first >= 0xc2 && first <= 0xdf:
			length = 2
		case first >= 0xe0 && first <= 0xef:
			length = 3
		case first >= 0xf0 && first <= 0xf4:
			length = 4
		}
		end := i + 1
		reason := "invalid start byte"
		if length > 0 {
			reason = "invalid continuation byte"
			for j := 1; j < length; j++ {
				if i+j >= len(raw) {
					end = len(raw)
					reason = "unexpected end of data"
					break
				}
				b := raw[i+j]
				valid := b >= 0x80 && b <= 0xbf
				if j == 1 {
					if first == 0xe0 {
						valid = valid && b >= 0xa0
					}
					if first == 0xed {
						valid = valid && b < 0xa0
					}
					if first == 0xf0 {
						valid = valid && b >= 0x90
					}
					if first == 0xf4 {
						valid = valid && b < 0x90
					}
				}
				if !valid {
					end = i + j
					break
				}
				end = i + j + 1
			}
		}
		if end == i+1 {
			return fmt.Sprintf("JSON parse error - 'utf-8' codec can't decode byte 0x%02x in position %d: %s", first, i, reason)
		}
		return fmt.Sprintf("JSON parse error - 'utf-8' codec can't decode bytes in position %d-%d: %s", i, end-1, reason)
	}
	return ""
}

type jsonDiagnostic struct {
	raw             []byte
	pos, at         int
	reason, special string
}

func (p *jsonDiagnostic) fail(reason string, at int) {
	if p.reason == "" {
		p.reason = reason
		p.at = at
	}
}
func (p *jsonDiagnostic) space() {
	for p.pos < len(p.raw) && strings.ContainsRune(" \t\r\n", rune(p.raw[p.pos])) {
		p.pos++
	}
}
func (p *jsonDiagnostic) take(c byte) bool {
	if p.pos < len(p.raw) && p.raw[p.pos] == c {
		p.pos++
		return true
	}
	return false
}
func (p *jsonDiagnostic) value(depth int) {
	if p.reason != "" {
		return
	}
	p.space()
	if p.pos == len(p.raw) {
		p.fail("Expecting value", p.pos)
		return
	}
	if depth > 1000 {
		p.fail("Maximum nesting depth exceeded", p.pos)
		return
	}
	switch p.raw[p.pos] {
	case '"':
		p.text()
		return
	case '{':
		p.pos++
		p.space()
		if p.take('}') {
			return
		}
		for p.reason == "" {
			if p.pos == len(p.raw) || p.raw[p.pos] != '"' {
				p.fail("Expecting property name enclosed in double quotes", p.pos)
				return
			}
			p.text()
			if p.reason != "" {
				return
			}
			p.space()
			if !p.take(':') {
				p.fail("Expecting ':' delimiter", p.pos)
				return
			}
			p.value(depth + 1)
			if p.reason != "" {
				return
			}
			p.space()
			if p.take('}') {
				return
			}
			comma := p.pos
			if !p.take(',') {
				p.fail("Expecting ',' delimiter", p.pos)
				return
			}
			p.space()
			if p.pos < len(p.raw) && p.raw[p.pos] == '}' {
				p.fail("Illegal trailing comma before end of object", comma)
				return
			}
		}
	case '[':
		p.pos++
		p.space()
		if p.take(']') {
			return
		}
		for p.reason == "" {
			p.value(depth + 1)
			if p.reason != "" {
				return
			}
			p.space()
			if p.take(']') {
				return
			}
			comma := p.pos
			if !p.take(',') {
				p.fail("Expecting ',' delimiter", p.pos)
				return
			}
			p.space()
			if p.pos < len(p.raw) && p.raw[p.pos] == ']' {
				p.fail("Illegal trailing comma before end of array", comma)
				return
			}
		}
	default:
		for _, literal := range []string{"true", "false", "null"} {
			if bytes.HasPrefix(p.raw[p.pos:], []byte(literal)) {
				p.pos += len(literal)
				return
			}
		}
		for _, literal := range []string{"NaN", "Infinity", "-Infinity"} {
			if bytes.HasPrefix(p.raw[p.pos:], []byte(literal)) {
				p.special = "Out of range float values are not JSON compliant: '" + literal + "'"
				p.fail("constant", p.pos)
				return
			}
		}
		start := p.pos
		p.take('-')
		if !p.take('0') {
			if p.pos == len(p.raw) || p.raw[p.pos] < '1' || p.raw[p.pos] > '9' {
				p.fail("Expecting value", start)
				return
			}
			for p.pos < len(p.raw) && p.raw[p.pos] >= '0' && p.raw[p.pos] <= '9' {
				p.pos++
			}
		}
		if p.pos+1 < len(p.raw) && p.raw[p.pos] == '.' && p.raw[p.pos+1] >= '0' && p.raw[p.pos+1] <= '9' {
			p.pos++
			for p.pos < len(p.raw) && p.raw[p.pos] >= '0' && p.raw[p.pos] <= '9' {
				p.pos++
			}
		}
		if p.pos < len(p.raw) && (p.raw[p.pos] == 'e' || p.raw[p.pos] == 'E') {
			exponent := p.pos
			p.pos++
			if !p.take('+') {
				p.take('-')
			}
			digits := p.pos
			for p.pos < len(p.raw) && p.raw[p.pos] >= '0' && p.raw[p.pos] <= '9' {
				p.pos++
			}
			if digits == p.pos {
				p.pos = exponent
			}
		}
	}
}
func (p *jsonDiagnostic) text() {
	start := p.pos
	p.pos++
	for p.pos < len(p.raw) {
		if p.take('"') {
			return
		}
		if p.raw[p.pos] < 0x20 {
			p.fail("Invalid control character at", p.pos)
			return
		}
		if p.raw[p.pos] == '\\' {
			escape := p.pos
			p.pos++
			if p.pos == len(p.raw) {
				p.fail("Unterminated string starting at", start)
				return
			}
			if p.raw[p.pos] == 'u' {
				unicodeStart := p.pos
				p.pos++
				for i := 0; i < 4; i++ {
					if p.pos == len(p.raw) || !strings.ContainsRune("0123456789abcdefABCDEF", rune(p.raw[p.pos])) {
						p.fail(`Invalid \uXXXX escape`, unicodeStart)
						return
					}
					p.pos++
				}
				continue
			}
			if !strings.ContainsRune(`"\/bfnrt`, rune(p.raw[p.pos])) {
				p.fail(`Invalid \escape`, escape)
				return
			}
		}
		p.pos++
	}
	p.fail("Unterminated string starting at", start)
}
