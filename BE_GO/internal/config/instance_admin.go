package config

import (
	"math/big"
	"strings"
	"unicode"
)

// InstanceAdminSettings are the core.settings values read by the owner-only
// instance administration views (BE/instance/administration.py,
// operations.py, integrations.py, diagnostics.py). Invalid* flags record
// values Django would reject while computing a response; the service answers
// those requests with the server error page instead of failing startup.
type InstanceAdminSettings struct {
	AppURL            string `json:"-"`
	ConfiguredVersion string
	DeploymentMode    string
	// Environment email (email_configuration.effective_email).
	EmailHost, EmailUsername string `json:"-"`
	EmailPassword            string `json:"-"`
	EmailPort                int64
	EmailPortInvalid         bool
	EmailUseTLS, EmailUseSSL bool
	// parseaddr(DEFAULT_FROM_EMAIL); FromInvalid when the simple form does not apply.
	FromName, FromAddress string `json:"-"`
	FromInvalid           bool
	// Environment integrations (integration_configuration.PROVIDERS).
	AIAPIKey, AIModel, AICheapModel, AIBaseURL string `json:"-"`
	// MCP_AGENT_* in AI_FIELDS order; AIIntegersInvalid when env.int fails.
	AIIntegers                                      [6]int64
	AIIntegersInvalid                               bool
	StripePublicKey, StripeKey, StripeWebhookSecret string `json:"-"`
}

func parseInstanceAdmin(values map[string]string) (InstanceAdminSettings, error) {
	var s InstanceAdminSettings
	var err error
	host, err := environmentString(values, "APP_HOST", "", false)
	if err != nil {
		return s, err
	}
	if s.AppURL, err = environmentString(values, "APP_URL", "https://"+host, false); err != nil {
		return s, err
	}
	s.AppURL = strings.TrimRight(s.AppURL, "/")
	if s.ConfiguredVersion, err = environmentString(values, "ASSOZETA_VERSION", "", false); err != nil {
		return s, err
	}
	if s.DeploymentMode, err = environmentString(values, "ASSOZETA_DEPLOYMENT_MODE", "development", false); err != nil {
		return s, err
	}
	// EMAIL_HOST, EMAIL_PORT, EMAIL_HOST_USER and EMAIL_HOST_PASSWORD are
	// os.environ.get values (no proxies); the TLS/SSL flags use env.bool.
	s.EmailHost, s.EmailPassword = values["EMAIL_HOST"], values["EMAIL_HOST_PASSWORD"]
	user, present := values["EMAIL_HOST_USER"]
	s.EmailUsername = user
	if !present {
		user = "None"
	}
	s.EmailPort = 465
	if port := values["EMAIL_PORT"]; port != "" {
		number, ok := pythonInt(port)
		s.EmailPort, s.EmailPortInvalid = number, !ok
	}
	if s.EmailUseTLS, err = registrationFlag(values, "EMAIL_USE_TLS", false); err != nil {
		return s, err
	}
	if s.EmailUseSSL, err = registrationFlag(values, "EMAIL_USE_SSL", true); err != nil {
		return s, err
	}
	whitelabel, err := environmentString(values, "WHITELABEL_NAME", "Bakney Sport", false)
	if err != nil {
		return s, err
	}
	s.FromName, s.FromAddress, s.FromInvalid = simpleFromAddress(whitelabel, user)
	targets := []*string{&s.AIAPIKey, &s.AIModel, &s.AICheapModel, &s.AIBaseURL, &s.StripePublicKey, &s.StripeKey, &s.StripeWebhookSecret}
	names := []string{"AI_API_KEY", "AI_MODEL", "AI_CHEAP_MODEL", "AI_BASE_URL", "STRIPE_PUBLIC_KEY", "STRIPE_KEY", "STRIPE_WEBHOOK_SECRET"}
	defaults := []string{"", "deepseek-v4-flash", "deepseek-v4-flash", "https://api.deepseek.com", "", "", ""}
	for index, target := range targets {
		if *target, err = environmentString(values, names[index], defaults[index], false); err != nil {
			return s, err
		}
	}
	for index, name := range []string{"MCP_AGENT_MAX_ITERATIONS", "MCP_AGENT_MAX_RESULTS", "MCP_AGENT_QUERY_TIMEOUT",
		"MCP_AGENT_WS_RATE_LIMIT", "MCP_AGENT_WS_TIMEOUT", "MCP_AGENT_HISTORY_CAP"} {
		fallback := []int64{15, 5000, 10, 10, 240, 50}[index]
		text, err := environmentString(values, name, "", false)
		if err != nil {
			return s, err
		}
		s.AIIntegers[index] = fallback
		if _, ok := values[name]; ok {
			number, valid := pythonInt(text)
			s.AIIntegers[index] = number
			s.AIIntegersInvalid = s.AIIntegersInvalid || !valid
		}
	}
	return s, nil
}

// pythonInt is int(text) for decimal text that fits an int64.
func pythonInt(text string) (int64, bool) {
	text = strings.TrimFunc(text, unicode.IsSpace)
	sign := ""
	if strings.HasPrefix(text, "+") || strings.HasPrefix(text, "-") {
		sign, text = text[:1], text[1:]
	}
	if text == "" || strings.HasPrefix(text, "_") || strings.HasSuffix(text, "_") || strings.Contains(text, "__") {
		return 0, false
	}
	for _, r := range text {
		if (r < '0' || r > '9') && r != '_' {
			return 0, false
		}
	}
	number, ok := new(big.Int).SetString(sign+strings.ReplaceAll(text, "_", ""), 10)
	if !ok || !number.IsInt64() {
		return 0, false
	}
	return number.Int64(), true
}

// simpleFromAddress is email.utils.parseaddr(f"{name} <{user}>") for a display
// name of plain atoms and an address without quoting, comments or routes.
// Other forms are reported invalid (refused) rather than approximated.
func simpleFromAddress(name, user string) (string, string, bool) {
	atom := func(text string, dots bool) bool {
		for _, r := range text {
			if r > 127 || !(unicode.IsLetter(r) || unicode.IsDigit(r) || strings.ContainsRune("!#$%&'*+/=?^_`{|}~-", r) || dots && strings.ContainsRune(".@", r)) {
				return false
			}
		}
		return true
	}
	words := strings.Fields(name)
	for _, word := range words {
		if !atom(word, false) {
			return "", "", true
		}
	}
	if !atom(user, true) || strings.Count(user, "@") > 1 || strings.HasPrefix(user, ".") || strings.HasSuffix(user, ".") ||
		strings.Contains(user, "..") || strings.HasPrefix(user, "@") || strings.HasSuffix(user, "@") {
		return "", "", true
	}
	if user == "None" {
		user = ""
	}
	return strings.Join(words, " "), user, false
}
