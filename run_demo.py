#!/usr/bin/env python3
"""Launch the browser-playable demo console of the game."""
from __future__ import annotations

import uvicorn

from grimhaven.config import settings
from grimhaven.demo.app import create_app

app = create_app()

if __name__ == "__main__":
    print("⚔️  Grimhaven demo console → http://%s:%s" % (settings.demo_host, settings.demo_port))
    uvicorn.run(app, host=settings.demo_host, port=settings.demo_port, log_level="info")
