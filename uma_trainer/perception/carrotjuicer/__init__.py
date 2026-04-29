"""CarrotJuicer packet perception pipeline.

This package contains the schema, routing, and state assembly code that
consumes decrypted msgpack server responses captured by a CarrotJuicer-style
hook (see docs/PACKET_INTERCEPTION_SPEC.md).

The ``schema/`` subpackage defines the typed dataclasses for each packet kind.
Upstream receivers (WS-5) and GameState API (WS-6) should import from
``uma_trainer.perception.carrotjuicer.schema``.
"""
