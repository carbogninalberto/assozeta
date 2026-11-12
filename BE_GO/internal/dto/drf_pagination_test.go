package dto

import (
	"errors"
	"testing"
)

func TestDRFPagination(t *testing.T) {
	for _, tc := range []struct {
		raw     any
		present bool
		want    int64
	}{{nil, false, 10}, {"5", true, 5}, {" 7 ", true, 7}, {"1_0", true, 10}, {"0", true, 10}, {"-3", true, 10}, {"x", true, 10}, {"2.0", true, 10}} {
		if got := DRFPageSize(tc.raw, tc.present, 10); got != tc.want {
			t.Errorf("size %v: got %d want %d", tc.raw, got, tc.want)
		}
	}
	for _, tc := range []struct {
		raw          any
		present      bool
		count        int64
		number, page int64
		invalid      bool
	}{
		{nil, false, 0, 1, 1, false}, {"", true, 12, 1, 3, false}, {"last", true, 12, 3, 3, false}, {"last", true, 0, 1, 1, false},
		{" 2 ", true, 12, 2, 3, false}, {"0", true, 12, 0, 3, true}, {"4", true, 12, 0, 3, true}, {"due", true, 12, 0, 3, true},
		{"1", true, 0, 1, 1, false}, {"2", true, 0, 0, 1, true},
	} {
		page, err := DRFPageNumber(tc.raw, tc.present, tc.count, 5)
		if tc.invalid != errors.Is(err, ErrDRFInvalidPage) || page.Pages != tc.page || (!tc.invalid && page.Number != tc.number) {
			t.Errorf("page %v of %d: got %+v %v", tc.raw, tc.count, page, err)
		}
	}
}
