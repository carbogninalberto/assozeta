package instanceprobe

import (
	"context"
	"errors"
	"math"
	"math/big"
	"strings"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/refusal"
)

// Result is diagnostic_probe.result(status, message, level).
type Result struct {
	Status, Level, Message string
}

func result(status, message, level string) *Result {
	return &Result{Status: status, Level: level, Message: message}
}

// Generic is diagnostic_probe.main()'s answer to any exception.
var Generic = Result{Status: "failed", Level: "connectivity",
	Message: "Verifica non riuscita. Controlla configurazione e disponibilità del servizio e riprova."}

// errProbe is an exception inside the probe (the generic failure).
var errProbe = errors.New("instanceprobe: probe raised")

// ErrProbeNotPorted marks an integration probe input this port does not
// send (an empty user information pair, which the settings endpoint cannot
// store).
var ErrProbeNotPorted = refusal.New("instanceprobe: integration probe input not ported")

const maxProbeBody = 262144

// getJSON is integration_probe.get_json.
func (c *Client) getJSON(ctx context.Context, target string, headers map[string]string) (any, *Result, error) {
	response, err := c.Get(ctx, target, headers, maxProbeBody)
	if err != nil {
		if ctx.Err() != nil {
			return nil, nil, ctx.Err()
		}
		return nil, nil, errProbe
	}
	return probeJSON(response)
}

// probeJSON is get_json after requests.get returned.
func probeJSON(response *Response) (any, *Result, error) {
	switch {
	case response.Status == 401 || response.Status == 403:
		return nil, result("failed", "Credenziale rifiutata o permessi insufficienti. Verifica la chiave e le autorizzazioni del provider.", "connectivity"), nil
	case response.Status == 429:
		return nil, result("warning", "Il provider limita le richieste. Attendi e ripeti la verifica.", "connectivity"), nil
	case response.Status != 200:
		return nil, result("failed", "Il provider non ha risposto correttamente. Verifica endpoint, rete e disponibilità del servizio.", "connectivity"), nil
	}
	if len(response.Body) > maxProbeBody {
		return nil, result("failed", "Risposta del provider troppo grande per la verifica.", "connectivity"), nil
	}
	value, err := LoadJSON(response.Body)
	switch {
	case errors.Is(err, ErrJSONEncoding):
		return nil, nil, ErrProbeNotPorted
	case err != nil:
		return nil, nil, errProbe
	}
	return value, nil, nil
}

// getJSONRequest is getJSON for the prepared AI request: the exact target
// bytes and Host header go on the wire.
func (c *Client) getJSONRequest(ctx context.Context, target *AIRequest, headers map[string]string) (any, *Result, error) {
	response, err := c.GetTarget(ctx, target, headers, maxProbeBody)
	if err != nil {
		if ctx.Err() != nil {
			return nil, nil, ctx.Err()
		}
		return nil, nil, errProbe
	}
	return probeJSON(response)
}

// authorization is 'Bearer ' + secret as http.client sends it: latin-1
// bytes; a character above U+00FF (UnicodeEncodeError) or a line break
// (requests' InvalidHeader) fails the probe.
func authorization(secret any) (string, error) {
	text, ok := secret.(string)
	if !ok || HasSurrogate(text) || strings.ContainsAny(text, "\r\n") {
		return "", errProbe
	}
	var out []byte
	for _, r := range "Bearer " + text {
		if r > 0xFF {
			return "", errProbe
		}
		out = append(out, byte(r))
	}
	return string(out), nil
}

func text(value any) (string, bool) {
	s, ok := value.(string)
	return s, ok
}

// IntegrationTarget is the request verify_integration would send for the
// effective settings, "" when it answers without one (invalid is then the
// probe's "URL del provider non valido." answer, and no request with invalid
// false is an input requests raises on: the generic failure), or
// ErrProbeNotPorted for an input this port does not send (an empty user
// information pair, which the settings endpoint cannot store), checked
// before the lease is claimed.
func IntegrationTarget(provider string, value map[string]any) (target string, invalid bool, err error) {
	request, invalid, err := integrationRequest(provider, value)
	if err != nil {
		return "", false, err
	}
	if invalid {
		return "", true, nil
	}
	if request == nil {
		return "", false, nil
	}
	return request.URL, false, nil
}

// VerifyIntegration is integration_probe.verify_integration after its
// revision and enabled checks, for the decrypted effective settings.
func (c *Client) VerifyIntegration(ctx context.Context, provider string, value map[string]any) (*Result, error) {
	out, err := c.verifyIntegration(ctx, provider, value)
	if errors.Is(err, errProbe) {
		generic := Generic
		return &generic, nil
	}
	return out, err
}

func (c *Client) verifyIntegration(ctx context.Context, provider string, value map[string]any) (*Result, error) {
	switch provider {
	case "google", "apple":
		if !Truthy(value["client_id"]) {
			return result("not_configured", "Client ID mancante.", "configuration"), nil
		}
		if provider == "google" {
			client, ok := text(value["client_id"])
			if !ok {
				return nil, errProbe
			}
			if !strings.HasSuffix(client, ".apps.googleusercontent.com") {
				return result("failed", "Il client ID Google non ha il formato di un client web.", "configuration"), nil
			}
		}
		target := "https://www.googleapis.com/oauth2/v3/certs"
		if provider == "apple" {
			target = "https://appleid.apple.com/auth/keys"
		}
		data, failure, err := c.getJSON(ctx, target, nil)
		if err != nil || failure != nil {
			return failure, err
		}
		valid := false
		if object, ok := data.(map[string]any); ok {
			keys, present := object["keys"]
			if !present {
				keys = []any{}
			}
			switch list := keys.(type) {
			case []any:
				for _, item := range list {
					key, ok := item.(map[string]any)
					if ok && Truthy(key["kid"]) && key["kty"] == "RSA" && Truthy(key["n"]) && Truthy(key["e"]) {
						valid = true
						break
					}
				}
			case map[string]any, string:
			default:
				return nil, errProbe
			}
		}
		if !valid {
			return result("failed", "Il provider non ha restituito chiavi pubbliche valide.", "connectivity"), nil
		}
		message := "Provider raggiungibile e chiavi pubbliche disponibili. Validità del client ID, origini autorizzate e accesso utente richiedono una prova di login."
		if provider == "apple" {
			message += " Il pulsante Apple sul web non è disponibile in questa versione."
		}
		return result("warning", message, "connectivity"), nil
	case "stripe":
		for _, key := range []string{"public_key", "secret_key", "webhook_secret"} {
			if !Truthy(value[key]) {
				return result("not_configured", "Completa le chiavi Stripe e la firma webhook.", "configuration"), nil
			}
		}
		header, err := authorization(value["secret_key"])
		if err != nil {
			return nil, err
		}
		data, failure, err := c.getJSON(ctx, "https://api.stripe.com/v1/balance", map[string]string{"Authorization": header})
		if err != nil || failure != nil {
			return failure, err
		}
		object, ok := data.(map[string]any)
		if !ok || object["object"] != "balance" {
			return result("failed", "Risposta Stripe non valida per la verifica.", "connectivity"), nil
		}
		public, ok := text(value["public_key"])
		if !ok {
			return nil, errProbe
		}
		live, isBool := object["livemode"].(bool)
		if !isBool || live != strings.HasPrefix(public, "pk_live_") {
			return result("failed", "La chiave pubblica e l’account Stripe indicano ambienti diversi.", "configuration"), nil
		}
		return result("passed", "Chiave segreta accettata da Stripe e saldo leggibile. Nessun pagamento eseguito; chiave pubblica e consegna webhook non verificate.", "connectivity"), nil
	case "ai":
		if !Truthy(value["api_key"]) {
			return result("not_configured", "Chiave API mancante.", "configuration"), nil
		}
		target, invalid, err := integrationRequest(provider, value)
		switch {
		case err != nil:
			return nil, err
		case invalid:
			return result("failed", "URL del provider non valido.", "configuration"), nil
		case target == nil:
			return nil, errProbe
		}
		header, err := authorization(value["api_key"])
		if err != nil {
			return nil, err
		}
		data, failure, err := c.getJSONRequest(ctx, target, map[string]string{"Authorization": header})
		if err != nil || failure != nil {
			return failure, err
		}
		object, ok := data.(map[string]any)
		var items []any
		if ok {
			items, ok = object["data"].([]any)
		}
		if !ok {
			return result("failed", "Il provider non ha restituito un elenco modelli compatibile.", "connectivity"), nil
		}
		model, present := value["model"]
		if !present {
			return nil, errProbe
		}
		found := false
		for _, item := range items {
			entry, ok := item.(map[string]any)
			if !ok {
				continue
			}
			id := entry["id"]
			if !hashable(id) {
				return nil, errProbe
			}
			if pythonEqualScalar(id, model) {
				found = true
			}
		}
		if !hashable(model) {
			return nil, errProbe
		}
		if !found {
			return result("warning", "Provider raggiungibile, ma il modello principale non compare nell’elenco. Verifica l’identificativo e i permessi. Generazione non provata.", "connectivity"), nil
		}
		return result("passed", "Elenco modelli accessibile con la chiave salvata e modello principale presente. Nessun messaggio generato; capacità del modello e strumenti non provati.", "connectivity"), nil
	}
	return nil, errProbe
}

func validScheme(scheme string) bool {
	if scheme == "" || !(scheme[0] >= 'a' && scheme[0] <= 'z' || scheme[0] >= 'A' && scheme[0] <= 'Z') {
		return false
	}
	for _, r := range scheme {
		if !(r >= 'a' && r <= 'z' || r >= 'A' && r <= 'Z' || r >= '0' && r <= '9' || r == '+' || r == '-' || r == '.') {
			return false
		}
	}
	return true
}

func hashable(value any) bool {
	switch value.(type) {
	case []any, map[string]any:
		return false
	}
	return true
}

// pythonEqualScalar is == for hashable JSON values (bools are ints).
func pythonEqualScalar(a, b any) bool {
	ra, aNumber := number(a)
	rb, bNumber := number(b)
	if aNumber || bNumber {
		return aNumber && bNumber && ra != nil && rb != nil && ra.Cmp(rb) == 0
	}
	switch x := a.(type) {
	case nil:
		return b == nil
	case string:
		y, ok := b.(string)
		return ok && x == y
	}
	return false
}

// number is a JSON number (or bool) as an exact rational; nil for NaN/inf.
func number(value any) (*big.Rat, bool) {
	switch v := value.(type) {
	case bool:
		if v {
			return big.NewRat(1, 1), true
		}
		return new(big.Rat), true
	case Int:
		r, ok := new(big.Rat).SetString(string(v))
		return r, ok
	case float64:
		if math.IsNaN(v) || math.IsInf(v, 0) {
			return nil, true
		}
		return new(big.Rat).SetFloat64(v), true
	}
	return nil, false
}
