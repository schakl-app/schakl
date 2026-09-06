"""The org-level policy a mailbox feed runs under — one vocabulary for every provider."""

from __future__ import annotations

from enum import StrEnum


class MailApprovalMode(StrEnum):
    """Whether a matched email needs the mailbox owner's approval before it is logged."""

    APPROVAL_REQUIRED = "approval_required"
    AUTO_APPROVE = "auto_approve"


class MailThreadFollowup(StrEnum):
    """What a follow-up in an already-mapped thread does: inherit mappings, or also auto-log."""

    INHERIT_PENDING = "inherit_pending"
    INHERIT_APPROVE = "inherit_approve"


def decide_status(approval_mode: str, thread_followup: str, *, inherited: bool) -> bool:
    """``True`` = pending (owner approval required before content is shared)."""
    if approval_mode == MailApprovalMode.AUTO_APPROVE.value:
        return False
    if inherited and thread_followup == MailThreadFollowup.INHERIT_APPROVE.value:
        return False
    return True
