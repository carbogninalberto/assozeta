package dto

import (
	"sort"
	"strings"
	"unicode"
)

// auditModelLabels is AuditLogEntrySerializer.MODEL_VERBOSE_NAMES.
var auditModelLabels = map[string]string{
	"payment":                       "Pagamento",
	"subscription":                  "Iscrizione",
	"course":                        "Corso",
	"coursesubscription":            "Iscrizione Corso",
	"coursesubscriptioninstallment": "Rata Corso",
	"associate":                     "Associato",
	"invoice":                       "Ricevuta",
	"invoicesuppliers":              "Fattura Fornitore",
	"invoicerows":                   "Riga Fattura",
	"customerinvoice":               "Fattura Cliente",
	"instructor":                    "Istruttore",
	"instructorhours":               "Ore Istruttore",
	"module":                        "Modulo",
	"moduleresponses":               "Risposta Modulo",
	"carnet":                        "Carnet",
	"carnetsubscription":            "Iscrizione Carnet",
	"medicalcertificate":            "Certificato Medico",
	"medicalappointments":           "Appuntamento Medico",
	"sportassociation":              "Associazione Sportiva",
	"supplierandcustomers":          "Fornitore/Cliente",
	"balancesheet":                  "Bilancio",
	"customaccounts":                "Conto Personalizzato",
	"customaccountstransfer":        "Trasferimento Conto",
	"tags":                          "Tag",
	"coursetags":                    "Tag Corso",
	"courselocation":                "Sede Corso",
	"subscriptionmembership":        "Tesseramento",
	"signature":                     "Firma",
	"attendanceregistry":            "Registro Presenze",
	"attendanceday":                 "Giornata Presenze",
	"globalcalendarevents":          "Evento Calendario",
	"reminders":                     "Promemoria",
	"group":                         "Gruppo",
	"family":                        "Famiglia",
	"campsandretreats":              "Campo/Ritiro",
	"campsandretreatsperiod":        "Periodo Campo",
	"campsandretreatssubscription":  "Iscrizione Campo",
	"paymentcategory":               "Categoria Pagamento",
	"vatmanagement":                 "Gestione IVA",
	"nurturingemailsplan":           "Piano Email Nurturing",
	"nurturingemails":               "Email Nurturing",
	"billingplan":                   "Piano Abbonamento",
	"billingsubscription":           "Abbonamento Billing",
	"billingpayment":                "Pagamento Billing",
	"user":                          "Utente",
	"accesstoken":                   "Token Accesso",
	"message":                       "Messaggio",
	"automationworkflow":            "Automazione",
	"messagetransaction":            "Invio Messaggio",
	"communicationconfiguration":    "Configurazione Comunicazioni",
}

// PythonTitle is str.title(): a cased rune following a cased rune is
// lowercased, any other cased rune is uppercased.
func PythonTitle(text string) string {
	var out strings.Builder
	previous := false
	for _, r := range text {
		cased := unicode.IsUpper(r) || unicode.IsLower(r) || unicode.IsTitle(r)
		if cased && previous {
			out.WriteRune(unicode.ToLower(r))
		} else if cased {
			out.WriteRune(unicode.ToTitle(r))
		} else {
			out.WriteRune(r)
		}
		previous = cased
	}
	return out.String()
}

type AuditModel struct {
	Name  string `json:"name"`
	Label string `json:"label"`
}

// AuditModelsFrom labels the content type models and sorts them by label
// (Python's stable sort on code points).
func AuditModelsFrom(names []string) []AuditModel {
	models := make([]AuditModel, 0, len(names))
	for _, name := range names {
		label, ok := auditModelLabels[name]
		if !ok {
			label = PythonTitle(strings.ReplaceAll(name, "_", " "))
		}
		models = append(models, AuditModel{Name: name, Label: label})
	}
	sort.SliceStable(models, func(i, j int) bool { return models[i].Label < models[j].Label })
	return models
}

type AuditActions struct {
	Create int64 `json:"create"`
	Update int64 `json:"update"`
	Delete int64 `json:"delete"`
}

type AuditStats struct {
	Total    int64        `json:"total"`
	ByAction AuditActions `json:"by_action"`
}
