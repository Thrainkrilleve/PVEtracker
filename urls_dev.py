from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path('admin/', admin.site.urls),
    path('pvetracker/', include('aa_pvetracker.urls', namespace='pvetracker')),
]
