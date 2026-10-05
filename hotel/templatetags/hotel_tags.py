from decimal import Decimal
from django import template
register = template.Library()

@register.filter
def money(value):
    return f'{Decimal(value or 0):,.2f}'.replace(',', '\u00a0').replace('.', ',') + ' ₽'
