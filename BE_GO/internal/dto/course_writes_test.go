package dto

import (
	"encoding/json"
	"testing"
)

func TestCourseUpdateDecimalHugeExponentsAndUnderflow(t *testing.T) {
	for _, tc := range []struct {
		value         any
		stored, audit string
		result        CourseSaveResult
	}{
		{json.Number("1e-400"), "0.00", "0.0", CourseSaveOK},
		{json.Number("-1e-400"), "0.00", "-0.0", CourseSaveOK},
		{"1E+10001", "", "1E+10001", CourseSaveFails},
		{"0E+20000", "0.00", "0E+20000", CourseSaveOK},
		{"0E+99999999999", "", "0E+99999999999", CourseSaveFails},
		{"1E-10001", "0.00", "1E-10001", CourseSaveOK},
		{"-1E-12000", "0.00", "-1E-12000", CourseSaveOK},
		{"1E-16384", "", "1E-16384", CourseSaveFails},
		{"0E-16384", "", "0E-16384", CourseSaveFails},
		{"1.000E-16381", "", "1.000E-16381", CourseSaveFails},
		{"1.000E-16380", "0.00", "1.000E-16380", CourseSaveOK},
		{"12E-16382", "0.00", "12E-16382", CourseSaveOK},
	} {
		stored, audit, result := CourseUpdateDecimal(tc.value)
		if stored != tc.stored || audit != tc.audit || result != tc.result {
			t.Errorf("%v: got (%q, %q, %d), want (%q, %q, %d)", tc.value, stored, audit, result, tc.stored, tc.audit, tc.result)
		}
	}
}

func TestCourseUpdateDateTimeOutsideUTCRaises(t *testing.T) {
	for _, text := range []string{"0001-01-01T00:00:00+01:00", "9999-12-31T23:59:59-01:00"} {
		if _, _, result := CourseUpdateDateTime(text); result != CourseSaveRaises {
			t.Errorf("%s: got %d, want CourseSaveRaises", text, result)
		}
	}
}
