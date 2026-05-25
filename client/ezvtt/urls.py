from django.urls import path

from . import views

urlpatterns = [
    path("", views.index),
    path("play", views.play),
    path("login", views.login),
    path("register", views.register),
    path("wiki", views.wiki),
    path("wiki/<path:page>", views.wiki),
    path("wiki-media/<path:rel>", views.wiki_media),
    path("admin", views.admin),
    path("admin/upload/<str:kind>", views.admin_upload),
    path("admin/grid", views.admin_grid),
    path("media/<str:kind>/<str:source>/<str:name>", views.media_file),
]
