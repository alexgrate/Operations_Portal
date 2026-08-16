from django import template

register = template.Library()


@register.filter
def initials(user):
    """Two letters for an avatar, falling back to the email when a person
    has no first or last name recorded."""
    if not user:
        return '?'

    first = (user.first_name or '').strip()
    last = (user.last_name or '').strip()
    if first or last:
        return f'{first[:1]}{last[:1]}'.upper()

    source = (user.email or user.get_username() or '').strip()
    return source[:2].upper() or '?'
