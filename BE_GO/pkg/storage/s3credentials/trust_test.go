package s3credentials

import (
	"context"
	"net/http"
	"path/filepath"
	"testing"

	sdkconfig "github.com/aws/aws-sdk-go-v2/config"
	"github.com/aws/aws-sdk-go-v2/credentials"
)

func TestSDKTrustDelegationIsExplicitAndDefaultUnchanged(t *testing.T) {
	directory := isolatedEnvironment(t)
	t.Setenv("AWS_CA_BUNDLE", filepath.Join(directory, "private-missing.pem"))
	base := []func(*sdkconfig.LoadOptions) error{sdkconfig.WithCredentialsProvider(credentials.NewStaticCredentialsProvider("fixture", "fixture", ""))}
	if _, err := sdkconfig.LoadDefaultConfig(context.Background(), base...); err == nil {
		t.Fatal("upstream default stopped loading configured CA")
	}
	missingClient := append(append([]func(*sdkconfig.LoadOptions) error{}, base...), sdkconfig.WithCustomCABundleHandledByHTTPClient())
	if _, err := sdkconfig.LoadDefaultConfig(context.Background(), missingClient...); err == nil {
		t.Fatal("delegation accepted without explicit HTTP client")
	}
	owned := append(missingClient, sdkconfig.WithHTTPClient(boundedHTTP{http.DefaultClient}))
	if _, err := sdkconfig.LoadDefaultConfig(context.Background(), owned...); err != nil {
		t.Fatal("explicit client delegation failed", err)
	}
	provider, err := New(context.Background(), Options{AccessKey: "fixture", SecretKey: "fixture"}, nil)
	if err != nil {
		t.Fatal("CA file eagerly loaded during provider initialization", err)
	}
	if _, err = provider.Retrieve(context.Background()); err != nil {
		t.Fatal("static credentials depended on HTTPS trust", err)
	}
}
