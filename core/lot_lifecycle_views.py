"""Owner-only, version-bound close/reopen commands; existing audit trail retained."""
from django.utils import timezone
from django.db.models import Sum
from rest_framework import serializers
from rest_framework.decorators import api_view,permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.exceptions import PermissionDenied,NotFound,ValidationError
from .permissions import require_owner
from .owner_http import owner_http_request
from .request_context import online_context
from .audit import audit_scope
from .models import Lot,Exploitation,Vente,Lettrage
from .lot_lifecycle import closed,latest_activity
from .access_policy import authorize

class Command(serializers.Serializer):
    action=serializers.ChoiceField(choices=['CLOSE','REOPEN'])
    farm=serializers.IntegerField(min_value=1)
    expected_revision=serializers.IntegerField(min_value=0)
    confirm=serializers.BooleanField()
    date_fin=serializers.DateField(required=False)
    reason=serializers.CharField(max_length=1000,required=False,allow_blank=True,default='')

@api_view(['GET','POST'])
@permission_classes([IsAuthenticated])
def lifecycle(request,pk):
    member=require_owner(request.user)
    if not owner_http_request(request):raise PermissionDenied('Accès propriétaire HTTP vérifié requis.')
    command=None
    if request.method=='POST':
        serializer=Command(data=request.data);serializer.is_valid(raise_exception=True);command=serializer.validated_data
    action=command['action'] if command else 'VIEW'
    with audit_scope(online_context(request),reason_code='LOT_'+action,reason_text=command['reason'] if command else ''):
        farm=Exploitation.objects.select_for_update().get(pk=member.exploitation_id)
        lot=Lot.objects.select_for_update().filter(pk=pk,exploitation_id=farm.pk).first()
        if lot is None:raise NotFound()
        authorize(request.user,farm,'lots.update',lot,owner_http=True)
        if command:
            if not command['confirm'] or request.data.get('confirm') is not True:raise ValidationError('Confirmation explicite requise.')
            if command['farm']!=farm.pk or command['expected_revision']!=farm.business_revision:raise ValidationError('Les données ont changé. Rechargez le lot avant de confirmer.')
            if action=='CLOSE':
                if closed(lot):raise ValidationError('Ce lot est déjà clôturé.')
                end=command.get('date_fin')
                if end is None or end>timezone.localdate() or end<latest_activity(lot):raise ValidationError('La clôture doit suivre les activités enregistrées et ne peut pas être future.')
                lot.date_fin=end
            else:
                if not closed(lot):raise ValidationError('Ce lot est déjà ouvert.')
                lot.date_fin=None
                if lot.statut_production=='TERMINE':lot.statut_production='PONTE' if lot.type_production=='OEUFS' and lot.date_debut_ponte else 'ELEVAGE'
            lot.save(update_fields=['date_fin','statut_production'])
            farm.business_revision+=1;farm.save(update_fields=['business_revision'])
        from .egg_services import get_egg_stock
        billed=Vente.objects.filter(lot=lot).aggregate(n=Sum('montant_total'))['n'] or 0
        paid=Lettrage.objects.filter(vente__lot=lot,vente__voided_at__isnull=True).aggregate(n=Sum('montant'))['n'] or 0
        return Response({'id':lot.pk,'farm':farm.pk,'revision':farm.business_revision,'closed':closed(lot),
            'date_fin':lot.date_fin,'stock_animaux':lot.stock,'stock_oeufs':get_egg_stock(farm,lot),
            'creances':max(billed-paid,0),'date_minimum':latest_activity(lot),
            'message':'La clôture ne crée aucune sortie de stock et conserve intégralement les opérations, paiements et historiques. Les règlements restent autorisés. Réouvrez le lot pour reprendre les opérations.'})
