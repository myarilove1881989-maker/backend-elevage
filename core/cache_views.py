"""Read-only, bounded bootstrap pages for the encrypted farm cache."""
from django.db.models import Q, Sum, Value, IntegerField, OuterRef, Subquery
from django.db.models.functions import Coalesce
from django.utils import timezone
from django.db import transaction
from rest_framework import serializers
from rest_framework.decorators import api_view
from rest_framework.response import Response
from .models import Lot, Client, Espece, Task, Exploitation, CategorieDepense, MouvementOeufs
from .permissions import require_member
from .serializers import LotSerializer, ClientSerializer
from .task_views import AgendaSerializer


class CachePageInput(serializers.Serializer):
    collection = serializers.ChoiceField(choices=['lots', 'clients', 'species', 'tasks', 'expense_categories'])
    after = serializers.IntegerField(min_value=0, max_value=9223372036854775807, default=0)
    limit = serializers.IntegerField(min_value=1, max_value=200, default=50)


class CacheLotSerializer(LotSerializer):
    stock = serializers.IntegerField(source='confirmed_stock', read_only=True)
    stock_oeufs = serializers.IntegerField(source='confirmed_egg_stock', read_only=True)


def lot_stock_queryset(farm_id):
    eggs = MouvementOeufs.objects.filter(lot_id=OuterRef('pk'), exploitation_id=farm_id).values('lot_id').annotate(
        total=Sum('quantite_signee')).values('total')[:1]
    return Lot.objects.filter(exploitation_id=farm_id).select_related('espece').annotate(
        confirmed_stock=Coalesce(Sum('mouvements__quantite_signee'),Value(0),output_field=IntegerField()),
        confirmed_egg_stock=Coalesce(Subquery(eggs,output_field=IntegerField()),Value(0)))


@api_view(['GET'])
@transaction.atomic
def cache_page(request):
    member = require_member(request.user)
    inputs = CachePageInput(data=request.query_params)
    inputs.is_valid(raise_exception=True)
    collection, after, limit = (inputs.validated_data[key] for key in ('collection', 'after', 'limit'))
    farm = Exploitation.objects.select_for_update().get(pk=member.exploitation_id)
    models = {'lots': Lot, 'clients': Client, 'species': Espece, 'tasks': Task, 'expense_categories':CategorieDepense}
    queryset = models[collection].objects.filter(exploitation_id=member.exploitation_id, pk__gt=after)
    if collection == 'tasks' and member.role == 'OPERATEUR':
        queryset = queryset.filter(Q(assigned_to_id=request.user.pk) | Q(assigned_to__isnull=True))
    if collection == 'lots':
        queryset = lot_stock_queryset(member.exploitation_id).filter(pk__gt=after)
    rows = list(queryset.order_by('pk')[:limit + 1])
    has_more = len(rows) > limit
    rows = rows[:limit]
    if collection in ('species','expense_categories'):
        data = [{'id': row.pk, 'nom': row.nom, 'exploitation': row.exploitation_id} for row in rows]
    else:
        serializer = {'lots': CacheLotSerializer, 'clients': ClientSerializer, 'tasks': AgendaSerializer}[collection]
        data = serializer(rows, many=True).data
    if collection == 'lots':
        for item in data:
            item['confirmed_business_revision'] = farm.business_revision
    return Response({'collection': collection, 'results': data,
        'next_cursor': rows[-1].pk if has_more else None, 'server_time': timezone.now(),
        'confirmed_business_revision':farm.business_revision})


def stock_snapshots(farm_id, ids):
    if not ids:
        return []
    revision = Exploitation.objects.values_list('business_revision',flat=True).get(pk=farm_id)
    lots = lot_stock_queryset(farm_id).filter(pk__in=ids)
    data = CacheLotSerializer(lots,many=True).data
    for item in data:
        item['confirmed_business_revision'] = revision
    return data
