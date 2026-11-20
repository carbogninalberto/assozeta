package config

type WhatsAppSettings struct {
	Disabled                                                                             bool
	PhoneID, AccountID, AccessToken, APIVersion, BaseURL, VerifyToken, AppSecret, Routes string
}

func WhatsAppFromEnv(get func(string) string) WhatsAppSettings {
	withDefault := func(key, fallback string) string {
		if value := get(key); value != "" {
			return value
		}
		return fallback
	}
	return WhatsAppSettings{
		PhoneID: get("WHATSAPP_PHONE_NUMBER_ID"), AccountID: get("WHATSAPP_BUSINESS_ACCOUNT_ID"),
		AccessToken: get("WHATSAPP_ACCESS_TOKEN"), VerifyToken: get("WHATSAPP_WEBHOOK_VERIFY_TOKEN"),
		AppSecret: get("WHATSAPP_APP_SECRET"), Routes: withDefault("WHATSAPP_NOTIFICATION_ROUTES", "{}"),
		APIVersion: withDefault("WHATSAPP_API_VERSION", "v23.0"),
		BaseURL:    withDefault("WHATSAPP_API_BASE_URL", "https://graph.facebook.com"),
	}
}
