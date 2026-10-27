from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.viewsets import ModelViewSet
from .models import Task, ExploitationMembership, Exploitation
from .permissions import HasExploitation, require_member
from .audit import audit_scope
from .request_context import online_context
from .access_policy import authorize, effective_permissions


class AgendaSerializer(serializers.ModelSerializer):
    expected_version = serializers.IntegerField(write_only=True, required=False)

    class Meta:
        model = Task
        fields = ('id', 'title', 'date', 'description', 'priority', 'status',
                  'assigned_to', 'created_by', 'created_at', 'updated_at',
                  'completed_by', 'completed_at', 'report', 'version', 'expected_version')
        fields += ('cancelled_by', 'cancelled_at', 'cancellation_reason')
        read_only_fields = ('created_by', 'created_at', 'updated_at', 'completed_by',
                            'completed_at', 'version')
        read_only_fields += ('cancelled_by', 'cancelled_at')

    def validate(self, attrs):
        user = self.context['request'].user
        member = require_member(user)
        rights = effective_permissions(member)
        planner = 'agenda.plan' in rights
        if attrs.get('status') == 'CANCELLED':
            if member.role != 'OWNER':
                raise PermissionDenied('TASK_CANCELLATION_OWNER_ONLY')
            if not self.instance:
                raise ValidationError({'status': 'EXISTING_TASK_REQUIRED'})
            if set(self.initial_data) - {'status', 'cancellation_reason', 'expected_version'}:
                raise ValidationError('CANCELLATION_FIELDS_ONLY')
            reason = attrs.get('cancellation_reason', '').strip()
            if not reason or len(reason) > 10000:
                raise ValidationError({'cancellation_reason': 'CANCELLATION_REASON_REQUIRED'})
            attrs['cancellation_reason'] = reason
        elif 'cancellation_reason' in attrs:
            raise ValidationError({'cancellation_reason': 'CANCELLATION_ONLY'})
        if self.instance and self.instance.status == 'CANCELLED':
            raise ValidationError({'status': 'CANCELLED_TASK_IMMUTABLE'})
        if self.instance and 'expected_version' not in attrs:
            raise ValidationError({'expected_version': 'TASK_VERSION_REQUIRED'})
        if self.instance and 'expected_version' in attrs:
            if attrs.pop('expected_version') != self.instance.version:
                raise ValidationError({'expected_version': 'TASK_VERSION_CONFLICT'})
        else:
            attrs.pop('expected_version', None)
        if not planner:
            authorize(user, user.exploitation_id, 'agenda.progress', self.instance)
            if not self.instance or set(self.initial_data) - {'status', 'report', 'expected_version'}:
                raise PermissionDenied('L’opérateur peut modifier uniquement état et compte rendu.')
            allowed = {'TODO': {'TODO', 'IN_PROGRESS'},
                       'IN_PROGRESS': {'IN_PROGRESS', 'DONE'}, 'DONE': {'DONE'},
                       'CANCELLED': set()}
            if attrs.get('status', self.instance.status) not in allowed[self.instance.status]:
                raise ValidationError({'status': 'Transition interdite.'})
        else:
            authorize(user, user.exploitation_id, 'agenda.plan', self.instance)
            if attrs.get('status') in ('IN_PROGRESS', 'DONE'):
                authorize(user, user.exploitation_id, 'agenda.progress', self.instance)
                allowed = {'TODO': {'TODO', 'IN_PROGRESS'},
                           'IN_PROGRESS': {'IN_PROGRESS', 'DONE'}, 'DONE': {'DONE'},
                           'CANCELLED': set()}
                if not self.instance or attrs['status'] not in allowed[self.instance.status]:
                    raise ValidationError({'status': 'Transition interdite.'})
        assignee = attrs.get('assigned_to')
        if assignee and not ExploitationMembership.objects.filter(
            user=assignee, exploitation_id=user.exploitation_id, role='OPERATEUR',
            is_active=True, user__is_active=True, user__exploitation_id=user.exploitation_id,
        ).exists():
            raise ValidationError({'assigned_to': 'Opérateur actif de cette exploitation requis.'})
        return attrs


class TaskViewSet(ModelViewSet):
    queryset = Task.objects.all()
    serializer_class = AgendaSerializer
    permission_classes = [IsAuthenticated, HasExploitation]
    agenda_endpoint = True

    def get_queryset(self):
        user = self.request.user
        qs = Task.objects.filter(exploitation_id=user.exploitation_id)
        if self.action == 'list' and self.request.query_params.get('active') == 'true':
            qs = qs.exclude(status='CANCELLED')
        if 'agenda.plan' not in effective_permissions(require_member(user)):
            qs = qs.filter(Q(assigned_to=user) | Q(assigned_to__isnull=True))
        if self.action in ('update', 'partial_update', 'destroy'):
            qs = qs.select_for_update()
        return qs

    def create(self, request, *args, **kwargs):
        with audit_scope(online_context(request)):
            farm = Exploitation.objects.select_for_update().get(pk=request.user.exploitation_id)
            request.user.exploitation = farm
            authorize(request.user, farm, 'agenda.plan')
            return super().create(request, *args, **kwargs)

    def perform_create(self, serializer):
        serializer.save(exploitation=self.request.user.exploitation, created_by=self.request.user)

    def update(self, request, *args, **kwargs):
        with audit_scope(online_context(request)):
            farm = Exploitation.objects.select_for_update().get(pk=request.user.exploitation_id)
            request.user.exploitation = farm
            require_member(request.user)
            return super().update(request, *args, **kwargs)

    def perform_update(self, serializer):
        old = serializer.instance.status
        new = serializer.validated_data.get('status', old)
        extra = {'version': serializer.instance.version + 1}
        if new == 'CANCELLED':
            extra.update(cancelled_by=self.request.user, cancelled_at=timezone.now())
        if new == 'DONE' and old != 'DONE':
            extra.update(completed_by=self.request.user, completed_at=timezone.now())
        elif new == 'TODO' and old != 'TODO':
            extra.update(completed_by=None, completed_at=None)
        serializer.save(**extra)

    def destroy(self, request, *args, **kwargs):
        raise PermissionDenied('Annuler la tâche pour conserver son historique.')
