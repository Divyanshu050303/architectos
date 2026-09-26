"""SQLAlchemy table models. Importing this package registers every table on ``Base.metadata``."""

from .architecture import ArchitectureLayoutRecord, ArchitectureRecord, ArchitectureRevisionRecord
from .audit_log import AuditLogRecord
from .base import Base
from .capacity import CapacityAnalysisRecord, CapacityBottleneckRecord, CapacityComponentRecord
from .email_verification_token import EmailVerificationTokenRecord
from .invitation import InvitationRecord
from .organization import OrganizationRecord
from .organization_member import OrganizationMemberRecord
from .password_reset_token import PasswordResetTokenRecord
from .pricing import PricingRecordRow, PricingSnapshotRecord
from .project import ProjectRecord
from .requirement import RequirementRecord, RequirementVersionRecord
from .requirement_analysis import RequirementAnalysisRecord
from .requirement_set import RequirementSetItemRecord, RequirementSetRecord
from .session import SessionRecord
from .user import UserRecord
from .validation import ValidationFindingRecord, ValidationRunRecord

__all__ = [
    "ArchitectureLayoutRecord",
    "ArchitectureRecord",
    "ArchitectureRevisionRecord",
    "AuditLogRecord",
    "Base",
    "CapacityAnalysisRecord",
    "CapacityBottleneckRecord",
    "CapacityComponentRecord",
    "EmailVerificationTokenRecord",
    "InvitationRecord",
    "OrganizationMemberRecord",
    "OrganizationRecord",
    "PasswordResetTokenRecord",
    "PricingRecordRow",
    "PricingSnapshotRecord",
    "ProjectRecord",
    "RequirementAnalysisRecord",
    "RequirementRecord",
    "RequirementSetItemRecord",
    "RequirementSetRecord",
    "RequirementVersionRecord",
    "SessionRecord",
    "UserRecord",
    "ValidationFindingRecord",
    "ValidationRunRecord",
]
