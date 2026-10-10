"""ACP round trip, denial, retry, dead-letter, and circuit breaker."""
import time

from swarm.acp import ACPStatus, Bus, routing_table

HAPPY = {
    "schema": "acp.v1",
    "sender": "A02",
    "recipient": "A03",
    "correlation_id": "corr-happy-1",
    "message_type": "requirements.spec",
    "payload": {"spec": "ping"},
    "timestamp": "2026-10-10T01:40:00Z",
    "timeout_hint_s": 1,
}


def test_routes_are_derived_from_the_manifest():
    table = routing_table()
    assert set(table) == {f"A{n:02d}" for n in range(1, 16)}
    assert table["A03"]["endpoint"] == "inproc://A03"
    assert "requirements.spec" in table["A02"]["produces"]
    assert "requirements.spec" in table["A03"]["accepts"]
    source = open("swarm/acp.py", encoding="utf-8").read()
    assert "A02 -> A03" not in source


def test_round_trip_echoes_the_correlation_id():
    bus = Bus(sleep=lambda _seconds: None)

    def handler(envelope):
        return {"correlation_id": envelope["correlation_id"], "accepted": True}

    bus.register("A03", handler)
    result = bus.request(HAPPY)
    assert result["state"] == "RESPONDED"
    assert result["ok"] is True
    assert result["response"]["correlation_id"] == "corr-happy-1"
    assert result["history"] == ["PENDING", "DELIVERED", "IN_PROGRESS", "RESPONDED"]


def test_malformed_envelope_is_rejected_without_retry():
    bus = Bus()
    broken = dict(HAPPY)
    del broken["correlation_id"]
    result = bus.request(broken)
    assert result["state"] == "INVALID_INPUT"
    assert result["attempts"] == 0
    assert any("correlation_id" in error for error in result["errors"])


def test_unauthorized_message_is_audited_and_not_retried():
    bus = Bus(sleep=lambda _seconds: (_ for _ in ()).throw(AssertionError("retried")))
    envelope = dict(HAPPY)
    envelope["message_type"] = "swarm.status"
    envelope["recipient"] = "A01"
    result = bus.request(envelope)
    assert result["state"] == "UNAUTHORIZED"
    assert result["attempts"] == 1
    assert bus.audit[-1]["caller"] == "A02"
    assert bus.audit[-1]["target"] == "A01"
    assert bus.audit[-1]["reason"]


def test_handler_invalid_input_does_not_retry():
    bus = Bus(sleep=lambda _seconds: (_ for _ in ()).throw(AssertionError("retried")))

    def handler(_envelope):
        raise ACPStatus("INVALID_INPUT", "spec field is empty")

    bus.register("A03", handler)
    result = bus.request(HAPPY)
    assert result["state"] == "INVALID_INPUT"
    assert result["attempts"] == 1
    assert result["retryable"] is False


def test_timeout_retries_then_dead_letters_the_full_envelope():
    delays = []
    bus = Bus(sleep=delays.append, max_attempts=3, backoff_s=0.01, breaker_threshold=5)

    def handler(_envelope):
        raise TimeoutError("slow")

    bus.register("A03", handler)
    envelope = dict(HAPPY)
    envelope["correlation_id"] = "corr-timeout-1"
    result = bus.request(envelope)
    assert result["state"] == "DEAD_LETTERED"
    assert result["attempts"] == 3
    assert delays == [0.01, 0.02]
    retained = bus.dead[-1]["envelope"]
    assert retained["correlation_id"] == "corr-timeout-1"
    assert retained["payload"] == {"spec": "ping"}
    assert retained["sender"] == "A02"


def test_slow_handler_hits_the_timeout_hint():
    bus = Bus(sleep=lambda _seconds: None, max_attempts=1, breaker_threshold=9)

    def handler(envelope):
        time.sleep(0.2)
        return {"correlation_id": envelope["correlation_id"]}

    bus.register("A03", handler)
    envelope = dict(HAPPY)
    envelope["correlation_id"] = "corr-slow"
    envelope["timeout_hint_s"] = 0.05
    result = bus.request(envelope)
    assert result["state"] == "DEAD_LETTERED"
    assert bus.dead[-1]["envelope"]["timeout_hint_s"] == 0.05


def test_circuit_breaker_fails_fast_while_local_tools_still_run():
    bus = Bus(sleep=lambda _seconds: None, max_attempts=1, breaker_threshold=1)

    def handler(_envelope):
        raise ConnectionError("down")

    bus.register("A03", handler)
    first = bus.request(HAPPY)
    assert first["state"] == "DEAD_LETTERED"
    assert "A03" in bus.open_circuits
    second = bus.request({**HAPPY, "correlation_id": "corr-open"})
    assert second["state"] == "FAILED"
    assert second["reason"] == "circuit open"
    assert second["attempts"] == 0
    local = bus.invoke_tool("A01", "orch_registry_ping", {"token": "ping"})
    assert local["state"] == "SUCCESS" and local["token"] == "ping"


def test_handler_exception_is_a_failed_delivery_and_is_not_retried():
    bus = Bus(sleep=lambda _seconds: (_ for _ in ()).throw(AssertionError("retried")))

    def handler(_envelope):
        raise RuntimeError("boom")

    bus.register("A03", handler)
    result = bus.request(HAPPY)
    assert result["state"] == "FAILED"
    assert result["retryable"] is False
    assert result["attempts"] == 1
    assert "boom" in result["reason"]


def test_response_must_echo_the_correlation_id():
    bus = Bus(sleep=lambda _seconds: (_ for _ in ()).throw(AssertionError("retried")))

    def missing(_envelope):
        return {}

    bus.register("A03", missing)
    omitted = bus.request(HAPPY)
    assert omitted["state"] == "FAILED"
    assert omitted["retryable"] is False
    assert "correlation_id" in omitted["reason"]

    def mismatched(_envelope):
        return {"correlation_id": "other"}

    bus.register("A03", mismatched)
    wrong = bus.request({**HAPPY, "correlation_id": "corr-echo"})
    assert wrong["state"] == "FAILED"
    assert wrong["retryable"] is False


def test_retries_keep_the_original_envelope():
    seen = []

    def handler(envelope):
        seen.append(envelope["payload"].get("spec"))
        envelope["payload"].clear()
        raise ConnectionError("down")

    bus = Bus(sleep=lambda _seconds: None, max_attempts=2, breaker_threshold=9)
    bus.register("A03", handler)
    result = bus.request(HAPPY)
    assert result["state"] == "DEAD_LETTERED"
    assert seen == ["ping", "ping"]
    assert result["envelope"]["payload"] == {"spec": "ping"}
    assert bus.dead[-1]["envelope"]["payload"] == {"spec": "ping"}
    assert HAPPY["payload"] == {"spec": "ping"}


def test_busy_handler_is_not_retried(monkeypatch):
    from swarm.tools.support import Busy

    def boom(_fn, _arg, _timeout):
        raise Busy("call")

    monkeypatch.setattr("swarm.acp.invoke_bounded", boom)
    bus = Bus(sleep=lambda _seconds: (_ for _ in ()).throw(AssertionError("retried")))
    bus.register("A03", lambda envelope: {"correlation_id": envelope["correlation_id"]})
    result = bus.request(HAPPY)
    assert result["state"] == "DEAD_LETTERED"
    assert result["attempts"] == 1
    assert result["retryable"] is True


def test_slow_handler_is_not_started_again_while_it_is_still_running():
    starts = []

    def handler(envelope):
        starts.append(envelope["correlation_id"])
        time.sleep(0.3)
        return {"correlation_id": envelope["correlation_id"]}

    bus = Bus(sleep=lambda _seconds: (_ for _ in ()).throw(AssertionError("retried")), max_attempts=3)
    bus.register("A03", handler)
    result = bus.request({**HAPPY, "correlation_id": "corr-inflight", "timeout_hint_s": 0.05})
    assert result["state"] == "DEAD_LETTERED"
    assert result["attempts"] == 1
    assert starts == ["corr-inflight"]


def test_invoke_tool_uses_the_bus_registry():
    import json
    from pathlib import Path

    data = json.loads(Path("agents.json").read_text(encoding="utf-8"))
    tool = data["agents"][0]["registered_tools"][0]
    assert tool["name"] == "orch_registry_ping"
    tool["permitted_callers"] = ["A15"]
    bus = Bus(data=data)
    denied = bus.invoke_tool("A01", "orch_registry_ping", {"token": "ping"})
    assert denied["state"] == "UNAUTHORIZED"
    allowed = bus.invoke_tool("A15", "orch_registry_ping", {"token": "ping"})
    assert allowed["state"] == "SUCCESS"
    assert allowed["token"] == "ping"


def test_unauthorized_tool_is_audited():
    bus = Bus()
    result = bus.invoke_tool("A02", "orch_registry_ping", {"token": "ping"})
    assert result["state"] == "UNAUTHORIZED"
    assert result["caller"] == "A02"
    assert result["target"] == "orch_registry_ping"
    assert bus.audit[-1]["caller"] == "A02"
    assert bus.audit[-1]["target"] == "orch_registry_ping"
    assert bus.audit[-1]["reason"]
