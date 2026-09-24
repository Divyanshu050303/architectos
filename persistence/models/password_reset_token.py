from sqlalchemy import CheckConstraint, Index, text

from .single_use_token import SingleUseTokenRecord


class PasswordResetTokenRecord(SingleUseTokenRecord):
    __tablename__ = "password_reset_tokens"

    __table_args__ = (
        # Outstanding tokens of a user, for revoking them when a new one is issued.
        Index(
            "ix_password_reset_tokens_user_id_outstanding",
            "user_id",
            postgresql_where=text("consumed_at IS NULL AND revoked_at IS NULL"),
        ),
        CheckConstraint("octet_length(token_hash) = 32", name="token_hash_length"),
        CheckConstraint("consumed_at IS NULL OR revoked_at IS NULL", name="single_outcome"),
    )
