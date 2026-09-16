from django.urls import path

from .views import api

# Kept for integrations that import ``apps.api.urls`` directly. Django's root
# URLConf mounts ``api.urls`` directly because Ninja returns a URLConf tuple.
urlpatterns = [path("", api.urls)]
