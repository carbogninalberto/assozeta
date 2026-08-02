from django.db import models
from django.db.models import QuerySet
from typing import Optional
from asgiref.local import Local
import logging

logger = logging.getLogger(__name__)
_local = Local()


class GroupSelectionMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # In Django, headers are prefixed with HTTP_ and uppercase
        group_id = request.headers.get('x-group-id')
        #logger.debug(f"Middleware received group_id: {group_id}")

        # Store in thread-local
        setattr(_local, 'group_id', group_id)
        #logger.debug(f"Stored group_id in _local: {group_id}")

        response = self.get_response(request)

        # Clean up
        try:
            delattr(_local, 'group_id')
            #logger.debug("Cleaned up group_id from _local")
        except AttributeError:
            pass

        return response


class GroupAwareQuerySet(QuerySet):
    def for_group(self, group_id: Optional[str] = None):
        """Filter queryset by group_id if provided"""
        if group_id is not None:
            return self.filter(group=group_id)
        return self


class GroupAwareManager(models.Manager):
    def get_queryset(self):
        qs = GroupAwareQuerySet(self.model, using=self._db)

        #logger = logging.getLogger(__name__)
        #logger.debug("Manager: Attempting to get group_id from _local")

        try:
            group_id = getattr(_local, 'group_id', None)
            #logger.debug(f"Manager: Retrieved group_id: {group_id}")

            # Debug local storage
            all_attrs = [attr for attr in dir(_local) if not attr.startswith('__')]
            #logger.debug(f"Manager: All attributes in _local: {all_attrs}")

            if group_id:
                #logger.debug(f"Manager: Applying group filter with id: {group_id}")
                return qs.for_group(group_id)
        except AttributeError as e:
            logger.exception(f"Manager: Error accessing group_id: {e}")
        except Exception as e:
            logger.exception(f"Manager: Unexpected error: {e}")

        #logger.debug("Manager: Returning unfiltered queryset")
        return qs

    # creation query after the creation set the group_id
    def create(self, **kwargs):
        group_id = getattr(_local, 'group_id', None)
        if group_id:
            kwargs['group_id'] = group_id
        return super().create(**kwargs)


class SoftDeleteGroupAwareManager(GroupAwareManager):
    def get_queryset(self):
        return super().get_queryset().filter(deleted=False)

    def all_objects(self):
        """Get all objects including deleted ones, but still respect group filtering"""
        return super(models.Manager, self).get_queryset()


class GroupModelMixin(models.Model):
    """Abstract base class for group-aware models"""
    group = models.ForeignKey(
        'Group',
        on_delete=models.CASCADE,
        null=True
    )

    objects = SoftDeleteGroupAwareManager()

    class Meta:
        abstract = True