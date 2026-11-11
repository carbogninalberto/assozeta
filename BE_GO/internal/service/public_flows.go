package service

// PublicUploadTooLarge is upload_too_large_message(limit) of the flows open
// without an account.
func PublicUploadTooLarge(limit int64) string { return certificateTooLarge(limit) }
