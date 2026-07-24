"""Deprecated entry point retained only to fail closed.

The deployed Universe pipeline is ``universe_downloader`` followed by
``universe_sic_worker``.  That pipeline produces the complete SIC/NAICS schema
and publishes it through the immutable dataset-run contract.  This older
single-function implementation wrote mutable keys directly and produced an
incomplete schema, so allowing it to run would bypass both integrity and data
quality controls.
"""


def lambda_handler(event, context):
    raise RuntimeError(
        "This legacy mutable Universe publisher is disabled. Invoke "
        "euclidean-universe-downloader; its SIC worker publishes a verified "
        "immutable run and refreshes the compatibility alias."
    )
