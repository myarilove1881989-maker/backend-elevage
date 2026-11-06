"""Sanitary metadata and links; Task remains the only task/status system."""
from django.db import models
from django.core.exceptions import ValidationError
from .audit import AuditedModel
from .supply_models import SupplyFact


class HealthResponsible(AuditedModel):
    """A planning contact, never a login, permission or offline authority."""
    exploitation = models.ForeignKey('core.Exploitation', on_delete=models.PROTECT)
    name = models.CharField(max_length=150)
    profession = models.CharField(max_length=16, choices=[('VETERINAIRE','Vétérinaire'),('OPERATEUR','Opérateur')])
    operation_uuid = models.UUIDField()
    created_by = models.ForeignKey('core.User', on_delete=models.PROTECT)
    class Meta:
        constraints=[models.UniqueConstraint(fields=['exploitation','operation_uuid'],name='health_responsible_once')]
    def clean(self):
        if self.created_by.exploitation_id != self.exploitation_id:
            raise ValidationError('HEALTH_RESPONSIBLE_OUTSIDE_FARM')
    def save(self,*args,**kwargs):
        self.full_clean();super().save(*args,**kwargs)


class HealthTask(AuditedModel):
    responsible = models.ForeignKey(HealthResponsible, null=True, blank=True, on_delete=models.PROTECT)
    task = models.OneToOneField('core.Task', on_delete=models.PROTECT, related_name='health_plan')
    product = models.ForeignKey('core.SupplyProduct', on_delete=models.PROTECT)
    lot = models.ForeignKey('core.Lot', on_delete=models.PROTECT)
    planned_time = models.TimeField(null=True, blank=True)
    original_date = models.DateField()
    original_time = models.TimeField(null=True, blank=True)
    series_uuid = models.UUIDField()
    interval_days = models.PositiveSmallIntegerField(null=True, blank=True)
    series_end = models.DateField(null=True, blank=True)

    def clean(self):
        if self.responsible_id and self.responsible.exploitation_id != self.task.exploitation_id:
            raise ValidationError('HEALTH_RESPONSIBLE_OUTSIDE_FARM')
        if self.task.exploitation_id!=self.product.exploitation_id or self.lot.exploitation_id!=self.task.exploitation_id or self.product.kind!='VACCINE':
            raise ValidationError('HEALTH_TASK_OUTSIDE_FARM')
        if not self._state.adding:
            old=type(self).objects.get(pk=self.pk)
            if any(getattr(self,field)!=getattr(old,field) for field in ('task_id','product_id','lot_id','original_date','original_time','series_uuid','interval_days','series_end')):
                raise ValidationError('HEALTH_ORIGINAL_PLAN_IMMUTABLE')

    def save(self,*args,**kwargs):
        self.full_clean();super().save(*args,**kwargs)


class HealthCommand(SupplyFact):
    exploitation = models.ForeignKey('core.Exploitation', on_delete=models.PROTECT)
    operation_uuid = models.UUIDField()
    author = models.ForeignKey('core.User', on_delete=models.PROTECT)
    kind = models.CharField(max_length=8)
    payload = models.JSONField()
    fingerprint = models.CharField(max_length=64)
    task_ids = models.JSONField()
    recorded_at = models.DateTimeField(auto_now_add=True)

    class Meta(SupplyFact.Meta):
        constraints=[models.UniqueConstraint(fields=['exploitation','operation_uuid'],name='health_command_once')]

    def clean(self):
        if self.author.exploitation_id!=self.exploitation_id:raise ValidationError('HEALTH_COMMAND_OUTSIDE_FARM')


class VaccinationTaskLink(SupplyFact):
    task = models.ForeignKey('core.Task', on_delete=models.PROTECT, related_name='vaccination_links')
    vaccination = models.OneToOneField('core.Vaccination', on_delete=models.PROTECT, related_name='planned_task_link')
    recorded_at = models.DateTimeField(auto_now_add=True)

    def clean(self):
        plan=HealthTask.objects.filter(task=self.task).first()
        if (plan is None or self.task.exploitation_id!=self.vaccination.exploitation_id
                or plan.lot_id!=self.vaccination.lot_id or plan.product_id!=self.vaccination.product_id):
            raise ValidationError('VACCINATION_TASK_MISMATCH')
