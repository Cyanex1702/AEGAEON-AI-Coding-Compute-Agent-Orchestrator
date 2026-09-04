from aegaeon.api import create_app
from aegaeon.config import get_settings

app = create_app()


if __name__ == "__main__":
    import uvicorn

    settings = get_settings()
    uvicorn.run(
        "apps.controller.main:app",
        host=settings.host,
        port=settings.port,
        reload=False,
        ws_ping_interval=settings.websocket_ping_interval_seconds,
        ws_ping_timeout=settings.websocket_ping_timeout_seconds,
    )
