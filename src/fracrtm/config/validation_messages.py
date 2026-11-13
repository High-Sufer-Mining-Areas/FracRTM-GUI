import re

from fracrtm.messages import join_text
from fracrtm.messages import message as msg


def format_validation_issue(issue, config=None):
    value = config
    object_name = None
    for field, index in re.findall("([A-Za-z_]\\w*)|\\[(\\d+)\\]", issue.path):
        try:
            if index:
                value = value[int(index)]
            else:
                value = value.get(field) if isinstance(value, dict) else getattr(value, field)
            name = value.get("name") if isinstance(value, dict) else getattr(value, "name", None)
            identity = value.get("id") if isinstance(value, dict) else getattr(value, "id", None)
            if name or identity:
                object_name = str(name or identity)
        except (AttributeError, IndexError, KeyError, TypeError):
            value = None
            break
    location = issue.path or issue.code
    if object_name:
        location = join_text("", [object_name, " / ", location])
    if isinstance(value, str) and value:
        location += msg("validation.value_context", value=repr(value))
    return join_text("", [location, ": ", issue.message])
