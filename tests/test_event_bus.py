"""Tests for EventBus — publish/subscribe event system."""

import threading
import pytest
from compiler.thinking.event_bus import EventBus, Event, get_event_bus


class TestEvent:
    def test_auto_id(self):
        evt = Event(type="test")
        assert evt.id.startswith("evt_")
        assert len(evt.id) > 4

    def test_auto_timestamp(self):
        evt = Event(type="test")
        assert evt.timestamp > 0

    def test_to_dict(self):
        evt = Event(type="thinking", source="agent", data={"key": "value"})
        d = evt.to_dict()
        assert d["type"] == "thinking"
        assert d["source"] == "agent"
        assert d["data"] == {"key": "value"}

    def test_to_sse(self):
        evt = Event(type="test", data="hello")
        sse = evt.to_sse()
        assert sse.startswith("data: ")
        assert '"test"' in sse


class TestEventBus:
    def setup_method(self):
        self.bus = EventBus()

    def test_subscribe_and_publish(self):
        received = []
        self.bus.subscribe("test", lambda e: received.append(e))
        self.bus.publish(Event(type="test", data="hello"))
        assert len(received) == 1
        assert received[0].data == "hello"

    def test_subscribe_multiple_types(self):
        received = []
        self.bus.subscribe(["edit", "approve"], lambda e: received.append(e.type))
        self.bus.publish(Event(type="edit"))
        self.bus.publish(Event(type="approve"))
        self.bus.publish(Event(type="other"))
        assert received == ["edit", "approve"]

    def test_wildcard_subscriber(self):
        received = []
        self.bus.subscribe("*", lambda e: received.append(e.type))
        self.bus.publish(Event(type="a"))
        self.bus.publish(Event(type="b"))
        assert received == ["a", "b"]

    def test_prefix_wildcard(self):
        received = []
        self.bus.subscribe("artifact:*", lambda e: received.append(e.type))
        self.bus.publish(Event(type="artifact:created"))
        self.bus.publish(Event(type="artifact:stale"))
        self.bus.publish(Event(type="other"))
        assert received == ["artifact:created", "artifact:stale"]

    def test_unsubscribe(self):
        received = []
        handler = lambda e: received.append(e)
        self.bus.subscribe("test", handler)
        self.bus.publish(Event(type="test"))
        assert len(received) == 1

        self.bus.unsubscribe("test", handler)
        self.bus.publish(Event(type="test"))
        assert len(received) == 1

    def test_broken_subscriber_doesnt_crash(self):
        def bad_handler(e):
            raise RuntimeError("boom")

        self.bus.subscribe("test", bad_handler)
        # Should not raise
        self.bus.publish(Event(type="test", data="ok"))

    def test_event_log(self):
        self.bus.publish(Event(type="a"))
        self.bus.publish(Event(type="b"))
        log = self.bus.get_log()
        assert len(log) == 2

    def test_event_log_filter_by_type(self):
        self.bus.publish(Event(type="a"))
        self.bus.publish(Event(type="b"))
        self.bus.publish(Event(type="a"))
        log = self.bus.get_log(event_type="a")
        assert len(log) == 2

    def test_event_log_limit(self):
        for i in range(10):
            self.bus.publish(Event(type="test"))
        log = self.bus.get_log(limit=3)
        assert len(log) == 3

    def test_clear_log(self):
        self.bus.publish(Event(type="test"))
        self.bus.clear_log()
        assert len(self.bus.get_log()) == 0

    def test_max_log_size(self):
        bus = EventBus(max_log_size=5)
        for i in range(10):
            bus.publish(Event(type="test"))
        assert len(bus.get_log(limit=100)) == 5

    def test_subscriber_count(self):
        self.bus.subscribe("a", lambda e: None)
        self.bus.subscribe("a", lambda e: None)
        self.bus.subscribe("b", lambda e: None)
        assert self.bus.subscriber_count("a") == 2
        assert self.bus.subscriber_count("b") == 1
        assert self.bus.subscriber_count() == 3

    def test_thread_safety(self):
        errors = []
        received = []
        self.bus.subscribe("test", lambda e: received.append(1))

        def publish_many():
            try:
                for _ in range(50):
                    self.bus.publish(Event(type="test"))
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=publish_many) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        assert len(received) == 200


class TestGlobalBus:
    def test_singleton(self):
        bus1 = get_event_bus()
        bus2 = get_event_bus()
        assert bus1 is bus2
