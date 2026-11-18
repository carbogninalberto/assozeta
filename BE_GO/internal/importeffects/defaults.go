package importeffects

import (
	"fmt"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
)

// The callable defaults of the imported models (plan.json: the default names
// of BE/application/models). The constants are copied from Python's own
// values, decoded by the same JSON reader the archive uses.

// defaultAdditionalSections is application.models.utils.DEFAULT_ADDITIONAL_SECTIONS.
const defaultAdditionalSectionsJSON = `[{"show_to_members": true, "show_to_both": true, "show_to_athletes": true, "name": "INFORMATIVA AI SENSI DELL'EX ART.13 D.LGS. 196/2003", "text": "Gentile sig./sig.ra, <br>\n                ai sensi dell'ex art.13 d. lgs. 196/2003 (di seguito T.U.), ed in relazione ai dati personali di cui\n                codesto ente entrerà in possesso,\n                La informiamo di quanto segue: <br>\n\n                <b> 1. FINALITA' DEL TRATTAMENTO DEI DATI.</b><br>\n                Il trattamento è finalizzato unicamente per la realizzazione delle finalità istituzionali promosse\n                dall'ente medesimo nei limiti delle disposizioni statuarie previste e in conformità a quanto stabilito\n                dal D.lgs. n. 196/2003. <br>\n\n                <b>2. MODALITA' DEL TRATTAMENTO DEI DATI.</b><br>\n                a) Il trattamento e realizzato per mezzo delle operazioni o complesso di operazioni indicate all'art. 4\n                comma 1 lett. a) T.U. : raccolta, registrazione, organizzazione, conservazione, consultazione,\n                e distribuzione dei dati. <br>\n\n                b) Le operazioni possono essere svolte con o senza l'ausilio di strumenti elettronici o comunque\n                automatizzati. <br>\n\n                c) Il trattamento è svolto dal titolare e/o dagli incaricati del trattamento. <br>\n\n                <b>3. CONFERIMENTO DEI DATI.</b><br>\n                Il conferimento di dati personali comuni e/o sensibili è strettamente necessario ai fini dello\n                svolgimento delle attività di cui al punto 1.<br>\n\n                <b>4. RIFIUTO DI CONFERIMENTO DI DATI.</b><br>\n                L'eventuale rifiuto da parte dell'interessato di conferire dati personali nel caso di cui al punto 3\n                comporta l'impossibilità di adempiere\n                alle attività di cui punto 1.<br>\n\n                <b>5. COMUNICAZIONE DEI DATI.</b><br>\n                I dati personali possono venire a conoscenza degli incaricati del trattamento e possono essere\n                comunicati per le finalità di cui al punto 1 a collaboratori esterni e, in genere, a tutti quei soggetti\n                cui la comunicazione sia necessaria per il corretto adempimento delle finalità\n                indicate nel punto 1.<br>\n\n                <b>6. DIFFUSIONE DEI DATI.</b><br>\n                I dati personali non sono soggetti a diffusione di nessun genere. <br>\n\n                <b>7. TITOLARE DEL TRATTAMENTO.</b><br>\n                Titolare del trattamento è <b>l'associazione</b>."}, {"show_to_members": true, "show_to_both": true, "show_to_athletes": true, "name": "CONSENSO PER IL TRATTAMENTO DEI DATI NON SENSIBILI AD USO ISTITUZIONALE", "text": "Il/La sottoscritto/a, acquisite le informazioni di cui all'ex articolo 13 del D.lgs. n. 196/2003, ai sensi\n                dell'art 23 del predetto decreto, presta\n                il proprio consenso all'intero trattamento dei propri dati personali e in particolare: <br>\n                <b> · </b> Per l'utilizzo degli stessi per il perseguimento degli scopi statuari per il fine di\n                ricevere comunicazioni cartacee o elettroniche\n                (newsletter/email) con informazioni in merito all'attività dell'Associazione. <br>\n                <b> · </b> Affinché i dati riguardanti l'iscrizione siano comunicati agli enti cui l'associazione\n                collabora e da questi trattati nella misura\n                necessaria all'adempimento di obblighi previsti dalla legge e dalle norme statuarie. Sono consapevole\n                che, in mancanza del mio\n                consenso l'ente sportivo non potrà dar luogo\n                ai servizi citati. <br>"}, {"show_to_members": true, "show_to_both": true, "show_to_athletes": true, "name": "SCRITTURA PRIVATA DI RICHIESTA CONSENSO", "text": "L'ASSOCIAZIONE CHIEDE ALL'ASSOCIATO E/O AL TUTORE,\n                SE MINORENNE, L'AUTORIZZAZIONE ALL'USO DI FOTOGRAFIE E RIPRESE DEL SAGGIO O DI OGNI ALTRA OCCASIONE\n                CHE RITRAGGONO IL/LA FIGLIO/A MINORE PER SCOPI PUBBLICITARI, COMPRESA LA PUBBLICAZIONE SUL SITO\n                DELL'ASSOCIAZIONE.\n                <br>"}]`

// defaultConfigurationJSON is get_default_configuration().
const defaultConfigurationJSON = `{"mandatory_phone": false, "mandatory_email": false, "mandatory_signature": false}`

// defaultEnabledForJSON is get_default_enabled_for().
const defaultEnabledForJSON = `["associate", "associate-membership", "membership"]`

// defaultStripeMethodsJSON is get_default_stripe_methods().
const defaultStripeMethodsJSON = `["card", "sepa_debit"]`

var (
	defaultAdditionalSections = mustPlanJSON(defaultAdditionalSectionsJSON)
	defaultConfiguration      = mustPlanJSON(defaultConfigurationJSON)
	defaultEnabledFor         = mustPlanJSON(defaultEnabledForJSON)
	defaultStripeMethods      = mustPlanJSON(defaultStripeMethodsJSON)
)

func mustPlanJSON(text string) any {
	value, err := dto.ParseProfileTablesJSON([]byte(text))
	if err != nil {
		panic("importeffects: default constant: " + err.Error())
	}
	return value
}

// callableDefault is field.get_default() for a callable default.
func (e *Effects) callableDefault(field *Field, name string) (Value, error) {
	switch name {
	case "builtins.dict":
		return Value{Kind: JSON, J: defaultObject()}, nil
	case "builtins.list":
		return Value{Kind: JSON, J: []any{}}, nil
	case "django.utils.timezone.now":
		return DateTimeValue(e.now().UTC()), nil
	case "uuid.uuid4":
		return UUIDValue(e.newUUID()), nil
	case "application.models.balance_sheet_models.current_year":
		return Value{Kind: Int, S: fmt.Sprintf("%d", e.now().UTC().Year())}, nil
	case "application.models.courses_models.random_color":
		return StrValue(e.randomColor()), nil
	case "application.models.subscriptions_models.default_expires_at":
		return DateTimeValue(e.now().UTC().Add(30 * 24 * time.Hour)), nil
	case "application.models.utils.get_default_additional_sections":
		return Value{Kind: JSON, J: defaultAdditionalSections}, nil
	case "application.models.utils.get_default_configuration":
		return Value{Kind: JSON, J: defaultConfiguration}, nil
	case "application.models.utils.get_default_enabled_for":
		return Value{Kind: JSON, J: defaultEnabledFor}, nil
	case "application.models.utils.get_default_stripe_methods":
		return Value{Kind: JSON, J: defaultStripeMethods}, nil
	}
	return Value{}, planUnreachable("callable default %s of %s", name, field.Name)
}

// defaultObject is builtins.dict().
func defaultObject() any {
	return mustPlanJSON("{}")
}
