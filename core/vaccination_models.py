from django.db import models
from .audit import AuditedModel


class Vaccination(AuditedModel):
    """Current projection; every version remains an immutable SupplyOperation."""
    exploitation = models.ForeignKey('core.Exploitation', on_delete=models.PROTECT)
    lot = models.ForeignKey('core.Lot', on_delete=models.PROTECT)
    product = models.ForeignKey('core.SupplyProduct', on_delete=models.PROTECT)
    original = models.OneToOneField('core.SupplyOperation', on_delete=models.PROTECT, related_name='vaccination_original')
    current = models.OneToOneField('core.SupplyOperation', on_delete=models.PROTECT, related_name='vaccination_current')
    vaccinated_at = models.DateTimeField()
    animals = models.PositiveIntegerField()
    observations = models.TextField(blank=True)
    created_by = models.ForeignKey('core.User', on_delete=models.PROTECT)
    voided_at = models.DateTimeField(null=True)
