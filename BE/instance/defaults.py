"""
Default configuration values for self-hosted instances.
"""

DEFAULT_PRIMARY_COLOR = "#351DC2"

DEFAULT_DISPLAY_SETTINGS = {
    "general": {
        "searchBar": True,
        "cookieConsent": True,
        "showDemoButton": False
    },
    "login": {
        "showSportAssociationCreation": False,
        "allowOauthLogin": True,
        "showFooterInfo": True,
        "allowOnlyAthletes": False,
        "showTestimonials": False,
        "termsAndConditionsUrl": ""
    },
    "navbar": {
        "showAffiliateLink": False,
        "showReview": False,
        "showNotifications": True
    },
    "sidebar": {
        "showPlanUpgrades": False,
        "showPrivacyPolicy": True,
        "showTermsOfService": True,
        "showPreferences": True,
        "showManual": True
    }
}

SUPPORTED_FEATURES = ["import", "export", "oauth"]
