# ACP topology

ACP is the in-repo message fabric (`swarm/acp.py`, schema `acp.v1`). It is not an external SDK. The envelope does not care which transport moves the bytes. The bus in this module is the in-process transport used by tests and by agents in one runtime.

The older signed `swarm.v1` envelopes (`swarm/envelope.py`) still carry task-store gate verdicts. ACP does not replace them.

## Envelope

| Field | Meaning |
| --- | --- |
| `schema` | Always `acp.v1`. |
| `sender` | Agent id from `agents.json`. |
| `recipient` | Agent id from `agents.json`. |
| `correlation_id` | Caller-chosen id. The response must echo it. |
| `message_type` | A manifest string such as `requirements.spec` or `gate.verdict`. |
| `payload` | JSON object. The bus does not interpret it. |
| `timestamp` | Sender clock, ISO-8601 string. |
| `timeout_hint_s` | Per-attempt budget. The bus stops the handler when this elapses. |

Validation runs before delivery. A malformed envelope returns `INVALID_INPUT` and a list of errors. It is not retried and it is not dead-lettered.

## Routing and discovery

`routing_table()` reads `agents.json` and emits one row per agent:

- `endpoint`: `inproc://` plus the agent id
- `accepts`: that agent's `consumes`
- `produces`: that agent's `produces`

No module contains a handwritten peer list. Adding an agent to the manifest adds a route.

## Permission matrix

Default deny, enforced inside `Bus.request` and `Bus.invoke_tool` (the transport), not inside the handler.

- Agent to agent: the message type must be in the sender's `produces` and the recipient's `accepts`. Sender and recipient must differ.
- Agent to tool: the caller must be in the tool's `permitted_callers`.

A denial returns `UNAUTHORIZED` and appends `{caller, target, reason}` to `Bus.audit`. `INVALID_INPUT` and `UNAUTHORIZED` are not retried.

## Lifecycle

`PENDING` → `DELIVERED` → `IN_PROGRESS` → one terminal state:

| State | When |
| --- | --- |
| `RESPONDED` | The handler returned an object whose `correlation_id` matches. |
| `INVALID_INPUT` | The envelope is malformed, or the handler raised `ACPStatus("INVALID_INPUT")`. |
| `UNAUTHORIZED` | The matrix denied the message or the tool. |
| `TIMED_OUT` | This attempt exceeded `timeout_hint_s`. Recorded on intermediate attempts. |
| `FAILED` | The peer is unreachable and attempts remain, or the circuit is open. |
| `DEAD_LETTERED` | Retry budget exhausted. The full envelope is kept on `Bus.dead`. |

## Retry, dead-letter, circuit breaker

Retryable failures are timeouts and connection errors. A timeout includes a call that was still queued when `timeout_hint_s` elapsed, so the handler never started. Backoff is `min(backoff_s * 2^(attempt-1), max_backoff_s)`, capped by `max_attempts` (default 3).

When the budget is exhausted the bus stores the original envelope (sender, recipient, correlation id, type, payload, timestamp, timeout hint) and returns `DEAD_LETTERED`.

An exhausted queue wait does not increment the peer's failure count. Each exhausted delivery that reached the handler does. At `breaker_threshold` the circuit opens. Later sends to that peer return `FAILED` / `circuit open` immediately, with zero handler calls. Tools invoked through `invoke_tool` ignore the breaker.

## Worked example: happy path

A02 is allowed to send `requirements.spec` to A03 because A02 produces it and A03 consumes it.

Request:

```json
{
  "schema": "acp.v1",
  "sender": "A02",
  "recipient": "A03",
  "correlation_id": "corr-happy-1",
  "message_type": "requirements.spec",
  "payload": {"spec": "ping"},
  "timestamp": "2026-10-10T01:40:00Z",
  "timeout_hint_s": 1
}
```

The handler returns `{"correlation_id": "corr-happy-1", "accepted": true}`. The bus record is `state: RESPONDED`, `attempts: 1`, history `PENDING, DELIVERED, IN_PROGRESS, RESPONDED`, and `response.correlation_id` is `corr-happy-1`.

## Worked example: timeout, retry, dead-letter

Same sender, recipient, and type. The handler raises `TimeoutError` (or runs past `timeout_hint_s`). With `max_attempts: 3` and `backoff_s: 0.01` the bus waits 0.01s and then 0.02s, then stops.

```json
{
  "schema": "acp.v1",
  "sender": "A02",
  "recipient": "A03",
  "correlation_id": "corr-timeout-1",
  "message_type": "requirements.spec",
  "payload": {"spec": "ping"},
  "timestamp": "2026-10-10T01:40:00Z",
  "timeout_hint_s": 1
}
```

Terminal state: `DEAD_LETTERED`, `attempts: 3`. `Bus.dead[-1].envelope` is this object, payload included, not a summary. `INVALID_INPUT` (empty spec raised as `ACPStatus`) would have stopped on attempt 1 with no sleep.
