"""
EdDSA Algorithm Support for SimpleJWT.

SimpleJWT's default TokenBackend only supports a limited set of algorithms
(HS256, HS384, HS512, RS256, RS384, RS512, ES256, ES384, ES512).

This module patches SimpleJWT to add EdDSA support.
"""


def patch_simplejwt_for_eddsa():
    """
    Add EdDSA to SimpleJWT's allowed algorithms.

    Must be called after Django apps are loaded but before SimpleJWT
    tries to create tokens.
    """
    import rest_framework_simplejwt.backends as simplejwt_backends
    simplejwt_backends.ALLOWED_ALGORITHMS.add("EdDSA")
