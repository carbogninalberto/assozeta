package service

import (
	"strings"

	"github.com/dlclark/regexp2"
	"golang.org/x/text/cases"
	"golang.org/x/text/language"
	"golang.org/x/text/unicode/norm"
)

// This file is BE/application/agent/routing.py and the routing half of
// BE/application/manuale/answers.py. Python's re patterns run on regexp2
// after agentPattern rewrites the escapes whose meaning differs: \b and \s
// are Unicode-aware in Python (str.isalnum()/'_' words, str.isspace() spaces).

const agentWordClass = `[\p{L}\p{N}_]`

const agentSpaceChars = "\\t\\n\\v\\f\\r\\x1c-\\x1f \\x85\\xa0\\u1680\\u2000-\\u200a\\u2028\\u2029\\u202f\\u205f\\u3000"

// agentPattern rewrites a Python pattern for regexp2 (.NET syntax).
func agentPattern(pattern string) string {
	var out strings.Builder
	inClass := false
	for i := 0; i < len(pattern); i++ {
		c := pattern[i]
		switch {
		case c == '\\' && i+1 < len(pattern):
			next := pattern[i+1]
			i++
			switch {
			case next == 'b' && !inClass:
				out.WriteString(`(?:(?<=` + agentWordClass + `)(?!` + agentWordClass + `)|(?<!` + agentWordClass + `)(?=` + agentWordClass + `))`)
			case next == 's' && inClass:
				out.WriteString(agentSpaceChars)
			case next == 's':
				out.WriteString("[" + agentSpaceChars + "]")
			case next == 'd':
				out.WriteString(`\p{Nd}`)
			default:
				out.WriteByte('\\')
				out.WriteByte(next)
			}
		case c == '[' && !inClass:
			inClass = true
			out.WriteByte(c)
			// A leading ']' (or '^]') is literal in Python.
			if i+1 < len(pattern) && pattern[i+1] == '^' {
				out.WriteByte('^')
				i++
			}
			if i+1 < len(pattern) && pattern[i+1] == ']' {
				out.WriteString(`\]`)
				i++
			}
		case c == ']' && inClass:
			inClass = false
			out.WriteByte(c)
		default:
			out.WriteByte(c)
		}
	}
	return out.String()
}

type agentRegexp struct{ re *regexp2.Regexp }

func agentCompile(pattern string, options regexp2.RegexOptions) agentRegexp {
	return agentRegexp{regexp2.MustCompile(agentPattern(pattern), options)}
}

func agentCompileFull(pattern string, options regexp2.RegexOptions) agentRegexp {
	return agentRegexp{regexp2.MustCompile(`\A(?:`+agentPattern(pattern)+`)\z`, options)}
}

func (r agentRegexp) search(text string) bool {
	found, err := r.re.MatchString(text)
	return err == nil && found
}

var agentCapabilityPatterns = []agentRegexp{
	agentCompile(`\b(?:cosa|che\s+cosa|che)\s+sai\s+fare\b`, 0),
	agentCompile(`\b(?:cosa|che\s+cosa|che)\s+puoi\s+fare\b`, 0),
	agentCompile(`\b(?:cosa|che)\s+posso\s+(?:fare|vedere|esportare|creare|richiedere|ottenere)\b`, 0),
	agentCompile(`\b(?:quali|che)\s+(?:cose|funzioni|capacita|capacità)\s+(?:puoi|sai)\s+(?:fare|gestire)\b`, 0),
	agentCompile(`\b(?:quali|che)\s+(?:report|export|analisi|dati)\s+(?:posso|puoi|si\s+possono)\s+`+
		`(?:creare|generare|esportare|richiedere|ottenere)\b`, 0),
	agentCompile(`\bquali\s+sono\s+le\s+(?:tue\s+)?(?:capacita|capacità|funzioni)\b`, 0),
	agentCompile(`\b(?:come|in\s+cosa)\s+puoi\s+aiutarmi\b`, 0),
	agentCompile(`\b(?:per|a)\s+cosa\s+puoi\s+aiutarmi\b`, 0),
	agentCompile(`^(?:(?:per\s+favore|gentilmente)\s+)?(?:aiutami|mi\s+aiuti)`+
		`(?:\s+(?:con|su)\s+(?:cosa|qualcosa))?(?:\s+per\s+favore)?[!.?\s]*$`, 0),
	agentCompile(`^(?:mi\s+serve|ho\s+bisogno\s+di|avrei\s+bisogno\s+di)\s+(?:un\s+)?aiuto`+
		`(?:\s+per\s+favore)?[!.?\s]*$`, 0),
	agentCompile(`^come\s+posso\s+(?:essere\s+)?aiutato[?.!\s]*$`, 0),
	agentCompile(`\b(?:chi\s+sei|sei\s+un\s+(?:bot|assistente))\b`, 0),
	agentCompile(`\bwhat\s+can\s+you\s+do\b`, 0),
	agentCompile(`^(?:ciao[,\s]*)?(?:puoi|mi\s+puoi|sai)\s+aiutarmi(?:\s+(?:con|su)\s+cosa)?[?.!\s]*$`, 0),
}

var (
	agentGreeting = agentCompileFull(`^(?:ciao|salve|buongiorno|buonasera|buon\s+pomeriggio|ehi|hey|hello|hi)`+
		`(?:[\s,!.?-]+(?:come\s+stai|tutto\s+bene|come\s+va))?[!.?\s]*$`, 0)
	agentThanks   = agentCompileFull(`^(?:grazie(?:\s+mille)?|perfetto|ottimo|va\s+bene|ok|capito)[!.?\s]*$`, 0)
	agentFarewell = agentCompileFull(`^(?:arrivederci|a\s+presto|ciao\s+ciao|buona\s+giornata)[!.?\s]*$`, 0)

	agentActionStart = agentCompile(`^(?:(?:per\s+favore|gentilmente)[,\s]+)?`+
		`(?:(?:puoi|potresti|mi\s+puoi|mi\s+potresti|sai|riesci\s+a)\s+)?`+
		`(?:fammi|farmi|fai|dammi|darmi|crea|creami|crearmi|genera|generami|generarmi|prepara|preparami|prepararmi|`+
		`esporta|esportami|esportarmi|salva|salvami|salvarmi|scarica|scaricami|scaricarmi|`+
		`invia|inviami|inviarmi|manda|mandami|mandarmi|mostra|mostrami|mostrarmi|fornisci|forniscimi|`+
		`dimmi|dirmi|mi\s+dici|mi\s+mostri|elenca|elencami|calcola|calcolami|aggrega|riepiloga|visualizza|`+
		`analizza|confronta|conta|trova|cerca|rifai|prosegui|procedi|fallo|crealo|esportalo|general[oi])\b`, 0)
	agentMiActionStart = agentCompile(`^(?:mi|ci)\s+(?:fai|dai|crei|generi|prepari|esporti|salvi|scarichi|invii|mandi|`+
		`mostri|dici|fornisci)\b`, 0)
	agentAssistedActionStart = agentCompile(`^(?:aiutami|mi\s+aiuti|mi\s+serve\s+aiuto|ho\s+bisogno\s+di\s+aiuto)\s+(?:a|per)\s+`+
		`(?:creare|generare|preparare|esportare|salvare|scaricare|inviare|mandare|mostrare|`+
		`elencare|calcolare|trovare|cercare|analizzare|confrontare)\b`, 0)
	agentPoliteInfinitiveActionStart = agentCompile(`^(?:puoi|potresti|mi\s+puoi|mi\s+potresti)\s+`+
		`(?:creare|generare|preparare|esportare|salvare|scaricare|inviare|mandare|mostrare|`+
		`elencare|calcolare|trovare|cercare|analizzare|confrontare)\b`, 0)
	agentOutputActionStart = agentCompile(`^(?:(?:per\s+favore|gentilmente)[,\s]+)?`+
		`(?:(?:puoi|potresti|mi\s+puoi|mi\s+potresti|sai|riesci\s+a)\s+)?`+
		`(?:crea|creami|crearmi|genera|generami|generarmi|`+
		`prepara|preparami|prepararmi|esporta|esportami|esportarmi|salva|salvami|salvarmi|`+
		`scarica|scaricami|scaricarmi|`+
		`fallo|crealo|esportalo|general[oi])\b`, 0)
	agentOutputMiActionStart       = agentCompile(`^(?:mi|ci)\s+(?:crei|generi|prepari|esporti|salvi|scarichi)\b`, 0)
	agentOutputAssistedActionStart = agentCompile(`^(?:aiutami|mi\s+aiuti|mi\s+serve\s+aiuto|ho\s+bisogno\s+di\s+aiuto)\s+(?:a|per)\s+`+
		`(?:creare|generare|preparare|esportare|salvare|scaricare)\b`, 0)
	agentOutputPoliteInfinitiveStart = agentCompile(`^(?:puoi|potresti|mi\s+puoi|mi\s+potresti)\s+`+
		`(?:creare|generare|preparare|esportare|salvare|scaricare)\b`, 0)
	agentEmbeddedOutputCommand = agentCompile(`\b(?:fammi|dammi|crea|creami|genera|generami|prepara|preparami|esporta|esportami|`+
		`salva|salvami|scarica|scaricami|invia|inviami|manda|mandami|mostra|mostrami)\b`, regexp2.IgnoreCase)
)

const agentDataNouns = `soc(?:io|i)|pagament[oi]|iscrizion[ei]|cors[oi]|certificat[oi]|ricevut[ae]|` +
	`fattur[ae]|presenz[ae]|carnet|bilanc(?:io|i)|report|export|esportazion[ei]|` +
	`comunicazion[ei]|modul[oi]|tag|registro`

var (
	agentQueryStart = agentCompile(`^(?:(?:per\s+favore|gentilmente)[,\s]+)?`+
		`(?:(?:puoi|potresti|mi\s+puoi|mi\s+potresti|sai|riesci\s+a)\s+)?`+
		`(?:(?:dirmi|dire|spiegarmi)\s+)?`+
		`(?:quanti|quante|quanto|quanta|chi|quando|quali|ci\s+sono|`+
		`qual\s+(?:e|è)\s+(?:il|la|lo|i|gli|le)|`+
		`cosa\s+(?:ho|abbiamo|risulta|sono)|che\s+(?:cosa\s+)?(?:ho|abbiamo|risulta|sono)|`+
		`fammi\s+vedere|voglio\s+vedere|vorrei\s+vedere|voglio\s+sapere|vorrei\s+sapere|`+
		`ho\s+bisogno|mi\s+serve)\b`, 0)
	agentReportAction = agentCompile(`\b(?:fai|farmi|crea|creare|creami|crearmi|genera|generare|generami|generarmi|prepara|preparare|preparami|prepararmi|`+
		`esporta|esportare|esportami|esportarmi|`+
		`salva|salvami|scarica|scaricami|posso\s+avere|vorrei|voglio|mi\s+serve|`+
		`ho\s+bisogno\s+di)\b.{0,80}\b(?:report|export|esportazione|pdf|xlsx|csv|file)\b`+
		`|\b(?:report|export|esportazione)\b.{0,50}\b(?:salvat[oi]|pdf|xlsx|csv)\b`, 0)
	agentDataRequest = agentCompile(`^(?:voglio|vorrei|mi\s+serve|mi\s+servono|ho\s+bisogno\s+di|avrei\s+bisogno\s+di)\b.{0,80}`+
		`\b(?:`+agentDataNouns+`)\b`, 0)
	agentDataExplanationStart = agentCompile(`^(?:mi\s+spieghi|spiegami)\s+`+
		`(?:(?:il|lo|la|l['’]|i|gli|le)\s*(?:totale|saldo|importo|elenco|lista|dati|`+
		`andamento|riepilogo|numero)\b|(?:quanti|quante|quanto|quanta|quali|quando|chi)\b|`+
		`(?:`+agentDataNouns+`)\b)`, 0)
	agentQuantifiedDataStart = agentCompile(`^(?:(?:puoi|potresti|mi\s+puoi|mi\s+potresti|sai)\s+)?`+
		`(?:(?:dirmi|dire|spiegarmi)\s+)?`+
		`(?:il|lo|la|i|gli|le)\s+`+
		`(?:numero|totale|saldo|importo|conteggio|somma|media|elenco|lista|dati|riepilogo|andamento)\b`, 0)
	agentFollowUpParameter = agentCompileFull(`^(?:(?:s[iì]|certo|ok|va\s+bene|perfetto|procedi|fallo|esportalo|crealo|generalo|`+
		`usa|scegli|seleziona|aggiungi|togli|includi|escludi|solo|in|nel|per|del|della|dei|delle|degli|`+
		`dal|dall|tra|fino|quello|quella)\b.*|`+
		`pdf|excel|xlsx|csv|gennaio|febbraio|marzo|aprile|maggio|giugno|luglio|agosto|`+
		`settembre|ottobre|novembre|dicembre|quest[ao]\s+(?:mese|anno)|`+
		`(?:tutti|tutte|tutto)\b.*|\d{4})[!.?\s]*$`, 0)
	agentWithoutGreeting = agentCompile(`^(?:ciao|salve|buongiorno|buonasera|buon\s+pomeriggio|ehi|hey|hello|hi)[,\s]+`, regexp2.IgnoreCase)
)

// manuale/answers.py
const agentPlatformOps = `creare|creo|crea|aggiungere|aggiungo|aggiunge|assegnare|assegno|assegna|` +
	`modificare|modifico|modifica|configurare|configuro|configura|importare|importo|` +
	`esportare|esporto|registrare|registro|abilitare|abilito|eliminare|elimino|` +
	`incassare|incasso|incassa|approvare|approvo|approva|scaricare|scarico|scarica|` +
	`pubblicare|pubblico|pubblica|annullare|annullo|annulla|gestire|gestisco|trovare|` +
	`trovo|cercare|cerco|cerca|svuotare|svuoto|svuota|cancellare|cancello|archiviare|` +
	`archivio|archivia|ripristinare|ripristino|ripristina|correggere|correggo|corregge|` +
	`copiare|copio|copia|controllare|controllo|controlla|cambiare|cambio|cambia|` +
	`personalizzare|personalizzo|personalizza|ridimensionare|ridimensiono|ridimensiona|` +
	`impostare|imposto|imposta|segnare|segno|segna|rimuovere|rimuovo|rimuove|` +
	`generare|genero|genera|stampare|stampo|caricare|carico|invitare|invito|salvare|` +
	`salvo|inviare|invio|invia`

const agentPlatformTerms = `report|export|esportazione|pagament[oi]|soc(?:io|i)|iscrizion[ei]|certificat[oi]|` +
	`tesser[ae]|cors[oi]|bilanc(?:io|i)|fattur[ae]|ricevut[ae]|presenz[ae]|carnet|` +
	`calendar(?:io|i)|comunicazion[ei]|modul[oi]|tag|registro|contabilita|contabilità|` +
	`bonific[oi]|quota|quote|dashboard|bacheca|impostazion[ei]|ann[oi]\s+(?:sportivo|fiscale)`

var (
	agentAssistantInstruction = agentCompile(`\b(?:ignore|disregard|override)\s+(?:all\s+)?(?:previous|prior|system|developer)\s+`+
		`(?:instructions?|messages?|prompts?)\b`+
		`|\b(?:ignora|ignorare|dimentica)\s+(?:tutte\s+)?(?:le\s+)?(?:istruzioni|indicazioni|regole)\s+`+
		`(?:precedenti|di\s+sistema)\b`+
		`|(?:^|\n)\s*(?:system|developer|assistant|sistema|assistente)\s*:`, regexp2.IgnoreCase)
	agentHowToCue = agentCompile(`\b(?:come|dove)\s+(?:(?:si\s+fa|posso\s+fare|faccio|posso|devo|fa|si|fare)\s+(?:a\s+|per\s+)?)?`+
		`(?:`+agentPlatformOps+`)\b`, 0)
	agentExplanationCue = agentCompile(`\b(?:spiegami|spiega|spiegarmi|mi\s+spieghi|vorrei\s+capire|ho\s+bisogno\s+di\s+capire|`+
		`sai\s+dirmi|qual\s+(?:e|è)\s+(?:la\s+procedura|il\s+modo)|quali\s+passaggi|`+
		`cosa\s+devo\s+fare\s+per|come\s+funziona)\b`, 0)
	agentDefinitionCue = agentCompile(`\b(?:che\s+cos['’]?è|che\s+cos['’]?e|cos['’]?è|cos['’]?e|che\s+cosa\s+(?:e|è)|cosa\s+significa|cosa\s+vuol\s+dire|`+
		`a\s+cosa\s+serve)\b`, 0)
	agentManualReferenceCue = agentCompile(`^(?:manuale|documentazione|istruzioni|tutorial|guida)\b`+
		`|\b(?:il|lo|la|i|gli|le|nel|nello|nella|sul|sullo|sulla|dal|dallo|dalla|del|dello|della|dei|degli|delle)\s+`+
		`(?:manuale|documentazione|istruzioni|tutorial)\b`+
		`|\buna\s+guida\b|\bla\s+guida\s+(?:per|su|d['’]uso|dell['’]utente|utente)\b`, 0)
	agentDataQueryCue = agentCompile(`\b(?:quanti|quante|quanto|quanta|quali|quando|chi|totale|saldo|importo|`+
		`elenco|lista|numero|conteggio|somma|media|dati|risultati|riepilogo|andamento|`+
		`incassat[oi]|pagat[oi]|insolut[oi]|scadut[oi]|attiv[oi])\b`, 0)
	agentProceduralCue = agentCompile(`\b(?:qual\s+(?:e|è)\s+(?:la\s+procedura|il\s+modo)|quali\s+passaggi|`+
		`cosa\s+devo\s+fare\s+per|come\s+funziona)\b`, 0)
	agentPlatformTermsRe  = agentCompile(`\b(?:`+agentPlatformTerms+`)\b`, 0)
	agentDiagnosticManual = agentCompile(`\b(manuale|documentazione)\b`, 0)
	agentDiagnosticTopic  = agentCompile(`\b(lacune|copertura|diagnostica|sezioni non verificate|fonti obsolete)\b`, 0)
	// core._FOLLOW_UP_ANSWER (re.I | re.M)
	agentFollowUpAnswer = agentCompile(`\?\s*$|(?:^|[\n.!?]\s*)(?:quale|quali|specifica|indica|dimmi|preferisci|vuoi|`+
		`conferma|confermi|scegli|seleziona)\b|`+
		`\b(?:per\s+procedere|se\s+va\s+bene|prima\s+di\s+procedere|per\s+continuare)`+
		`[,\s]+(?:conferma|dimmi|specifica|scegli|seleziona)\b`, regexp2.IgnoreCase|regexp2.Multiline)
)

// agentLower is str.lower(); agentCasefold is str.casefold().
func agentLower(text string) string    { return cases.Lower(language.Und).String(text) }
func agentCasefold(text string) string { return cases.Fold().String(text) }

// agentIsSpace is str.isspace() for one character.
func agentIsSpace(r rune) bool {
	switch r {
	case ' ', '\t', '\n', '\v', '\f', '\r', 0x1c, 0x1d, 0x1e, 0x1f, 0x85, 0xa0, 0x1680, 0x2028, 0x2029, 0x202f, 0x205f, 0x3000:
		return true
	}
	return r >= 0x2000 && r <= 0x200a
}

func agentStrip(text string) string { return strings.TrimFunc(text, agentIsSpace) }

// agentNormalize is normalize_message: NFKC, strip, runs of \s as one space.
func agentNormalize(message string) string {
	text := agentStrip(norm.NFKC.String(message))
	var out strings.Builder
	space := false
	for _, r := range text {
		if agentIsSpace(r) {
			space = true
			continue
		}
		if space {
			out.WriteByte(' ')
			space = false
		}
		out.WriteRune(r)
	}
	return out.String()
}

func agentMatching(message string) string { return agentCasefold(agentNormalize(message)) }

func agentIsCapabilityQuestion(message string) bool {
	normalized := agentMatching(message)
	for _, pattern := range agentCapabilityPatterns {
		if pattern.search(normalized) {
			return true
		}
	}
	return false
}

func agentIsConversation(message string) bool {
	normalized := agentMatching(message)
	return agentGreeting.search(normalized) || agentThanks.search(normalized) || agentFarewell.search(normalized)
}

func agentIsActionRequest(message string) bool {
	n := agentMatching(message)
	return agentActionStart.search(n) || agentMiActionStart.search(n) || agentAssistedActionStart.search(n) ||
		agentPoliteInfinitiveActionStart.search(n) || agentQueryStart.search(n) || agentDataRequest.search(n) ||
		agentDataExplanationStart.search(n) || agentQuantifiedDataStart.search(n) || agentReportAction.search(n)
}

func agentIsOutputActionRequest(message string) bool {
	n := agentMatching(message)
	return agentOutputActionStart.search(n) || agentOutputMiActionStart.search(n) || agentOutputAssistedActionStart.search(n) ||
		agentOutputPoliteInfinitiveStart.search(n)
}

func agentIsActionFollowUp(message string) bool {
	return agentFollowUpParameter.search(agentMatching(message))
}

func agentStripGreeting(normalized string) string {
	match, err := agentWithoutGreeting.re.FindStringMatch(normalized)
	if err != nil || match == nil {
		return normalized
	}
	return normalized[len(match.String()):]
}

// agentIsManualQuestion is answers.is_manual_question.
func agentIsManualQuestion(message string) bool {
	message = agentStrip(agentLower(message))
	switch {
	case agentManualReferenceCue.search(message), agentHowToCue.search(message):
		return true
	case agentProceduralCue.search(message) && agentPlatformTermsRe.search(message):
		return true
	case agentExplanationCue.search(message) && agentPlatformTermsRe.search(message) && !agentDataQueryCue.search(message):
		return true
	case agentDefinitionCue.search(message) && agentPlatformTermsRe.search(message):
		return true
	}
	return false
}

func agentIsManualDiagnostic(message string) bool {
	message = agentLower(message)
	return agentDiagnosticManual.search(message) && agentDiagnosticTopic.search(message)
}

func agentRequiresFollowUp(text string) bool { return text != "" && agentFollowUpAnswer.search(text) }

type agentIntent string

const (
	intentCapabilities     agentIntent = "capabilities"
	intentConversation     agentIntent = "conversation"
	intentManualFAQ        agentIntent = "manual_faq"
	intentManualDiagnostic agentIntent = "manual_diagnostic"
	intentAction           agentIntent = "action"
	intentAmbiguous        agentIntent = "ambiguous"
)

type agentRoute struct {
	intent agentIntent
	query  string
}

// agentRouteMessage is route_message.
func agentRouteMessage(message string, pending bool) agentRoute {
	query := agentNormalize(message)
	routing := agentStripGreeting(query)
	if routing == "" {
		routing = query
	}
	switch {
	case agentIsManualDiagnostic(routing):
		return agentRoute{intentManualDiagnostic, query}
	case agentIsOutputActionRequest(routing):
		return agentRoute{intentAction, query}
	case agentIsManualQuestion(routing):
		return agentRoute{intentManualFAQ, query}
	case agentIsCapabilityQuestion(routing):
		if agentEmbeddedOutputCommand.search(routing) {
			return agentRoute{intentAction, query}
		}
		return agentRoute{intentCapabilities, query}
	case agentEmbeddedOutputCommand.search(routing):
		return agentRoute{intentAction, query}
	case pending && agentIsActionFollowUp(routing):
		return agentRoute{intentAction, query}
	case agentIsActionRequest(routing):
		return agentRoute{intentAction, query}
	case agentIsConversation(query):
		return agentRoute{intentConversation, query}
	}
	return agentRoute{intentAmbiguous, query}
}

// agentCapabilityAnswer is capability_answer.
func agentCapabilityAnswer(aiEnabled, manualAvailable, savedReports bool) string {
	if aiEnabled {
		abilities := []string{"cercare e riepilogare i dati dell'associazione", "rispondere a domande sui dati", "preparare export e report"}
		if savedReports {
			abilities = append(abilities, "consultare i report salvati")
		}
		if manualAvailable {
			abilities = append(abilities, "consultare le istruzioni verificate del manuale")
		}
		return "In base alle autorizzazioni del tuo account, posso " + strings.Join(abilities[:len(abilities)-1], ", ") +
			" e " + abilities[len(abilities)-1] + "."
	}
	if manualAvailable {
		return "Posso cercare istruzioni verificate nel manuale di Assozeta. " +
			"Le analisi dei dati, gli export e la creazione di report richiedono l'attivazione dell'AI."
	}
	return "In questa configurazione non sono disponibili la ricerca nel manuale né le funzioni AI."
}

func agentManualOnlyActionAnswer(manualAvailable bool) string {
	if manualAvailable {
		return "Non posso eseguire analisi dei dati, export o report perché l'AI non è configurata. " +
			"Posso cercare le istruzioni verificate nel manuale: indica la funzione che vuoi usare."
	}
	return "Non posso eseguire questa operazione perché l'AI non è configurata in questa istanza."
}

const agentAmbiguousManualAnswer = "Non sono riuscito a collegare la richiesta a un’istruzione verificata. " +
	"Indica la funzione del gestionale e cosa vuoi ottenere; " +
	"per analisi ed export dei dati serve l'AI configurata."

// agentContainsAny is `any(word in normalized for word in words)`.
func agentContainsAny(text string, words ...string) bool {
	for _, word := range words {
		if strings.Contains(text, word) {
			return true
		}
	}
	return false
}
