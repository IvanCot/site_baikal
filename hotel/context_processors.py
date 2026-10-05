from django.utils import timezone

def navigation(request):
    return {'is_manager': request.user.is_authenticated and request.user.is_manager,
            'today': timezone.localdate(), 'house_timezone': timezone.get_current_timezone_name()}
