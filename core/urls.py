from django.urls import path, include
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView
from rest_framework.routers import DefaultRouter
   
from . import views

# ================= ROUTER =================
router = DefaultRouter()
router.register("tasks", views.TaskViewSet)


urlpatterns = [

    # ROUTER
    path('', include(router.urls)),

    # AUTH
    path('register/', views.api_register),
    path('token/', TokenObtainPairView.as_view()),
    path('token/refresh/', TokenRefreshView.as_view()),
    path('password-reset/request/', views.api_password_reset_request),
    path('password-reset/verify/', views.api_password_reset_verify),
    path('password-reset/confirm/', views.api_password_reset_confirm),

    # DASHBOARD
    path('dashboard/', views.api_dashboard),

    # LOTS
    path('lots/', views.api_lots),
    path('lots/<int:pk>/', views.api_lot_detail),

    # MOUVEMENTS
    path('mouvements/', views.api_mouvements),
    path('mouvements/create/', views.api_create_mouvement),
    path('mouvements/delete/<int:pk>/', views.api_delete_mouvement),
    

    # DEPENSES
    path('depenses/', views.api_depenses),
    path('depenses/create/', views.api_create_depense),
    path('depenses/delete/<int:pk>/', views.api_delete_depense),
    
    
    # ACHATS
    path('achats/', views.api_achats),
    path('achats/create/', views.api_create_achat),
    path('achats/delete/<int:pk>/', views.api_delete_achat),

    # ESPECES
    path('especes/', views.api_especes),

    # CATEGORIES
    path('categories-depense/', views.api_categories_depense),

    # ANALYTICS
    path('stock-detail/', views.api_stock_detail),
    path('ca-par-lot/', views.api_ca_par_lot),
    path('depenses-detail/', views.api_depenses_detail),
    path('marge-par-lot/', views.api_marge_par_lot),

    # CLIENTS
    path('clients/', views.client_list),
    path('clients/create/', views.create_client),

    # DETAIL CLIENT
    path('clients/<int:client_id>/balance/', views.api_client_balance),
    path('clients/<int:client_id>/payments/', views.api_client_payments),
    path('clients/<int:client_id>/ventes/', views.api_client_ventes),

    # PAYMENTS
    path('payments/create/', views.api_create_payment),

    # TOTAL DETTES
    path('clients/total-dettes/', views.api_total_dettes),

    # TOTAL DETTES CLIENTS
    path('dettes-clients/', views.api_dettes_clients),

    # PERFORMANCE LOTS
    path("performance-lots/", views.api_performance_lots),
    path("performance-especes/", views.api_performance_especes),

    # PRODUCTION D'ŒUFS
    path("oeufs/collectes/", views.api_collectes_oeufs),
    path("oeufs/collectes/<int:pk>/", views.api_collecte_oeufs_detail),
    path("oeufs/statistiques/", views.api_statistiques_oeufs),
    path("oeufs/stock-date/", views.api_stock_date_oeufs),
    path("oeufs/kpi/", views.api_kpi_oeufs),
    path("oeufs/ventes/", views.api_ventes_oeufs),
    path("oeufs/ventes/<int:pk>/", views.api_vente_oeufs_detail),
    path("oeufs/alimentation/", views.api_consommations_aliment),
    path("oeufs/alimentation/<int:pk>/", views.api_consommation_aliment_detail),
    # Accès générique par lot ; les anciennes URL œufs restent compatibles.
    path("alimentation/distributions/", views.api_consommations_aliment),
    path("alimentation/distributions/<int:pk>/", views.api_consommation_aliment_detail),
    path("production/pesees/", views.api_pesees_production),
]
