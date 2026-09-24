"""Email content. Plain text first (it is what many security-conscious clients show), with an
HTML alternative. Every user-controlled value is escaped before it goes into HTML."""

from dataclasses import dataclass
from email.message import EmailMessage
from html import escape


@dataclass(frozen=True, slots=True)
class Links:
    """Web app URLs that emails point to. Tokens travel in the query string of an https link;
    the web app's pages for them send no Referer (see apps/web)."""

    frontend_url: str

    def _page(self, path: str) -> str:
        return f"{self.frontend_url.rstrip('/')}{path}"

    def verify_email(self, token: str) -> str:
        return self._page(f"/verify-email?token={token}")

    def reset_password(self, token: str) -> str:
        return self._page(f"/reset-password?token={token}")

    def accept_invitation(self, token: str) -> str:
        return self._page(f"/accept-invitation?token={token}")

    def sign_in(self) -> str:
        return self._page("/login")

    def forgot_password(self) -> str:
        return self._page("/forgot-password")


def _message(*, sender: str, to: str, subject: str, text: str, html: str) -> EmailMessage:
    message = EmailMessage()
    message["From"] = sender
    message["To"] = to
    message["Subject"] = subject
    # RFC 3834: tells auto-responders not to reply to a machine-generated message.
    message["Auto-Submitted"] = "auto-generated"
    message.set_content(text)
    message.add_alternative(html, subtype="html")
    return message


def _html(paragraphs: list[str], *, button: tuple[str, str] | None = None) -> str:
    body = "".join(f"<p>{paragraph}</p>" for paragraph in paragraphs)
    if button:
        label, url = button
        body += f'<p><a href="{escape(url, quote=True)}">{escape(label)}</a></p>'
    return f'<!doctype html><html><body style="font-family:sans-serif">{body}</body></html>'


def verification_email(*, sender: str, to: str, name: str, url: str, valid_hours: int) -> EmailMessage:
    text = (
        f"Hi {name},\n\n"
        "Confirm your email address to finish setting up your ArchitectOS account:\n\n"
        f"{url}\n\n"
        f"The link works once and expires in {valid_hours} hours. "
        "If you did not create an account, ignore this email.\n"
    )
    html = _html(
        [
            f"Hi {escape(name)},",
            "Confirm your email address to finish setting up your ArchitectOS account.",
            f"The link works once and expires in {valid_hours} hours. "
            "If you did not create an account, ignore this email.",
        ],
        button=("Verify email address", url),
    )
    return _message(sender=sender, to=to, subject="Verify your email address", text=text, html=html)


def account_exists_email(
    *, sender: str, to: str, name: str, sign_in_url: str, reset_url: str
) -> EmailMessage:
    text = (
        f"Hi {name},\n\n"
        "Someone tried to create an ArchitectOS account with this email address, "
        "but you already have one. Nothing was changed.\n\n"
        f"Sign in: {sign_in_url}\n"
        f"Forgot your password? {reset_url}\n\n"
        "If this was not you, you can ignore this email.\n"
    )
    html = _html(
        [
            f"Hi {escape(name)},",
            "Someone tried to create an ArchitectOS account with this email address, "
            "but you already have one. Nothing was changed.",
            f'Forgot your password? <a href="{escape(reset_url, quote=True)}">Reset it</a>.',
            "If this was not you, you can ignore this email.",
        ],
        button=("Sign in", sign_in_url),
    )
    return _message(
        sender=sender, to=to, subject="You already have an ArchitectOS account", text=text, html=html
    )


def password_reset_email(*, sender: str, to: str, name: str, url: str, valid_minutes: int) -> EmailMessage:
    text = (
        f"Hi {name},\n\n"
        "Someone asked to reset the password of your ArchitectOS account. To choose a new one:\n\n"
        f"{url}\n\n"
        f"The link works once and expires in {valid_minutes} minutes. Resetting signs you out everywhere.\n"
        "If you did not ask for this, ignore this email; your password stays the same.\n"
    )
    html = _html(
        [
            f"Hi {escape(name)},",
            "Someone asked to reset the password of your ArchitectOS account.",
            f"The link works once and expires in {valid_minutes} minutes. "
            "Resetting signs you out everywhere.",
            "If you did not ask for this, ignore this email; your password stays the same.",
        ],
        button=("Choose a new password", url),
    )
    return _message(sender=sender, to=to, subject="Reset your password", text=text, html=html)


def password_changed_email(*, sender: str, to: str, name: str, reset_url: str) -> EmailMessage:
    text = (
        f"Hi {name},\n\n"
        "The password of your ArchitectOS account was just changed.\n\n"
        "If this was you, there is nothing to do. If it was not, reset your password now and "
        f"review your active sessions: {reset_url}\n"
    )
    html = _html(
        [
            f"Hi {escape(name)},",
            "The password of your ArchitectOS account was just changed.",
            "If this was you, there is nothing to do. If it was not, reset your password now "
            "and review your active sessions.",
        ],
        button=("Reset password", reset_url),
    )
    return _message(sender=sender, to=to, subject="Your password was changed", text=text, html=html)


def invitation_email(
    *, sender: str, to: str, inviter_name: str, organization_name: str, role: str, url: str, valid_days: int
) -> EmailMessage:
    text = (
        f"{inviter_name} invited you to join {organization_name} on ArchitectOS as {role}.\n\n"
        f"Accept the invitation: {url}\n\n"
        f"Sign in (or create an account) with this email address, {to}, to accept. "
        f"The link works once and expires in {valid_days} days. "
        "If you were not expecting it, ignore this email.\n"
    )
    html = _html(
        [
            f"{escape(inviter_name)} invited you to join <strong>{escape(organization_name)}</strong> "
            f"on ArchitectOS as {escape(role)}.",
            f"Sign in (or create an account) with this email address, {escape(to)}, to accept. "
            f"The link works once and expires in {valid_days} days.",
            "If you were not expecting it, ignore this email.",
        ],
        button=("Accept invitation", url),
    )
    return _message(
        sender=sender, to=to, subject=f"Join {organization_name} on ArchitectOS", text=text, html=html
    )
