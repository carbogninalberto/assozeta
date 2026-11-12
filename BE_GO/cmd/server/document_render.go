package main

import (
	"os"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/config"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/service"
)

// documentRenderSettings are the core.settings values the PDF views read:
// DOCUMENT_BYPASS_TOKEN, APP_URL, DEFAULT_TEAM_EMAIL and DEFAULT_SUPPORT_EMAIL
// (f"{WHITELABEL_NAME} <{EMAIL_HOST_SUPPORT}>", None when unset).
func documentRenderSettings(cfg config.Config) service.DocumentRenderSettings {
	support := "None"
	if cfg.EmailHostSupport != nil {
		support = *cfg.EmailHostSupport
	}
	return service.DocumentRenderSettings{BypassToken: os.Getenv("DOCUMENT_BYPASS_TOKEN"), AppURL: cfg.InstanceAdmin.AppURL,
		TeamEmail: cfg.DefaultTeamEmail, SupportEmail: cfg.WhitelabelName + " <" + support + ">"}
}
