# Profile settings and integrations — implementing with HTTP parity

`GET`/`POST profile/settings` (`profile_settings`) and `GET`/`PATCH profile/integrations` (`profile_integrations`) are implemented in Go (`BE_GO/internal/{domain,dto,repository,service,handler}/profile_settings.go`). The membership-card get-or-create reuses the login-response service (`service/membership_card_configuration.go`).

## profile/settings

- `@api_view(['GET', 'POST'])`: `Allow` lists GET, POST, OPTIONS; PUT and PATCH are 405. The registry maps `('GET', 'profile/settings')` to `other.settings.read` and `('PATCH', ...)` to `other.settings.update`. There is no POST or OPTIONS mapping, so a restricted collaborator gets `Access denied: No permission mapping for endpoint 'profile/settings'. ...` for those methods. Owners in an athlete-scoped session are refused (`profile_settings` is not an athlete view).
- Every method starts with `user.sport_association`. Athletes get 403 `User is not a sport association` (also on POST, before validation). An owner without a live association gets a 500. The view then takes the association's first membership-card configuration (lowest UUID) and creates it, with its create audit, when there is none. A GET or an invalid POST can therefore write.
- GET renders the 30 settings. `balance_sheet_year` is a string. `table_settings` is `tables_settings`. The relations render as IDs. The response also has the first configuration. `current_year` is the year in which the balance year that contains `datetime.now()` (UTC) starts. A start date `datetime()` rejects in this year or the previous one is a 500.
- POST validates `UserSettingsSerializer`. Every field is optional (model defaults):
  - BooleanFields use DRF's case-insensitive true/false sets. `null` is refused, and lists or dicts are invalid.
  - `balance_sheet_year` and the two durations are ChoiceFields that match on `str(value)` (`"1.0"` and `"True"` are invalid).
  - IntegerFields get the model's `MaxValueValidator` plus the integer range (`0..31`, `0..12`, `-32768..32767`, `-2147483648..2147483647`; `"7.000"` is 7, and strings over 1000 characters are too large).
  - `default_membership_type` is CharField(255), nullable and blank, and trimmed.
  - Two `PaymentCategory` relations accept any category in the default manager, including archived or deleted ones. A bool is an incorrect type. Lists, dicts, floats, negative ints and malformed strings fail through the model `UUIDField` (`“<str(value)>” is not a valid UUID.`). `''` is `None`. A category of another association raises tenant_scope's generic 403, which escapes the other field errors. A global category is allowed.
  - HTML input: an absent BooleanField is False; an empty value of a non-nullable optional field is skipped; empty relations are `None`.
- The view then reads `validated_data[...]` for every field. A missing one is a KeyError 500 before any save. It then calls `user.save()`, and auditlog records the changed fields as `str()` pairs on the owner, resolved `user.sport_association`. Next it reads `request.data['membership_card_configuration']['emit_only_on_approval' | 'customized_template']`. A missing key, a non-dict value or any form body is a 500 after the user save. Finally it saves the configuration. auditlog writes that update in `pre_save` (`str()` of the raw value, `json.dumps(sort_keys=True)` of the template), so `1` audits `['True', '1']` without a change. A value the model `BooleanField.to_python` rejects is a 500 that keeps the audit entry but not the row. Response `{'status': 'success'}`.

## profile/integrations

- `@api_view(['GET', 'PATCH'])`, and the registry maps every method to `other.settings.read`, so a read-only collaborator may PATCH. Any association-scoped session gets 403 `Torna al tuo account per gestire le credenziali.`. Athletes get 403 `User is not a sport association`, and an owner without an association gets a 500.
- PATCH assigns `request.data.get(key, stored)` for `review_url` and `review_url_enabled` with no validation, saves the association and echoes the assigned raw values (`5`, `0`, `"True"`, form strings, the last value of a repeated form key). auditlog compares `str()` values in `pre_save`, resolved `self`. The column conversion then runs, which is `str()` for the URL and `BooleanField.to_python` for the flag. A rejected flag (`'yes'`, `'true'`), `null`, a URL over 255 characters or one containing NUL is a 500 after the audit entry. A non-dict body is an AttributeError 500.

Requests are not atomic. Go commits each Django autocommit step separately (configuration creation, user save, configuration save) and commits a pre_save audit entry before refusing a value the model would reject.

Not ported: a multipart body carrying an uploaded file. Go refuses it with a 500 after the association check and before any write. The configuration get-or-create keeps the login service's association lock. That lock coordinates Go writers only; Django has none (see `BE_GO/internal/service/membership_card_configuration.md`).

Evidence: `TESTS/fixtures/profile_settings_http.json` (59 cases, with the contract computing `current_year` at request time). RED: Django 59/59, Go 404. GREEN: 59/59 on native Django, native Go, Caddy `/api/v1` and `/api/v2`, with exact configuration, user, association, audit and index effects. Rolling back the configuration's pre_save audit when the model rejects the boolean fails `post-card-invalid-boolean-keeps-audit`.
