from django.urls import include, path
urlpatterns = [path('', include('hotel.urls'))]
handler403 = 'hotel.views.error403'
handler404 = 'hotel.views.error404'
handler500 = 'hotel.views.error500'
