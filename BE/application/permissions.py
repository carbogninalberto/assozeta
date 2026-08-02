from rest_framework.permissions import BasePermission
from application.models import User, BillingSubscription, BillingPlan


class IsProPlanAssociation(BasePermission):
    def has_permission(self, request, view):

        if request.user.role == User.ATHLETE:
            return False
        else:
            billing_subscription = BillingSubscription.objects.get(user=request.user)
            return bool(billing_subscription.billing_plan.billing_type == BillingPlan.PRO_PLAN)


class IsTeamsPlanAssociation(BasePermission):
    def has_permission(self, request, view):

        if request.user.role == User.ATHLETE:
            return False
        else:
            billing_subscription = BillingSubscription.objects.get(user=request.user)
            return bool(billing_subscription.billing_plan.billing_type == BillingPlan.TEAMS_PLAN)


class IsAthleteUser(BasePermission):
    def has_permission(self, request, view):
        return request.user.role == User.ATHLETE
