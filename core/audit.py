"""Explicit transactional audit scopes, shared by API and administration."""
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from django.db import models, transaction
from django.db.models.deletion import Collector
from django.utils import timezone


@dataclass(frozen=True)
class ActorContext:
    author_id: int
    exploitation_id: int
    device_id: int | None = None
    transport_identity: str = "USER"
    decision_actor_id: int | None = None
    source: str = 'ONLINE'
    operation_id: uuid.UUID | None = None
    local_entity_id: uuid.UUID | None = None
    business_occurred_at: datetime | None = None


_scope = ContextVar("audit_scope", default=None)
SECRET_NAMES = {"password", "pin", "token", "access", "refresh", "refresh_token",
                "private_key", "secret", "code_hash", "public_key"}


def safe_data(value):
    if isinstance(value, dict):
        return {str(k): safe_data(v) for k, v in value.items()
                if not any(secret in str(k).lower() for secret in SECRET_NAMES)}
    if isinstance(value, (list, tuple)):
        return [safe_data(v) for v in value]
    if isinstance(value, (datetime, date, Decimal, uuid.UUID)):
        return str(value)
    return value


def snapshot(obj):
    return safe_data({f.attname: getattr(obj, f.attname) for f in obj._meta.concrete_fields})


@contextmanager
def audit_scope(context, *, reason_code="", reason_text=""):
    # Nested services keep the same author and correlation.
    if _scope.get() is not None:
        yield
        return
    with transaction.atomic():
        token = _scope.set((context, uuid.uuid4(), reason_code, reason_text))
        try:
            yield
        finally:
            _scope.reset(token)


def record(obj, action, before=None, after=None, *, category=None):
    scope = _scope.get()
    if scope is None:
        return
    from .models import AuditEvent
    context, correlation, reason, text = scope
    if category is None:
        category = ('AGENDA' if obj._meta.model_name == 'task' else
                    'SECURITY' if obj._meta.model_name in ('exploitationmembership',
                    'deviceregistration', 'offlineauthorization') else 'BUSINESS')
    AuditEvent.objects.create(
        exploitation_id=context.exploitation_id, actor_user_id=context.author_id,
        decision_actor_id=context.decision_actor_id, device_id=context.device_id,
        transport_identity=context.transport_identity, category=category,
        action=action, entity_type=obj._meta.label_lower, entity_id=str(obj.pk),
        correlation_id=correlation, before_data=safe_data(before or {}),
        after_data=safe_data(after or {}), reason_code=reason, reason_text=text,
        applied_at=timezone.now(),
        source=context.source, operation_id=context.operation_id,
        local_entity_id=context.local_entity_id,
        business_occurred_at=context.business_occurred_at,
    )


class AuditedQuerySet(models.QuerySet):
    def update(self, **kwargs):
        if _scope.get() is not None:
            raise ValueError('Use explicit model saves within an audited write scope.')
        return super().update(**kwargs)

    def bulk_update(self, *args, **kwargs):
        if _scope.get() is not None:
            raise ValueError('Use explicit model saves within an audited write scope.')
        return super().bulk_update(*args, **kwargs)

    def bulk_create(self, objs, **kwargs):
        objs = list(objs)
        if _scope.get() is None:
            return super().bulk_create(objs, **kwargs)
        if kwargs.get('ignore_conflicts') or kwargs.get('update_conflicts'):
            raise ValueError('Audited bulk inserts cannot silently resolve conflicts.')
        with transaction.atomic():
            result = super().bulk_create(objs, **kwargs)
            for obj in result:
                record(obj, 'CREATE', {}, snapshot(obj))
            return result

    def delete(self):
        return delete_with_audit(self)


def delete_with_audit(objects):
    """Record cascade victims and SET_NULL survivors before Django deletes."""
    collector = Collector(using=objects.db)
    collector.collect(objects)
    victims = {}
    for model, instances in collector.data.items():
        for obj in instances:
            if isinstance(obj, AuditedModel):
                victims[(model, obj.pk)] = obj
    for qs in collector.fast_deletes:
        for obj in qs:
            if isinstance(obj, AuditedModel):
                victims[(type(obj), obj.pk)] = obj
    # A normal delete must never erase the facts of an offline declaration or
    # their later compensation, including victims reached through a cascade.
    from .models import AuditEvent
    traced = models.Q(pk__in=[])
    for obj in victims.values():
        traced |= models.Q(entity_type=obj._meta.label_lower, entity_id=str(obj.pk))
        if getattr(obj, 'voided_at', None) is not None:
            from rest_framework.exceptions import ValidationError
            raise ValidationError('TERRAIN_HISTORY_REQUIRES_REASONED_DECISION')
    if victims and AuditEvent.objects.filter(traced, operation_id__isnull=False).exists():
        from rest_framework.exceptions import ValidationError
        raise ValidationError('TERRAIN_HISTORY_REQUIRES_REASONED_DECISION')
    with transaction.atomic():
        for obj in victims.values():
            record(obj, "DELETE", snapshot(obj), {})
        for (field, value), groups in collector.field_updates.items():
            for group in groups:
                for obj in group:
                    if isinstance(obj, AuditedModel) and (type(obj), obj.pk) not in victims:
                        before = snapshot(obj)
                        after = dict(before)
                        after[field.attname] = value
                        record(obj, "UPDATE", before, after)
        return collector.delete()


class AuditedModel(models.Model):
    objects = AuditedQuerySet.as_manager()

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        scope = _scope.get()
        before = {}
        if scope is not None and self.pk:
            original = type(self)._base_manager.filter(pk=self.pk).first()
            if original:
                before = snapshot(original)
        if scope is not None and self._state.adding and hasattr(self, "created_by_id"):
            self.created_by_id = scope[0].author_id
        super().save(*args, **kwargs)
        if scope is not None:
            self.refresh_from_db()
            after = snapshot(self)
            if before != after:
                record(self, "UPDATE" if before else "CREATE", before, after)

    def delete(self, *args, **kwargs):
        result = delete_with_audit(type(self)._base_manager.filter(pk=self.pk))
        self.pk = None
        return result


class ActiveBusinessManager(models.Manager.from_queryset(AuditedQuerySet)):
    def get_queryset(self):
        return super().get_queryset().filter(voided_at__isnull=True)


class ReversibleAuditedModel(AuditedModel):
    """Operational lists exclude voided facts; unfiltered history remains intact."""
    voided_at = models.DateTimeField(null=True, editable=False)
    void_decision_uuid = models.UUIDField(null=True, editable=False)
    objects = ActiveBusinessManager()
    history = AuditedQuerySet.as_manager()

    class Meta:
        abstract = True
        constraints = [models.CheckConstraint(
            condition=(models.Q(voided_at__isnull=True, void_decision_uuid__isnull=True) |
                models.Q(voided_at__isnull=False, void_decision_uuid__isnull=False)),
            name='%(app_label)s_%(class)s_void_consistent')]
