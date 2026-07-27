"""Address lookup — postcode + house number → street + city (issue #241).

A core capability, not companies-internal: any surface that captures an address (company
billing identity today; hosting/domain registrant details or contact addresses later) calls
the same seam. The provider is an abstraction — PDOK (the Dutch BAG ``Locatieserver``) is
the keyless default, so a fresh self-hosted instance gets Dutch lookups with zero
configuration. A keyed international provider (Mapbox) can slot in later behind the same
interface with an ``AISettings``-style encrypted settings row; nothing here assumes PDOK is
the only backend.
"""
