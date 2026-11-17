package importeffects

import (
	"strings"
	"testing"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
)

// TestDecimalCorpus pins _parse_value's Decimal(value) (if value else None) and
// DecimalField.get_db_prep_save for Payment.amount. The expected strings come
// from CPython 3.13 and Django 5.2.18 (psycopg2 binds str(Decimal), so the
// bound text is str(value) as well).
func TestDecimalCorpus(t *testing.T) {
	plan, err := LoadPlan()
	if err != nil {
		t.Fatal(err)
	}
	field := plan.Model("Payment").Field("amount")
	cases := []struct {
		json  string
		ok    bool
		value string
		bound string
	}{
		{"\" 1 \"", true, "1", "1"},
		{"\"1_0\"", true, "10", "10"},
		{"\"1__0\"", true, "10", "10"},
		{"\"_1\"", true, "1", "1"},
		{"\"1_\"", true, "1", "1"},
		{"\"+1\"", true, "1", "1"},
		{"\"-0\"", true, "-0", "-0"},
		{"\"-0.0\"", true, "-0.0", "-0.0"},
		{"\".5\"", true, "0.5", "0.5"},
		{"\"5.\"", true, "5", "5"},
		{"\"1e5\"", true, "1E+5", "1E+5"},
		{"\"1E+05\"", true, "1E+5", "1E+5"},
		{"\"1e-5\"", true, "0.00001", "0.00001"},
		{"\"Inf\"", true, "Infinity", "ERR ['Il valore \"Infinity\" deve essere un numero decimale.']"},
		{"\"-inf\"", true, "-Infinity", "ERR ['Il valore \"-Infinity\" deve essere un numero decimale.']"},
		{"\"nan\"", true, "NaN", "ERR ['Il valore \"NaN\" deve essere un numero decimale.']"},
		{"\"sNaN\"", true, "sNaN", "ERR ['Il valore \"sNaN\" deve essere un numero decimale.']"},
		{"\"-NaN\"", true, "-NaN", "ERR ['Il valore \"-NaN\" deve essere un numero decimale.']"},
		{"\"1,5\"", false, "[<class 'decimal.ConversionSyntax'>]", ""},
		{"\"\\u0661\\u0662\"", true, "12", "12"},
		{"\"0x10\"", false, "[<class 'decimal.ConversionSyntax'>]", ""},
		{"\"1d\"", false, "[<class 'decimal.ConversionSyntax'>]", ""},
		{"\"1e9999999999\"", true, "1E+9999999999", "1E+9999999999"},
		{"\"1e99999999999999999999\"", false, "[<class 'decimal.InvalidOperation'>]", ""},
		{"\"12345678.90\"", true, "12345678.90", "12345678.90"},
		{"\"0.00\"", true, "0.00", "0.00"},
		{"\"1_000.500\"", true, "1000.500", "1000.500"},
		{"\"\"", true, "null", ""},
		{"\"abc\"", false, "[<class 'decimal.ConversionSyntax'>]", ""},
		{"\"1e-400\"", true, "1E-400", "1E-400"},
		{"0", true, "null", ""},
		{"1", true, "1", "1"},
		{"true", true, "1", "1"},
		{"false", true, "null", ""},
		{"0.1", true, "0.1000000000000000055511151231257827021181583404541015625", "0.1000000000000000055511151231257827021181583404541015625"},
		{"1e+308", true, "100000000000000001097906362944045541740492309677311846336810682903157585404911491537163328978494688899061249669721172515611590283743140088328307009198146046031271664502933027185697489699588559043338384466165001178426897626212945177628091195786707458122783970171784415105291802893207873272974885715430223118336", "100000000000000001097906362944045541740492309677311846336810682903157585404911491537163328978494688899061249669721172515611590283743140088328307009198146046031271664502933027185697489699588559043338384466165001178426897626212945177628091195786707458122783970171784415105291802893207873272974885715430223118336"},
		{"5e-324", true, "4.940656458412465441765687928682213723650598026143247644255856825006755072702087518652998363616359923797965646954457177309266567103559397963987747960107818781263007131903114045278458171678489821036887186360569987307230500063874091535649843873124733972731696151400317153853980741262385655911710266585566867681870395603106249319452715914924553293054565444011274801297099995419319894090804165633245247571478690147267801593552386115501348035264934720193790268107107491703332226844753335720832431936092382893458368060106011506169809753078342277318329247904982524730776375927247874656084778203734469699533647017972677717585125660551199131504891101451037862738167250955837389733598993664809941164205702637090279242767544565229087538682506419718265533447265625E-324", "4.940656458412465441765687928682213723650598026143247644255856825006755072702087518652998363616359923797965646954457177309266567103559397963987747960107818781263007131903114045278458171678489821036887186360569987307230500063874091535649843873124733972731696151400317153853980741262385655911710266585566867681870395603106249319452715914924553293054565444011274801297099995419319894090804165633245247571478690147267801593552386115501348035264934720193790268107107491703332226844753335720832431936092382893458368060106011506169809753078342277318329247904982524730776375927247874656084778203734469699533647017972677717585125660551199131504891101451037862738167250955837389733598993664809941164205702637090279242767544565229087538682506419718265533447265625E-324"},
	}
	for _, c := range cases {
		raw, err := dto.ParseProfileTablesJSON([]byte(c.json))
		if err != nil {
			t.Fatalf("decode %s: %v", c.json, err)
		}
		value, err := ParseArchived(field, raw)
		if !c.ok {
			if err == nil || err.Error() != c.value {
				t.Fatalf("%s: err %v, want %s", c.json, err, c.value)
			}
			continue
		}
		if err != nil {
			t.Fatalf("%s: %v", c.json, err)
		}
		got := "null"
		if !value.IsNone() {
			got = value.S
		}
		if got != c.value {
			t.Fatalf("%s: value %q, want %q", c.json, got, c.value)
		}
		bound, err := dbValue(field, value)
		if strings.HasPrefix(c.bound, "ERR ") {
			if err == nil || err.Error() != strings.TrimPrefix(c.bound, "ERR ") {
				t.Fatalf("%s: prep err %v, want %s", c.json, err, c.bound)
			}
			continue
		}
		if err != nil {
			t.Fatalf("%s: prep %v", c.json, err)
		}
		if bound != nil {
			text, _ := bound.(string)
			if text != c.bound {
				t.Fatalf("%s: bound %v, want %q", c.json, bound, c.bound)
			}
		}
	}
}
