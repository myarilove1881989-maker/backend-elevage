"""Server-authoritative sales and Decimal cash recognition for terrain receipts."""
from decimal import Decimal
from django.db.models import Sum
from django.utils import timezone
from rest_framework import serializers
from .models import Client, Vente, VenteOeufs, Mouvement, Payment, Lettrage, EncaissementTerrain
from .serializers import VenteOeufsSerializer
from .egg_services import create_egg_sale
from .terrain_application import BusinessConflict
from .terrain_operations import Reference, resolve, data_with_lot, context, stock, mark_lots, create_only


def client_for(row, payload):
    return resolve(row, Reference().run_validation(payload.pop('client_ref',None)), 'CLIENT', Client)


class AnimalSaleInput(serializers.Serializer):
    quantite = serializers.IntegerField(min_value=1)
    prix_unitaire = serializers.DecimalField(max_digits=10, decimal_places=2, min_value=Decimal('0.01'))


def animal_sale(row, user):
    create_only(row)
    payload, lot = data_with_lot(row, ['client_ref','quantite','prix_unitaire'])
    client = client_for(row, payload)
    serializer = AnimalSaleInput(data=payload); serializer.is_valid(raise_exception=True)
    data = serializer.validated_data
    day = timezone.localdate(row.business_occurred_at)
    historical = lot.mouvements.filter(date__lte=day).aggregate(n=Sum('quantite_signee'))['n'] or 0
    if data['quantite'] > min(stock(lot), historical):
        raise BusinessConflict('STOCK_INSUFFICIENT')
    if data['quantite'] * data['prix_unitaire'] > Decimal('9999999999.99'):
        raise BusinessConflict('AMOUNT_OUT_OF_RANGE')
    sale = Vente.objects.create(lot=lot, client=client, created_by=user, date=day, **data)
    Mouvement.objects.create(lot=lot, exploitation_id=row.exploitation_id, created_by=user,
        type_mouvement='VENTE', vente=sale, date=day, **data)
    return mark_lots(sale,lot)


def egg_sale(row,user):
    create_only(row)
    payload,lot=data_with_lot(row,['client_ref','conditionnement','nombre_conditionnements',
        'oeufs_par_conditionnement','prix_unitaire_conditionnement','nombre_alveoles',
        'oeufs_supplementaires','prix_total'])
    client=client_for(row,payload)
    payload.update(lot=lot.pk,client=client.pk,date=timezone.localdate(row.business_occurred_at).isoformat())
    serializer=VenteOeufsSerializer(data=payload,context=context(user));serializer.is_valid(raise_exception=True)
    try:
        sale=create_egg_sale(validated_data=dict(serializer.validated_data),user=user,
            business_occurred_at=row.business_occurred_at)
    except ValueError:
        raise BusinessConflict('EGG_STOCK_REVIEW_REQUIRED')
    return mark_lots(sale,lot)


class CashInput(serializers.Serializer):
    montant_recu=serializers.DecimalField(max_digits=12,decimal_places=2,min_value=Decimal('0.01'))
    mode=serializers.ChoiceField(choices=['ESPECES','MOBILE_MONEY','VIREMENT','CHEQUE'])
    note=serializers.CharField(max_length=10000,allow_blank=True,default='')
    vente_type=serializers.ChoiceField(choices=['VENTE_ANIMAUX','VENTE_OEUFS'],default='VENTE_ANIMAUX')


def cash_receipt(row,user):
    create_only(row)
    payload=dict(row.payload)
    if set(payload)-{'client_ref','vente_ref','vente_type','montant_recu','mode','note'}:
        raise BusinessConflict('UNKNOWN_BUSINESS_FIELDS')
    client=client_for(row,payload)
    sale_ref=payload.pop('vente_ref',None)
    serializer=CashInput(data=payload);serializer.is_valid(raise_exception=True)
    data=serializer.validated_data
    kind=data.pop('vente_type')
    sale=None
    if sale_ref is not None:
        sale=resolve(row,Reference().run_validation(sale_ref),kind,VenteOeufs if kind=='VENTE_OEUFS' else Vente)
        if kind=='VENTE_OEUFS':sale=sale.vente
        if sale.client_id!=client.pk:
            raise BusinessConflict('SALE_CLIENT_MISMATCH')
    amount=data['montant_recu']
    assigned=Decimal('0.00')
    if sale is not None:
        assigned=min(amount,max(Decimal('0.00'),sale.reste_a_payer))
    # Payment records the full recognized receipt; only the explicit target is allocated.
    payment=Payment.objects.create(exploitation_id=row.exploitation_id,client=client,created_by=user,
        montant=amount,date=timezone.localdate(row.business_occurred_at),note=data['note'])
    if assigned:
        Lettrage.objects.create(vente=sale,payment=payment,created_by=user,montant=assigned)
    cash=EncaissementTerrain.objects.create(exploitation_id=row.exploitation_id,submission=row,
        client=client,created_by=user,payment=payment,business_occurred_at=row.business_occurred_at,
        montant_affecte=assigned,montant_a_rapprocher=amount-assigned,**data)
    if cash.montant_a_rapprocher:
        cash._terrain_business_status='NEEDS_RECONCILIATION'
        cash._terrain_reason_code='CASH_REMAINDER_REVIEW'
    return cash


HANDLERS={'VENTE_ANIMAUX':animal_sale,'VENTE_OEUFS':egg_sale,'ENCAISSEMENT':cash_receipt}
