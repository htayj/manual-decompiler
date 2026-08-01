"""LMDOC schema and structural validation."""

from .conformance import Finding, ValidationReport, validate_lmdoc, validate_package, validate_tree
from .facets import FacetReport, validate_facets
from .replica import (
    ReplicaAttestation,
    ReplicaAttestationInputs,
    ReplicaEvidence,
    ReplicaReport,
    accessibility_structure_evidence,
    attest_replica,
    authoritative_text_equivalent,
    replica_evidence_from_dict,
    validate_replica,
)
from .schema import SchemaValidationError, validate_instance, validate_schema

__all__ = [
    "Finding",
    "FacetReport",
    "SchemaValidationError",
    "ReplicaAttestation",
    "ReplicaAttestationInputs",
    "ReplicaEvidence",
    "ReplicaReport",
    "accessibility_structure_evidence",
    "attest_replica",
    "authoritative_text_equivalent",
    "replica_evidence_from_dict",
    "ValidationReport",
    "validate_instance",
    "validate_lmdoc",
    "validate_replica",
    "validate_facets",
    "validate_package",
    "validate_schema",
    "validate_tree",
]
