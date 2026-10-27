from django.http import JsonResponse
from django.urls import path
from django.db import connection
from .urls import urlpatterns as application_urls


def health(request):
    from core.reset_mail_worker import enabled, is_running
    if enabled() and not is_running():
        return JsonResponse({'status': 'unavailable', 'environment': 'pilot'}, status=503)
    with connection.cursor() as cursor:
        cursor.execute('SELECT 1')
        cursor.fetchone()
    return JsonResponse({'status': 'ok', 'environment': 'pilot'})


urlpatterns = [path('api/pilot-health/', health)] + application_urls
