"""SQLAlchemy table models. Importing this package registers every table on ``Base.metadata``."""

from .audit_log import AuditLogRecord
from .base import Base
from .email_verification_token import EmailVerificationTokenRecord
from .invitation import InvitationRecord
from .organization import OrganizationRecord
from .organization_member import OrganizationMemberRecord
from .password_reset_token import PasswordResetTokenRecord
from .project import ProjectRecord
from .requirement import RequirementRecord, RequirementVersionRecord
from .requirement_set import RequirementSetItemRecord, RequirementSetRecord
from .session import SessionRecord
from .user import UserRecord

__all__ = [
    "AuditLogRecord",
    "Base",
    "EmailVerificationTokenRecord",
    "InvitationRecord",
    "OrganizationMemberRecord",
    "OrganizationRecord",
    "PasswordResetTokenRecord",
    "ProjectRecord",
    "RequirementRecord",
    "RequirementSetItemRecord",
    "RequirementSetRecord",
    "RequirementVersionRecord",
    "SessionRecord",
    "UserRecord",
]
