"""Service boundary for direct calls as well as REST calls."""
from functools import wraps
from django.db import transaction


def guarded_handler(action, handler):
    @wraps(handler)
    @transaction.atomic
    def guarded(row, user):
        from .models import Exploitation
        from .access_policy import authorize_business_service
        farm = Exploitation.objects.select_for_update().get(pk=row.exploitation_id)
        checked_action = action
        if checked_action is None:
            from .permission_catalog import operation_action
            checked_action = operation_action(getattr(row, 'entity_type', None),
                                              getattr(row, 'operation_type', None))
        authorize_business_service(user, farm, checked_action)
        return handler(row, user)
    return guarded
