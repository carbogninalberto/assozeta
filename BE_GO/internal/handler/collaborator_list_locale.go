package handler

import "net/http"

// Keep the collaborator handler's existing internal call sites while sharing
// Django-compatible selection and catalogs with the public manifest.
func collaboratorLanguage(r *http.Request) string {
	return handlerReadRouteLocale.Language(r)
}

func collaboratorText(r *http.Request, kind string) string {
	if kind == "notfound" {
		return handlerReadRouteLocale.Text(r, readRouteNotFound)
	}
	return handlerReadRouteLocale.Text(r, readRouteMessage(kind))
}
