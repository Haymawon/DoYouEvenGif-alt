"""
Notification feed storage.

The feed is a flat JSON array in notifications.json, next to this file. Each
entry has the following shape:

    {
        "id":        1,                       # integer, unique, never reused
        "message":   "text",                  # the notification body
        "timestamp": "2024-01-01 12:00:00",   # local time, formatted string
        "read":      false,                   # unread flag
        "avatar":    "/favicon.png"           # image shown next to the entry
    }

The avatar path is read from the AVATAR_PATH environment variable, so no
site-specific filename is committed. A generic default is used when unset.

Writes are not atomic. Two processes writing at the same moment can lose one
of the two updates. That is acceptable for the traffic this service handles.
If that changes, write to a temporary file and move it into place with
os.replace(), which is atomic on POSIX filesystems.

This module can also be run directly to append a notification from the shell:

    python notify.py "Your notification message"
"""

import json
import os
import sys
from datetime import datetime

# Absolute path of the directory containing this file, so the data file is
# found regardless of the working directory the process was started from.
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
NOTIFICATIONS_FILE = os.path.join(BASE_DIR, 'notifications.json')

# Path served as the avatar image. Relative by design so no hostname is
# embedded in the data file.
AVATAR_PATH = os.environ.get('AVATAR_PATH', '').strip() or '/favicon.png'


def get_notifications():
    """Return the notification feed as a list.

    Returns an empty list when the file is missing, unreadable, or does not
    contain valid JSON. A corrupt data file must not break the endpoints that
    depend on this module, so every failure degrades to an empty feed.
    """
    if not os.path.exists(NOTIFICATIONS_FILE):
        return []

    try:
        with open(NOTIFICATIONS_FILE, 'r', encoding='utf-8') as handle:
            return json.load(handle)
    except (json.JSONDecodeError, OSError):
        return []


def _write_notifications(notifications):
    """Persist the feed to disk, overwriting the previous contents."""
    with open(NOTIFICATIONS_FILE, 'w', encoding='utf-8') as handle:
        json.dump(notifications, handle, indent=2, ensure_ascii=False)


def get_unread_count():
    """Return how many entries in the feed have 'read' set to false.

    An entry with no 'read' key at all counts as unread, which keeps older
    entries written before the flag existed working correctly.
    """
    return sum(
        1 for notification in get_notifications()
        if not notification.get('read', False)
    )


def add_notification(message):
    """Append a notification and return the updated feed.

    The new id is one higher than the highest id currently in the feed. It is
    not derived from the list length, because clearing the feed would then
    cause ids to be reused.
    """
    notifications = get_notifications()

    new_id = max(
        (notification.get('id', 0) for notification in notifications),
        default=0,
    ) + 1

    notifications.append({
        'id': new_id,
        'message': message,
        'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'read': False,
        'avatar': AVATAR_PATH,
    })

    _write_notifications(notifications)

    return notifications


def mark_as_read(notification_id):
    """Mark one notification as read and return the updated feed.

    Does nothing if no entry has the given id. The feed is still written back
    in that case, which is harmless and keeps the function's behaviour
    consistent for callers.
    """
    notifications = get_notifications()

    for notification in notifications:
        if notification.get('id') == notification_id:
            notification['read'] = True
            break

    _write_notifications(notifications)

    return notifications


def clear_all():
    """Delete every notification and return the now-empty feed."""
    _write_notifications([])
    return []


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print('Usage: python notify.py "Your notification message"')
        sys.exit(1)

    # Everything after the script name is joined into a single message, so the
    # caller does not have to quote the text as one shell argument.
    message = ' '.join(sys.argv[1:])

    result = add_notification(message)

    print('Notification added. Total: {count}'.format(count=len(result)))
