"""A local stand-in for Gmail's SMTP server, for the failure/recovery tests.

Speaks SMTP with STARTTLS and AUTH LOGIN/PLAIN (any user and password are
accepted), like smtp.gmail.com:587, and appends every message it receives to
an inbox file. Started and stopped by failure_recovery_tests.py, so a test
can take the mail server away and bring it back.

    python fake_smtp.py PORT INBOX_FILE CERT_FILE KEY_FILE
Needs: pip install aiosmtpd
"""

import ssl
import sys
import time
from pathlib import Path

from aiosmtpd.controller import Controller
from aiosmtpd.smtp import AuthResult, LoginPassword


class Inbox:
    def __init__(self, path: Path):
        self.path = path

    async def handle_DATA(self, server, session, envelope):
        with self.path.open("a", encoding="utf-8") as inbox:
            inbox.write(f"=== {time.strftime('%H:%M:%S')} to {','.join(envelope.rcpt_tos)}\n")
            inbox.write(envelope.content.decode("utf-8", errors="replace") + "\n")
        return "250 OK"


def accept_any(server, session, envelope, mechanism, auth_data):
    return AuthResult(success=isinstance(auth_data, LoginPassword))


def main() -> None:
    port, inbox, cert, key = int(sys.argv[1]), Path(sys.argv[2]), sys.argv[3], sys.argv[4]
    context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    context.load_cert_chain(cert, key)
    controller = Controller(Inbox(inbox), hostname="127.0.0.1", port=port, tls_context=context,
                            require_starttls=True, authenticator=accept_any, auth_require_tls=True)
    controller.start()
    print(f"fake SMTP listening on 127.0.0.1:{port}", flush=True)
    try:
        while True:
            time.sleep(3600)
    finally:
        controller.stop()


if __name__ == "__main__":
    main()
