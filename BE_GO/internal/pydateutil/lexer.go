package pydateutil

import (
	"sort"
	"strings"
	"unicode/utf8"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/pystr"
)

func inRanges(table [][2]rune, r rune) bool {
	i := sort.Search(len(table), func(i int) bool { return table[i][1] >= r })
	return i < len(table) && table[i][0] <= r
}

// isAlpha is str.isalpha() for one character.
func isAlpha(r rune) bool {
	if r < 0x80 {
		return 'a' <= r && r <= 'z' || 'A' <= r && r <= 'Z'
	}
	return inRanges(alphaRanges, r)
}

// isDigit is str.isdigit() for one character (wider than the decimal
// digits: superscripts and circled digits are digits too).
func isDigit(r rune) bool {
	if r < 0x80 {
		return '0' <= r && r <= '9'
	}
	return inRanges(digitRanges, r)
}

// isSpace is str.isspace() for one character.
func isSpace(r rune) bool { return inRanges(spaceRanges, r) }

// decimalValue is the value of a decimal digit (str.isdecimal()), or -1.
func decimalValue(r rune) int {
	if r < 0x80 {
		if '0' <= r && r <= '9' {
			return int(r - '0')
		}
		return -1
	}
	i := sort.Search(len(decimalZeros), func(i int) bool { return decimalZeros[i]+9 >= r })
	if i < len(decimalZeros) && decimalZeros[i] <= r {
		return int(r - decimalZeros[i])
	}
	return -1
}

// isDigitString is str.isdigit(): non-empty and every character a digit.
func isDigitString(s string) bool {
	for _, r := range s {
		if !isDigit(r) {
			return false
		}
	}
	return s != ""
}

// runeCount is len() of the Python str a token encodes: a lone surrogate is
// written as a three-byte form, which a Go range would count three times.
func runeCount(s string) int {
	count := 0
	for i := 0; i < len(s); {
		if s[i] < utf8.RuneSelf {
			count++
			i++
			continue
		}
		if s[i] == 0xED && i+2 < len(s) && s[i+1] >= 0xA0 && s[i+1] <= 0xBF && s[i+2] >= 0x80 && s[i+2] <= 0xBF {
			count++
			i += 3
			continue
		}
		_, size := utf8.DecodeRuneInString(s[i:])
		count++
		i += size
	}
	return count
}

const (
	lexStart = iota
	lexWord
	lexNumber
	lexWordDot
	lexNumberDot
)

// timelex is _timelex.split(text): runs of letters, runs of digits, single
// other characters (whitespace becomes ' '), with dotted runs kept whole or
// split around '.' and ',' exactly as get_token does. chars are the Python
// code points of the text and the tokens are their Python str forms.
func timelex(chars []rune) []string {
	// get_token skips every NUL it reads from the stream.
	text := make([]rune, 0, len(chars))
	for _, r := range chars {
		if r != 0 {
			text = append(text, r)
		}
	}
	var tokens []string
	pos := 0
	for pos < len(text) {
		var token []rune
		state := lexStart
		seenLetters := false
	scan:
		for pos < len(text) {
			c := text[pos]
			pos++
			switch state {
			case lexStart:
				token = append(token, c)
				switch {
				case isAlpha(c):
					state = lexWord
				case isDigit(c):
					state = lexNumber
				case isSpace(c):
					token[0] = ' '
					break scan
				default:
					break scan
				}
			case lexWord:
				seenLetters = true
				switch {
				case isAlpha(c):
					token = append(token, c)
				case c == '.':
					token = append(token, c)
					state = lexWordDot
				default:
					pos--
					break scan
				}
			case lexNumber:
				switch {
				case isDigit(c):
					token = append(token, c)
				case c == '.' || (c == ',' && len(token) >= 2):
					token = append(token, c)
					state = lexNumberDot
				default:
					pos--
					break scan
				}
			case lexWordDot:
				seenLetters = true
				switch {
				case c == '.' || isAlpha(c):
					token = append(token, c)
				case isDigit(c) && token[len(token)-1] == '.':
					token = append(token, c)
					state = lexNumberDot
				default:
					pos--
					break scan
				}
			case lexNumberDot:
				switch {
				case c == '.' || isDigit(c):
					token = append(token, c)
				case isAlpha(c) && token[len(token)-1] == '.':
					token = append(token, c)
					state = lexWordDot
				default:
					pos--
					break scan
				}
			}
		}
		dots := 0
		for _, c := range token {
			if c == '.' {
				dots++
			}
		}
		last := token[len(token)-1]
		if (state == lexWordDot || state == lexNumberDot) && (seenLetters || dots > 1 || last == '.' || last == ',') {
			// re.split("([.,])", token): the first piece is the token, the
			// non-empty rest is queued.
			first := true
			start := 0
			emit := func(piece []rune) {
				if first {
					tokens = append(tokens, pystr.String(piece))
					first = false
				} else if len(piece) > 0 {
					tokens = append(tokens, pystr.String(piece))
				}
			}
			for i, c := range token {
				if c == '.' || c == ',' {
					emit(token[start:i])
					emit(token[i : i+1])
					start = i + 1
				}
			}
			emit(token[start:])
			continue
		}
		text := pystr.String(token)
		if state == lexNumberDot && dots == 0 {
			text = strings.ReplaceAll(text, ",", ".")
		}
		tokens = append(tokens, text)
	}
	return tokens
}
