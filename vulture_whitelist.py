# Vulture whitelist — false positives that are actually used
# These are parameters required by framework signatures (Click, signal handlers, etc.)

# Signal handler signature requires 'frame' parameter
frame  # noqa
# Click callback parameters bound by decorator, not called directly
parameters  # noqa
formatter  # noqa
headless  # noqa
# Trigger source interface requires 'raw_event'
raw_event  # noqa
# Used at runtime via string reference
_Literal  # noqa
