package service

import (
	"strings"
	"testing"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

// TestCompensationNotesDecimalEdgeCases pins the notes
// instructor_hours_add_compensation composes for the decimal edge cases:
// str(Decimal(str(amount))) for the amount, and the default-context product
// with percentage_billing / 100 (a NaN payload cut to 28 digits, subnormal
// products rounded at Etiny, an adjusted exponent above Emax raising).
func TestCompensationNotesDecimalEdgeCases(t *testing.T) {
	percentage := "50.00"
	hour := func(day, amount, data string) domain.InstructorHour {
		return domain.InstructorHour{Date: day, Hours: "1.00", Amount: amount, CompensationType: "percentage",
			PercentageBilling: &percentage, CalculationData: &data}
	}
	notes, failed, err := compensationNotes([]domain.InstructorHour{
		hour("2026-09-22", "8.00", `[{"amount":"NaN12345678901234567890123456789012345","full_athlete_name":"N","course":"C"}]`),
		hour("2026-09-24", "2.00", `[{"amount":"1E-1000030","full_athlete_name":"S","course":"C"}]`),
		hour("2026-09-25", "3.00", `[{"amount":"5E-1000026","full_athlete_name":"T","course":"C"}]`),
	})
	if err != nil || failed != -1 {
		t.Fatalf("notes: failed %v, err %v", failed, err)
	}
	for _, want := range []string{
		"Importo: NaN12345678901234567890123456789012345 € * 50.00 % = NaN8901234567890123456789012345 €",
		"Importo: 1E-1000030 € * 50.00 % = 0E-1000026 €",
		"Importo: 5E-1000026 € * 50.00 % = 2E-1000026 €",
	} {
		if !strings.Contains(notes, want) {
			t.Fatalf("notes missing %q:\n%s", want, notes)
		}
	}
	// 1E1000000000 is a valid Decimal; its product overflows the context.
	_, failed, err = compensationNotes([]domain.InstructorHour{
		hour("2026-09-23", "5.00", `[{"amount":"1E1000000000","full_athlete_name":"A","course":"C"}]`),
	})
	if err != nil || failed != 0 {
		t.Fatalf("overflow: failed %v, err %v, want the exception branch", failed, err)
	}
}
