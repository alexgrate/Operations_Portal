"""Sidebar data, needed on every signed-in page."""
from django.conf import settings

from . import queues


def sidebar(request):
    user = getattr(request, 'user', None)
    if not (user and user.is_authenticated):
        return {'asset_version': settings.ASSET_VERSION}

    items = []
    for key, label, icon in queues.visible_queues(user):
        _, qs = queues.get_queue(key, user)
        items.append({'key': key, 'label': label, 'icon': icon, 'count': qs.count()})

    return {
        'sidebar_queues': items,
        'is_management': queues.is_management(user),
        'is_head': queues.is_head(user),
        'asset_version': settings.ASSET_VERSION,
    }
