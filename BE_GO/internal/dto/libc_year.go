package dto

import (
	"runtime"
	"strconv"
)

// LibcYear is strftime('%Y') of a year from 1 to 9999 as the C library of
// the running platform writes it: glibc writes the digits as they are, the
// BSD library (macOS) pads them to four.
func LibcYear(year int) string { return libcYear(year, runtime.GOOS) }

func libcYear(year int, platform string) string {
	text := strconv.Itoa(year)
	if platform == "linux" {
		return text
	}
	for len(text) < 4 {
		text = "0" + text
	}
	return text
}
