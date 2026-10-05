"""Каркас worker. Планировщик опроса Jira и задачи LLM добавляют задачи Dev-4/Dev-6."""
import pathlib
import time

HEARTBEAT = pathlib.Path("/tmp/worker.heartbeat")


def main() -> None:
    while True:
        HEARTBEAT.touch()
        time.sleep(30)


if __name__ == "__main__":
    main()
