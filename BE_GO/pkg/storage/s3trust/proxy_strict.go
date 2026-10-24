package s3trust

import (
	"bytes"
	"crypto/x509"
	"encoding/asn1"
)

// urllib3's default outer HTTPS-proxy context enables OpenSSL X509_STRICT in
// pinned Python 3.13. These additional constraints were characterized against
// actual one-extension-at-a-time leaf/intermediate/root chains. Direct target
// TLS and the inner CONNECT handshake do not use this stricter proxy context.
// Input must be successfully verified chains, including their trust anchors;
// unrelated certificates supplied on the wire are not alternative chains.
func validStrictProxyChains(chains [][]*x509.Certificate) bool {
	for _, chain := range chains {
		if len(chain) == 0 {
			continue
		}
		valid := true
		for _, cert := range chain {
			selfSigned := bytes.Equal(cert.RawIssuer, cert.RawSubject) && cert.CheckSignature(cert.SignatureAlgorithm, cert.RawTBSCertificate, cert.Signature) == nil
			if !selfSigned && len(cert.AuthorityKeyId) == 0 {
				valid = false
				break
			}
			if cert.IsCA {
				if len(cert.SubjectKeyId) == 0 || cert.KeyUsage&x509.KeyUsageCertSign == 0 || !criticalExtension(cert, asn1.ObjectIdentifier{2, 5, 29, 19}) {
					valid = false
					break
				}
			} else if cert.KeyUsage&x509.KeyUsageCertSign != 0 {
				valid = false
				break
			}
		}
		if valid {
			return true
		}
	}
	return false
}

func criticalExtension(cert *x509.Certificate, oid asn1.ObjectIdentifier) bool {
	for _, extension := range cert.Extensions {
		if extension.Id.Equal(oid) {
			return extension.Critical
		}
	}
	return false
}
