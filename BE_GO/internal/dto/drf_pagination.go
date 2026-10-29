package dto

import (
	"errors"
	"strings"
)

// ErrDRFInvalidPage is PageNumberPagination's NotFound("Invalid page.").
var ErrDRFInvalidPage = errors.New("invalid page")

// DRFPage is the resolved page of a Django Paginator with orphans=0 and
// allow_empty_first_page=True.
type DRFPage struct {
	Number, Size, Pages, Count int64
}

func (p DRFPage) Offset() int64 { return (p.Number - 1) * p.Size }

// DRFPageSize is get_page_size with page_size_query_param: a strictly
// positive int(value), else the default (max_page_size is unset).
func DRFPageSize(raw any, present bool, fallback int64) int64 {
	if !present {
		return fallback
	}
	text, _ := raw.(string)
	size, err := PythonIntPrep(text)
	if err != nil || size <= 0 {
		return fallback
	}
	return size
}

// DRFPageNumber is paginate_queryset's page lookup: an absent or empty value
// is page 1, "last" the last page, otherwise int(value) within the pages.
func DRFPageNumber(raw any, present bool, count, size int64) (DRFPage, error) {
	pages := int64(1)
	if count > 0 {
		pages = (count + size - 1) / size
	}
	page := DRFPage{Size: size, Pages: pages, Count: count}
	text, _ := raw.(string)
	if !present || text == "" {
		text = "1"
	}
	if text == "last" {
		page.Number = pages
		return page, nil
	}
	number, err := PythonIntPrep(strings.Clone(text))
	if err != nil || number < 1 || number > pages {
		return page, ErrDRFInvalidPage
	}
	page.Number = number
	return page, nil
}
