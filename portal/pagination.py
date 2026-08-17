"""One way of splitting long lists across pages, used by every list view."""
from django.conf import settings
from django.core.paginator import EmptyPage, PageNotAnInteger, Paginator


def paginate(request, items, per_page=None):
    """Return the page of items to show, plus what the template needs.

    Takes a queryset or a plain list, so views that sort in Python (the queues
    order by urgency, which no database column knows about) work unchanged.

    A page number that is missing, not a number, or past the end lands on a
    real page rather than erroring: somebody deleting rows should not turn
    another person's bookmark into a crash.
    """
    per_page = per_page or settings.PAGE_SIZE
    paginator = Paginator(items, per_page)

    try:
        page = paginator.page(request.GET.get('page'))
    except PageNotAnInteger:
        page = paginator.page(1)
    except EmptyPage:
        page = paginator.page(paginator.num_pages)

    # Everything except the page number, so filters and searches survive a
    # click on "Next".
    params = request.GET.copy()
    params.pop('page', None)
    querystring = params.urlencode()

    return {
        'page': page,
        'items': page.object_list,
        'paginator': paginator,
        'is_paginated': paginator.num_pages > 1,
        'querystring': f'&{querystring}' if querystring else '',
    }
