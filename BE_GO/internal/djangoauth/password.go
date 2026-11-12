package djangoauth

import (
	"bufio"
	"bytes"
	"compress/gzip"
	_ "embed"
	"regexp"
	"strconv"
	"sync"
)

// commonPasswords is django/contrib/auth/common-passwords.txt.gz from Django
// 5.2.18 (BSD-3-Clause; see BE_GO/THIRD_PARTY_NOTICES.md).
//
//go:embed common-passwords.txt.gz
var commonPasswords []byte

var (
	commonOnce sync.Once
	commonSet  map[string]struct{}
	commonErr  error
)

func loadCommon() (map[string]struct{}, error) {
	commonOnce.Do(func() {
		reader, err := gzip.NewReader(bytes.NewReader(commonPasswords))
		if err != nil {
			commonErr = err
			return
		}
		defer reader.Close()
		set := make(map[string]struct{}, 20000)
		scanner := bufio.NewScanner(reader)
		for scanner.Scan() {
			set[PythonStrip(scanner.Text())] = struct{}{}
		}
		commonSet, commonErr = set, scanner.Err()
	})
	return commonSet, commonErr
}

// UserAttributes are the unsaved User values UserAttributeSimilarityValidator
// reads (username, first_name, last_name, email) with their verbose names.
type UserAttributes struct {
	Username, FirstName, LastName, Email string
}

var nonWord = regexp.MustCompile(`[^\p{L}\p{N}_]+`)

// quickRatio is difflib.SequenceMatcher(a=a, b=b).quick_ratio().
func quickRatio(a, b []rune) float64 {
	total := len(a) + len(b)
	if total == 0 {
		return 1.0
	}
	available := map[rune]int{}
	for _, r := range b {
		available[r]++
	}
	matches := 0
	for _, r := range a {
		if available[r] > 0 {
			available[r]--
			matches++
		}
	}
	return 2.0 * float64(matches) / float64(total)
}

// similar is UserAttributeSimilarityValidator.validate (max_similarity 0.7).
func similar(password string, user UserAttributes) (string, bool) {
	const maximum = 0.7
	lowered := []rune(PythonLower(password))
	for _, attribute := range []struct{ value, verbose string }{
		{user.Username, "nome utente"}, {user.FirstName, "nome"}, {user.LastName, "cognome"}, {user.Email, "indirizzo email"},
	} {
		if attribute.value == "" {
			continue
		}
		value := PythonLower(attribute.value)
		parts := append(nonWord.Split(value, -1), value)
		for _, part := range parts {
			partRunes := []rune(part)
			// exceeds_maximum_length_ratio skips parts far shorter than the password.
			if len(lowered) >= 10*len(partRunes) && float64(len(partRunes)) < maximum/2*float64(len(lowered)) {
				continue
			}
			if quickRatio(lowered, partRunes) >= maximum {
				return attribute.verbose, true
			}
		}
	}
	return "", false
}

// ValidatePassword is validate_password(password, user) with the project's
// AUTH_PASSWORD_VALIDATORS in order (similarity, minimum length 8, common,
// numeric). It returns every message, as ValidationError.messages does, in
// the active Italian catalog (the minimum length message has no Italian
// entry in Django 5.2.18 and stays English).
func ValidatePassword(password string, user UserAttributes) ([]string, error) {
	var messages []string
	if verbose, ok := similar(password, user); ok {
		messages = append(messages, "La password è troppo simile a "+verbose+".")
	}
	if runeCount(password) < 8 {
		messages = append(messages, "This password is too short. It must contain at least 8 characters.")
	}
	common, err := loadCommon()
	if err != nil {
		return nil, err
	}
	if _, ok := common[PythonStrip(PythonLower(password))]; ok {
		messages = append(messages, "Questa password è troppo comune.")
	}
	if pythonIsDigit(password) {
		messages = append(messages, "Questa password è interamente numerica.")
	}
	return messages, nil
}

// maxLength is MaxLengthValidator's Italian message.
func maxLength(limit, actual int) string {
	unit := "caratteri"
	if limit == 1 {
		unit = "carattere"
	}
	return "Assicurati che questo valore non contenga più di " + strconv.Itoa(limit) + " " + unit + " (ne ha " + strconv.Itoa(actual) + ")."
}
