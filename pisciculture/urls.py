from django.urls import path
from . import views, observation_views, stock_views

urlpatterns = [
    path('mortalities/',stock_views.mortalities),path('loss-reconciliations/',stock_views.reconciliations),
    path('inventories/',stock_views.inventories),path('inventory-credits/assign/',stock_views.assign_credit),
    path('stock-cancellations/',stock_views.cancellations),path('stock-state/',stock_views.stock_state),
    path('stock-history/',stock_views.stock_history),
    path('weights/',observation_views.weights),path('water-readings/',observation_views.water),
    path('observation-rules/',observation_views.rules),
    path('capabilities/',views.capabilities),path('activation/',views.activation),
    path('onboarding/',views.onboarding),
    path('installations/',views.installations),path('installations/<uuid:pk>/',views.installation),
    path('species/',views.species),path('stages/',views.stages),
    path('cohorts/',views.cohorts),path('cohorts/<uuid:pk>/',views.cohort),
    path('positions/',views.positions),path('transfers/',views.transfers),
    path('events/',views.events),path('dashboard/',views.dashboard),
    path('commands/<uuid:operation_uuid>/',views.command),
]
