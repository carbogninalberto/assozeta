package s3credentials

import (
	"context"
	"math/big"
	"strconv"
	"strings"
	"sync/atomic"
	"time"
	"unicode"
	"unicode/utf8"

	"github.com/aws/aws-sdk-go-v2/aws"
	"github.com/aws/aws-sdk-go-v2/credentials/stscreds"
	"github.com/aws/aws-sdk-go-v2/service/sts"
)

// Retain option presence: the SDK drops empty external IDs and conflates an
// invalid duration (ignored by Boto3) with a parsed zero (rejected by Boto3).
func readRoleProfiles(config, credentials string) (map[string]map[string]string, map[string]string, error) {
	profiles := map[string]map[string]string{}
	aliases := map[string]string{}
	for index, path := range []string{config, credentials} {
		sections, err := readSharedFile(path)
		if err != nil {
			return nil, nil, err
		}
		for section, values := range sections {
			name := section
			if index == 0 && name != "default" {
				if !strings.HasPrefix(name, "profile ") {
					continue
				}
				name = strings.TrimPrefix(name, "profile ")
				if len(name) >= 2 && (name[0] == '\'' || name[0] == '"') && name[len(name)-1] == name[0] {
					aliases[name[1:len(name)-1]] = name
				}
			}
			if profiles[name] == nil {
				profiles[name] = map[string]string{}
			}
			for key, value := range values {
				profiles[name][key] = value
			}
		}
	}
	return profiles, aliases, nil
}

type roleClient struct {
	inner    stscreds.AssumeRoleAPIClient
	duration *int32
	external *string
	session  string
	serial   *string
	token    MFATokenProvider
	used     atomic.Bool
	invalid  bool
}

// The SDK treats quotes in section names literally. Resolve a selected quoted
// section without creating a second file containing credentials.
func sdkProfileName(profiles map[string]map[string]string, aliases map[string]string, name string) string {
	if _, exists := profiles[name]; exists {
		return name
	}
	if alias, exists := aliases[name]; exists {
		return alias
	}
	return name
}

func newRoleClient(inner stscreds.AssumeRoleAPIClient, values map[string]string, token MFATokenProvider) (*roleClient, error) {
	p := &roleClient{inner: inner, session: values["role_session_name"]}
	p.invalid = utf8.RuneCountInString(values["role_arn"]) < 20
	if serial, present := values["mfa_serial"]; present {
		if token == nil {
			return nil, ErrConfiguration
		}
		p.serial, p.token = aws.String(serial), token
	}
	if p.session == "" {
		p.session = "botocore-session-" + strconv.FormatInt(time.Now().Unix(), 10)
	}
	if utf8.RuneCountInString(p.session) < 2 {
		p.invalid = true
	}
	if value, present := values["external_id"]; present {
		if utf8.RuneCountInString(value) < 2 {
			p.invalid = true
		}
		p.external = aws.String(value)
	}
	if value, ok := pythonRoleInteger(values["duration_seconds"]); ok {
		// STS's wire type is int32. Oversized integers cannot be valid STS durations.
		if !value.IsInt64() || value.Int64() < 900 || value.Int64() > 1<<31-1 {
			p.invalid = true
		} else {
			p.duration = aws.Int32(int32(value.Int64()))
		}
	}
	return p, nil
}

// Python int() accepts decimal Unicode digits and single underscores between
// digits. Invalid text leaves DurationSeconds absent in Botocore's role loader.
func pythonRoleInteger(raw string) (*big.Int, bool) {
	raw = strings.TrimSpace(raw)
	var normalized strings.Builder
	previousDigit := false
	for index, r := range raw {
		if index == 0 && (r == '+' || r == '-') {
			normalized.WriteRune(r)
			continue
		}
		if r == '_' {
			if !previousDigit {
				return nil, false
			}
			previousDigit = false
			continue
		}
		digit := -1
		for _, span := range unicode.Digit.R16 {
			if r >= rune(span.Lo) && r <= rune(span.Hi) && (r-rune(span.Lo))%rune(span.Stride) == 0 {
				digit = int((r-rune(span.Lo))/rune(span.Stride)) % 10
				break
			}
		}
		if digit < 0 {
			for _, span := range unicode.Digit.R32 {
				if r >= rune(span.Lo) && r <= rune(span.Hi) && (r-rune(span.Lo))%rune(span.Stride) == 0 {
					digit = int((r-rune(span.Lo))/rune(span.Stride)) % 10
					break
				}
			}
		}
		if digit < 0 {
			return nil, false
		}
		normalized.WriteByte(byte('0' + digit))
		previousDigit = true
	}
	if !previousDigit {
		return nil, false
	}
	return new(big.Int).SetString(normalized.String(), 10)
}

func (p *roleClient) AssumeRole(ctx context.Context, in *sts.AssumeRoleInput, options ...func(*sts.Options)) (*sts.AssumeRoleOutput, error) {
	ctx, cancel := context.WithTimeout(ctx, 10*time.Second)
	defer cancel()
	request := *in
	if p.serial != nil {
		if !p.used.CompareAndSwap(false, true) {
			return nil, ErrCredentials
		}
		code, err := p.token(ctx, *p.serial)
		if err != nil || ctx.Err() != nil {
			return nil, ErrCredentials
		}
		// Botocore validates minimum lengths after prompting, and sends literal
		// values to STS. Character patterns/maximums remain the service's policy.
		if utf8.RuneCountInString(*p.serial) < 9 || utf8.RuneCountInString(code) < 6 {
			return nil, ErrCredentials
		}
		request.SerialNumber, request.TokenCode = p.serial, aws.String(code)
	}
	if p.invalid {
		return nil, ErrCredentials
	}
	request.DurationSeconds = p.duration
	request.ExternalId = p.external
	request.RoleSessionName = aws.String(p.session)
	out, err := p.inner.AssumeRole(ctx, &request, options...)
	if err != nil {
		return nil, ErrCredentials
	}
	// The SDK provider dereferences these fields without validating malformed
	// successful responses. Fail closed before caching or dereferencing them.
	if out == nil || out.Credentials == nil {
		return nil, ErrCredentials
	}
	c := out.Credentials
	if c.AccessKeyId == nil || c.SecretAccessKey == nil || c.SessionToken == nil || c.Expiration == nil || *c.AccessKeyId == "" || *c.SecretAccessKey == "" || *c.SessionToken == "" || !c.Expiration.After(time.Now()) {
		return nil, ErrCredentials
	}
	return out, nil
}
