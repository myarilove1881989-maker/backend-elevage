from django.contrib import admin
from django.urls import path, include

# ✅ IMPORT COMPLET (corrige ton erreur)
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView
from core.admin_dashboard import superadmin_dashboard
from core.admin_exports import export_all_statistics

urlpatterns = [
    path('admin/export-statistics/', admin.site.admin_view(export_all_statistics), name='admin-export-statistics'),
    path('admin/super-dashboard/', admin.site.admin_view(superadmin_dashboard), name='superadmin-dashboard'),
    path('admin/', admin.site.urls),

    # API principale
    path('api/', include('core.urls')),

    # ✅ LOGIN pour Flutter
    path('api/login/', TokenObtainPairView.as_view(), name='login'),

    # JWT (optionnel mais utile)
    path('api/token/', TokenObtainPairView.as_view()),
    path('api/token/refresh/', TokenRefreshView.as_view()),
]
