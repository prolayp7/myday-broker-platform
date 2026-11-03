"""Reads the shared mailbox and sends email. Gmail for now; Outlook replaces
this file only. Named mail, not mailbox, which is a Python standard module."""

import email
import imaplib
import os
import re
import smtplib
import time
from datetime import datetime, timezone
from email import policy
from email.message import EmailMessage
from email.utils import parseaddr

_IMAP_HOST, _SMTP_HOST = "imap.gmail.com", "smtp.gmail.com"
_TIMEOUT_SECONDS = 60


def authenticated(message: EmailMessage, sender: str) -> bool:
    """Did the receiving server confirm the sender, not just the From line?"""
    domain = sender.rpartition("@")[2]
    for header in message.get_all("Authentication-Results") or []:
        text = str(header).lower()
        if not text.lstrip().startswith("mx.google.com"):
            continue
        if "dmarc=pass" in text:
            return True
        if re.search(rf"dkim=pass[^;]*header\.i=[^;\s]*@{re.escape(domain)}\b", text):
            return True
    return False


def first_name(display_name: str, address: str) -> str:
    """For a greeting: 'Eldho George', or else eldho.george@..., or Broker."""
    name = display_name.strip()
    if "," in name:
        name = name.split(",", 1)[1].strip()
    words = re.findall(r"[^\W\d_]+", name) or re.findall(r"[^\W\d_]+", address.partition("@")[0])
    word = words[0] if words else ""
    return word.capitalize() if len(word) > 1 else "Broker"


def parse(raw: bytes, occurred_at: datetime) -> dict:
    message = email.message_from_bytes(raw, policy=policy.default)
    display_name, address = parseaddr(str(message.get("From", "")))
    sender = address.lower()
    pdfs = []
    for part in message.iter_attachments():
        name = part.get_filename() or "attachment.pdf"
        if part.get_content_type() == "application/pdf" or name.lower().endswith(".pdf"):
            pdfs.append((name, part.get_content()))
    return {
        "message_id": str(message.get("Message-ID", "")).strip(),
        "thread_id": str(message.get("In-Reply-To", "")).strip() or None,
        "from_address": sender,
        "first_name": first_name(display_name, sender),
        "subject": str(message.get("Subject", "")),
        "occurred_at": occurred_at,
        "authenticated": authenticated(message, sender),
        "pdfs": pdfs,
    }


def _when(fetch_line: bytes) -> datetime:
    return datetime.fromtimestamp(
        time.mktime(imaplib.Internaldate2tuple(fetch_line)), timezone.utc
    )


def new_messages(since: datetime, finished) -> list[dict]:
    """Mail from `since` on that `finished(ids)` does not already cover.

    Read-only and by PEEK, so the mailbox looks untouched afterwards.
    """
    imap = imaplib.IMAP4_SSL(_IMAP_HOST, timeout=_TIMEOUT_SECONDS)
    try:
        imap.login(os.environ["REVIEW_MAILBOX_USER"], os.environ["REVIEW_MAILBOX_PASSWORD"])
        imap.select("INBOX", readonly=True)
        _, found = imap.uid("search", None, f"SINCE {since:%d-%b-%Y}")
        uids = found[0].split()
        if not uids:
            return []
        _, rows = imap.uid(
            "fetch", b",".join(uids).decode(),
            "(INTERNALDATE BODY.PEEK[HEADER.FIELDS (MESSAGE-ID)])",
        )
        headers = {}
        for row in rows:
            if not isinstance(row, tuple):
                continue
            occurred = _when(row[0])
            uid = re.search(rb"UID (\d+)", row[0]).group(1).decode()
            message_id = email.message_from_bytes(row[1]).get("Message-ID", "").strip()
            if occurred >= since and message_id:
                headers[message_id] = (uid, occurred)
        done = finished(list(headers))
        messages = []
        for message_id, (uid, occurred) in headers.items():
            if message_id not in done:
                _, body = imap.uid("fetch", uid, "(BODY.PEEK[])")
                messages.append(parse(body[0][1], occurred))
        return messages
    finally:
        try:
            imap.logout()
        except (imaplib.IMAP4.error, OSError):
            pass


def send(to_address: str, subject: str, body: str) -> None:
    user = os.environ["REVIEW_MAILBOX_USER"]
    message = EmailMessage()
    message["From"], message["To"], message["Subject"] = user, to_address, subject
    message.set_content(body)
    with smtplib.SMTP_SSL(_SMTP_HOST, 465, timeout=_TIMEOUT_SECONDS) as smtp:
        smtp.login(user, os.environ["REVIEW_MAILBOX_PASSWORD"])
        smtp.send_message(message)
