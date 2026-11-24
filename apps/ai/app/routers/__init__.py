"""HTTP / WebSocket routes. Thin: parse the request, call the agent or a service, stream the result."""

from . import chat, command, health, phone, speech, voice

routers = [health.router, chat.router, command.router, speech.router, phone.router, voice.router]
