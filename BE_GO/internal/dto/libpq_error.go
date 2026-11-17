package dto

import (
	"strconv"
	"strings"
	"unicode"

	"golang.org/x/text/width"
)

const (
	libpqDisplaySize = 60 // screen width limit, in screen columns
	libpqMinRightCut = 10 // try to keep this far away from the end of the line
)

// Psycopg2ServerErrorText is str(e) of the psycopg2 exception for a server error raised by `query` (the exact text sent):
// libpq's default-verbosity message without its "ERROR:  " prefix.
func Psycopg2ServerErrorText(query string, message string, position int32, detail, hint, internalQuery, where string) string {
	var out strings.Builder
	out.WriteString(message)
	out.WriteString("\n")
	if position > 0 {
		out.WriteString(LibpqErrorPosition(query, int(position)))
	}
	if detail != "" {
		out.WriteString("DETAIL:  ")
		out.WriteString(detail)
		out.WriteString("\n")
	}
	if hint != "" {
		out.WriteString("HINT:  ")
		out.WriteString(hint)
		out.WriteString("\n")
	}
	if internalQuery != "" {
		out.WriteString("QUERY:  ")
		out.WriteString(internalQuery)
		out.WriteString("\n")
	}
	if where != "" {
		out.WriteString("CONTEXT:  ")
		out.WriteString(where)
		out.WriteString("\n")
	}
	return out.String()
}

// LibpqErrorPosition is libpq's reportErrorPosition: the "LINE n: ...\n" and caret lines (empty when position is out of
// range).
func LibpqErrorPosition(query string, position int) string {
	loc := position - 1 // 1-based to 0-based
	if loc < 0 {
		return ""
	}

	// Work on runes: the query is valid UTF-8 and position is a rune index. Tabs become spaces, as in libpq's
	// writable copy.
	chars := []rune(query)
	for i, r := range chars {
		if r == '\t' {
			chars[i] = ' '
		}
	}
	qidx := make([]int, len(chars)+1)   // rune index -> rune offset
	scridx := make([]int, len(chars)+1) // rune index -> starting screen column

	scroffset := 0
	locLine := 1
	ibeg := 0
	iend := -1 // -1 means not set yet
	cno := 0
	for ; cno < len(chars); cno++ {
		ch := chars[cno]
		qidx[cno] = cno
		scridx[cno] = scroffset
		if ch == '\r' || ch == '\n' {
			if cno < loc {
				// Each \r or \n counts as a line except when \r\n appear together.
				if ch == '\r' || cno == 0 || chars[cno-1] != '\r' {
					locLine++
				}
				ibeg = cno + 1
			} else {
				iend = cno
				break
			}
		}
		w := libpqDisplayWidth(ch)
		if w <= 0 {
			w = 1
		}
		scroffset += w
	}
	if iend < 0 {
		iend = cno // query length in runes
		qidx[iend] = cno
		scridx[iend] = scroffset
	}
	if loc > cno {
		return ""
	}

	begTrunc, endTrunc := false, false
	if scridx[iend]-scridx[ibeg] > libpqDisplaySize {
		if scridx[ibeg]+libpqDisplaySize >= scridx[loc]+libpqMinRightCut {
			// Truncate the right end if that is enough.
			for scridx[iend]-scridx[ibeg] > libpqDisplaySize {
				iend--
			}
			endTrunc = true
		} else {
			// Truncate the right end if not too close to loc.
			for scridx[loc]+libpqMinRightCut < scridx[iend] {
				iend--
				endTrunc = true
			}
			// Truncate the left end if still too long.
			for scridx[iend]-scridx[ibeg] > libpqDisplaySize {
				ibeg++
				begTrunc = true
			}
		}
	}

	prefix := "LINE " + strconv.Itoa(locLine) + ": "
	var out strings.Builder
	out.WriteString(prefix)
	prefixWidth := 0
	for _, r := range prefix {
		w := libpqDisplayWidth(r)
		if w <= 0 {
			w = 1
		}
		prefixWidth += w
	}
	if begTrunc {
		out.WriteString("...")
		prefixWidth += 3
	}
	out.WriteString(string(chars[ibeg:iend]))
	if endTrunc {
		out.WriteString("...")
	}
	out.WriteString("\n")
	out.WriteString(strings.Repeat(" ", prefixWidth+scridx[loc]-scridx[ibeg]))
	out.WriteString("^\n")
	return out.String()
}

// libpqDisplayWidth is PostgreSQL's ucs_wcwidth narrowed to what libpq uses: 2 columns for East Asian wide or
// fullwidth runes that are not nonspacing, 1 column otherwise.
func libpqDisplayWidth(r rune) int {
	if unicode.Is(unicode.Mn, r) || unicode.Is(unicode.Me, r) || unicode.Is(unicode.Cf, r) {
		return 1
	}
	switch width.LookupRune(r).Kind() {
	case width.EastAsianWide, width.EastAsianFullwidth:
		return 2
	}
	return 1
}
