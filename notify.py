"""
Backend service for the DoYouEvenGif-alt site.

Responsibilities
----------------
1. Newsletter subscription and unsubscription, stored in subscribers.json.
2. Contact form intake, stored in contacts.json.
3. Transactional email delivery through the Brevo HTTP API.
4. A small notification feed, implemented in notify.py.

Endpoints
---------
    GET  /                              health check
    GET  /notify                        HTML composer for the notification feed
    POST /api/subscribe                 add an address to the newsletter list
    GET  /api/unsubscribe?email=...     remove an address (renders a page)
    POST /api/unsubscribe               remove an address (returns JSON)
    POST /api/contact                   store and forward a contact message
    GET  /api/notifications             full notification feed
    GET  /api/notifications/unread      unread count only
    POST /api/notifications/clear       delete every notification
    POST /api/notifications/add         append one notification

Configuration
-------------
Every secret and deployment-specific value is read from environment
variables. Nothing sensitive is hard-coded in this file. See .env.example for
the complete list of keys.
"""

import json
import logging
import os
import urllib.parse
from datetime import datetime

import requests
from dotenv import load_dotenv
from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS

from notify import (
    add_notification,
    clear_all,
    get_notifications,
    get_unread_count,
)

# ---------------------------------------------------------------------------
# Filesystem paths
# ---------------------------------------------------------------------------

# Absolute path of the directory that contains this file. Deriving paths from
# __file__ rather than os.getcwd() means the service behaves identically no
# matter which directory it is launched from (shell, systemd, gunicorn, IDE).
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Load key=value pairs from a .env file that sits next to this script.
# load_dotenv does not overwrite variables that already exist in the real
# environment, so values injected by the host (systemd, Docker, a PaaS panel)
# always win over the file.
load_dotenv(os.path.join(BASE_DIR, '.env'))

SUBSCRIBERS_FILE = os.path.join(BASE_DIR, 'subscribers.json')
CONTACTS_FILE = os.path.join(BASE_DIR, 'contacts.json')

# The notification feed is owned by notify.py, which resolves the same path on
# its own. It is declared here as well so the storage layout is visible in one
# place when reading this file.
NOTIFICATIONS_FILE = os.path.join(BASE_DIR, 'notifications.json')

# ---------------------------------------------------------------------------
# Flask application and CORS
# ---------------------------------------------------------------------------

app = Flask(__name__)
app.logger.setLevel(logging.INFO)

# The browser only needs to reach this API from the site itself. Restricting
# the origin keeps other pages from calling these endpoints with the visitor's
# credentials. Override with ALLOWED_ORIGIN when running a staging deploy.
ALLOWED_ORIGIN = os.environ.get(
    'ALLOWED_ORIGIN',
    'https://example.com',
)

CORS(
    app,
    origins=[ALLOWED_ORIGIN],
    methods=['GET', 'POST', 'OPTIONS'],
    allow_headers=['Content-Type', 'Authorization'],
    # No cookies or Authorization-based sessions are used, so credentials are
    # deliberately left off. Turning this on would require an exact origin and
    # would widen the attack surface for no benefit.
    supports_credentials=False,
    # Cache the preflight result for 24 hours to avoid an OPTIONS round trip on
    # every request.
    max_age=86400,
)

# ---------------------------------------------------------------------------
# Brevo configuration
# ---------------------------------------------------------------------------
# BREVO_API_KEY is read from the environment only. If it is missing, the
# endpoints still work: submissions are stored on disk and email delivery is
# skipped with an error written to the log. That keeps the site usable while
# credentials are being rotated or a mail outage is in progress.

BREVO_API_KEY = os.environ.get('BREVO_API_KEY')

# Must be an address that has been verified as a sender in the Brevo account.
# Brevo rejects the request otherwise.
BREVO_SENDER_EMAIL = os.environ.get('BREVO_SENDER_EMAIL')
BREVO_SENDER_NAME = os.environ.get('BREVO_SENDER_NAME', 'DoYouEvenGif-alt')

CONTACT_RECIPIENT = os.environ.get(
    'CONTACT_RECIPIENT',
    'example@domain.xxx',
)
NEWSLETTER_RECIPIENT = os.environ.get(
    'NEWSLETTER_RECIPIENT',
    'example@domain.xxx',
)

# Public base URL used to build absolute links inside outgoing email. The
# unsubscribe link has to be absolute because it is opened from a mail client,
# not from the site.
PUBLIC_BASE_URL = os.environ.get(
    'PUBLIC_BASE_URL',
    'https://example.com',
)

BREVO_API_URL = 'https://api.brevo.com/v3/smtp/email'

# ---------------------------------------------------------------------------
# JSON storage helpers
# ---------------------------------------------------------------------------


def load_json(filepath, default=None):
    """Read a JSON file and return its contents.

    Returns `default` (an empty list unless stated otherwise) when the file
    does not exist, cannot be read, or does not contain valid JSON. A
    truncated or corrupt data file must not take an endpoint down, so every
    failure mode degrades to "no data" rather than raising.
    """
    if default is None:
        default = []

    if not os.path.exists(filepath):
        return default

    try:
        with open(filepath, 'r', encoding='utf-8') as handle:
            return json.load(handle)
    except (json.JSONDecodeError, OSError):
        return default


def save_json(filepath, data):
    """Write `data` to `filepath` as UTF-8 encoded JSON.

    The write is not atomic. Two requests arriving at the same moment can
    interleave and one of them can lose its change. That is acceptable at the
    traffic level this service handles. If that assumption ever changes,
    write to a temporary file in the same directory and move it into place
    with os.replace(), which is atomic on POSIX filesystems.
    """
    with open(filepath, 'w', encoding='utf-8') as handle:
        json.dump(data, handle, indent=2, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Email delivery
# ---------------------------------------------------------------------------


def send_email(recipient, subject, body_plain, body_html=None):
    """Send one transactional email through the Brevo HTTP API.

    Returns True when Brevo accepted the message, False for every other
    outcome. Network errors, timeouts and non-2xx responses are logged and
    swallowed: a mail failure must not turn an otherwise successful
    subscription or contact submission into a 500 response.

    `body_plain` is always sent. `body_html` is optional; when omitted Brevo
    delivers the plain text version only.
    """
    if not BREVO_API_KEY:
        app.logger.error('BREVO_API_KEY is not set; email was not sent.')
        return False

    if not BREVO_SENDER_EMAIL:
        app.logger.error('BREVO_SENDER_EMAIL is not set; email was not sent.')
        return False

    payload = {
        'sender': {
            'name': BREVO_SENDER_NAME,
            'email': BREVO_SENDER_EMAIL,
        },
        'to': [{'email': recipient}],
        'subject': subject,
        'textContent': body_plain,
    }

    if body_html:
        payload['htmlContent'] = body_html

    try:
        response = requests.post(
            BREVO_API_URL,
            json=payload,
            headers={
                'api-key': BREVO_API_KEY,
                'Content-Type': 'application/json',
                'Accept': 'application/json',
            },
            # Bounded timeout so a slow Brevo response cannot hold a worker
            # open indefinitely.
            timeout=15,
        )
    except requests.RequestException as exc:
        app.logger.error('Brevo request failed: %s', exc)
        return False

    if not response.ok:
        app.logger.error(
            'Brevo rejected the request: %s %s',
            response.status_code,
            response.text,
        )
        return False

    return True


# ---------------------------------------------------------------------------
# Email templates
# ---------------------------------------------------------------------------


def build_welcome_html(email):
    """Return the HTML body of the subscription confirmation email.

    `email` is URL-quoted before it is placed in the unsubscribe link so that
    characters such as '+' and '@' survive the query string intact. It is not
    interpolated anywhere else, so no further escaping is required here.
    """
    unsubscribe_url = '{base}/api/unsubscribe?email={email}'.format(
        base=PUBLIC_BASE_URL,
        email=urllib.parse.quote(email),
    )

    return f"""<!DOCTYPE html>
<html>
<head><meta charset="UTF-8"><title>Welcome</title></head>
<body style="margin:0;padding:0;background:#17121b;font-family:Georgia,serif;">
  <table width="100%" style="background:#17121b;padding:28px 12px;">
    <tr><td align="center">
      <table width="600" style="max-width:600px;background:#2a2030;border-radius:30px;border:2px solid #6d536f;overflow:hidden;">
        <tr><td align="center" style="padding:34px 28px 26px; background:#35263b; border-bottom:1px solid #725676;">
          <div style="font-size:46px;">&#127824;</div>
          <h1 style="color:#fff5f7; margin:0 0 7px;">DoYouEvenGif-alt</h1>
          <p style="color:#e9b9d3; letter-spacing:0.14em; margin:0;">&#9825; &mdash; Alternative &mdash; &#9825;</p>
        </td></tr>
        <tr><td align="center" style="padding:30px 34px 28px;">
          <div style="display:inline-block;background:#443047;border:1px solid #80617e;border-radius:999px;padding:8px 15px;color:#f7d7e6;">&#10022; YOU'RE IN &#10022;</div>
          <p style="color:#fff1f5;font-size:19px;line-height:1.7;">you clicked the button.</p>
          <p style="color:#fff1f5;font-size:19px;line-height:1.7;">it's too late now. &#9825;</p>
          <p style="color:#fff1f5;font-size:18px;line-height:1.7;">you're officially subscribed to <strong>DoYouEvenGif-alt</strong>.</p>
          <p style="color:#f2bdd5;font-size:30px;line-height:1.3;">welcome &#127824;</p>
        </td></tr>
        <tr><td align="center" style="padding:22px 24px 28px; border-top:1px solid #443448; background:#251d2a;">
          <p style="color:#8f7b89;font-size:11px;letter-spacing:0.04em;margin:0;">DoYouEvenGif-alt &middot; a weird little corner of the internet</p>
          <p style="margin:13px 0 0 0;">
            <a href="{unsubscribe_url}" style="display:inline-block;color:#f4c5d9;text-decoration:none;border:1px solid #75566d;background:#332536;border-radius:999px;padding:8px 14px;">&#127824; unsubscribe anytime &#127824;</a>
          </p>
        </td></tr>
      </table>
    </td></tr>
  </table>
</body>
</html>"""


def build_unsubscribe_html(email):
    """Return the HTML body of the unsubscribe confirmation email.

    `email` is accepted so that both template builders share one signature and
    the caller does not need a special case. The current template does not
    reference it.
    """
    return """<!DOCTYPE html>
<html>
<head><meta charset="UTF-8"><title>Unsubscribed</title></head>
<body style="margin:0;padding:0;background:#17121b;font-family:Georgia,serif;">
  <table width="100%" style="background:#17121b;padding:28px 12px;">
    <tr><td align="center">
      <table width="600" style="max-width:600px;background:#2a2030;border-radius:30px;border:2px solid #6d536f;overflow:hidden;">
        <tr><td align="center" style="padding:34px 28px 26px; background:#35263b; border-bottom:1px solid #725676;">
          <div style="font-size:48px;">&#127824;</div>
          <h1 style="color:#fff5f7; margin:0 0 7px;">DoYouEvenGif-alt</h1>
          <p style="color:#e9b9d3; letter-spacing:0.12em; margin:0;">&mdash; you're out &mdash;</p>
        </td></tr>
        <tr><td align="center" style="padding:31px 34px 34px;">
          <p style="color:#fff1f5;font-size:19px;line-height:1.7;">you've been unsubscribed from the DoYouEvenGif-alt newsletter.</p>
          <p style="color:#f2bdd5;font-size:25px;line-height:1.3;">&#9996;&#65039; &#9825;</p>
        </td></tr>
      </table>
    </td></tr>
  </table>
</body>
</html>"""


# ---------------------------------------------------------------------------
# Request parsing helpers
# ---------------------------------------------------------------------------


def _extract_email_and_message():
    """Return (email, message) from the current request.

    Both form-encoded and JSON bodies are accepted, because the browser form
    posts form data while the JavaScript clients post JSON. Email is lowercased
    and stripped so that 'User@Example.com ' and 'user@example.com' are treated
    as the same subscriber.

    Either value may come back as an empty string when it was not supplied.
    """
    email = (request.form.get('email') or '').strip().lower()
    message = (request.form.get('message') or '').strip()

    # Only fall back to the JSON body when the form did not supply the value,
    # so a mixed request cannot have one field silently overwritten.
    if not email or not message:
        data = request.get_json(silent=True) or {}
        email = email or (data.get('email') or '').strip().lower()
        message = message or (data.get('message') or '').strip()

    return email, message


# ---------------------------------------------------------------------------
# General routes
# ---------------------------------------------------------------------------


@app.route('/')
def index():
    """Health check. Confirms the process is up and lists the main routes."""
    return jsonify({
        'status': 'online',
        'message': (
            'DoYouEvenGif-alt API is running. '
            'Use /api/subscribe, /api/contact, /api/unsubscribe'
        ),
    })


@app.route('/notify')
def notify_composer():
    """Serve the standalone notification composer page."""
    return send_from_directory(BASE_DIR, 'notify.html')


# ---------------------------------------------------------------------------
# Newsletter routes
# ---------------------------------------------------------------------------


@app.route('/api/subscribe', methods=['POST'])
def subscribe():
    """Add an address to the newsletter list.

    Accepts 'email' in either a form body or a JSON body. On success the
    address is appended to subscribers.json, a welcome email is sent to the
    subscriber, and a heads-up email is sent to NEWSLETTER_RECIPIENT.

    Returns 400 when the address is missing or malformed, and 400 when it is
    already on the list.
    """
    email = (request.form.get('email') or '').strip().lower()

    if not email:
        data = request.get_json(silent=True) or {}
        email = (data.get('email') or '').strip().lower()

    # '@' is the only structural check performed. Full address validation
    # belongs to the mail provider, and an over-strict regex here would reject
    # legitimate addresses.
    if not email or '@' not in email:
        return jsonify({
            'success': False,
            'message': 'Invalid email.',
        }), 400

    subscribers = load_json(SUBSCRIBERS_FILE)

    if email in subscribers:
        return jsonify({
            'success': False,
            'message': 'Already subscribed.',
        }), 400

    subscribers.append(email)
    save_json(SUBSCRIBERS_FILE, subscribers)

    # Both sends are attempted independently. A failure on either one leaves
    # the subscription in place; the address is on disk regardless.
    welcome_ok = False
    notify_ok = False

    if BREVO_API_KEY:
        welcome_ok = send_email(
            email,
            'welcome to DoYouEvenGif-alt \U0001F350',
            "you clicked the button.\n\nit's too late now.",
            build_welcome_html(email),
        )
        notify_ok = send_email(
            NEWSLETTER_RECIPIENT,
            '\U0001F350 New subscriber: {email}'.format(email=email),
            '{email} just subscribed.'.format(email=email),
            '<p>{email} joined the newsletter.</p>'.format(email=email),
        )

    return jsonify({
        'success': True,
        'message': 'Subscribed successfully!',
        'email_sent': welcome_ok and notify_ok,
    })


@app.route('/api/unsubscribe', methods=['GET', 'POST'])
def unsubscribe():
    """Remove an address from the newsletter list.

    GET  /api/unsubscribe?email=...  renders a confirmation page, which is the
                                     form used by the link inside emails.
    POST /api/unsubscribe            returns JSON.

    Returns 400 when no address is supplied and 404 when the address is not on
    the list. A confirmation email is sent on success, but a delivery failure
    does not change the response, because the address has already been removed.
    """
    if request.method == 'GET':
        email = (request.args.get('email') or '').strip().lower()
    else:
        email = (request.form.get('email') or '').strip().lower()

        if not email:
            data = request.get_json(silent=True) or {}
            email = (data.get('email') or '').strip().lower()

    if not email:
        return jsonify({
            'success': False,
            'message': 'Email required.',
        }), 400

    subscribers = load_json(SUBSCRIBERS_FILE)

    if email not in subscribers:
        return jsonify({
            'success': False,
            'message': 'Email not found in subscribers.',
        }), 404

    subscribers.remove(email)
    save_json(SUBSCRIBERS_FILE, subscribers)

    if BREVO_API_KEY:
        send_email(
            email,
            "you're out of DoYouEvenGif-alt",
            "you've been unsubscribed.",
            build_unsubscribe_html(email),
        )

    if request.method == 'GET':
        return """
        <html><body style="background:#0b0a0c;color:#f0ebe3;font-family:Georgia,serif;text-align:center;padding:60px 20px;">
          <div style="max-width:500px;margin:0 auto;background:rgba(255,255,255,0.03);border-radius:24px;padding:40px;border:1px solid rgba(255,215,150,0.1);">
            <div style="font-size:48px;">&#127824;</div>
            <h2 style="color:#f0d5a0;">you're out.</h2>
            <p style="color:#cbc4bc;font-size:18px;line-height:1.7;">you've been unsubscribed. no hard feelings.</p>
            <p style="margin-top:30px;"><a href="/" style="color:#f0d5a0;text-decoration:none;">&larr; back to home</a></p>
          </div>
        </body></html>
        """

    return jsonify({
        'success': True,
        'message': 'Unsubscribed successfully.',
    })


# ---------------------------------------------------------------------------
# Contact route
# ---------------------------------------------------------------------------


@app.route('/api/contact', methods=['POST'])
def contact():
    """Store a contact form submission and forward it by email.

    The submission is written to contacts.json before any email is attempted,
    so a mail outage never loses a message. The response reports whether the
    email actually went out through the 'email_sent' field.

    Returns 400 for a malformed address or an empty message.
    """
    email, message = _extract_email_and_message()

    if not email or '@' not in email:
        return jsonify({
            'success': False,
            'message': 'Invalid email.',
        }), 400

    if not message:
        return jsonify({
            'success': False,
            'message': 'Message cannot be empty.',
        }), 400

    contacts = load_json(CONTACTS_FILE)
    contacts.append({
        'email': email,
        'message': message,
        'timestamp': str(datetime.now()),
    })
    save_json(CONTACTS_FILE, contacts)

    sent = False

    if BREVO_API_KEY:
        sent = send_email(
            CONTACT_RECIPIENT,
            '\u2709\ufe0f Contact from {email}'.format(email=email),
            'From: {email}\n\n{message}'.format(email=email, message=message),
            '<p>From: {email}</p><p>{message}</p>'.format(
                email=email,
                message=message,
            ),
        )

    return jsonify({
        'success': True,
        'message': 'Message sent!' if sent else 'Saved, but email failed.',
        'email_sent': sent,
    })


# ---------------------------------------------------------------------------
# Notification routes
# ---------------------------------------------------------------------------
# These wrap the functions in notify.py, which owns the file format and the
# default notification shape. The routes here only handle HTTP concerns.


@app.route('/api/notifications', methods=['GET'])
def notifications_api():
    """Return the full notification feed, newest last."""
    return jsonify(get_notifications())


@app.route('/api/notifications/unread', methods=['GET'])
def unread_count_api():
    """Return the number of notifications that have not been read."""
    return jsonify({'count': get_unread_count()})


@app.route('/api/notifications/clear', methods=['POST'])
def clear_api():
    """Delete every notification."""
    clear_all()
    return jsonify({'success': True})


@app.route('/api/notifications/add', methods=['POST'])
def add_notification_api():
    """Append one notification.

    Accepts 'message' in either a form body or a JSON body. Returns 400 when
    the message is empty. On success the newly created notification is
    returned so the caller can render it without refetching the feed.
    """
    message = (request.form.get('message') or '').strip()

    if not message:
        data = request.get_json(silent=True) or {}
        message = (data.get('message') or '').strip()

    if not message:
        return jsonify({
            'success': False,
            'error': 'Message cannot be empty.',
        }), 400

    notifications = add_notification(message)

    return jsonify({
        'success': True,
        'notification': notifications[-1],
    })


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    # debug=False on purpose. The Flask debugger exposes an interactive Python
    # console to anyone who can reach the port, so it must stay off outside of
    # a local, isolated environment.
    app.run(host='0.0.0.0', port=5000, debug=False)
