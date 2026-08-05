from django.urls import path, include

from .views.archive_views import FolderViewSet, DocumentArchiveViewSet, SportAssociationModuleTemplatesViewSet
from .views.export_views import AssociationExportViewSet, AssociationImportViewSet
from .views.camp_and_retreats_views import camps_and_retreats_list, camps_and_retreats_add, camps_and_retreats_update, \
    camps_and_retreats_delete, camps_and_retreats_info, camps_and_retreats_periods_add, \
    camps_and_retreats_periods_update, camps_and_retreats_periods_delete, camps_and_retreats_periods_services_add, \
    camps_and_retreats_periods_services_update, camps_and_retreats_periods_services_delete, \
    camps_and_retreats_periods_info, camps_and_retreats_subscriptions_list, camps_and_retreats_subscriptions_add, \
    camps_and_retreats_subscriptions_update, camps_and_retreats_subscriptions_delete
from .views.google_views import google_oauth2callback, google_calendar_config, google_calendar_list, \
    google_calendar_export_course, google_check
from .views.attendee_views import calendar_update, calendar, attendees, attendees_update, subscription_attendance, \
    subscription_calendar, full_events_calendar, full_events_calendar_export, full_events_calendar_update
from .views.balance_sheet_views import balance_sheet, balance_sheet_archived, balance_sheet_accounts_list, \
    balance_sheet_accounts_add, balance_sheet_accounts_update, balance_sheet_accounts_delete, \
    balance_sheet_accounts_transfer_list, balance_sheet_accounts_transfer_add, balance_sheet_accounts_transfer_update, \
    balance_sheet_accounts_transfer_delete
from .views.billing_views import billing_active_plan, billing_checkout
from .views.carnet_views import carnet_list, carnet_add, carnet_delete, carnet_info, carnet_assign, carnet_update, \
    carnet_replace, carnet_unassign, carnet_subscription_enable, carnet_subscription_disable, carnet_subscription_list, \
    carnet_subscription_delete, carnet_subscription_update, carnet_subscription_topup
from .views.collaborator_views import collaborators_list, collaborators_add, collaborators_update, collaborators_delete
from .views.course_views import course_list, course_add, course_update, course_enable, course_disable, course_delete, \
    course_overview, course_overview_delete, course_overview_add, \
    course_overview_update, course_installment_make_payment, course_tags_list, course_tags_add, course_tags_update, \
    course_tags_delete, course_tags_assign, course_tags_unassign, course_pin, CourseLocationViewSet, \
    CourseSubscriptionViewSet
from .views.modules_views import modules_list, modules_check_link, modules_add, modules_custom_link_info, \
    modules_delete, modules_overview, modules_response_add, modules_response_approve, modules_response_delete, \
    modules_response_add_attachment, modules_response_export, modules_update, modules_duplicate
from .views.onboarding_views import onboarding_update
from .views.personas_views import AssociateViewSet, AssociateSubscriptionViewSet
from .views.printing_views import printing_generate
from .views.supplier_views import supplier_delete, supplier_list, supplier_add, supplier_update, supplier_bulk_delete, \
    supplier_info
from .views.instructor_views import instructor_list, instructor_add, instructor_info, instructor_update, \
    instructor_delete, instructor_hours_list, instructor_hours_add, instructor_hours_delete, instructor_hours_update, \
    instructor_hours_add_compensation, instructor_hours_calculate, instructor_report
from .views.invoice_views import invoice_list, invoice_list_export, invoice_list_archived, invoice_delete, \
    invoice_update, invoice_suppliers_list, invoice_suppliers_update, invoice_suppliers_add, invoice_suppliers_delete, \
    invoice_suppliers_stats, invoice_bulk_delete, invoice_bulk_archive, invoice_customers_list, invoice_customers_add, \
    invoice_customers_stats, invoice_customers_delete, invoice_customers_update, invoice_send
from .views.profile_views import profile_update, profile_update_subscription_template, profile_image, \
    profile_update_password, profile_associates_course, profile_associates_sport_association, \
    profile_settings, profile_info, profile_settings_tables, sport_association_list, \
    testimonials_update, testimonials_create, profile_integrations, sport_association_admin_update, export_all_data
from .views.search_views import search_all, search_profile
from .views.statistic_views import statistic_dashboard, statistic_athlete_dashboard, check_inconsistencies, \
    statistic_report, attendance_day_mark_absent, attendance_day_delete, statistic_dashboard_layout, attendance_mark
from .views.auth_views import oauth2_login, oauth2_signup, oauth2_reset_password, oauth2_check_email, \
    oauth2_check_username, oauth2_refresh_token, partial_signup, oauth2_delete_account
from .views.stripe_views import stripe_on_boarding, stripe_complete_on_boarding, stripe_info, \
    stripe_pay, stripe_webhook, stripe_multiple_pay
from .views.subscriptions_views import subscription_add, subscription_sign, \
    subscription_list, subscription_medical_certificate_upload, subscription_info, \
    subscription_approve, subscription_reject, subscription_list_export, subscription_delete, subscription_edit, \
    subscription_import_upload, subscription_associates_draft_list, subscription_associates_draft_edit, \
    subscription_associates_draft_delete, subscription_list_archived, \
    subscription_medical_certificate_set_certificate_expiration, subscription_archive, \
    subscription_medical_certificate_send_email_reminder, subscription_associates_draft_add, subscription_update, \
    subscription_transfer, subscription_tags_list, subscription_tags_add, subscription_tags_update, \
    subscription_tags_delete, subscription_tags_assign, subscription_tags_unassign, \
    subscription_medical_certificate_edit, subscription_upload_document, subscription_delete_document, \
    subscription_list_all, subscription_associates_draft_approve, subscription_import_status, \
    subscription_calculate_tax_code, subscription_medical_appointments_list, subscription_medical_appointments_add, \
    subscription_medical_appointments_delete, subscription_renew, subscription_card, \
    subscription_associates_draft_bulk_delete, SubscriptionMembershipViewSet, subscription_payments, \
    subscription_generate_token_link, validate_token_link_and_get_subscriptions, \
    get_associations_for_federation
from .views.payment_views import payment_list, payment_add, payment_bulk_add, payment_update, payment_approve, payment_info, \
    payment_delete, payment_list_export, payment_request, \
    payment_category_list, payment_category_add, payment_category_update, payment_category_delete, \
    payment_generate_invoice, payment_stats, payment_bulk_archive, payment_archive, payment_simulation_partial_quotes, \
    payment_simulation_partial_quotes_apply, payment_cancel, payment_sign, payment_bulk_delete
from rest_framework import routers

from .views.two_fa_views import two_fa_info, two_fa_setup, two_fa_update
from .views.audit_views import audit_log_list, audit_log_detail, audit_log_models, audit_log_stats
from .views.report_views import SavedReportViewSet

router = routers.DefaultRouter()
router.register(r'course-locations', CourseLocationViewSet, basename='course-location')
router.register(r'folders', FolderViewSet)
router.register(r'documents', DocumentArchiveViewSet)
router.register(r'templates', SportAssociationModuleTemplatesViewSet)
router.register(r'subscription/memberships', SubscriptionMembershipViewSet, basename='subscription-membership')
router.register(r'course-subscriptions', CourseSubscriptionViewSet, basename='course-subscription')
router.register(r'personas', AssociateViewSet, basename='persona')
router.register(r'personas-subscriptions', AssociateSubscriptionViewSet, basename='persona-subscriptions')

urlpatterns = [
    path('', include(router.urls)),
    # Health endpoint moved to WebSocket: ws://host/ws/health/
    path(r'check-inconsistencies', check_inconsistencies, name="check_inconsistencies"),
    path(r'oauth2/login', oauth2_login),
    path(r'oauth2/refresh-token', oauth2_refresh_token),
    path(r'oauth2/signup', oauth2_signup),
    path(r'oauth2/reset', oauth2_reset_password),
    path(r'oauth2/check/email', oauth2_check_email),
    path(r'oauth2/check/username', oauth2_check_username),
    path(r'oauth2/partial-signup', partial_signup),
    # Folder endpoints
    path('folders/add', FolderViewSet.as_view({'post': 'add'}),
         name='folder-add'),
    path('folders/<str:pk>/delete', FolderViewSet.as_view({'delete': 'delete'}),
         name='folder-delete'),
    path('folders/<str:pk>/update', FolderViewSet.as_view({'patch': 'update'}),
         name='folder-update'),
    path('folders/list', FolderViewSet.as_view({'get': 'list'}),
         name='folder-list'),
    path('folders/<str:pk>/move', FolderViewSet.as_view({'post': 'move'}),
         name='folder-move'),
    # Document Archive endpoints
    path('documents/add', DocumentArchiveViewSet.as_view({'post': 'add'}),
         name='document-add'),
    path('documents/<str:pk>/delete', DocumentArchiveViewSet.as_view({'delete': 'delete'}),
         name='document-delete'),
    path('documents/bulk-delete', DocumentArchiveViewSet.as_view({'delete': 'bulk_delete'}),
         name='document-bulk-delete'),
    path('documents/<str:pk>/update', DocumentArchiveViewSet.as_view({'patch': 'update'}),
         name='document-update'),
    path('documents/list', DocumentArchiveViewSet.as_view({'get': 'list'}),
         name='document-list'),
    path('documents/<str:pk>/move', DocumentArchiveViewSet.as_view({'post': 'move_to_folder'}),
         name='document-move'),
    # Module Templates endpoints
    path('templates/add', SportAssociationModuleTemplatesViewSet.as_view({'post': 'create'}),
         name='template-add'),
    path('templates/<str:pk>/delete', SportAssociationModuleTemplatesViewSet.as_view({'delete': 'delete'}),
         name='template-delete'),
    path('templates/bulk-delete', SportAssociationModuleTemplatesViewSet.as_view({'delete': 'bulk_delete'}),
         name='template-bulk-delete'),
    path('templates/<str:pk>/update', SportAssociationModuleTemplatesViewSet.as_view({'patch': 'update'}),
         name='template-update'),
    path('templates/list', SportAssociationModuleTemplatesViewSet.as_view({'get': 'list'}),
         name='template-list'),
    # onboarding
    path(r'onboarding/update', onboarding_update),
    # delete account
    path(r'oauth2/delete-account', oauth2_delete_account),
    # add collaborators
    path(r'collaborators/list', collaborators_list),
    path(r'collaborators/add', collaborators_add),
    path(r'collaborators/<str:uid>/update', collaborators_update),
    path(r'collaborators/<str:uid>/delete', collaborators_delete),
    path(r"two-fa/info", two_fa_info),
    path(r"two-fa/setup", two_fa_setup),
    path(r"two-fa/update", two_fa_update),
    path(r"billing/active-plan", billing_active_plan),
    path(r"billing/checkout", billing_checkout),
    path(r'profile/update', profile_update),
    path(r'profile/associates/course/<str:uid>', profile_associates_course),
    path(r'profile/associates/sport-association/<str:uid>', profile_associates_sport_association),
    path(r'profile/image/<str:uid>', profile_image),
    path(r'profile/update/password', profile_update_password),
    # THIS IS FOR SUPERUSER ONLY
    path(r'sport-associations/list', sport_association_list),
    path(r'sport-associations/<uid>/admin-update', sport_association_admin_update),
    path(r'profile/info', profile_info),
    path(r'profile/settings', profile_settings),
    path(r'profile/integrations', profile_integrations),
    path(r'profile/settings/tables', profile_settings_tables),
    path(r'profile/update/subscription/template', profile_update_subscription_template),
    path(r'calendar/events', full_events_calendar),
    path(r'calendar/events/update', full_events_calendar_update),
    path(r'calendar/events/export', full_events_calendar_export),
    path('subscription/memberships/add', SubscriptionMembershipViewSet.as_view({'post': 'add'}), name='subscription-membership-add'),
    path('subscription/memberships/<str:pk>/delete', SubscriptionMembershipViewSet.as_view({'delete': 'delete'}), name='subscription-membership-delete'),
    path('subscription/memberships/<str:pk>/update', SubscriptionMembershipViewSet.as_view({'patch': 'update'}), name='subscription-membership-update'),
    path('subscription/memberships/list', SubscriptionMembershipViewSet.as_view({'get': 'list'}), name='subscription-membership-list'),
    path(r'subscription/tags/list', subscription_tags_list),
    path(r'subscription/tags/add', subscription_tags_add),
    path(r'subscription/tags/<str:tag_id>/update', subscription_tags_update),
    path(r'subscription/tags/<str:tag_id>/delete', subscription_tags_delete),
    path(r'subscription/tags/<str:tag_id>/assign/<str:subscription_id>', subscription_tags_assign),
    path(r'subscription/tags/<str:tag_id>/unassign/<str:subscription_id>', subscription_tags_unassign),
    path(r'subscription/add', subscription_add),
    path(r'subscription/renew', subscription_renew),
    path(r'subscription/sign', subscription_sign),
    path(r'subscription/list', subscription_list),
    path(r'subscription/get-associations-for-federation', get_associations_for_federation),
    path(r'subscription/generate-token-link', subscription_generate_token_link),
    path(r'subscription/validate-token-link-and-get-subscriptions', validate_token_link_and_get_subscriptions),
    path(r'subscription/calculate-tax-code', subscription_calculate_tax_code),
    path(r'subscription/list/all', subscription_list_all),
    path(r'subscription/list/archived', subscription_list_archived),
    path(r'subscription/list/export', subscription_list_export),
    path(r'subscription/<str:uid>/info', subscription_info),
    path(r'subscription/<str:uid>/payments', subscription_payments),
    path(r'subscription/<str:uid>/card', subscription_card),
    path(r'subscription/<str:uid>/attendance', subscription_attendance),
    path(r'subscription/<str:uid>/calendar', subscription_calendar),
    path(r'subscription/<str:uid>/approve', subscription_approve),
    path(r'subscription/<str:uid>/reject', subscription_reject),
    path(r'subscription/<str:uid>/delete', subscription_delete),
    path(r'subscription/<str:uid>/archive', subscription_archive),
    path(r'subscription/<str:uid>/update', subscription_update),
    path(r'subscription/<str:uid>/edit', subscription_edit),  # TODO: refactor to associates
    path(r'subscription/<str:uid>/medical-appointments/list', subscription_medical_appointments_list),
    path(r'subscription/<str:uid>/medical-appointments/add', subscription_medical_appointments_add),
    path(r'subscription/<str:uid>/medical-appointments/<str:medical_appointments_id>/delete', subscription_medical_appointments_delete),
    path(r'subscription/<str:uid>/medical-certificate/upload', subscription_medical_certificate_upload),
    path(r'subscription/<str:uid>/medical-certificate/set-certificate-expiration',
         subscription_medical_certificate_set_certificate_expiration),
    path(r'subscription/<str:uid>/medical-certificate/send-email-reminder',
         subscription_medical_certificate_send_email_reminder),
    path(r'subscription/<str:uid>/medical-certificate/edit',
            subscription_medical_certificate_edit),
    path(r'subscription/<str:uid>/transfer', subscription_transfer),
    path(r'subscription/<str:uid>/upload-document', subscription_upload_document),
    path(r'subscription/<str:uid>/delete-document/<str:subscription_file_id>', subscription_delete_document),
    path(r'subscription/import/upload', subscription_import_upload),
    path(r'subscription/import/status', subscription_import_status),
    path(r'subscription/associates-draft/add', subscription_associates_draft_add),
    path(r'subscription/associates-draft/list', subscription_associates_draft_list),
    path(r'subscription/associates-draft/list/approve', subscription_associates_draft_approve),
    path(r'subscription/associates-draft/<str:uid>/edit', subscription_associates_draft_edit),
    path(r'subscription/associates-draft/<str:uid>/delete', subscription_associates_draft_delete),
    path(r'subscription/associates-draft/bulk-delete', subscription_associates_draft_bulk_delete),
    path(r'payment/list', payment_list),
    path(r'payment/stats', payment_stats),
    path(r'payment/add', payment_add),
    path(r'payment/bulk-add', payment_bulk_add),
    path(r'payment/sign', payment_sign),
    path(r'payment/simulation/partial-quotes', payment_simulation_partial_quotes),
    path(r'payment/simulation/partial-quotes-apply', payment_simulation_partial_quotes_apply),
    path(r'payment-bulk/archive', payment_bulk_archive),
    path(r'payment-bulk/delete', payment_bulk_delete),
    path(r'payment/<str:uid>/update', payment_update),
    path(r'payment/<str:uid>/request', payment_request),
    path(r'payment/list/export', payment_list_export),
    path(r'payment/<str:uid>/info', payment_info),
    path(r'payment/<str:uid>/approve', payment_approve),
    path(r'payment/<str:uid>/cancel', payment_cancel),
    path(r'payment/<str:uid>/delete', payment_delete),
    path(r'payment/<str:uid>/archive', payment_archive),
    path(r'payment/<str:uid>/generate-invoice', payment_generate_invoice),
    path(r'payment/category/list', payment_category_list),
    path(r'payment/category/add', payment_category_add),
    path(r'payment/category/<str:uid>/update', payment_category_update),
    path(r'payment/category/<str:uid>/delete', payment_category_delete),
    path(r'personas/add', AssociateViewSet.as_view({'post': 'add'}), name='persona-add'),
    path(r'personas/list', AssociateViewSet.as_view({'get': 'list'}), name='persona-list'),
    path(r'personas/all', AssociateViewSet.as_view({'get': 'all'}), name='persona-all'),
    path(r'personas/all-with-subscriptions', AssociateViewSet.as_view({'get': 'all_with_subscriptions'}), name='persona-all-with-subscriptions'),
    path(r'personas/all-tutors', AssociateViewSet.as_view({'get': 'all_tutors'}), name='persona-all-tutors'),
    path(r'personas/bulk-delete', AssociateViewSet.as_view({'delete': 'bulk_delete'}), name='persona-bulk-delete'),
    path(r'personas/<str:pk>/delete', AssociateViewSet.as_view({'delete': 'delete'}), name='persona-delete'),
    path(r'personas/<str:pk>/update', AssociateViewSet.as_view({'patch': 'update'}), name='persona-update'),
    path(r'personas/<str:pk>/info', AssociateViewSet.as_view({'get': 'info'}), name='persona-info'),
    path(r'personas/<str:pk>/recover', AssociateViewSet.as_view({'post': 'recover'}), name='persona-recover'),
    path(r'personas-subscriptions/<str:pk>/list', AssociateSubscriptionViewSet.as_view({'get': 'list'}), name='persona-subscriptions-list'),
    path(r'supplier/list', supplier_list),
    path(r'supplier/<str:uid>/info', supplier_info),
    path(r'supplier/add', supplier_add),
    path(r'supplier/<str:uid>/update', supplier_update),
    path(r'supplier/<str:uid>/delete', supplier_delete),
    path(r'supplier/bulk-delete', supplier_bulk_delete),
    path(r'invoice/list', invoice_list),
    path(r'invoice/list/archived', invoice_list_archived),
    path(r'invoice/list/export', invoice_list_export),
    path(r'invoice/<str:uid>/update', invoice_update),
    path(r'invoice/<str:uid>/delete', invoice_delete),
    path(r'invoice/<str:uid>/send', invoice_send),
    path(r'invoice-bulk/delete', invoice_bulk_delete),
    path(r'invoice-bulk/archive', invoice_bulk_archive),
    path(r'invoice-suppliers/list', invoice_suppliers_list),
    path(r'invoice-suppliers/stats', invoice_suppliers_stats),
    path(r'invoice-suppliers/add', invoice_suppliers_add),
    path(r'invoice-suppliers/<str:uid>/update', invoice_suppliers_update),
    path(r'invoice-suppliers/<str:uid>/delete', invoice_suppliers_delete),
    # path(r'invoice/<str:uid>/invoice-suppliers/list/export', invoice_suppliers_list_export),
    path(r'invoice-customers/stats', invoice_customers_stats),
    path(r'invoice-customers/list', invoice_customers_list),
    path(r'invoice-customers/add', invoice_customers_add),
    path(r'invoice-customers/<str:uid>/update', invoice_customers_update),
    path(r'invoice-customers/<str:uid>/delete', invoice_customers_delete),
    path(r'carnet/list', carnet_list),
    path(r'carnet/add', carnet_add),
    path(r'carnet/<str:uid>/info', carnet_info),
    path(r'carnet/<str:uid>/update', carnet_update),
    path(r'carnet/<str:uid>/delete', carnet_delete),
    path(r'carnet/<str:uid>/assign/<str:uid_subscription>', carnet_assign),
    path(r'carnet/<str:uid>/replace/<str:uid_subscription>', carnet_replace),
    path(r'carnet/<str:uid>/unassign/<str:uid_subscription>', carnet_unassign),
    path(r'carnet-subscription/list', carnet_subscription_list),
    path(r'carnet-subscription/<str:uid>/enable', carnet_subscription_enable),
    path(r'carnet-subscription/<str:uid>/disable', carnet_subscription_disable),
    path(r'carnet-subscription/<str:uid>/update', carnet_subscription_update),
    path(r'carnet-subscription/<str:uid>/topup', carnet_subscription_topup),
    path(r'carnet-subscription/<str:uid>/delete/<str:uid_course>', carnet_subscription_delete),
    path(r'attendance/<str:uid>/mark', attendance_mark),
    path(r'attendance-day/<str:uid>/delete', attendance_day_delete),
    path(r'attendance-day/<str:uid>/mark-absent', attendance_day_mark_absent),
    path(r'course-subscriptions/list', CourseSubscriptionViewSet.as_view({'get': 'list', 'post': 'list'}), name='course-subscription-list'),
    path(r'course-subscriptions/add', CourseSubscriptionViewSet.as_view({'post': 'add'}), name='course-subscription-add'),
    path(r'course-subscriptions/<str:pk>/delete', CourseSubscriptionViewSet.as_view({'delete': 'delete'}), name='course-subscription-delete'),
    path(r'course-subscriptions/bulk-delete', CourseSubscriptionViewSet.as_view({'delete': 'bulk_delete'}), name='course-subscription-bulk-delete'),
    path(r'course-subscriptions/<str:pk>/update', CourseSubscriptionViewSet.as_view({'patch': 'update'}), name='course-subscription-update'),
    path(r'course/tags/list', course_tags_list),
    path(r'course/tags/add', course_tags_add),
    path(r'course/tags/<str:tag_id>/update', course_tags_update),
    path(r'course/tags/<str:tag_id>/delete', course_tags_delete),
    path(r'course/tags/<str:tag_id>/assign/<str:course_id>', course_tags_assign),
    path(r'course/tags/<str:tag_id>/unassign/<str:course_id>', course_tags_unassign),
    path(r'course/list', course_list),
    path(r'course/add', course_add),
    path(r'course/<str:uid>/update', course_update),
    path(r'course/<str:uid>/enable', course_enable),
    path(r'course/<str:uid>/disable', course_disable),
    path(r'course/<str:uid>/pin', course_pin),
    path(r'course/<str:uid>/delete', course_delete),
    path(r'course/<str:uid>/overview', course_overview),
    path('course/locations/add', CourseLocationViewSet.as_view({'post': 'add'}), name='course-location-add'),
    path('course/locations/<str:pk>/delete', CourseLocationViewSet.as_view({'delete': 'delete'}),
         name='course-location-delete'),
    path('course/locations/<str:pk>/update', CourseLocationViewSet.as_view({'patch': 'update'}),
         name='course-location-update'),
    path('course/locations/list', CourseLocationViewSet.as_view({'get': 'list'}), name='course-location-list'),
    path(r'course/<str:uid>/overview/<str:uid_subscription>/delete', course_overview_delete),
    path(r'course/<str:uid>/overview/<str:uid_subscription>/add', course_overview_add),
    path(r'course/<str:uid>/overview/<str:uid_subscription>/update', course_overview_update),
    path(r'course/<str:uid>/calendar', calendar),
    path(r'course/<str:uid>/calendar/update', calendar_update),
    path(r'course/<str:uid>/attendees', attendees),
    path(r'course/<str:uid>/attendees/<str:attendance_day_uid>/update', attendees_update),
    path(r'course-installment/<str:uid>/make-payment', course_installment_make_payment),
    # camps and retreats
    path(r'camps-and-retreats/list', camps_and_retreats_list),
    path(r'camps-and-retreats/add', camps_and_retreats_add),
    path(r'camps-and-retreats/<str:uid>/update', camps_and_retreats_update),
    path(r'camps-and-retreats/<str:uid>/delete', camps_and_retreats_delete),
    path(r'camps-and-retreats/<str:uid>/info', camps_and_retreats_info),
    path(r'camps-and-retreats/<str:uid>/subscriptions/list', camps_and_retreats_subscriptions_list),
    path(r'camps-and-retreats/<str:uid>/subscriptions/add', camps_and_retreats_subscriptions_add),
    path(r'camps-and-retreats/<str:uid>/subscriptions/update', camps_and_retreats_subscriptions_update),
    path(r'camps-and-retreats/<str:uid>/subscriptions/delete', camps_and_retreats_subscriptions_delete),
    path(r'camps-and-retreats/periods/add', camps_and_retreats_periods_add),
    path(r'camps-and-retreats/periods/<str:uid>/update', camps_and_retreats_periods_update),
    path(r'camps-and-retreats/periods/<str:uid>/delete', camps_and_retreats_periods_delete),
    path(r'camps-and-retreats/periods/<str:uid>/info', camps_and_retreats_periods_info),
    path(r'camps-and-retreats/periods/services/add', camps_and_retreats_periods_services_add),
    path(r'camps-and-retreats/periods/services/<str:uid>/update', camps_and_retreats_periods_services_update),
    path(r'camps-and-retreats/periods/services/<str:uid>/delete', camps_and_retreats_periods_services_delete),
    path(r'modules/list', modules_list),
    path(r'modules/add', modules_add),
    path(r'modules/check-link', modules_check_link),
    path(r'modules/response/<str:module_response_id>/approve', modules_response_approve),
    path(r'modules/response/<str:module_response_id>/delete', modules_response_delete),
    path(r'modules/response/<str:module_response_id>/add-attachment', modules_response_add_attachment),
    path(r'modules/<str:module_id>/update', modules_update),
    path(r'modules/<str:module_id>/duplicate', modules_duplicate),
    path(r'modules/<str:module_id>/delete', modules_delete),
    path(r'modules/<str:custom_link>/info', modules_custom_link_info),
    path(r'modules/<str:module_id>/overview', modules_overview),
    path(r'modules/<str:module_id>/response/add', modules_response_add),
    path(r'modules/<str:module_id>/export', modules_response_export),
    path(r'instructor/list', instructor_list),
    path(r'instructor/report', instructor_report),
    path(r'instructor/add', instructor_add),
    path(r'instructor/<str:uid>/info', instructor_info),
    path(r'instructor/<str:uid>/hours/list', instructor_hours_list),
    path(r'instructor/<str:uid>/hours/calculate', instructor_hours_calculate),
    path(r'instructor/<str:uid>/hours/add', instructor_hours_add),
    path(r'instructor/<str:uid>/hours/add/compensation', instructor_hours_add_compensation),
    path(r'instructor/<str:uid>/hours/<str:instructor_hours_id>/update', instructor_hours_update),
    path(r'instructor/<str:uid>/hours/<str:instructor_hours_id>/delete', instructor_hours_delete),
    path(r'instructor/<str:uid>/update', instructor_update),
    path(r'instructor/<str:uid>/delete', instructor_delete),
    path(r'balance-sheet', balance_sheet),
    path(r'balance-sheet/archived', balance_sheet_archived),
    path(r'balance-sheet/accounts/list', balance_sheet_accounts_list),
    path(r'balance-sheet/accounts/add', balance_sheet_accounts_add),
    path(r'balance-sheet/accounts/<str:uid>/update', balance_sheet_accounts_update),
    path(r'balance-sheet/accounts/<str:uid>/delete', balance_sheet_accounts_delete),
    path(r'balance-sheet/accounts-transfer/list', balance_sheet_accounts_transfer_list),
    path(r'balance-sheet/accounts-transfer/add', balance_sheet_accounts_transfer_add),
    path(r'balance-sheet/accounts-transfer/<str:uid>/update', balance_sheet_accounts_transfer_update),
    path(r'balance-sheet/accounts-transfer/<str:uid>/delete', balance_sheet_accounts_transfer_delete),
    path(r'statistic/dashboard', statistic_dashboard),
    path(r'statistic/dashboard/layout', statistic_dashboard_layout),
    path(r'statistic/athlete-dashboard', statistic_athlete_dashboard),
    path(r'statistic/report', statistic_report),
    path(r'search/all', search_all),
    path(r'search/profile/<str:username>', search_profile),
    path(r'stripe/on-boarding', stripe_on_boarding),
    path(r'stripe/info', stripe_info),
    path(r'stripe/complete-on-boarding', stripe_complete_on_boarding),
    path(r'stripe/pay/<str:payment_id>', stripe_pay),
    path(r'stripe/multiple-pay', stripe_multiple_pay),
    path(r'stripe/webhook', stripe_webhook),
    path(r'testimonials/add', testimonials_create),
    path(r'testimonials/update', testimonials_update),
    path(r'google/check', google_check),
    path(r'google/oauth2callback', google_oauth2callback),
    path(r'google/calendar/config', google_calendar_config),
    path(r'google/calendar/list', google_calendar_list),
    path(r'google/calendar/<str:course_id>/export', google_calendar_export_course),
    path(r'printing/generate', printing_generate),
    path(r'export-all-data', export_all_data),
    # Audit Log endpoints
    path(r'audit-logs/list', audit_log_list, name='audit_log_list'),
    path(r'audit-logs/<int:log_id>/detail', audit_log_detail, name='audit_log_detail'),
    path(r'audit-logs/models', audit_log_models, name='audit_log_models'),
    path(r'audit-logs/stats', audit_log_stats, name='audit_log_stats'),
    # Association Export endpoints
    path(r'association/export/start', AssociationExportViewSet.as_view({'post': 'start_export'}), name='association_export_start'),
    path(r'association/export/status', AssociationExportViewSet.as_view({'get': 'export_status'}), name='association_export_status'),
    path(r'association/export/list', AssociationExportViewSet.as_view({'get': 'list_exports'}), name='association_export_list'),
    path(r'association/export/delete', AssociationExportViewSet.as_view({'delete': 'delete_export'}), name='association_export_delete'),
    # Association Import endpoints
    path(r'association/import/validate', AssociationImportViewSet.as_view({'post': 'validate_import'}), name='association_import_validate'),
    path(r'association/import/start', AssociationImportViewSet.as_view({'post': 'start_import'}), name='association_import_start'),
    path(r'association/import/status', AssociationImportViewSet.as_view({'get': 'import_status'}), name='association_import_status'),
    # Saved Reports
    path(r'saved-reports/list', SavedReportViewSet.as_view({'get': 'list'}), name='saved_report_list'),
    path(r'saved-reports/add', SavedReportViewSet.as_view({'post': 'add'}), name='saved_report_add'),
    path(r'saved-reports/<str:pk>/info', SavedReportViewSet.as_view({'get': 'info'}), name='saved_report_info'),
    path(r'saved-reports/<str:pk>/update', SavedReportViewSet.as_view({'patch': 'update'}), name='saved_report_update'),
    path(r'saved-reports/<str:pk>/delete', SavedReportViewSet.as_view({'delete': 'delete'}), name='saved_report_delete'),
    path(r'saved-reports/<str:pk>/run', SavedReportViewSet.as_view({'post': 'run'}), name='saved_report_run'),
]
