from __future__ import annotations

import os
import time
from uuid import uuid4

import raindrop.analytics as raindrop


def main() -> None:
    local_workshop_url = os.getenv("RAINDROP_LOCAL_DEBUGGER", "http://localhost:5899/v1/")
    write_key = os.getenv("RAINDROP_WRITE_KEY") or None
    raindrop.init(
        api_key=write_key,
        tracing_enabled=bool(write_key),
        auto_instrument=False,
        local_workshop_url=local_workshop_url,
    )

    interaction = raindrop.begin(
        user_id=os.getenv("USER", "local"),
        event="raindrop_hello_world",
        event_id=f"hello-world-{uuid4()}",
        input="Say hello from a minimal Raindrop Python script.",
        properties={"source": "examples/raindrop_hello_world.py"},
    )

    try:
        output = "Hello, Raindrop Workshop!"
        time.sleep(0.05)
        interaction.finish(output=output)
        print(output)
    except Exception as error:
        interaction.finish(output=f"error: {error}")
        raise
    finally:
        raindrop.flush()
        raindrop.shutdown()


if __name__ == "__main__":
    main()
