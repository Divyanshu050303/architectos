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
