"""Existing terrain rules with explicit tenant references and locked stock."""
from decimal import Decimal
from types import SimpleNamespace
import uuid
from django.db.models import Sum
from django.utils import timezone
from rest_framework import serializers
from .models import (Lot, Espece, CategorieDepense, Depense, Achat, Mouvement,
    CollecteOeufs, ConsommationAliment, PeseeProduction, TerrainEntityMapping)
from .serializers import (AchatSerializer, NaissanceCreationSerializer, CollecteOeufsSerializer,
    ConsommationAlimentSerializer, PeseeProductionSerializer)
from .terrain_application import BusinessConflict
from .egg_services import save_collection


class Reference(serializers.Field):
    def to_internal_value(self, value):
        if not isinstance(value, dict) or len(value) != 1:
            raise serializers.ValidationError('Référence requise.')
        if set(value) == {'server_id'} and type(value['server_id']) is int and value['server_id'] > 0:
            return value
        if set(value) == {'local_uuid'}:
            try:
                return {'local_uuid':str(uuid.UUID(value['local_uuid']))}
            except (ValueError, TypeError, AttributeError):
                pass
        raise serializers.ValidationError('Référence invalide.')


def resolve(row, reference, kind='LOT', model=Lot):
    if 'local_uuid' in reference:
        try:
            mapping = TerrainEntityMapping.objects.select_related('submission__outcome').get(
                exploitation_id=row.exploitation_id, entity_type=kind,
                local_entity_id=reference['local_uuid'])
        except TerrainEntityMapping.DoesNotExist:
            raise BusinessConflict('LOCAL_REFERENCE_UNRESOLVED')
        if mapping.submission.outcome.business_status != 'CONFIRMED':
            raise BusinessConflict('LOCAL_REFERENCE_NOT_CONFIRMED')
        pk = mapping.server_entity_id
    else:
        pk = reference['server_id']
    try:
        tenant_field = 'lot__exploitation_id' if model._meta.model_name in ('depense','vente') else 'exploitation_id'
        entity = model.objects.select_for_update().get(pk=pk, **{tenant_field:row.exploitation_id})
        if model is Lot and TerrainEntityMapping.objects.filter(exploitation_id=row.exploitation_id,
            entity_type='LOT',server_entity_id=pk,submission__outcome__business_status='SUPERSEDED').exists():
            raise BusinessConflict('LOT_ORIGIN_REVERSED')
        return entity
    except model.DoesNotExist:
        raise BusinessConflict('REFERENCE_OUTSIDE_FARM')


def data_with_lot(row, allowed):
    data = dict(row.payload)
    if set(data)-set(allowed)-{'lot_ref'}:
        raise BusinessConflict('UNKNOWN_BUSINESS_FIELDS')
    reference = Reference().run_validation(data.pop('lot_ref', None))
    return data, resolve(row, reference)


def context(user, **extra):
    return {'request':SimpleNamespace(user=user), **extra}


def stock(lot):
    return lot.stock


def mark_lots(entity, *lots):
    entity._terrain_lot_ids = [lot.pk for lot in lots]
    return entity


def create_only(row):
    if row.operation_type != 'CREATE':
        raise BusinessConflict('OPERATION_REQUIRES_REVIEW')


class ExpenseInput(serializers.Serializer):
    montant = serializers.DecimalField(max_digits=12, decimal_places=2, min_value=Decimal('0.01'))
    categorie_id = serializers.IntegerField(min_value=1, required=False, allow_null=True)
    note = serializers.CharField(max_length=255, allow_blank=True, default='')


def expense(row, user):
    create_only(row)
    payload, lot = data_with_lot(row, ['montant', 'categorie_id', 'note'])
    serializer = ExpenseInput(data=payload); serializer.is_valid(raise_exception=True)
    data = serializer.validated_data
    category_id = data.pop('categorie_id', None)
    category = None
    if category_id:
        try:
            category = CategorieDepense.objects.get(pk=category_id, exploitation_id=row.exploitation_id)
        except CategorieDepense.DoesNotExist:
            raise BusinessConflict('CATEGORY_OUTSIDE_FARM')
    return Depense.objects.create(lot=lot, categorie=category, created_by=user,
        date=timezone.localdate(row.business_occurred_at), **data)


def feed(row, user):
    create_only(row)
    payload, lot = data_with_lot(row, ['aliment', 'quantite_kg', 'prix_kg', 'note', 'depense_ref'])
    expense_ref = payload.pop('depense_ref', None)
    if expense_ref is not None:
        depense = resolve(row, Reference().run_validation(expense_ref), 'DEPENSE', Depense)
        if depense.lot_id != lot.pk:
            raise BusinessConflict('EXPENSE_LOT_MISMATCH')
        payload['depense'] = depense.pk
    payload.update(lot=lot.pk, distribution_at=row.business_occurred_at.isoformat())
    serializer = ConsommationAlimentSerializer(data=payload, context=context(user))
    serializer.is_valid(raise_exception=True)
    return serializer.save(exploitation_id=row.exploitation_id, created_by=user)


def weighing(row, user):
    create_only(row)
    payload, lot = data_with_lot(row, ['nombre_animaux_peses', 'poids_total_kg', 'note'])
    payload.update(lot=lot.pk, pesee_at=row.business_occurred_at.isoformat())
    serializer = PeseeProductionSerializer(data=payload, context=context(user, stock_actuel=stock(lot)))
    serializer.is_valid(raise_exception=True)
    return serializer.save(exploitation_id=row.exploitation_id, created_by=user)


def egg_collection(row, user):
    if row.operation_type == 'UPDATE':
        from .egg_services import update_collection
        from .egg_collection_version import collection_version
        data = dict(row.payload)
        reference = Reference().run_validation(data.pop('collection_ref', None))
        original = resolve(row, reference, 'COLLECTE_OEUFS', CollecteOeufs)
        lot = resolve(row, Reference().run_validation(data.pop('lot_ref', None)))
        if lot.pk != original.lot_id:
            raise BusinessConflict('COLLECTION_LOT_MISMATCH')
        previous = data.pop('previous_quantity', None)
        if row.expected_server_version != collection_version(original):
            raise BusinessConflict('COLLECTION_VERSION_CONFLICT')
        if previous != original.nombre_commercialisable:
            raise BusinessConflict('COLLECTION_QUANTITY_CONFLICT')
        allowed = {'nombre_collecte','nombre_casses','nombre_declasses','nombre_consommes_donnes','note'}
        if set(data) != allowed:
            raise BusinessConflict('UNKNOWN_BUSINESS_FIELDS')
        serializer = CollecteOeufsSerializer(original, data=data, partial=True,
            context=context(user, expected_collection_version=row.expected_server_version))
        serializer.is_valid(raise_exception=True)
        return mark_lots(update_collection(serializer=serializer), lot)
    create_only(row)
    payload, lot = data_with_lot(row, ['nombre_collecte','nombre_alveoles','oeufs_restants',
        'nombre_casses','nombre_declasses','nombre_consommes_donnes','note'])
    payload.update(lot=lot.pk, collecte_at=row.business_occurred_at.isoformat())
    serializer = CollecteOeufsSerializer(data=payload, context=context(user))
    serializer.is_valid(raise_exception=True)
    return mark_lots(save_collection(serializer=serializer, user=user), lot)


class RemovalInput(serializers.Serializer):
    quantite = serializers.IntegerField(min_value=1)
    note = serializers.CharField(max_length=10000, allow_blank=True, default='')


def removal(row, user):
    create_only(row)
    payload, lot = data_with_lot(row, ['quantite','note'])
    serializer = RemovalInput(data=payload);serializer.is_valid(raise_exception=True)
    data = serializer.validated_data
    if data['quantite'] > stock(lot):
        raise BusinessConflict('STOCK_INSUFFICIENT')
    entity = Mouvement.objects.create(lot=lot, exploitation_id=row.exploitation_id, created_by=user,
        type_mouvement=row.entity_type, date=timezone.localdate(row.business_occurred_at), **data)
    return mark_lots(entity, lot)


def map_entity(row, kind, entity):
    if row.local_entity_id:
        if TerrainEntityMapping.objects.filter(exploitation_id=row.exploitation_id,
                entity_type=kind, local_entity_id=row.local_entity_id).exists():
            raise BusinessConflict('LOCAL_ENTITY_ALREADY_MAPPED')
        TerrainEntityMapping.objects.create(exploitation_id=row.exploitation_id, entity_type=kind,
            local_entity_id=row.local_entity_id, server_entity_id=entity.pk, submission=row)


def purchase(row, user):
    create_only(row)
    allowed = {'nom_lot','espece','quantite','prix_total','prix_unitaire','fournisseur','note',
        'type_production','statut_production','date_naissance','age_arrivee_semaines','date_debut_ponte'}
    if set(row.payload)-allowed or not row.local_entity_id:
        raise BusinessConflict('INVALID_PURCHASE_DECLARATION')
    payload = dict(row.payload)
    payload['date'] = timezone.localdate(row.business_occurred_at)
    species_id = payload.get('espece')
    if (type(species_id) is int or (isinstance(species_id, str) and species_id.isascii() and species_id.isdigit())):
        if not Espece.objects.filter(pk=species_id, exploitation_id=row.exploitation_id).exists():
            raise BusinessConflict('SPECIES_OUTSIDE_FARM')
    serializer = AchatSerializer(data=payload, context=context(user));serializer.is_valid(raise_exception=True)
    if not Espece.objects.filter(pk=serializer.validated_data['espece'], exploitation_id=row.exploitation_id).exists():
        raise BusinessConflict('SPECIES_OUTSIDE_FARM')
    entity = serializer.save()
    map_entity(row, 'LOT', entity.lot)
    return mark_lots(entity, entity.lot)


def birth(row, user):
    create_only(row)
    payload, parent = data_with_lot(row, ['total_naissances','mort_nes','nom_nouveau_lot','type_production','note'])
    if not row.local_entity_id:
        raise BusinessConflict('LOCAL_LOT_UUID_REQUIRED')
    payload['date'] = timezone.localdate(row.business_occurred_at)
    serializer = NaissanceCreationSerializer(data=payload);serializer.is_valid(raise_exception=True)
    data = serializer.validated_data
    child = Lot.objects.create(nom=data['nom_nouveau_lot'], espece=parent.espece,
        exploitation_id=row.exploitation_id, date_debut=data['date'], date_naissance=data['date'],
        type_production=data['type_production'], statut_production='ELEVAGE', created_by=user)
    movement = Mouvement.objects.create(lot=child, lot_origine=parent, exploitation_id=row.exploitation_id,
        created_by=user, type_mouvement='NAISSANCE', quantite=data['total_naissances']-data['mort_nes'],
        mort_nes=data['mort_nes'], date=data['date'], note=data['note'])
    map_entity(row, 'LOT', child)
    return mark_lots(movement, child)


HANDLERS = {'DEPENSE':expense,'ALIMENTATION':feed,'PESEE':weighing,'COLLECTE_OEUFS':egg_collection,
    'MORTALITE':removal,'DON':removal,'VOL':removal,'ACHAT':purchase,'NAISSANCE':birth}
from .business_authorization import guarded_handler
from .permission_catalog import operation_action
HANDLERS = {kind: guarded_handler(None if kind == 'COLLECTE_OEUFS' else operation_action(kind, 'CREATE'), handler)
            for kind, handler in HANDLERS.items()}
# Exported direct service calls get the same boundary as dispatch-table calls.
for _kind, _handler in HANDLERS.items():
    if _kind not in {'MORTALITE', 'DON', 'VOL'}:
        globals()[_handler.__name__] = _handler
removal = guarded_handler(None, removal)
