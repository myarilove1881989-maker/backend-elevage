"""Extensions of TaskViewSet, never an alternative task repository."""
from django.db import transaction
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.exceptions import ValidationError
from .audit import audit_scope
from .request_context import online_context
from .access_policy import authorize,effective_permissions
from .models import Exploitation,ExploitationMembership,AuditEvent


class HealthTaskActions:
    @action(detail=False,methods=['post'],url_path='health-plan')
    @transaction.atomic
    def health_plan(self,request):
        from .health_agenda import plan
        with audit_scope(online_context(request),reason_code='HEALTH_PLAN'):
            tasks=plan(request,dict(request.data))
            return Response(self.get_serializer(tasks,many=True).data,status=201)

    @action(detail=True,methods=['post'],url_path='health-move')
    @transaction.atomic
    def health_move(self,request,pk=None):
        from .health_agenda import move
        with audit_scope(online_context(request),reason_code='HEALTH_MOVE',reason_text=str(request.data.get('reason',''))):
            Exploitation.objects.select_for_update().get(pk=request.user.exploitation_id)
            task=self.get_object()
            return Response(self.get_serializer(move(request,task,dict(request.data))).data)

    @action(detail=False,methods=['get'],url_path='health-assignees')
    def health_assignees(self,request):
        authorize(request.user,request.user.exploitation_id,'agenda.plan')
        rows=ExploitationMembership.objects.select_related('user','exploitation').filter(exploitation_id=request.user.exploitation_id,
            user__exploitation_id=request.user.exploitation_id,role='OPERATEUR',is_active=True,user__is_active=True)
        return Response([{'user':row.user_id,'name':row.user.username} for row in rows
            if {'agenda.read','agenda.progress','vaccinations.create'}<=effective_permissions(row)])

    @action(detail=True,methods=['get'],url_path='health-history')
    def health_history(self,request,pk=None):
        task=self.get_object()
        from .health_models import HealthTask
        from django.db.models import Q
        meta=HealthTask.objects.filter(task=task).first()
        if meta is None:raise ValidationError('HEALTH_TASK_REQUIRED')
        rows=AuditEvent.objects.filter(exploitation_id=request.user.exploitation_id).filter(
            Q(entity_type='core.task',entity_id=str(task.pk))|Q(entity_type='core.healthtask',entity_id=str(meta.pk))).order_by('pk')
        return Response([{'date':row.applied_at,'operator':row.actor_user_id,'reason':row.reason_text,
            'before':row.before_data,'after':row.after_data,'source':row.source} for row in rows])
