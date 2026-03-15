from django.contrib import admin

from .models import ValidationRun


@admin.register(ValidationRun)
class ValidationRunAdmin(admin.ModelAdmin):
    list_display = ('label', 'created_at', 'thesis_rating', 'improvement_pct')
    search_fields = ('label', 'thesis_rating')
