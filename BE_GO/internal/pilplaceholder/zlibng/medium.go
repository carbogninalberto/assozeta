package zlibng

// Port of zlib-ng's deflate_medium.c.

// match mirrors struct match; the fields are 16-bit in the C source and the
// arithmetic below relies on the same wrap-around.
type match struct {
	matchStart  uint16
	matchLength uint16
	strstart    uint16
	orgstart    uint16
}

// emitMatch mirrors emit_match.
func (s *Deflater) emitMatch(m match) int {
	bflush := 0

	// Matches that are not long enough are emitted as literals.
	if m.matchLength < wantMinMatch {
		for m.matchLength != 0 {
			if s.tallyLit(s.window[m.strstart]) {
				bflush++
			}
			s.lookahead--
			m.strstart++
			m.matchLength--
		}
		return bflush
	}

	if s.tallyDist(int(m.strstart)-int(m.matchStart), int(m.matchLength)-stdMinMatch) {
		bflush++
	}
	s.lookahead -= uint32(m.matchLength)
	return bflush
}

// insertMatch mirrors insert_match.
func (s *Deflater) insertMatch(m match) {
	if s.lookahead <= uint32(m.matchLength)+wantMinMatch {
		return
	}

	// Matches that are not long enough are emitted as literals.
	if m.matchLength < wantMinMatch {
		m.strstart++
		m.matchLength--
		if m.matchLength > 0 {
			if m.strstart >= m.orgstart {
				if int(m.strstart)+int(m.matchLength)-1 >= int(m.orgstart) {
					s.insertString(uint32(m.strstart), uint32(m.matchLength))
				} else {
					s.insertString(uint32(m.strstart), uint32(m.orgstart)-uint32(m.strstart)+1)
				}
			}
		}
		return
	}

	// Insert new strings in the hash table only if the match length is not
	// too large.
	if m.matchLength <= 16*maxLazyMatch && s.lookahead >= wantMinMatch {
		m.matchLength-- // string at strstart already in table
		m.strstart++

		if m.strstart >= m.orgstart {
			if int(m.strstart)+int(m.matchLength)-1 >= int(m.orgstart) {
				s.insertString(uint32(m.strstart), uint32(m.matchLength))
			} else {
				s.insertString(uint32(m.strstart), uint32(m.orgstart)-uint32(m.strstart)+1)
			}
		} else if int(m.orgstart) < int(m.strstart)+int(m.matchLength) {
			s.insertString(uint32(m.orgstart), uint32(int(m.strstart)+int(m.matchLength)-int(m.orgstart)))
		}
	} else {
		m.strstart += m.matchLength
		if m.strstart >= stdMinMatch-2 {
			s.quickInsertString(uint32(m.strstart) + 2 - stdMinMatch)
		}
	}
}

// fizzleMatches mirrors fizzle_matches.
func (s *Deflater) fizzleMatches(current, next *match) {
	if current.matchLength <= 1 {
		return
	}
	if int(current.matchLength) > 1+int(next.matchStart) {
		return
	}
	if int(current.matchLength) > 1+int(next.strstart) {
		return
	}

	// Quick exit check: if this fails then do not bother with anything else.
	m := int(next.matchStart) - int(current.matchLength) + 1
	o := int(next.strstart) - int(current.matchLength) + 1
	if s.window[m] != s.window[o] {
		return
	}

	c := *current
	n := *next

	// Step one: try to move the "next" match to the left as much as possible.
	var limit uint16
	if next.strstart > maxDist {
		limit = next.strstart - maxDist
	}

	m = int(n.matchStart) - 1
	o = int(n.strstart) - 1
	changed := 0
	for s.window[m] == s.window[o] {
		if c.matchLength < 1 {
			break
		}
		if n.strstart <= limit {
			break
		}
		if n.matchLength >= 256 {
			break
		}
		if n.matchStart <= 1 {
			break
		}

		n.strstart--
		n.matchStart--
		n.matchLength++
		c.matchLength--
		m--
		o--
		changed++
	}

	if changed == 0 {
		return
	}

	if c.matchLength <= 1 && n.matchLength != 2 {
		n.orgstart++
		*current = c
		*next = n
	}
}

// deflateMedium mirrors deflate_medium for level 6 (no early exit).
func (s *Deflater) deflateMedium(flush int) blockState {
	var currentMatch, nextMatch match

	for {
		var hashHead uint16

		// Make sure that we always have enough lookahead, except at the end
		// of the input file.
		if s.lookahead < minLookahead {
			s.fillWindow()
			if s.lookahead < minLookahead && flush == NoFlush {
				return needMore
			}
			if s.lookahead == 0 {
				break // flush the current block
			}
			nextMatch.matchLength = 0
		}

		// If we already have a future match from a previous round, use that.
		if nextMatch.matchLength > 0 {
			currentMatch = nextMatch
			nextMatch.matchLength = 0
		} else {
			hashHead = 0
			if s.lookahead >= wantMinMatch {
				hashHead = s.quickInsertString(s.strstart)
			}

			currentMatch.strstart = uint16(s.strstart)
			currentMatch.orgstart = currentMatch.strstart

			dist := int64(s.strstart) - int64(hashHead)
			if dist <= maxDist && dist > 0 && hashHead != 0 {
				currentMatch.matchLength = uint16(s.longestMatch(hashHead))
				currentMatch.matchStart = uint16(s.matchStart)
				if currentMatch.matchLength < wantMinMatch {
					currentMatch.matchLength = 1
				}
				if currentMatch.matchStart >= currentMatch.strstart {
					// This can happen due to some restarts.
					currentMatch.matchLength = 1
				}
			} else {
				// Set up the match to be a 1 byte literal.
				currentMatch.matchStart = 0
				currentMatch.matchLength = 1
			}
		}

		s.insertMatch(currentMatch)

		// Now, look ahead one.
		if s.lookahead > minLookahead && uint32(currentMatch.strstart)+uint32(currentMatch.matchLength) < windowSize-minLookahead {
			s.strstart = uint32(currentMatch.strstart) + uint32(currentMatch.matchLength)
			hashHead = s.quickInsertString(s.strstart)

			nextMatch.strstart = uint16(s.strstart)
			nextMatch.orgstart = nextMatch.strstart

			dist := int64(s.strstart) - int64(hashHead)
			if dist <= maxDist && dist > 0 && hashHead != 0 {
				nextMatch.matchLength = uint16(s.longestMatch(hashHead))
				nextMatch.matchStart = uint16(s.matchStart)
				if nextMatch.matchStart >= nextMatch.strstart {
					// This can happen due to some restarts.
					nextMatch.matchLength = 1
				}
				if nextMatch.matchLength < wantMinMatch {
					nextMatch.matchLength = 1
				} else {
					s.fizzleMatches(&currentMatch, &nextMatch)
				}
			} else {
				// Set up the match to be a 1 byte literal.
				nextMatch.matchStart = 0
				nextMatch.matchLength = 1
			}

			s.strstart = uint32(currentMatch.strstart)
		} else {
			nextMatch.matchLength = 0
		}

		// Now emit the current match.
		bflush := s.emitMatch(currentMatch)

		// Move the "cursor" forward.
		s.strstart += uint32(currentMatch.matchLength)

		if bflush != 0 {
			s.flushBlockOnly(false)
			if len(s.out) == 0 {
				return needMore
			}
		}
	}
	if s.strstart < stdMinMatch-1 {
		s.insert = s.strstart
	} else {
		s.insert = stdMinMatch - 1
	}
	if flush == Finish {
		s.flushBlockOnly(true)
		if len(s.out) == 0 {
			return finishStarted
		}
		return finishDone
	}
	if s.symNext != 0 {
		s.flushBlockOnly(false)
		if len(s.out) == 0 {
			return needMore
		}
	}
	return blockDone
}
