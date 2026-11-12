package s3trust

import (
	"crypto/x509"
	"encoding/json"
	"encoding/pem"
	"os"
	"testing"
	"time"
)

func TestPinnedOpenSSLProxyStrictChains(t *testing.T) {
	data, err := os.ReadFile("testdata/proxy-strict-chains.json")
	if err != nil {
		t.Fatal(err)
	}
	var fixture struct {
		Cases []struct {
			Name         string `json:"name"`
			Root         string `json:"root_pem"`
			Intermediate string `json:"intermediate_pem"`
			Leaf         string `json:"leaf_pem"`
			Strict       bool   `json:"openssl_strict"`
			Ordinary     bool   `json:"go_ordinary"`
		}
	}
	if err = json.Unmarshal(data, &fixture); err != nil {
		t.Fatal(err)
	}
	if len(fixture.Cases) != 30 {
		t.Fatal("strict fixture case count differs")
	}
	seen := map[string]bool{}
	var good, bad []*x509.Certificate
	for _, c := range fixture.Cases {
		t.Run(c.Name, func(t *testing.T) {
			if seen[c.Name] {
				t.Fatal("duplicate strict fixture case")
			}
			seen[c.Name] = true
			var certificates []*x509.Certificate
			for _, text := range []string{c.Leaf, c.Intermediate, c.Root} {
				block, _ := pem.Decode([]byte(text))
				if block == nil {
					t.Fatal("invalid fixture PEM")
				}
				cert, e := x509.ParseCertificate(block.Bytes)
				if e != nil {
					if c.Strict || c.Ordinary {
						t.Fatal(e)
					}
					return
				}
				certificates = append(certificates, cert)
			}
			roots := x509.NewCertPool()
			roots.AddCert(certificates[2])
			intermediates := x509.NewCertPool()
			intermediates.AddCert(certificates[1])
			chains, e := certificates[0].Verify(x509.VerifyOptions{Roots: roots, Intermediates: intermediates, DNSName: "proxy.test", CurrentTime: certificates[0].NotBefore.Add(time.Minute), KeyUsages: []x509.ExtKeyUsage{x509.ExtKeyUsageServerAuth}})
			if (e == nil) != c.Ordinary {
				t.Fatal("ordinary Go verification changed", e)
			}
			accepted := e == nil && validStrictProxyChains(chains)
			if accepted != c.Strict {
				t.Fatalf("strict=%v want%v", accepted, c.Strict)
			}
			if c.Name == "baseline" {
				good = chains[0]
			}
			if c.Name == "leaf-missing-aki" {
				bad = chains[0]
			}
		})
	}
	if good == nil || bad == nil {
		t.Fatal("alternative verified-chain controls missing")
	}
	if !validStrictProxyChains([][]*x509.Certificate{bad, good}) || validStrictProxyChains([][]*x509.Certificate{bad}) || validStrictProxyChains(nil) {
		t.Fatal("alternative verified-chain selection differs")
	}
}
