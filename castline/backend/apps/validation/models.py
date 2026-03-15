from django.db import models


class ValidationRun(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    label = models.CharField(max_length=120)
    thesis_rating = models.CharField(max_length=32, blank=True)
    improvement_pct = models.FloatField(null=True, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self) -> str:
        return f'{self.label} ({self.created_at:%Y-%m-%d %H:%M})'
