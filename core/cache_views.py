"""Read-only, bounded bootstrap pages for the encrypted farm cache."""
from django.db.models import Q, Sum, Value, IntegerField
from django.db.models.functions import Coalesce
from django.utils import timezone
from rest_framework import serializers
from rest_framework.decorators import api_view
from rest_framework.response import Response
from .models import Lot, Client, Espece, Task
from .permissions import require_member
from .serializers import LotSerializer, ClientSerializer
from .task_views import AgendaSerializer


class CachePageInput(serializers.Serializer):
    collection = serializers.ChoiceField(choices=['lots', 'clients', 'species', 'tasks'])
    after = serializers.IntegerField(min_value=0, max_value=9223372036854775807, default=0)
    limit = serializers.IntegerField(min_value=1, max_value=200, default=50)


class CacheLotSerializer(LotSerializer):
    stock = serializers.IntegerField(source='confirmed_stock', read_only=True)


@api_view(['GET'])
def cache_page(request):
    member = require_member(request.user)
    inputs = CachePageInput(data=request.query_params)
    inputs.is_valid(raise_exception=True)
    collection, after, limit = (inputs.validated_data[key] for key in ('collection', 'after', 'limit'))
    models = {'lots': Lot, 'clients': Client, 'species': Espece, 'tasks': Task}
    queryset = models[collection].objects.filter(exploitation_id=member.exploitation_id, pk__gt=after)
    if collection == 'tasks' and member.role == 'OPERATEUR':
        queryset = queryset.filter(Q(assigned_to_id=request.user.pk) | Q(assigned_to__isnull=True))
    if collection == 'lots':
        queryset = queryset.select_related('espece').annotate(confirmed_stock=Coalesce(
            Sum('mouvements__quantite_signee'), Value(0), output_field=IntegerField()))
    rows = list(queryset.order_by('pk')[:limit + 1])
    has_more = len(rows) > limit
    rows = rows[:limit]
    if collection == 'species':
        data = [{'id': row.pk, 'nom': row.nom, 'exploitation': row.exploitation_id} for row in rows]
    else:
        serializer = {'lots': CacheLotSerializer, 'clients': ClientSerializer, 'tasks': AgendaSerializer}[collection]
        data = serializer(rows, many=True).data
    return Response({'collection': collection, 'results': data,
        'next_cursor': rows[-1].pk if has_more else None, 'server_time': timezone.now()})
