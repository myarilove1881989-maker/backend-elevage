"""Readiness probe: no models, authentication, migrations or business queries."""
from django.db import DatabaseError, connections
from django.http import JsonResponse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_safe


@never_cache
@require_safe
def health(request):
    connection = connections["health"]
    healthy = False
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            healthy = cursor.fetchone() == (1,)
    except DatabaseError:
        # No exception details, credentials or database identity in the response.
        pass
    finally:
        try:
            connection.close()
        except DatabaseError:
            healthy = False
    return JsonResponse(
        {"status": "ok" if healthy else "unavailable"},
        status=200 if healthy else 503,
    )
