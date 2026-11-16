package dto

import (
	"fmt"
	"runtime"
	"strconv"
)

// strftimeGOOS is runtime.GOOS; tests set it to exercise both C libraries, as
// the FatturaPA package does for its own strftime.
var strftimeGOOS = runtime.GOOS

// PythonStrftimeYear is CPython's strftime('%Y') of a year: the C library
// writes the year, and below 1000 glibc (the Linux images) leaves it unpadded
// ('999', '1') while the BSD library (macOS) pads it to four digits ('0999',
// '0001'). A negative year (never produced by datetime, whose MINYEAR is 1)
// follows the same rule: glibc writes '-1', the BSD library '-001'. Django and
// Go run on the same platform, so Go follows its own (owner decision X16).
func PythonStrftimeYear(year int) string { return pythonStrftimeYear(year, strftimeGOOS) }

func pythonStrftimeYear(year int, platform string) string {
	if platform == "darwin" {
		return fmt.Sprintf("%04d", year)
	}
	return strconv.Itoa(year)
}
