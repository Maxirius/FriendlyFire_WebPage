"""Mailcow SMTP sender for invite/password-reset mails (plain smtplib)."""
import asyncio
import hashlib
import logging
import os
import ssl
import smtplib
from email.message import EmailMessage
from email.utils import formataddr
from typing import Optional

log = logging.getLogger(__name__)

# Mailcow's postfix presents a self-signed cert (its ACME renewal has been
# expired since June 2025). Instead of disabling verification entirely we
# PIN the presented certificate's SHA-256 fingerprint; rotate this value
# after Mailcow obtains a proper LE cert again (override via env).
_EXPECTED_SMTP_CERT_SHA256 = os.environ.get(
    "FF_SMTP_CERT_PIN",
    "97fc7211a7c33eb581083f7e43f739bce697996a71bbeac0615e25c10a50dc33",
)


def _pinned_ssl_context() -> ssl.SSLContext:
    """Require TLS with SOME cert; the fingerprint check happens after the
    handshake in send_sync (CA chains and hostname checks are meaningless
    against a self-signed cert)."""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def _pin_matches(peer_der: bytes) -> bool:
    return (hashlib.sha256(peer_der).hexdigest().lower()
            == _EXPECTED_SMTP_CERT_SHA256.lower())



def _smtp_conf_from_env():
    host = os.environ.get("FF_SMTP_HOST", "")
    port = int(os.environ.get("FF_SMTP_PORT", "587"))
    user = os.environ.get("FF_SMTP_USER", "")
    pwd = os.environ.get("FF_SMTP_PASS", "")
    from_addr = os.environ.get("FF_MAIL_FROM", "")
    tls = os.environ.get("FF_SMTP_TLS", "starttls").lower()
    return host, port, user, pwd, from_addr, tls


def mail_configured() -> bool:
    host, _, _, _, from_addr, _ = _smtp_conf_from_env()
    return bool(host and from_addr)


BASE_HTML = """\
<div style="font-family:'Segoe UI',Arial,sans-serif;max-width:520px;
            margin:0 auto;border:1px solid #26303a;">
  <div style="background:#171d23;padding:18px 24px;
              font-family:monospace;font-size:13px;color:#e9eee2;">
    FRIENDLY<b style="color:#77cf52">FIRE</b> · Minecraft-Community
  </div>
  <div style="padding:26px 24px;color:#1c2228;line-height:1.6">
    {body}
    <hr style="border:none;border-top:1px solid #dde3dd;margin:22px 0 12px"/>
    <p style="font-size:11px;color:#8a938a;margin:0">
      Wenn du dieses Mail nicht erwartet hast, ignoriere es einfach —
      der Link läuft automatisch ab ({ttl}).
    </p>
  </div>
</div>
"""

INVITE_BODY = """\
<h2 style="margin:0 0 14px">Du bist eingeladen!</h2>
<p>Hallo,</p>
<p><b>{creator}</b> hat dich zur FriendyFire-Minecraft-Community eingeladen.
Über den folgenden Link kannst du deinen Account anlegen:</p>
<p style="margin:22px 0"><a href="{url}"
   style="background:#77cf52;color:#10230c;padding:12px 20px;
          text-decoration:none;font-weight:bold">{url}</a></p>
<p>Nach der Registrierung trägst du deinen <b>Minecraft-Nicknamen</b> ein —
damit landest du automatisch auf der Server-Whitelist.</p>
<p>Der Link ist gültig bis <b>{expiry}</b>.</p>
"""

RESET_BODY = """\
<h2 style="margin:0 0 14px">Passwort zurücksetzen</h2>
<p>Hallo,</p>
<p>für deinen Account wurde ein Passwort-Reset angefordert:</p>
<p style="margin:22px 0"><a href="{url}"
   style="background:#77cf52;color:#10230c;padding:12px 20px;
          text-decoration:none;font-weight:bold">{url}</a></p>
<p>Gültig bis <b>{expiry}</b>. Wenn du das nicht warst, ändere nichts
— dein bisheriges Passwort bleibt unverändert weiter gültig.</p>
"""


def render_invite(base_url: str, creator: str, token_raw: str,
                  ttl_hours: float) -> str:
    expiry = _fmt_ttl(ttl_hours)
    body = INVITE_BODY.format(
        creator=_esc(creator), url=f"{base_url}/invite/{token_raw}",
        expiry=expiry, ttl=expiry,
    )
    return BASE_HTML.format(body=body, ttl=expiry)


def render_reset(base_url: str, token_raw: str, ttl_minutes: float) -> str:
    expiry = _fmt_ttl(ttl_minutes / 60.0)
    body = RESET_BODY.format(
        url=f"{base_url}/reset/{token_raw}", expiry=expiry, ttl=expiry,
    )
    return BASE_HTML.format(body=body, ttl=expiry)


def _fmt_ttl(hours: float) -> str:
    if hours >= 1:
        return f"{hours:g} h".replace(".0 ", " ")
    return f"{round(hours * 60)} Min."


def _esc(s: str) -> str:
    return (s.replace("&", "&amp;").replace("<", "&lt;")
             .replace(">", "&gt;").replace('"', "&quot;"))


def send_sync(to: str, subject: str, html: str) -> None:
    host, port, user, pwd, from_addr, tls = _smtp_conf_from_env()
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = formataddr(("FriendlyFire Minecraft", from_addr))
    msg["To"] = to
    msg.set_content(_strip_html(html))
    msg.add_alternative(html, subtype="html")

    ctx = _pinned_ssl_context()
    if tls == "ssl":
        server_cls = lambda: smtplib.SMTP_SSL(host, port, context=ctx)  # noqa: E731
    elif tls == "none":
        server_cls = lambda: smtplib.SMTP(host, port)  # noqa: E731
    else:
        server_cls = lambda: smtplib.SMTP(host, port)  # noqa: E731

    with server_cls() as srv:
        if tls == "starttls":
            srv.starttls(context=ctx)
            peer = srv.sock.getpeercert(binary_form=True)  # SSL socket now
            if peer is None:
                raise RuntimeError("SMTP offered no TLS certificate")
            if not _pin_matches(peer):
                raise RuntimeError(
                    "SMTP cert fingerprint mismatch (MITM or rotated cert;"
                    " update FF_SMTP_CERT_PIN)")
        if user:
            srv.login(user, pwd)
        srv.send_message(msg)


async def send_async(to: str, subject: str, html: str) -> None:
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, send_sync, to, subject, html)


def _strip_html(html: str) -> str:
    import re
    txt = re.sub(r"<[^>]+>", " ", html)
    return re.sub(r"\s+", " ", txt).strip()
