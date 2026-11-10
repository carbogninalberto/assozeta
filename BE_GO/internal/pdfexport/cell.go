package pdfexport

import (
	"strings"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/xlsxexport"
)

// CellOf is the table cell of a Python value: its str(), None, or a list
// (an Object whose repr starts with '['; a dict is drawn as its repr).
func CellOf(value xlsxexport.Value) Cell {
	switch value.Kind {
	case xlsxexport.None:
		return Cell{None: true}
	case xlsxexport.Object:
		if strings.HasPrefix(value.Text, "[") || strings.HasPrefix(value.Text, "(") {
			return Cell{Text: value.Text, Sequence: true, Empty: strings.Trim(value.Text, "[](), ") == ""}
		}
	}
	return Cell{Text: value.PythonStr()}
}

// RowsOf turns columns of values into the rows zip(*columns) yields.
func RowsOf(columns [][]xlsxexport.Value) [][]Cell {
	if len(columns) == 0 {
		return nil
	}
	n := len(columns[0])
	for _, column := range columns {
		if len(column) < n {
			n = len(column)
		}
	}
	rows := make([][]Cell, n)
	for i := range rows {
		rows[i] = make([]Cell, len(columns))
		for j, column := range columns {
			rows[i][j] = CellOf(column[i])
		}
	}
	return rows
}
