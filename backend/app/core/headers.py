"""Security headers on every API response.

- X-Content-Type-Options: nosniff - the browser must trust our Content-Type
  (image/jpeg) and never "sniff" an uploaded file as HTML or script.
- X-Frame-Options: DENY and CSP frame-ancestors 'none' - no other site may
  show our pages inside a frame (clickjacking).
- Referrer-Policy - links to other sites don't carry our full URLs.

Written as a plain ASGI middleware (like core/capacity.py), so it adds no
thread hop and also covers error responses.
"""

from starlette.types import ASGIApp, Message, Receive, Scope, Send

SECURITY_HEADERS = [
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"strict-origin-when-cross-origin"),
    (b"content-security-policy", b"frame-ancestors 'none'"),
]
_NAMES = {name for name, _ in SECURITY_HEADERS}


class SecurityHeaders:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = [h for h in message.get("headers", []) if h[0].lower() not in _NAMES]
                message["headers"] = headers + SECURITY_HEADERS
            await send(message)

        await self.app(scope, receive, send_with_headers)
