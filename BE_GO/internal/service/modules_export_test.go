package service

import (
	"encoding/json"
	"strings"
	"testing"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/xlsxexport"
)

// A response value that is a JSON integer: json.loads gives an int of any
// size, and the frame keeps one beyond int64 for xlsxwriter's float(number).
func TestModulesExportValueIntegers(t *testing.T) {
	huge := "1" + strings.Repeat("0", 400)
	for _, item := range []struct {
		text string
		want xlsxexport.Value
	}{
		{"0", xlsxexport.IntValue(0)},
		{"30", xlsxexport.IntValue(30)},
		{"-9223372036854775808", xlsxexport.IntValue(-9223372036854775808)},
		{"9223372036854775808", xlsxexport.BigIntValue("9223372036854775808")},
		{"123456789012345678901234567890", xlsxexport.BigIntValue("123456789012345678901234567890")},
		{huge, xlsxexport.BigIntValue(huge)},
	} {
		got, err := modulesExportValue(json.Number(item.text))
		if err != nil || got != item.want {
			t.Fatalf("%s: got (%+v, %v), want %+v", item.text, got, err, item.want)
		}
	}
}
