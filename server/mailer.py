"""Mailcow SMTP sender for invite/password-reset mails (plain smtplib)."""
import asyncio
import logging
import os
import ssl
import smtplib
from email.message import EmailMessage
from email.utils import formataddr
from typing import Optional

log = logging.getLogger(__name__)


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

    ctx = ssl.create_default_context()
    if tls == "ssl":
        server_cls = lambda: smtplib.SMTP_SSL(host, port, context=ctx)  # noqa: E731
    elif tls == "none":
        server_cls = lambda: smtplib.SMTP(host, port)  # noqa: E731
    else:
        server_cls = lambda: smtplib.SMTP(host, port)  # noqa: E731

    with server_cls() as srv:
        if tls == "starttls":
            srv.starttls(context=ctx)
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
