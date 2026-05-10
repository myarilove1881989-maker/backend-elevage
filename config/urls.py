from django.contrib import admin
from django.urls import path, include

# ✅ IMPORT COMPLET (corrige ton erreur)
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

urlpatterns = [
    path('admin/', admin.site.urls),

    # API principale
    path('api/', include('core.urls')),

    # ✅ LOGIN pour Flutter
    path('api/login/', TokenObtainPairView.as_view(), name='login'),

    # JWT (optionnel mais utile)
    path('api/token/', TokenObtainPairView.as_view()),
    path('api/token/refresh/', TokenRefreshView.as_view()),
]