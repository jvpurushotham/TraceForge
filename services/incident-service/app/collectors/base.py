class CollectorError(Exception):
    """A telemetry source could not be queried. Reported as MISSING evidence, never papered over."""
