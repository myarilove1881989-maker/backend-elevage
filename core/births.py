"""Existing HTTP birth semantics, shared without altering offline declarations."""
from django.db import transaction
from .models import Lot,Mouvement
@transaction.atomic
def create_birth(user,parent,data):
    child=Lot.objects.create(nom=data['nom_nouveau_lot'],espece=parent.espece,exploitation=parent.exploitation,
        date_debut=data['date'],date_naissance=data['date'],type_production=data['type_production'],statut_production='ELEVAGE',created_by=user)
    movement=Mouvement.objects.create(lot=child,lot_origine=parent,exploitation=parent.exploitation,created_by=user,
        type_mouvement='NAISSANCE',quantite=data['total_naissances']-data['mort_nes'],mort_nes=data['mort_nes'],date=data['date'],note=data.get('note',''))
    return child,movement
