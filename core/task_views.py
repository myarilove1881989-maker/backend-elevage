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


class AgendaSerializer(serializers.ModelSerializer):
    expected_version = serializers.IntegerField(write_only=True, required=False)

    class Meta:
        model = Task
        fields = ('id', 'title', 'date', 'description', 'priority', 'status',
                  'assigned_to', 'created_by', 'created_at', 'updated_at',
                  'completed_by', 'completed_at', 'report', 'version', 'expected_version')
        read_only_fields = ('created_by', 'created_at', 'updated_at', 'completed_by',
                            'completed_at', 'version')

    def validate(self, attrs):
        user = self.context['request'].user
        member = require_member(user)
        if self.instance and 'expected_version' in attrs:
            if attrs.pop('expected_version') != self.instance.version:
                raise ValidationError({'expected_version': 'TASK_VERSION_CONFLICT'})
        else:
            attrs.pop('expected_version', None)
        if user.exploitation.offline_policy_enabled:
            if member.role == 'OPERATEUR':
                if not self.instance or set(self.initial_data) - {'status', 'report', 'expected_version'}:
                    raise PermissionDenied('L’opérateur peut modifier uniquement état et compte rendu.')
                allowed = {'TODO': {'TODO', 'IN_PROGRESS'},
                           'IN_PROGRESS': {'IN_PROGRESS', 'DONE'}, 'DONE': {'DONE'},
                           'CANCELLED': set()}
                if attrs.get('status', self.instance.status) not in allowed[self.instance.status]:
                    raise ValidationError({'status': 'Transition interdite.'})
            elif attrs.get('status') in ('IN_PROGRESS', 'DONE'):
                raise ValidationError({'status': 'La réalisation appartient à l’opérateur.'})
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
        if user.exploitation.offline_policy_enabled and require_member(user).role == 'OPERATEUR':
            qs = qs.filter(Q(assigned_to=user) | Q(assigned_to__isnull=True))
        if self.action in ('update', 'partial_update', 'destroy'):
            qs = qs.select_for_update()
        return qs

    def create(self, request, *args, **kwargs):
        with audit_scope(online_context(request)):
            farm = Exploitation.objects.select_for_update().get(pk=request.user.exploitation_id)
            request.user.exploitation = farm
            if farm.offline_policy_enabled and require_member(request.user).role != 'OWNER':
                raise PermissionDenied('Planification réservée au propriétaire.')
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
        if new == 'DONE' and old != 'DONE':
            extra.update(completed_by=self.request.user, completed_at=timezone.now())
        elif new == 'TODO' and old != 'TODO':
            extra.update(completed_by=None, completed_at=None)
        serializer.save(**extra)

    def destroy(self, request, *args, **kwargs):
        if request.user.exploitation.offline_policy_enabled:
            raise PermissionDenied('Annuler la tâche pour conserver son historique.')
        with audit_scope(online_context(request)):
            return super().destroy(request, *args, **kwargs)
