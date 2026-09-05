"""Public exceptions shared by VaultCompute integration surfaces."""


class ProtectionError(ValueError):
    """A declared result cannot be protected without risking disclosure."""
