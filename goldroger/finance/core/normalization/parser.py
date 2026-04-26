import re

def parse_float(value, default=None):
    if value is None:
        return default

    try:
        if isinstance(value, (int, float)):
            return float(value)

        value = str(value)

        value = value.replace("%", "")
        value = value.replace(",", "")

        # handle M / B
        multiplier = 1
        if "b" in value.lower():
            multiplier = 1e9
        elif "m" in value.lower():
            multiplier = 1e6

        value = re.sub(r"[^0-9.]", "", value)

        return float(value) * multiplier

    except:
        return default