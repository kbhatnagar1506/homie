"""Which person a piece of agent work is for. Set once when a request arrives; flows through every agent it touches."""

from contextvars import ContextVar

CURRENT_USER: ContextVar[str] = ContextVar("homie_user", default="")
