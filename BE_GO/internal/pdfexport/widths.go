package pdfexport

import "sort"

// elementWidth is Table._elementWidth of a cell holding flowables: the
// widest minimum width (Paragraph.minWidth: its widest word).
func elementWidth(cell tableCell) (float64, bool) {
	widest := 0.0
	for _, f := range cell.flows {
		p, ok := f.(*paragraph)
		if !ok {
			return 0, false
		}
		if w := p.minWidth(); w > widest {
			widest = w
		}
	}
	return widest, true
}

// preliminaryWidths is Table._calcPreliminaryWidths for a table none of
// whose column widths is given and whose cells are paragraphs: every column
// starts from its minimum (the widest word and the paddings); spare width
// is shared towards equal columns, a shortage shrinks every column by the
// same ratio.
func (t *table) preliminaryWidths(c *canvas, availWidth float64) ([]float64, error) {
	n := t.ncols()
	minimums := make([]float64, n)
	totalMinimum := 0.0
	for j := 0; j < n; j++ {
		final := 0.0
		for i, row := range t.cells {
			if !row[j].list {
				return nil, ErrNotPorted
			}
			w, ok := elementWidth(row[j])
			if !ok {
				return nil, ErrNotPorted
			}
			s := t.styles[i][j]
			w += s.leftPadding + s.rightPadding
			if w > final {
				final = w
			}
		}
		minimums[j] = final
		totalMinimum += final
	}
	widths := make([]float64, n)
	remaining := availWidth - (totalMinimum + 0.0)
	if remaining <= 0 {
		adj := 1.0
		if availWidth > 0 {
			adj = availWidth / totalMinimum
		}
		for j, minimum := range minimums {
			widths[j] = minimum * adj
		}
		return widths, nil
	}
	defaultWeight := 100.0 / float64(n)
	defaultDesired := (defaultWeight / 100.0) * availWidth
	type wish struct {
		disappointment, minimum, desired float64
		column                           int
	}
	var wishes []wish
	totalDesired := 0.0
	effectiveRemaining := remaining
	for j, minimum := range minimums {
		desired := defaultDesired
		if desired == 0 {
			desired = 1
		}
		if desired <= minimum {
			widths[j] = minimum
			continue
		}
		wishes = append(wishes, wish{desired - minimum, minimum, desired, j})
		totalDesired += desired
		effectiveRemaining += minimum
	}
	if len(wishes) == 0 {
		return widths, nil
	}
	proportion := effectiveRemaining / totalDesired
	sort.SliceStable(wishes, func(a, b int) bool {
		x, y := wishes[a], wishes[b]
		if x.disappointment != y.disappointment {
			return x.disappointment < y.disappointment
		}
		if x.minimum != y.minimum {
			return x.minimum < y.minimum
		}
		if x.desired != y.desired {
			return x.desired < y.desired
		}
		return x.column < y.column
	})
	var final []wish
	for _, w := range wishes {
		if adjusted := proportion * w.desired; adjusted < w.minimum {
			widths[w.column] = w.minimum
			totalDesired -= w.desired
			effectiveRemaining -= w.minimum
			if totalDesired != 0 {
				proportion = effectiveRemaining / totalDesired
			}
		} else {
			final = append(final, w)
		}
	}
	for _, w := range final {
		widths[w.column] = proportion * w.desired
	}
	return widths, nil
}
