from __future__ import annotations

import queue

from supervisor import workers


def test_handle_chat_direct_processes_follow_up_messages_arrived_during_task(monkeypatch):
    class FakeAgent:
        def __init__(self) -> None:
            self._incoming_messages: queue.Queue[str] = queue.Queue()
            self.calls: list[str] = []

        def handle_task(self, task):
            self.calls.append(str(task.get("text") or ""))
            if len(self.calls) == 1:
                self._incoming_messages.put("follow-up")
            return [{"type": "send_message", "text": task["text"]}]

    class FakeEventQueue:
        def __init__(self) -> None:
            self.events = []

        def put(self, event) -> None:
            self.events.append(event)

    agent = FakeAgent()
    event_q = FakeEventQueue()

    monkeypatch.setattr(workers, "_get_chat_agent", lambda: agent)
    monkeypatch.setattr(workers, "get_event_q", lambda: event_q)

    workers.handle_chat_direct(chat_id=123, text="first")

    assert agent.calls == ["first", "follow-up"]
    assert [evt["text"] for evt in event_q.events] == ["first", "follow-up"]


def test_handle_chat_direct_preserves_follow_up_message_id(monkeypatch):
    class FakeAgent:
        def __init__(self) -> None:
            self._incoming_messages: queue.Queue[dict] = queue.Queue()
            self.calls: list[dict] = []

        def handle_task(self, task):
            self.calls.append(dict(task))
            if len(self.calls) == 1:
                self._incoming_messages.put({"text": "follow-up", "message_id": 999})
            return [{"type": "send_message", "text": task["text"]}]

    class FakeEventQueue:
        def __init__(self) -> None:
            self.events = []

        def put(self, event) -> None:
            self.events.append(event)

    agent = FakeAgent()
    event_q = FakeEventQueue()

    monkeypatch.setattr(workers, "_get_chat_agent", lambda: agent)
    monkeypatch.setattr(workers, "get_event_q", lambda: event_q)

    workers.handle_chat_direct(chat_id=123, text="first", message_id=111)

    assert agent.calls[0]["message_id"] == 111
    assert agent.calls[1]["message_id"] == 999
