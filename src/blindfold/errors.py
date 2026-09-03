"""Public exceptions shared by Blindfold integration surfaces."""


class ProtectionError(ValueError):
    """A declared result cannot be protected without risking disclosure."""
