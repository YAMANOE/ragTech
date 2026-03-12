"""models package — expose schema dataclasses at package level."""
from .schema import (
    Document,
    DocumentVersion,
    Section,
    Entity,
    EntityRole,
    Topic,
    TopicAssignment,
    DocumentRelationship,
    SectionRelationship,
    CleanOutput,
    FetchRecord,
    ValidationResult,
    PipelineResult,
)

__all__ = [
    "Document",
    "DocumentVersion",
    "Section",
    "Entity",
    "EntityRole",
    "Topic",
    "TopicAssignment",
    "DocumentRelationship",
    "SectionRelationship",
    "CleanOutput",
    "FetchRecord",
    "ValidationResult",
    "PipelineResult",
]
