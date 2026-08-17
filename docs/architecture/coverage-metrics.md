# Coverage metrics (auto-generated)

Generated from `docs/matrix/architecture-inventory.json` at build time.

## Totals
- backend endpoint patterns: 353
- permission registry patterns: 241
- front-end routes: 85
- routes with explicit permission checks: 49
- routes without explicit permission checks: 36
- unmapped backend endpoints: 89
- permissions without backend match: 1

## Backend route volume by top-level prefix

| prefix | patterns |
| --- | ---: |
| subscription | 51 |
| course | 25 |
| payment | 20 |
| camps-and-retreats | 16 |
| document | 16 |
| communications | 15 |
| modules | 13 |
| instructor | 12 |
| personas | 11 |
| balance-sheet | 10 |
| profile | 10 |
| association | 8 |
| carnet | 8 |
| oauth2 | 8 |
| documents | 7 |
| invoice | 7 |
| carnet-subscription | 6 |
| course-subscriptions | 6 |
| folders | 6 |
| saved-reports | 6 |
| stripe | 6 |
| supplier | 6 |
| templates | 6 |
| google | 5 |
| invoice-customers | 5 |
| invoice-suppliers | 5 |
| audit-logs | 4 |
| collaborators | 4 |
| statistic | 4 |
| calendar | 3 |
| schema | 3 |
| two-fa | 3 |
| attendance-day | 2 |
| billing | 2 |
| invoice-bulk | 2 |
| payment-bulk | 2 |
| personas-subscriptions | 2 |
| sport-associations | 2 |
| testimonials | 2 |
| (root) | 1 |
| api-auth | 1 |
| attendance | 1 |
| blog | 1 |
| chat | 1 |
| check-inconsistencies | 1 |
| config | 1 |
| configure | 1 |
| course-installment | 1 |
| course-locations | 1 |
| export-all-data | 1 |
| healthz | 1 |
| instance | 1 |
| logo | 1 |
| logo.png | 1 |
| manifest.json | 1 |
| onboarding | 1 |
| printing | 1 |
| readyz | 1 |
| reconfigure | 1 |
| search | 1 |
| setup-token | 1 |
| silk | 1 |
| status | 1 |

## Frontend route volume by top-level prefix

| prefix | routes |
| --- | ---: |
| course | 13 |
| members | 12 |
| subscription | 7 |
| stripe | 5 |
| communication | 4 |
| payment | 3 |
| invoice | 2 |
| search | 2 |
| subscribe | 2 |
| (root) | 1 |
| * | 1 |
| accounting | 1 |
| accounting-transfer | 1 |
| archive | 1 |
| attendance-scanner-mode | 1 |
| audit | 1 |
| balance-sheet | 1 |
| billing-checkout | 1 |
| calendar | 1 |
| camps-and-retreats | 1 |
| card | 1 |
| carnet | 1 |
| connected-collaborators | 1 |
| customers-invoice | 1 |
| email-builder | 1 |
| error | 1 |
| forms | 1 |
| invite | 1 |
| login | 1 |
| personas | 1 |
| profile | 1 |
| report | 1 |
| reset | 1 |
| saved-reports | 1 |
| shared-calendar | 1 |
| subscribe-multiple | 1 |
| subscribefamily | 1 |
| suppliers-and-customers | 1 |
| suppliers-invoice | 1 |
| templates | 1 |
| third-party-licenses | 1 |
| tools | 1 |
| update-tutors | 1 |
| welcome | 1 |

## Backend app route volume

| app | routes |
| --- | ---: |
| application | 304 |
| chat | 1 |
| communications | 15 |
| core | 10 |
| docmanager | 16 |
| instance | 8 |

## Frontend routes without explicit permission checks

- connected-collaborators
- profile
- search
- search/profile/*?/*?
- card/*/*?/*?
- forms/*/*?
- subscribe/*?
- subscribe/*/*
- subscribe-multiple/*?/*?/*?
- invite/*
- error
- subscription/list/*?
- carnet/list
- subscription/list/detail/*/attendance?
- subscription/list/detail/*/calendar?
- subscription/list/detail/*/carnet?
- camps-and-retreats/forms/*?
- shared-calendar/*?
- third-party-licenses
- login
- stripe/onboarding
- stripe/onboarded
- stripe/pay/*?/*?
- stripe/cart-pay
- stripe/payment/done
- update-tutors
- subscription
- billing-checkout
- subscription/upgrade
- tools/sport-associations-manager
- welcome
- attendance-scanner-mode
- email-builder/*/*
- subscribefamily/*/*
- reset
- *
