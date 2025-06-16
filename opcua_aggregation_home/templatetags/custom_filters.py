from django import template

register = template.Library()

@register.filter
def get_machine_name(endpoints_and_machines, endpoint):

    for machine in endpoints_and_machines:
        if machine['endpoint'] == endpoint:
            return machine['machine']
    return endpoint


@register.filter
def get_namespaceuri_name(endpoints_and_machines, endpoint):

    for machine in endpoints_and_machines:
        if machine['endpoint'] == endpoint:
            return machine['namespaceuri']
    return endpoint
