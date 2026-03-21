from django.contrib import admin
from django.http import JsonResponse
from django.urls import path

from castline.backend.apps.core.views import (
    batch_predict, best_fishing, best_fishing_v2, catch_report,
    forecast_v2, locations, model_info, predict, predict_v2,
    resolve_prediction, signal_dashboard,
)


def healthcheck(_request):
    return JsonResponse({'status': 'ok', 'lane': 'phase-1-production'})


urlpatterns = [
    path('admin/', admin.site.urls),
    path('healthz/', healthcheck),
    path('api/predict/', predict),
    path('api/predict/batch/', batch_predict),
    path('api/model/info/', model_info),
    path('api/locations/', locations),
    path('api/best-fishing/', best_fishing),

    # v2 — 4-layer decision system
    path('api/v2/predict/', predict_v2),
    path('api/v2/forecast/', forecast_v2),
    path('api/v2/catch-report/', catch_report),
    path('api/v2/best-fishing/', best_fishing_v2),

    # Negative signal tracking
    path('api/v2/resolve-prediction/', resolve_prediction),
    path('api/v2/signals/', signal_dashboard),
]
