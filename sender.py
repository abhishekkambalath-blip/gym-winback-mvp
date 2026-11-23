"""Step 6: sending. Only drafts.approve_and_send() calls a sender, after human approval."""
import uuid
from typing import Protocol


class MessageSender(Protocol):
    def send(self, phone: str, message: str, channel: str) -> str:
        """Send a message and return a message id."""


class MockSender:
    def send(self, phone: str, message: str, channel: str) -> str:
        print(f"SENT via {channel} to {phone}: {message}")
        return f"mock-{uuid.uuid4().hex[:8]}"


class WhatsAppSender:
    def send(self, phone: str, message: str, channel: str) -> str:
        # TODO: plug in a WhatsApp Business provider here (Twilio, Gupshup or the
        # Meta Cloud API): call the provider's send-message endpoint with the phone
        # and message text, and return the provider's message id. Keep API keys in .env.
        raise NotImplementedError("WhatsAppSender is a stub: connect a WhatsApp Business provider first.")


def get_sender() -> MessageSender:
    return MockSender()
