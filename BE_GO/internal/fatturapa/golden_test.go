package fatturapa

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"testing"
	"time"
)

// testdata/a38_renders.json comes from TESTS/adapters/go/fatturapa_golden.py:
// the real generate_invoice_{html,xml,json} on authored and seeded invoices.
type goldenFile struct {
	Progressivi [][3]any `json:"progressivi"`
	Cases       []struct {
		Name        string                    `json:"name"`
		Fields      map[string]any            `json:"fields"`
		Unsupported *string                   `json:"unsupported"`
		Progressivo string                    `json:"progressivo"`
		Outputs     map[string]map[string]any `json:"outputs"`
	} `json:"cases"`
}

func goldenInvoice(t *testing.T, fields map[string]any) Invoice {
	text := func(key string) string { return fields[key].(string) }
	optional := func(key string) *string {
		if value, ok := fields[key].(string); ok {
			return &value
		}
		return nil
	}
	integer := func(key string) *int64 {
		if value, ok := fields[key].(float64); ok {
			n := int64(value)
			return &n
		}
		return nil
	}
	date := func(key string) *time.Time {
		value, ok := fields[key].(string)
		if !ok {
			return nil
		}
		parsed, err := time.Parse("2006-01-02", value)
		if err != nil {
			t.Fatal(err)
		}
		return &parsed
	}
	return Invoice{
		Prefix: optional("prefix"), Number: integer("number"), FiscalYear: integer("fiscal_year"),
		AssignorPrefixVAT: text("assignor_prefix_vat_number"), AssignorVAT: optional("assignor_vat_number"),
		AssignorTaxCode: optional("assignor_tax_code"), AssignorDenomination: optional("assignor_denomination"),
		AssignorFiscalRegime: text("assignor_fiscal_regime"), AssignorAddress: text("assignor_address"),
		AssignorPostalCode: text("assignor_postal_code"), AssignorCity: text("assignor_city"),
		AssignorProvince: text("assignor_province"), AssignorCountry: text("assignor_country"),
		AssignorEmail: optional("assignor_contact_email"), AssignorPhone: optional("assignor_contact_phone"),
		AssignorFax:         optional("assignor_fax"),
		TransferorPrefixVAT: text("transferor_prefix_vat_number"), TransferorVAT: optional("transferor_vat_number"),
		TransferorTaxCode: optional("transferor_tax_code"), TransferorDenomination: optional("transferor_denomination"),
		TransferorFiscalRegime: text("transferor_fiscal_regime"), TransferorAddress: text("transferor_address"),
		TransferorPostalCode: text("transferor_postal_code"), TransferorCity: text("transferor_city"),
		TransferorProvince: text("transferor_province"), TransferorCountry: text("transferor_country"),
		TransferorEmail: optional("transferor_contact_email"), TransferorPhone: optional("transferor_contact_phone"),
		TransferorFax:            optional("transferor_fax"),
		CountryTransmitterPrefix: text("country_transmitter_prefix"), IDTransmitter: optional("id_transmitter"),
		TransmittingFormat: text("transmitting_format"), DocumentType: text("document_type"), Currency: text("currency"),
		TransmittingDate: date("transmitting_date"), Causal: optional("causal"), Lines: optional("lines"),
		PaymentCondition: optional("payment_condition"), PaymentModality: optional("payment_modality"),
		PaymentExpiryDate: *date("payment_expiry_date"),
	}
}

func TestGoldenRenders(t *testing.T) {
	raw, err := os.ReadFile(filepath.Join("testdata", "a38_renders.json"))
	if err != nil {
		t.Fatal(err)
	}
	var golden goldenFile
	if err := json.Unmarshal(raw, &golden); err != nil {
		t.Fatal(err)
	}
	for _, p := range golden.Progressivi {
		if got := Progressivo(int64(p[0].(float64)), int(p[1].(float64))); got != p[2].(string) {
			t.Errorf("Progressivo(%v, %v) = %q, want %q", p[0], p[1], got, p[2])
		}
	}
	dump := os.Getenv("FATTURAPA_DUMP")
	counts := map[string]int{}
	for _, c := range golden.Cases {
		inv := goldenInvoice(t, c.Fields)
		renders := map[string]func() (string, error){
			"html": func() (string, error) { return HTML(inv) },
			"xml":  func() (string, error) { return XML(inv, c.Progressivo) },
			"json": func() (string, error) { return JSON(inv, c.Progressivo) },
		}
		for kind, render := range renders {
			want := c.Outputs[kind]
			got, err := render()
			switch {
			case errors.Is(err, ErrUnsupported):
				if c.Unsupported == nil {
					t.Errorf("%s %s: unexpected ErrUnsupported", c.Name, kind)
				}
				counts["unsupported"]++
			case want["raised"] != nil:
				if !errors.Is(err, ErrInvalid) {
					t.Errorf("%s %s: Python raised %v, Go gave %v", c.Name, kind, want["raised"], err)
				}
				counts["invalid"]++
			case err != nil:
				t.Errorf("%s %s: %v", c.Name, kind, err)
			default:
				sum := sha256.Sum256([]byte(got))
				if hex.EncodeToString(sum[:]) != want["sha256"] || float64(len(got)) != want["length"] {
					t.Errorf("%s %s: bytes differ (len %d, want %v)", c.Name, kind, len(got), want["length"])
					if dump != "" {
						_ = os.MkdirAll(dump, 0o755)
						_ = os.WriteFile(filepath.Join(dump, c.Name+"."+kind), []byte(got), 0o644)
					}
				}
				counts["identical"]++
			}
		}
		if c.Unsupported != nil {
			if _, err := HTML(inv); !errors.Is(err, ErrUnsupported) {
				t.Errorf("%s: tagged unsupported but Go answered %v", c.Name, err)
			}
		}
	}
	t.Logf("outputs: %v", counts)
	if counts["identical"] < 600 {
		t.Errorf("only %d byte-identical outputs", counts["identical"])
	}
}
