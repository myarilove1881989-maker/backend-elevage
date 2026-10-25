from django.http import JsonResponse
from django.urls import path
from django.db import connection
from .urls import urlpatterns as application_urls


def health(request):
    with connection.cursor() as cursor:
        cursor.execute('SELECT 1')
        cursor.fetchone()
    return JsonResponse({'status': 'ok', 'environment': 'pilot'})


urlpatterns = [path('api/pilot-health/', health)] + application_urls
