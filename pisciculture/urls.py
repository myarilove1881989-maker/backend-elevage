from django.urls import path
from . import views, observation_views, stock_views, feed_views, health_views, breeding_views

urlpatterns = [
    path('breeding-revisions/',breeding_views.revisions),
    path('brood-groups/',breeding_views.groups),path('brood-groups/<uuid:pk>/',breeding_views.group),
    path('brood-group-revisions/',breeding_views.group_revision),path('reproductions/',breeding_views.reproductions),
    path('reproductions/<uuid:pk>/',breeding_views.reproduction),path('reproduction-revisions/',breeding_views.reproduction_revision),
    path('breeding-facts/',breeding_views.facts),path('hatch-counts/',breeding_views.count),
    path('fry-placements/',breeding_views.place),path('breeding-voids/',breeding_views.cancel),
    path('health-products/',health_views.products),path('health-observations/',health_views.observations),
    path('health-treatments/',health_views.treatments),path('health-history/',health_views.history),
    path('health-records/<uuid:pk>/',health_views.record),path('health-corrections/',health_views.corrections),
    path('health-state/',health_views.state),path('health-sale-check/',health_views.sale_verification),
    path('feed-products/',feed_views.catalog),path('feed-purchases/',feed_views.purchases),
    path('feed-distributions/',feed_views.distributions),path('feed-reserves/',feed_views.reserves),
    path('feed-summary/',feed_views.summary),path('feed-stock-history/',feed_views.movements),
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

from . import commerce_views as commerce
urlpatterns += [path('harvests/',commerce.harvests),path('harvests/<uuid:pk>/',commerce.harvest),
    path('harvest-sales/',commerce.sales),path('postharvest-losses/',commerce.losses),path('commerce-history/',commerce.history),
    path('commerce-payments/',commerce.payments),path('commerce-clients/',commerce.clients),path('commerce-invoices/<int:pk>/',commerce.bill),
    path('commerce-check/',commerce.verification),path('commerce-cancellations/',commerce.cancellations),path('commerce-corrections/',commerce.corrections)]

from . import finance_views as planning
urlpatterns += [path('finance-summary/',planning.summary),path('finance-expenses/',planning.expense_list),path('finance-expense-revisions/',planning.expense_edit),path('finance-assignments/',planning.assign),path('finance-categories/',planning.categories),path('finance-revenues/',planning.revenues),path('finance-history/',planning.revisions),path('agenda/',planning.tasks),path('agenda-revisions/',planning.task_edit),path('agenda-status/',planning.task_progress),path('agenda-history/',planning.task_history)]

urlpatterns += [path('finance-detail/',planning.detail),path('finance-client-ledger/',planning.client_ledger)]
