from django.urls import path

from . import views

urlpatterns = [
    path('', views.index, name='index'),
    path('clips/upload', views.upload, name='upload'),
    path('timeline/drop', views.drop, name='drop'),
    path('regions/<int:pk>', views.region, name='region'),
    path('regions/<int:pk>/split', views.split, name='split'),
    path('chat', views.chat, name='chat'),
    path('events', views.events, name='events'),
]
