package config

import (
	"fmt"
	"math/big"
	"strings"
)

// parseDataRestoreMaxUpload is DATA_RESTORE_MAX_UPLOAD_BYTES =
// env.int(..., 5 * 1024**3): Python int() of the value (surrounding
// whitespace, one sign, ASCII digits with single underscores between them).
// A value Django would reject at startup is a startup error here too.
func parseDataRestoreMaxUpload(values map[string]string) (int64, error) {
	raw, err := environmentString(values, "DATA_RESTORE_MAX_UPLOAD_BYTES", "5368709120", false)
	if err != nil {
		return 0, err
	}
	text := strings.TrimSpace(raw)
	digits := strings.TrimLeft(text, "+-")
	if len(text)-len(digits) > 1 || digits == "" || strings.HasPrefix(digits, "_") || strings.HasSuffix(digits, "_") ||
		strings.Contains(digits, "__") || strings.Trim(digits, "0123456789_") != "" {
		return 0, fmt.Errorf("DATA_RESTORE_MAX_UPLOAD_BYTES is not an integer")
	}
	value, ok := new(big.Int).SetString(strings.ReplaceAll(text, "_", ""), 10)
	if !ok || !value.IsInt64() {
		return 0, fmt.Errorf("DATA_RESTORE_MAX_UPLOAD_BYTES is out of range")
	}
	return value.Int64(), nil
}

// dataRestoreRecoveryBackups is os.environ.get('DATA_RESTORE_RECOVERY_BACKUPS'):
// the raw value (the deployment .env file is loaded into os.environ), parsed
// by the cleanup task at each run.
func dataRestoreRecoveryBackups(values map[string]string) *string {
	value, present := values["DATA_RESTORE_RECOVERY_BACKUPS"]
	if !present {
		return nil
	}
	return &value
}
