package s3trust

import (
	"crypto/x509"
	_ "embed"
	"sync"
)

// Default storage roots are the same explicitly pinned certifi bundle used by
// Django/botocore. Platform roots and SSL_CERT_FILE/SSL_CERT_DIR are not additions
// to that trust policy. Configured AWS/shared/requests bundles still replace it.
//
//go:embed certifi/cacert.crt
var defaultBundle []byte

var parsedDefaultRoots = sync.OnceValue(func() *x509.CertPool {
	roots := x509.NewCertPool()
	if !roots.AppendCertsFromPEM(defaultBundle) {
		// This immutable build asset is checked against Python in CI. Never
		// return nil here, since nil would silently select system roots.
		panic("invalid embedded storage trust bundle")
	}
	return roots
})

func defaultRoots() *x509.CertPool {
	return parsedDefaultRoots().Clone()
}
