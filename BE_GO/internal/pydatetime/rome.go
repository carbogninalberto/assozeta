package pydatetime

import (
	"sort"
	"time"
)

// romeOffset is pytz's utcoffset of Europe/Rome at a UTC instant.
func romeOffset(instant time.Time) int {
	seconds := instant.Unix()
	index := sort.Search(len(romeTransitions), func(i int) bool { return romeTransitions[i][0] > seconds })
	return int(romeTransitions[index-1][1])
}

// InRome is calendar_datetime_in_rome's astimezone(pytz.timezone('Europe/Rome')):
// naive values are UTC. The result is the Rome wall clock as a UTC-labelled
// time; ok is false when datetime's range overflows (OverflowError).
func (m Moment) InRome() (time.Time, bool) {
	instant, ok := m.UTCInstant()
	if !ok {
		return instant, false
	}
	local := instant.Add(time.Duration(romeOffset(instant)) * time.Second)
	if local.Year() < 1 || local.Year() > 9999 {
		return local, false
	}
	return local, true
}
