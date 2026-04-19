"""Integration tests for artifact sinks (oai-003).

Every test module in this package is gated by provider-specific
availability: either a local emulator (LocalStack for S3,
fake-gcs-server for GCS, Azurite for Azure Blob) or real cloud
credentials. CI runs them only on runners with the emulator
containers started; developer laptops without the emulators ``skip``
rather than fail.
"""
