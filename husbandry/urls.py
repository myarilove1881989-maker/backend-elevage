from django.urls import path
from . import views
urlpatterns=[path('capabilities/',views.capabilities),path('technical/setup/',views.setup),path('technical/parameters/',views.parameters),path('technical/rules/',views.rules),path('technical/readings/',views.readings),path('technical/evolution/',views.evolution),path('reproduction/identities/',views.identities),path('reproduction/cycles/',views.cycles),path('reproduction/events/',views.events)]
