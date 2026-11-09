package emailrender

import (
	"fmt"
	"math"
	"regexp"
	"strings"
	"time"
)

// InstanceConfig is what instance_email_brand reads from
// InstanceConfiguration.get_config() (objects.first(), by primary key).
type InstanceConfig struct {
	SelfHosted   bool
	PrimaryColor string
	Name         string
	LogoPath     string
	UpdatedAt    time.Time
}

// Settings are the core.settings values the brand reads.
type Settings struct {
	AppURL         string
	WhitelabelName string
	IsWhitelabel   bool
}

const (
	defaultPrimaryColor = "#351DC2" // instance.defaults.DEFAULT_PRIMARY_COLOR
	bakneyLogoURL       = "https://bakney.com/images/logo.png"
	bakneySiteURL       = "https://bakney.com"
)

var (
	colorPattern = regexp.MustCompile(`^#[0-9a-fA-F]{6}$`)
	urlPattern   = regexp.MustCompile(`^https?://`)
)

// Brand is application/templatetags/instance_branding.instance_email_brand
// for the configuration (nil: no InstanceConfiguration row).
func Brand(config *InstanceConfig, settings Settings) (map[string]any, error) {
	selfHosted := config != nil && config.SelfHosted
	palette := Palette(config)
	logo, err := brandLogo(config, settings)
	if err != nil {
		return nil, err
	}
	name := settings.WhitelabelName
	if selfHosted && config.Name != "" {
		name = config.Name
	}
	brand := map[string]any{
		"primary": palette.Primary, "hover": palette.Hover, "foreground": palette.Foreground,
		"app_name": settings.WhitelabelName, "name": name, "self_hosted": selfHosted, "logo": nil,
	}
	if logo != nil {
		brand["logo"] = logo
	}
	return brand, nil
}

// PaletteColors is instance_branding._palette's result.
type PaletteColors struct{ Primary, Hover, Foreground string }

// Palette is instance_branding._palette.
func Palette(config *InstanceConfig) PaletteColors {
	color := defaultPrimaryColor
	if config != nil && config.SelfHosted {
		color = config.PrimaryColor
	}
	if !colorPattern.MatchString(color) {
		color = defaultPrimaryColor
	}
	color = strings.ToLower(color)
	channels := [3]int{}
	for i := range channels {
		fmt.Sscanf(color[1+2*i:3+2*i], "%02x", &channels[i])
	}
	light := luminance(channels)
	foreground, target := "#000000", 255
	if 1.05/(light+.05) >= (light+.05)/.05 {
		foreground, target = "#ffffff", 0
	}
	hover := "#"
	for _, channel := range channels {
		hover += fmt.Sprintf("%02x", hoverChannel(channel, target))
	}
	return PaletteColors{Primary: color, Hover: hover, Foreground: foreground}
}

// luminance sums the weighted linear channels left to right; the explicit
// float64 conversions keep each product rounded (no fused multiply-add),
// as in CPython.
func luminance(channels [3]int) float64 {
	weights := [3]float64{.2126, .7152, .0722}
	sum := 0.0
	for i, channel := range channels {
		sum = float64(sum + float64(math.Float64frombits(linearChannels[channel])*weights[i]))
	}
	return sum
}

// hoverChannel is round(channel * .88 + target * .12) (round half to even).
func hoverChannel(channel, target int) int {
	scaled := float64(float64(channel) * .88)
	mixed := float64(float64(target) * .12)
	return int(math.RoundToEven(scaled + mixed))
}

func brandLogo(config *InstanceConfig, settings Settings) (map[string]any, error) {
	if config != nil && config.SelfHosted {
		path := config.LogoPath
		if path == "" {
			return nil, nil
		}
		if !urlPattern.MatchString(path) {
			path = settings.AppURL + "/" + strings.TrimLeft(path, "/")
		}
		updated := config.UpdatedAt.UTC()
		if updated.Year() < 1000 {
			return nil, unsupported("updated_at year %d", updated.Year())
		}
		// strftime("%Y%m%d%H%M%S%f")
		path += "?v=" + updated.Format("20060102150405") + fmt.Sprintf("%06d", updated.Nanosecond()/1000)
		return map[string]any{"url": path, "href": settings.AppURL}, nil
	}
	if settings.IsWhitelabel {
		return nil, nil
	}
	return map[string]any{"url": bakneyLogoURL, "href": bakneySiteURL}, nil
}
