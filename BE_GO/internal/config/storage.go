package config

import (
	"errors"
	"strings"

	"github.com/carbogninalberto/assozeta/BE_GO/pkg/validate"
)

// StorageSettings preserves core.settings and django-storages defaults. Reading
// settings does not open profiles, discover credentials or contact a provider.
type StorageSettings struct {
	AccessKey      string  `json:"-"`
	SecretKey      string  `json:"-"`
	Bucket         string  `json:"-"`
	Region         string  `json:"-"`
	Endpoint       string  `json:"-"`
	Location       string  `json:"-"`
	DefaultACL     *string `json:"-"`
	FileOverwrite  bool    `json:"-"`
	UseObjectACL   bool    `json:"-"`
	PublicBaseURL  string  `json:"-"`
	StoreOnDisk    bool    `json:"-"`
	StorageDir     string  `json:"-"`
	Backend        string  `json:"-"`
	CacheControl   string  `json:"-"`
	SessionToken   *string `json:"-"`
	SessionProfile *string `json:"-"`
}

func (s StorageSettings) String() string   { return "Assozeta storage configuration (redacted)" }
func (s StorageSettings) GoString() string { return s.String() }

// ValidateBackend follows the lazy S3Storage constructor's validation boundary.
// Settings can load successfully before this boundary is reached.
func (s StorageSettings) ValidateBackend() error {
	if strings.HasPrefix(s.Location, "/") {
		return errors.New("invalid AWS_LOCATION")
	}
	if s.SessionProfile != nil && (s.AccessKey != "" || s.SecretKey != "") {
		return errors.New("AWS_S3_SESSION_PROFILE conflicts with static credentials")
	}
	return nil
}

func parseStorage(values map[string]string) (StorageSettings, error) {
	var s StorageSettings
	for _, field := range []struct {
		name  string
		value *string
	}{
		{"AWS_ACCESS_KEY_ID", &s.AccessKey}, {"AWS_SECRET_ACCESS_KEY", &s.SecretKey},
		{"AWS_STORAGE_BUCKET_NAME", &s.Bucket}, {"AWS_S3_REGION_NAME", &s.Region},
		{"AWS_S3_ENDPOINT_URL", &s.Endpoint}, {"AWS_LOCATION", &s.Location},
	} {
		value, err := environmentString(values, field.name, "", true)
		if err != nil {
			return StorageSettings{}, err
		}
		*field.value = value
	}
	var err error
	s.StoreOnDisk, err = registrationFlag(values, "STORE_ON_DISK", false)
	if err != nil {
		return StorageSettings{}, err
	}
	// Django evaluates this setting before unconditionally replacing it with "".
	if _, err = environmentString(values, "STORAGE_DIR", "storage", false); err != nil {
		return StorageSettings{}, err
	}
	endpoint := s.Endpoint
	if !strings.Contains(endpoint, "://") {
		endpoint = "//" + endpoint
	}
	origin, valid := validate.PythonURLOrigin(endpoint)
	if !valid {
		return StorageSettings{}, errors.New("invalid AWS_S3_ENDPOINT_URL")
	}
	// Only ASCII can match the fixed suffix. Unicode simple case folding would
	// incorrectly accept e.g. dotted capital I as a literal ASCII i.
	host := strings.Map(func(r rune) rune {
		if r >= 'A' && r <= 'Z' {
			return r + ('a' - 'A')
		}
		return r
	}, origin.RawHostname())
	spaces := host == "digitaloceanspaces.com" || strings.HasSuffix(host, ".digitaloceanspaces.com")
	s.FileOverwrite, err = registrationFlag(values, "AWS_S3_FILE_OVERWRITE", false)
	if err != nil {
		return StorageSettings{}, err
	}
	s.UseObjectACL, err = registrationFlag(values, "AWS_S3_USE_OBJECT_ACL", spaces)
	if err != nil {
		return StorageSettings{}, err
	}
	publicURL := ""
	if spaces {
		publicURL = "https://" + s.Bucket + "." + s.Region + ".digitaloceanspaces.com"
	}
	s.PublicBaseURL, err = environmentString(values, "AWS_S3_PUBLIC_BASE_URL", publicURL, false)
	if err != nil {
		return StorageSettings{}, err
	}
	s.PublicBaseURL = strings.TrimRight(s.PublicBaseURL, "/")
	acl, err := environmentString(values, "AWS_DEFAULT_ACL", "", false)
	if err != nil {
		return StorageSettings{}, err
	}
	if acl != "" {
		s.DefaultACL = &acl
	}
	// django-storages uses raw os.environ values here, without Env proxies.
	firstNonempty := func(names ...string) *string {
		for _, name := range names {
			if value := values[name]; value != "" {
				return &value
			}
		}
		return nil
	}
	s.SessionToken = firstNonempty("AWS_SESSION_TOKEN", "AWS_SECURITY_TOKEN")
	s.SessionProfile = firstNonempty("AWS_S3_SESSION_PROFILE")
	s.Backend = "storages.backends.s3boto3.S3Boto3Storage"
	s.CacheControl = "max-age=86400"
	return s, nil
}
