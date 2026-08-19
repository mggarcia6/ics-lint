"""icslint: a strict, position-aware checker for .ics calendar files."""

from .parser import Component, IcsError, Pos, Property, parse_document, validate

__version__ = "0.1.0"

__all__ = [
    "Component",
    "IcsError",
    "Pos",
    "Property",
    "parse_document",
    "validate",
    "__version__",
]
