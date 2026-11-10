package xlsxexport

import (
	"math"
	"regexp"
	"strconv"
	"strings"
	"time"
)

// ReportSheet is one sheet of a saved-report workbook after
// format_export_data: its title, the column labels and the values by column.
type ReportSheet struct {
	Title   string
	Columns []string
	Data    [][]Value
}

var openpyxlInvalidTitle = regexp.MustCompile(`[\\*?:/\[\]]`)

// sheetTitles tracks the titles pandas hands openpyxl: an invalid or empty
// one raises, one that differs from an earlier title only by case raises
// (openpyxl renames the sheet and pandas does not find it), and an equal one
// would write into the earlier sheet, which is not ported.
type sheetTitles []string

func (titles *sheetTitles) add(title string) error {
	if title == "" || openpyxlInvalidTitle.MatchString(title) {
		return ErrDjango
	}
	for _, earlier := range *titles {
		if earlier == title {
			return ErrNotPorted
		}
		if strings.EqualFold(earlier, title) {
			if strings.ToLower(earlier) == strings.ToLower(title) {
				return ErrDjango
			}
			return ErrNotPorted
		}
	}
	*titles = append(*titles, title)
	return nil
}

func pySlice(text string, n int) string {
	runes := []rune(text)
	if n < 0 {
		n = 0
	}
	if len(runes) <= n {
		return text
	}
	return string(runes[:n])
}

// reportSheet is one DataFrame of XLSXExporter written by to_excel: the
// numeric coercion of every column whose values all convert, Int64 for
// all-integer columns and the widths min(max(len(str(value)), len(label)) +
// 2, 50).
func reportSheet(title string, columns []string, data [][]Value) (opSheet, error) {
	if len(data) != len(columns) || len(columns) == 0 || len(columns) > 702 {
		return opSheet{}, ErrNotPorted
	}
	rows := len(data[0])
	uses := map[string]int{}
	for _, name := range columns {
		uses[name]++
	}
	frame := make([]frameColumn, len(columns))
	widths := make([]string, len(columns))
	for i := range columns {
		if len(data[i]) != rows || rows == 0 {
			return opSheet{}, ErrNotPorted
		}
		for _, value := range data[i] {
			if value.Kind == None || value.Kind == Bool || (value.Kind == Float && (math.IsNaN(value.Float) || math.IsInf(value.Float, 0))) {
				return opSheet{}, ErrNotPorted // format_export_data leaves no None or bool
			}
		}
		column, err := inferColumn(data[i])
		if err != nil {
			return opSheet{}, err
		}
		frame[i] = column
		if uses[columns[i]] > 1 {
			widths[i] = strconv.Itoa(min(max(rows, pyLen(columns[i]))+2, 50))
			continue
		}
		object := false
		for _, value := range data[i] {
			object = object || value.Kind == Object
		}
		if !object { // a list or dict never converts: the column stays as it is
			if column, err = excelUtilsCoerce(column); err != nil {
				return opSheet{}, err
			}
			frame[i] = column
		}
		longest := 0
		for _, value := range column.values {
			longest = max(longest, pyLen(frameStr(column.dtype, value)))
		}
		widths[i] = strconv.Itoa(min(max(longest, pyLen(columns[i]))+2, 50))
	}
	return frameSheet(title, columns, frame, rows, widths)
}

// frameSheet is DataFrame.to_excel(index=False) through openpyxl.
func frameSheet(title string, columns []string, frame []frameColumn, rows int, widths []string) (opSheet, error) {
	sheet := opSheet{title: title, widths: widths}
	header := opRow{row: 1}
	for i, name := range columns {
		cell, err := openpyxlString(i+1, name, true)
		if err != nil {
			return opSheet{}, err
		}
		header.cells = append(header.cells, cell)
	}
	sheet.rows = append(sheet.rows, header)
	for r := 0; r < rows; r++ {
		row := opRow{row: r + 2}
		for c, column := range frame {
			cell, err := pandasOpenpyxlCell(c+1, column.dtype, column.values[r])
			if err != nil {
				return opSheet{}, err
			}
			row.cells = append(row.cells, cell)
		}
		sheet.rows = append(sheet.rows, row)
	}
	return sheet, nil
}

// ReportWorkbook is XLSXExporter.export: one sheet named title[:31], or
// Sheet1 without a title.
func ReportWorkbook(sheet ReportSheet, now time.Time) ([]byte, error) {
	name := "Sheet1"
	if sheet.Title != "" {
		name = pySlice(sheet.Title, 31)
	}
	var titles sheetTitles
	if err := titles.add(name); err != nil {
		return nil, err
	}
	built, err := reportSheet(name, sheet.Columns, sheet.Data)
	if err != nil {
		return nil, err
	}
	return openpyxlPackage([]opSheet{built}, true, now)
}

// dedupTitle is the exporters' sheet naming: the title cut to 31 characters,
// a repeated one cut further and numbered ' (n)'.
func dedupTitle(used map[string]int, raw, cut string) string {
	if count, ok := used[cut]; ok {
		used[cut] = count + 1
		suffix := " (" + strconv.Itoa(count+1) + ")"
		return pySlice(raw, 31-pyLen(suffix)) + suffix
	}
	used[cut] = 1
	return cut
}

// ReportSheetsWorkbook is XLSXExporter.export_multi_sheet.
func ReportSheetsWorkbook(sheets []ReportSheet, now time.Time) ([]byte, error) {
	used := map[string]int{}
	var titles sheetTitles
	var out []opSheet
	for _, sheet := range sheets {
		name := dedupTitle(used, sheet.Title, pySlice(sheet.Title, 31))
		if err := titles.add(name); err != nil {
			return nil, err
		}
		built, err := reportSheet(name, sheet.Columns, sheet.Data)
		if err != nil {
			return nil, err
		}
		out = append(out, built)
	}
	if len(out) == 0 {
		return nil, ErrNotPorted
	}
	return openpyxlPackage(out, true, now)
}

// AttendanceRow is one enrolled person of an attendance matrix.
type AttendanceRow struct {
	Name       string
	Attendance []string
	Present    int64
}

// AttendanceCourse is one course matrix.
type AttendanceCourse struct {
	Course string
	Dates  []string
	Rows   []AttendanceRow
}

// attendanceFrame is the DataFrame of the dict rows: '#', [Corso,] the name,
// one column per distinct date label (a repeated label keeps its last value)
// and the count.
func attendanceFrame(title string, lead int, columns []string, frame []frameColumn, rows int, widths []string, dates int) (opSheet, error) {
	sheet, err := frameSheet(title, columns, frame, rows, widths)
	if err != nil {
		return opSheet{}, err
	}
	// The rotated alignment goes to the first row of len(dates) columns from
	// the first date column on, whatever they hold.
	head := &sheet.rows[0]
	for i := 0; i < dates; i++ {
		col := lead + i
		if col < len(head.cells) {
			head.cells[col].style = 2
		} else {
			head.cells = append(head.cells, opCell{col: col + 1, kind: 'n', style: 3})
		}
	}
	return sheet, nil
}

func courseSheet(title string, course AttendanceCourse) (opSheet, error) {
	if len(course.Rows) == 0 {
		return opSheet{}, ErrDjango // DataFrame([]) has no 'Nome e Cognome' column
	}
	columns := []string{"#", "Nome e Cognome"}
	index := map[string]int{}
	for _, date := range course.Dates {
		if _, ok := index[date]; !ok {
			if date == "#" || date == "Nome e Cognome" || date == "Presenze" {
				return opSheet{}, ErrNotPorted
			}
			index[date] = len(columns)
			columns = append(columns, date)
		}
	}
	columns = append(columns, "Presenze")
	frame := make([]frameColumn, len(columns))
	for i := range frame {
		frame[i] = frameColumn{dtype: dtObject, values: make([]Value, len(course.Rows))}
	}
	frame[0].dtype, frame[len(frame)-1].dtype = dtInt64, dtInt64
	longest := pyLen("Nome e Cognome")
	for r, row := range course.Rows {
		frame[0].values[r] = IntValue(int64(r + 1))
		frame[1].values[r] = StrValue(row.Name)
		longest = max(longest, pyLen(row.Name))
		for i := 2; i < len(columns)-1; i++ {
			frame[i].values[r] = StrValue("")
		}
		for i, date := range course.Dates {
			mark := ""
			if i < len(row.Attendance) {
				mark = row.Attendance[i]
			}
			frame[index[date]].values[r] = StrValue(mark)
		}
		frame[len(frame)-1].values[r] = IntValue(row.Present)
	}
	widths := []string{"6", strconv.Itoa(min(longest+2, 40))}
	for range course.Dates {
		widths = append(widths, "5")
	}
	widths = append(widths, "10")
	return attendanceFrame(title, 2, columns, frame, len(course.Rows), widths, len(course.Dates))
}

var (
	attendanceUnsafe = regexp.MustCompile(`[<>:"/\\|?*\x00-\x1f\[\]]`)
	attendanceDashes = regexp.MustCompile(`-+`)
)

const pyStripSpaces = " \u0085                 　"

func attendanceSheetName(name string) string {
	if name == "" {
		name = "Foglio"
	}
	name = attendanceDashes.ReplaceAllString(attendanceUnsafe.ReplaceAllString(name, "-"), "-")
	name = strings.Trim(strings.Trim(name, pyStripSpaces), "-")
	if name == "" {
		name = "Foglio"
	}
	return pySlice(name, 31)
}

// AttendanceWorkbook is export_attendance_matrix_xlsx: mode "single" (one
// course), "flat" (every course in one sheet with a Corso column) or
// "sheets" (one sheet per course).
func AttendanceWorkbook(mode string, courses []AttendanceCourse, now time.Time) ([]byte, error) {
	var titles sheetTitles
	var out []opSheet
	switch mode {
	case "single":
		if len(courses) != 1 {
			return nil, ErrNotPorted
		}
		name := pySlice(courses[0].Course, 31)
		if err := titles.add(name); err != nil {
			return nil, err
		}
		sheet, err := courseSheet(name, courses[0])
		if err != nil {
			return nil, err
		}
		out = append(out, sheet)
	case "sheets":
		used := map[string]int{}
		for _, course := range courses {
			cut := attendanceSheetName(course.Course)
			name := cut
			if count, ok := used[cut]; ok {
				used[cut] = count + 1
				suffix := " (" + strconv.Itoa(count+1) + ")"
				name = pySlice(cut, 31-pyLen(suffix)) + suffix
			} else {
				used[cut] = 1
			}
			// The frame is built (and fails without rows) before the sheet is named.
			if len(course.Rows) == 0 {
				return nil, ErrDjango
			}
			if err := titles.add(name); err != nil {
				return nil, err
			}
			sheet, err := courseSheet(name, course)
			if err != nil {
				return nil, err
			}
			out = append(out, sheet)
		}
	case "flat":
		var ordered []string
		seen := map[string]int{}
		for _, course := range courses {
			for _, date := range course.Dates {
				if _, ok := seen[date]; !ok {
					if date == "#" || date == "Corso" || date == "Nome e Cognome" || date == "Presenze" {
						return nil, ErrNotPorted
					}
					seen[date] = len(ordered) + 3
					ordered = append(ordered, date)
				}
			}
		}
		columns := append(append([]string{"#", "Corso", "Nome e Cognome"}, ordered...), "Presenze")
		frame := make([]frameColumn, len(columns))
		frame[0].dtype, frame[len(frame)-1].dtype = dtInt64, dtInt64
		corso, names := pyLen("Corso"), pyLen("Nome e Cognome")
		rows := 0
		for _, course := range courses {
			first := map[string]int{}
			for i, date := range course.Dates {
				if _, ok := first[date]; !ok {
					first[date] = i
				}
			}
			for r, row := range course.Rows {
				rows++
				frame[0].values = append(frame[0].values, IntValue(int64(r+1)))
				frame[1].values = append(frame[1].values, StrValue(course.Course))
				frame[2].values = append(frame[2].values, StrValue(row.Name))
				corso, names = max(corso, pyLen(course.Course)), max(names, pyLen(row.Name))
				for i, date := range ordered {
					mark := ""
					if at, ok := first[date]; ok && at < len(row.Attendance) {
						mark = row.Attendance[at]
					}
					frame[3+i].values = append(frame[3+i].values, StrValue(mark))
				}
				frame[len(frame)-1].values = append(frame[len(frame)-1].values, IntValue(row.Present))
			}
		}
		if rows == 0 {
			return nil, ErrDjango // DataFrame([]) has no 'Corso' column
		}
		widths := []string{"6", strconv.Itoa(min(corso+2, 40)), strconv.Itoa(min(names+2, 40))}
		for range ordered {
			widths = append(widths, "5")
		}
		widths = append(widths, "10")
		if err := titles.add("Presenze"); err != nil {
			return nil, err
		}
		sheet, err := attendanceFrame("Presenze", 3, columns, frame, rows, widths, len(ordered))
		if err != nil {
			return nil, err
		}
		out = append(out, sheet)
	default:
		return nil, ErrNotPorted
	}
	if len(out) == 0 {
		return nil, ErrNotPorted
	}
	return openpyxlPackage(out, true, now)
}
