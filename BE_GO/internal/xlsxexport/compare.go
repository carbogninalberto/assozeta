package xlsxexport

import (
	"archive/zip"
	"bytes"
	"fmt"
	"io"
	"regexp"
	"time"
)

// Part is one zip entry of a workbook as the parity standard compares it.
type Part struct {
	Name     string
	Method   uint16
	Flags    uint16
	External uint32
	Data     []byte
}

// Parts lists a workbook's zip entries in order with their uncompressed data.
func Parts(workbook []byte) ([]Part, error) {
	reader, err := zip.NewReader(bytes.NewReader(workbook), int64(len(workbook)))
	if err != nil {
		return nil, err
	}
	parts := make([]Part, 0, len(reader.File))
	for _, file := range reader.File {
		handle, err := file.Open()
		if err != nil {
			return nil, err
		}
		data, err := io.ReadAll(handle)
		handle.Close()
		if err != nil {
			return nil, err
		}
		parts = append(parts, Part{Name: file.Name, Method: file.Method, Flags: file.Flags, External: file.ExternalAttrs, Data: data})
	}
	return parts, nil
}

var coreStamp = regexp.MustCompile(`(<dcterms:(?:created|modified) xsi:type="dcterms:W3CDTF">)(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ)(</dcterms:(?:created|modified)>)`)

// CoreTimes returns docProps/core.xml with its dcterms:created and
// dcterms:modified values blanked, and the values.
func CoreTimes(core []byte) ([]byte, []time.Time, error) {
	var stamps []time.Time
	var failure error
	blank := coreStamp.ReplaceAllFunc(core, func(match []byte) []byte {
		groups := coreStamp.FindSubmatch(match)
		stamp, err := time.Parse("2006-01-02T15:04:05Z", string(groups[2]))
		if err != nil {
			failure = err
		}
		stamps = append(stamps, stamp)
		return append(append(append([]byte{}, groups[1]...), "-"...), groups[3]...)
	})
	if failure == nil && len(stamps) != 2 {
		failure = fmt.Errorf("core.xml has %d timestamps", len(stamps))
	}
	return blank, stamps, failure
}

// Equivalent applies the XLSX parity standard: the same entry names in the
// same order with the same compression method and flags, byte-identical
// parts except core.xml's created/modified values, which must fall within
// [from, to] at second precision (from is truncated). Zip time stamps are not
// compared. The compression of the bytes themselves is free.
func Equivalent(expected, actual []byte, from, to time.Time) error {
	want, err := Parts(expected)
	if err != nil {
		return fmt.Errorf("expected workbook: %w", err)
	}
	got, err := Parts(actual)
	if err != nil {
		return fmt.Errorf("actual workbook: %w", err)
	}
	if len(want) != len(got) {
		return fmt.Errorf("entries: expected %d, got %d", len(want), len(got))
	}
	for i := range want {
		w, g := want[i], got[i]
		if w.Name != g.Name || w.Method != g.Method || w.Flags != g.Flags {
			return fmt.Errorf("entry %d: expected %s method %d flags %d, got %s method %d flags %d", i, w.Name, w.Method, w.Flags,
				g.Name, g.Method, g.Flags)
		}
		wd, gd := w.Data, g.Data
		if w.Name == "docProps/core.xml" {
			var stamps []time.Time
			if wd, _, err = CoreTimes(wd); err != nil {
				return fmt.Errorf("expected core.xml: %w", err)
			}
			if gd, stamps, err = CoreTimes(gd); err != nil {
				return fmt.Errorf("actual core.xml: %w", err)
			}
			for _, stamp := range stamps {
				if stamp.Before(from.UTC().Truncate(time.Second)) || stamp.After(to.UTC()) {
					return fmt.Errorf("core.xml time %s outside [%s, %s]", stamp, from.UTC(), to.UTC())
				}
			}
		}
		if !bytes.Equal(wd, gd) {
			return fmt.Errorf("part %s differs: %s", w.Name, firstDifference(wd, gd))
		}
	}
	return nil
}

func firstDifference(expected, actual []byte) string {
	i := 0
	for i < len(expected) && i < len(actual) && expected[i] == actual[i] {
		i++
	}
	clip := func(data []byte) string {
		start, end := max(0, i-80), min(len(data), i+120)
		return string(data[start:end])
	}
	return fmt.Sprintf("at byte %d\n  expected: %q\n  actual:   %q", i, clip(expected), clip(actual))
}
