"""Operator-controlled mapping of slotless occurrences to a TermOffering.

Nothing here infers a section from the professor, course, room, time or the
students who happened to be captured. An operator states the association,
gives a reason, and may only replace it with an audited supersede.
"""

from sqlalchemy import func, or_

from ..extensions import db
from ..models import LegacyOccurrenceMapping, ScheduledClass, Section, TermOffering
from ..util import utcnow
from . import occurrences


class MappingError(Exception):
    def __init__(self, error, message, status=409):
        super().__init__(message)
        self.error, self.message, self.status = error, message, status


def active_mapping(occurrence_id):
    """The one active decision for an occurrence, or None."""
    return LegacyOccurrenceMapping.query.filter_by(
        occurrence_id=occurrence_id, superseded_at=None,
    ).first()


def active_occurrences(term_id, start, end, *, professor_id=None, department=None,
                       section_id=None, course_code=None):
    """Active mapped occurrences in range, with their authoritative section.

    Returns ``(ScheduledClass, Section)`` pairs. The offering's term must be
    the requested term; a mapping never moves an occurrence between terms.
    """
    query = db.session.query(ScheduledClass, Section).join(
        LegacyOccurrenceMapping,
        LegacyOccurrenceMapping.occurrence_id == ScheduledClass.id,
    ).join(
        TermOffering, TermOffering.id == LegacyOccurrenceMapping.offering_id,
    ).join(
        Section, Section.id == TermOffering.section_id,
    ).filter(
        LegacyOccurrenceMapping.superseded_at.is_(None),
        ScheduledClass.slot_id.is_(None),
        TermOffering.term_id == term_id,
        ScheduledClass.start_time >= start,
        ScheduledClass.start_time < end,
    )
    if professor_id is not None:
        query = query.filter(ScheduledClass.professor_id == professor_id)
    if department is not None:
        query = query.filter(Section.department == department)
    if section_id is not None:
        query = query.filter(Section.id == section_id)
    if course_code is not None:
        query = query.filter(
            func.lower(ScheduledClass.course_code) == course_code.casefold())
    return query.order_by(ScheduledClass.start_time, ScheduledClass.id).all()


def _active_exists():
    """EXISTS subquery: this ScheduledClass row has an active mapping."""
    return db.session.query(LegacyOccurrenceMapping.id).filter(
        LegacyOccurrenceMapping.occurrence_id == ScheduledClass.id,
        LegacyOccurrenceMapping.superseded_at.is_(None),
    ).exists()


def unmapped_total():
    """Operator-facing backlog size: every slotless row without a mapping."""
    return db.session.query(func.count(ScheduledClass.id)).filter(
        ScheduledClass.slot_id.is_(None),
        ~_active_exists(),
    ).scalar() or 0


def any_unmapped():
    """True while ANY slotless occurrence lacks an active mapping.

    Deliberately range- and department-independent. The HOD refusal must be
    identical for every requested filter set; a per-range answer would itself
    confirm that the range holds legacy rows (an existence side channel).
    """
    return db.session.query(ScheduledClass.id).filter(
        ScheduledClass.slot_id.is_(None),
        ~_active_exists(),
    ).first() is not None


def unmapped_occurrences(start, end, *, professor_id=None, course_code=None, term_id=None):
    """Slotless rows in range with no active mapping, optionally by owner/course.

    A slotless row without a term is in scope for every term whose dates
    cover it, so the term filter keeps those rows and matching terms only.
    """
    query = ScheduledClass.query.filter(
        ScheduledClass.slot_id.is_(None),
        ScheduledClass.start_time >= start,
        ScheduledClass.start_time < end,
        ~_active_exists(),
    )
    if professor_id is not None:
        query = query.filter(ScheduledClass.professor_id == professor_id)
    if term_id is not None:
        query = query.filter(or_(ScheduledClass.term_id == term_id,
                                 ScheduledClass.term_id.is_(None)))
    if course_code is not None:
        query = query.filter(
            func.lower(ScheduledClass.course_code) == course_code.casefold())
    return query.order_by(ScheduledClass.start_time, ScheduledClass.id).all()


def _validated(occurrence_id, offering_id):
    """Lock the occurrence and offering and enforce every mapping invariant."""
    occurrences.lock_writes()
    row = ScheduledClass.query.filter_by(id=occurrence_id).populate_existing().with_for_update().first()
    if row is None:
        raise MappingError("occurrence_not_found", "occurrence not found", 404)
    if row.slot_id is not None:
        raise MappingError(
            "occurrence_not_legacy",
            "occurrence already has a slot; no mapping is needed or allowed",
            409,
        )
    offering = db.session.get(TermOffering, offering_id)
    if offering is None:
        raise MappingError("offering_not_found", "term offering not found", 404)
    if row.term_id is not None and row.term_id != offering.term_id:
        raise MappingError(
            "term_conflict",
            f"occurrence belongs to term {row.term_id} but offering belongs to term "
            f"{offering.term_id}; resolve the term before mapping",
            409,
        )
    return row, offering


def _snapshot(occurrence_id, offering_id, mapped_by, reason):
    row = ScheduledClass.query.filter_by(id=occurrence_id).populate_existing().first()
    return LegacyOccurrenceMapping(
        occurrence_id=occurrence_id,
        offering_id=offering_id,
        occurrence_start_time=row.start_time,
        occurrence_course_code=row.course_code,
        mapped_by=mapped_by,
        reason=reason,
        mapped_at=utcnow(),
    )


def map_occurrence(occurrence_id, offering_id, mapped_by, reason):
    """Record the first authoritative mapping. Caller commits."""
    mapped_by = (mapped_by or "").strip()
    reason = (reason or "").strip()
    if not mapped_by or len(mapped_by) > 128:
        raise MappingError("invalid_mapped_by", "mapped_by is required (max 128 characters)", 400)
    if len(reason) < 3:
        raise MappingError("invalid_reason", "reason must be at least 3 characters", 400)
    row, offering = _validated(occurrence_id, offering_id)
    if active_mapping(row.id) is not None:
        raise MappingError(
            "already_mapped",
            "occurrence already has an active mapping; use supersede",
            409,
        )
    entry = _snapshot(row.id, offering.id, mapped_by, reason)
    db.session.add(entry)
    db.session.flush()
    return entry


def supersede(occurrence_id, offering_id, mapped_by, reason):
    """Replace the active mapping, keeping the old row as history. Caller commits."""
    mapped_by = (mapped_by or "").strip()
    reason = (reason or "").strip()
    if not mapped_by or len(mapped_by) > 128:
        raise MappingError("invalid_mapped_by", "mapped_by is required (max 128 characters)", 400)
    if len(reason) < 3:
        raise MappingError("invalid_reason", "reason must be at least 3 characters", 400)
    row, offering = _validated(occurrence_id, offering_id)
    current = LegacyOccurrenceMapping.query.filter_by(
        occurrence_id=row.id, superseded_at=None,
    ).populate_existing().with_for_update().first()
    if current is None:
        raise MappingError("not_mapped", "occurrence has no active mapping to replace", 404)
    if current.offering_id == offering.id:
        raise MappingError("mapping_unchanged", "occurrence is already mapped to that offering", 409)
    current.superseded_at = utcnow()
    entry = _snapshot(row.id, offering.id, mapped_by, reason)
    db.session.add(entry)
    db.session.flush()
    return entry


def history(occurrence_id):
    return LegacyOccurrenceMapping.query.filter_by(occurrence_id=occurrence_id).order_by(
        LegacyOccurrenceMapping.id).all()


def release_for_deleted_occurrence(occurrence_id):
    """Supersede the active mapping immediately before a deletion (D15).

    Phase 2.5 requires a closure to delete future unstarted occurrences. The
    application releases the mapping in the same transaction so no FK or
    unique-active row can block that deletion; the DB release trigger is the
    out-of-band guard for direct SQL. History is kept, never rewritten.
    """
    entry = LegacyOccurrenceMapping.query.filter_by(
        occurrence_id=occurrence_id, superseded_at=None,
    ).populate_existing().with_for_update().first()
    if entry is not None:
        entry.superseded_at = utcnow()
    return entry
