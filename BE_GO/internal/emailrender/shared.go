package emailrender

// Exports for internal/djtemplate, the general document-template renderer:
// the number formatting below is the same Django code path for both.

// FloatFormat is the floatformat filter for the active language: value is
// nil, string, int64, float64 or Decimal; precision nil or int64. The
// result is a Safe string ("" for None and "").
func FloatFormat(value any, precision any, language string) (any, error) {
	separator, ok := decimalSeparators[language]
	if !ok {
		return nil, unsupported("language %q", language)
	}
	return floatformat(value, precision, separator)
}

// Localize is formats.localize for a Decimal (by its str()) or a float in
// the active language, as render_value_in_context prints them.
func Localize(value any, language string) (string, error) {
	separator, ok := decimalSeparators[language]
	if !ok {
		return "", unsupported("language %q", language)
	}
	switch v := value.(type) {
	case Decimal:
		return localizeDecimal(string(v), separator)
	case float64:
		return localizeFloat(v, separator)
	}
	return "", unsupported("localize %T", value)
}

// FloatRepr is repr(float); non-finite values are refused.
func FloatRepr(value float64) (string, error) { return pythonFloatRepr(value) }

// KnownLanguage reports whether the code is one of Django's LANGUAGES.
func KnownLanguage(language string) bool {
	_, ok := decimalSeparators[language]
	return ok
}
