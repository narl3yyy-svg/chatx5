"""Show Android notifications from the embedded Python server."""

from chatx5.utils.platform import is_android


def show_message_notification(title, body, peer_hash=None):
    if not is_android():
        return
    try:
        from java import jclass
        jclass("com.chatx5.android.Chatx5NotificationHelper").show(
            title or "chatx5",
            body or "New message",
            peer_hash or "",
        )
    except Exception as e:
        print(f"[notify] Android notification failed: {e}")