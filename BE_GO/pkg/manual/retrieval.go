package manual

import (
	"context"
	"encoding/json"
	"math"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"unicode"
)

var manualWord = regexp.MustCompile(`[a-zà-ÿ0-9]+`)
var electronicWord = regexp.MustCompile(`^elettronic[aohe]+$`)
var manualStop = func() map[string]bool {
	m := map[string]bool{}
	for _, s := range strings.Fields("a ad al alla alle ai agli anche che chi con da dal dalla dei del della delle di e ed è gli i il in la le lo l ma nel nella nelle o per più quale quali se si su sul sulla un una uno come cosa posso devo fare manuale istruzioni guida tutorial") {
		m[s] = true
	}
	return m
}()
var manualActions = func() map[string]string {
	m := map[string]string{}
	for name, words := range map[string]string{
		"approve": "incassare incasso incassa approvare approvo approva", "download": "scaricare scarico scarica scarichi",
		"generate": "generare genero genera generazione", "create": "creare creo crea aggiungere aggiungo aggiungi aggiunge",
		"assign": "assegnare assegno assegna assegni assegnano", "delete": "eliminare elimino elimina eliminano rimuovere rimuovo rimuovi cancellare cancella",
		"export": "esportare esporto esporta esporti", "import": "importare importo importa importi",
		"configure": "configurare configuro configura impostare imposto imposta", "invite": "invitare invito invita inviti",
		"print": "stampare stampo stampa stampi", "search": "cercare cerco cerca cercano ricerca trovare trovo trova",
		"update": "modificare modifico modifica modifiche cambiare cambio cambia", "archive": "archiviare archivio archivia archiviazione",
		"restore": "ripristinare ripristino ripristina ripristinata ripristinato",
	} {
		for _, word := range strings.Fields(words) {
			m[word] = name
		}
	}
	return m
}()

// Python lower expands U+0130; accent stripping is deliberately absent.
func manualLower(s string) string { return strings.ToLower(strings.ReplaceAll(s, "İ", "i\u0307")) }
func manualTokens(s string) []string {
	result := []string{}
	for _, word := range manualWord.FindAllString(manualLower(s), -1) {
		if manualStop[word] || len([]rune(word)) <= 1 {
			continue
		}
		if action := manualActions[word]; action != "" {
			word = "operazione_" + action
		}
		result = append(result, word)
	}
	return result
}
func unicodeWords(s string) []string {
	return strings.FieldsFunc(s, func(r rune) bool { return !(unicode.IsLetter(r) || unicode.IsNumber(r) || r == '_') })
}
func manualOperations(s string) map[string]bool {
	result := map[string]bool{}
	for _, word := range unicodeWords(manualLower(s)) {
		if action := manualActions[word]; action != "" {
			result[action] = true
		}
	}
	return result
}
func includesActions(have, want map[string]bool) bool {
	for action := range want {
		if !have[action] {
			return false
		}
	}
	return true
}
func manualProtocols(s string) map[string]bool {
	// Python re.I additionally equates the long s and dotless/dotted I.
	s = strings.NewReplacer("ſ", "s", "ı", "i", "İ", "i").Replace(strings.ToLower(s))
	result := map[string]bool{}
	for _, word := range unicodeWords(s) {
		switch {
		case electronicWord.MatchString(word):
			result["electronic"] = true
		case word == "oauth":
			result["oauth"] = true
		case word == "sdi" || word == "interscambio":
			result["sdi"] = true
		}
	}
	return result
}
func tokenCounts(s string) map[string]int {
	result := map[string]int{}
	for _, word := range manualTokens(s) {
		result[word]++
	}
	return result
}
func countLength(m map[string]int) int {
	n := 0
	for _, v := range m {
		n += v
	}
	return n
}
func numeric(value any) (float64, bool) {
	var f float64
	switch v := value.(type) {
	case json.Number:
		var err error
		f, err = v.Float64()
		if err != nil {
			return 0, false
		}
	case float64:
		f = v
	case int:
		f = float64(v)
	default:
		return 0, false
	}
	return f, !math.IsNaN(f) && !math.IsInf(f, 0)
}
func vectorNumbers(value any) ([]float64, error) {
	items, ok := value.([]any)
	if !ok {
		return nil, ErrReaderUnavailable
	}
	result := make([]float64, len(items))
	for i, v := range items {
		n, ok := numeric(v)
		if !ok {
			return nil, ErrReaderUnavailable
		}
		result[i] = n
	}
	return result, nil
}
func roundedScore(value float64) float64 {
	s := strconv.FormatFloat(value, 'f', 6, 64)
	f, _ := strconv.ParseFloat(s, 64)
	return f
}
func stringField(m map[string]any, key string) (string, error) {
	v, ok := m[key].(string)
	if !ok {
		return "", ErrReaderUnavailable
	}
	return v, nil
}
func readerTitles(chunk map[string]any) ([]string, error) {
	raw, present := chunk["reader"]
	if !present {
		return []string{}, nil
	}
	blocks, ok := raw.([]any)
	if !ok {
		return nil, ErrReaderUnavailable
	}
	titles := []string{}
	for _, raw := range blocks {
		block, ok := raw.(map[string]any)
		if !ok {
			return nil, ErrReaderUnavailable
		}
		title := ""
		if raw, present := block["title"]; present {
			var ok bool
			title, ok = raw.(string)
			if !ok {
				return nil, ErrReaderUnavailable
			}
		}
		titles = append(titles, title)
	}
	return titles, nil
}

// searchSections uses the sealed model exactly as supplied. Candidate filtering
// changes BM25 statistics but never reindexes or retrains the full-corpus vectors.
func searchSections(ctx context.Context, index map[string]any, applicable []map[string]any, query string) ([]map[string]any, error) {
	requested := manualOperations(query)
	protocols := manualProtocols(query)
	candidates := []map[string]any{}
	for _, chunk := range applicable {
		if err := ctx.Err(); err != nil {
			return nil, err
		}
		title, err := stringField(chunk, "title")
		if err != nil {
			return nil, err
		}
		body, err := stringField(chunk, "text")
		if err != nil {
			return nil, err
		}
		if !includesActions(manualOperations(title+" "+body), requested) {
			continue
		}
		if len(protocols) > 0 {
			titles, err := readerTitles(chunk)
			if err != nil {
				return nil, err
			}
			if !includesActions(manualProtocols(title+" "+strings.Join(titles, " ")), protocols) {
				continue
			}
		}
		candidates = append(candidates, chunk)
	}
	q := tokenCounts(query)
	if len(q) == 0 {
		return []map[string]any{}, nil
	}
	counts := map[string]map[string]int{}
	for _, chunk := range candidates {
		id, err := stringField(chunk, "id")
		if err != nil {
			return nil, err
		}
		counts[id] = tokenCounts(chunk["text"].(string) + " " + chunk["title"].(string))
	}
	model, ok := index["semantics"].(map[string]any)
	if !ok {
		return nil, ErrReaderUnavailable
	}
	vocabulary, ok := model["vocabulary"].([]any)
	if !ok {
		return nil, ErrReaderUnavailable
	}
	if len(vocabulary) == 0 {
		return []map[string]any{}, nil
	}
	idf, err := vectorNumbers(model["idf"])
	if err != nil || len(idf) != len(vocabulary) {
		return nil, ErrReaderUnavailable
	}
	vector := make([]float64, len(vocabulary))
	for i, raw := range vocabulary {
		word, ok := raw.(string)
		if !ok {
			return nil, ErrReaderUnavailable
		}
		vector[i] = math.Log1p(float64(q[word])) * idf[i]
	}
	rawBasis, ok := model["basis"].([]any)
	if !ok || len(rawBasis) == 0 {
		return nil, ErrReaderUnavailable
	}
	latent := make([]float64, len(rawBasis))
	norm := 0.0
	for i, raw := range rawBasis {
		if err := ctx.Err(); err != nil {
			return nil, err
		}
		basis, err := vectorNumbers(raw)
		if err != nil || len(basis) != len(vector) {
			return nil, ErrReaderUnavailable
		}
		for j, v := range basis {
			latent[i] += vector[j] * v
		}
		norm += latent[i] * latent[i]
	}
	norm = math.Max(math.Sqrt(norm), 1e-12)
	for i := range latent {
		latent[i] /= norm
	}
	all, ok := index["chunks"].([]any)
	if !ok {
		return nil, ErrReaderUnavailable
	}
	rawVectors, ok := model["vectors"].([]any)
	if !ok {
		return nil, ErrReaderUnavailable
	}
	vectors := map[string]any{}
	for i, raw := range all {
		if i >= len(rawVectors) {
			break
		}
		chunk, ok := raw.(map[string]any)
		if !ok {
			return nil, ErrReaderUnavailable
		}
		id, err := stringField(chunk, "id")
		if err != nil {
			return nil, err
		}
		vectors[id] = rawVectors[i]
	}
	n := float64(len(candidates))
	avg := 0.0
	documentFrequency := map[string]int{}
	for _, count := range counts {
		if err := ctx.Err(); err != nil {
			return nil, err
		}
		avg += float64(countLength(count))
		for word := range count {
			documentFrequency[word]++
		}
	}
	avg /= math.Max(n, 1)
	ranked := []map[string]any{}
	for _, chunk := range candidates {
		if err := ctx.Err(); err != nil {
			return nil, err
		}
		id := chunk["id"].(string)
		count := counts[id]
		overlap := []string{}
		contentOverlap := false
		for word := range q {
			if count[word] > 0 {
				overlap = append(overlap, word)
				if !strings.HasPrefix(word, "operazione_") {
					contentOverlap = true
				}
			}
		}
		if !contentOverlap {
			continue
		}
		sort.Strings(overlap)
		bm := 0.0
		for _, word := range overlap {
			df := float64(documentFrequency[word])
			weight := math.Log(1 + (n-df+.5)/(df+.5))
			freq := float64(count[word])
			bm += weight * freq * 2.2 / (freq + 1.2*(.25+.75*float64(countLength(count))/math.Max(avg, 1)))
		}
		lexical := bm / (1 + bm)
		v, err := vectorNumbers(vectors[id])
		if err != nil || len(v) != len(latent) {
			return nil, ErrReaderUnavailable
		}
		semantic := 0.0
		for i, x := range v {
			semantic += latent[i] * x
		}
		semantic = math.Max(0, semantic)
		coverage := float64(len(overlap)) / float64(len(q))
		titleCount := tokenCounts(chunk["title"].(string))
		titleOverlap := 0
		for word := range q {
			if titleCount[word] > 0 {
				titleOverlap++
			}
		}
		titleCoverage := float64(titleOverlap) / float64(len(q))
		stepCoverage := 0.0
		// The Python generator iterates reader even without an action, but
		// does not inspect its optional titles in that case.
		if raw, present := chunk["reader"]; present {
			switch raw.(type) {
			case []any, map[string]any, string:
			default:
				return nil, ErrReaderUnavailable
			}
		}
		if len(requested) > 0 {
			titles, err := readerTitles(chunk)
			if err != nil {
				return nil, err
			}
			for _, title := range titles {
				if !includesActions(manualOperations(title), requested) {
					continue
				}
				tc := tokenCounts(title)
				overlap := 0
				for word := range q {
					if tc[word] > 0 {
						overlap++
					}
				}
				stepCoverage = math.Max(stepCoverage, float64(overlap)/float64(len(q)))
			}
		}
		score := .75*(.7*lexical+.3*semantic)*coverage + .25*titleCoverage + .2*stepCoverage
		if score >= .18 {
			copy := map[string]any{}
			for k, v := range chunk {
				copy[k] = v
			}
			copy["score"] = roundedScore(score)
			ranked = append(ranked, copy)
		}
	}
	sort.SliceStable(ranked, func(i, j int) bool {
		a, b := ranked[i]["score"].(float64), ranked[j]["score"].(float64)
		if a != b {
			return a > b
		}
		return ranked[i]["id"].(string) < ranked[j]["id"].(string)
	})
	if len(ranked) > 10 {
		ranked = ranked[:10]
	}
	return ranked, nil
}
