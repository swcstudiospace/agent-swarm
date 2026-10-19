# The swarm bar

The swarm bar is the topmost of the bars above the omp editor (widget key `agent-swarm`). It shows, live, what every subagent of a swarm run is doing and for how long, and the measured token savings of the session. It is display-only: it never changes a tool decision, never writes a file and never calls a model. It is always there: from the first event of an interactive session until the session ends it is exactly two rows, always the full width of the terminal, and when there is nothing to show it says so (state `idle`) instead of going away. Stack, top to bottom: the swarm bar, Ultrathink's bar, omp's own bar.

- Source: `omp/src/swarm_bar.ts` (state reducer, renderer, controller), the row composer of `omp/src/swarm_dock.ts` (`composeBar`), the token chips of `omp/src/token_savings.ts` and the stack protocol of `omp/src/bar_stack.ts`.
- Contract: `.planning/research/v2.2-DESIGN.md` section 6 and section 7.
- Every capture below was produced by the real renderer (`renderSwarmBar`) with the real omp theme `dark-lavender` (truecolor, Nerd Font symbols), at the width its name says: every row is exactly that many columns wide. `omp/test/render_states.test.ts` fails when a capture here differs from what the renderer produces today, so this page cannot drift from the code. The colour-preserved originals are in `omp/test/fixtures/bar_states.ansi` (`cat` it in a truecolor terminal).
- The powerline glyphs of the Nerd Font preset (U+E0B6, U+E0B0, U+E0B1) are shown as `◖`, `◗` and `│` so the captures read in any font. Widths are identical (one column each).

## 1. Reading the bar

The bar is exactly two rows, in every state, and each row is exactly as wide as the terminal: omp hands a widget the full terminal width, and the bar fills it with its own background, so its left and right caps sit in the same columns whatever the swarm is doing.

```
row 1  ◖ ● swarm 5m00s │ 3 run │ 1 queued │ slots 3/4        …gap…        cache 87% │ saved 48.2k ◗   summary left, token chips right
row 2  ◖ ● a05-backend  4m12s │ ● a06-frontend  1m35s │ ○ queued a08-qa   0s            …gap…       ◗   the agents
```

### Its place in the stack

From the top: the swarm bar (2 rows), Ultrathink's bar (1 row), omp's own bar. omp stacks `aboveEditor` widgets in insertion order and re-setting a widget key moves that widget to the bottom, so a bar that remounts would drop below the bars that were meant to be under it. The bars keep their order with the `bars:restack` protocol on omp's event bus (`pi.events`, whose handlers run synchronously inside `emit`):

- Every time a bar (re)sets its widget (first mount, the remount one tick after events that move other bars, a restack-triggered remount) it emits `bars:restack` with `{ v: 1, after: <its rank> }`. Ranks: swarm bar 10, Ultrathink bar 20.
- A mounted bar whose rank is greater than `after` re-sets its own widget, which puts it below the announcer, and announces itself in turn. Payloads with another version, a bad shape or `after` greater than or equal to the bar's own rank are ignored. Ranks only increase along the chain, so it ends after at most one step per bar.
- It does not matter which extension loads first, and it works when one of them is absent: with no Ultrathink bar the swarm bar's announcement has no listener and the swarm bar sits alone above omp's own bar; with no swarm bar Ultrathink's bar is unchanged. Third-party widgets that set themselves after these bars end up below them until the next remount.

### Row 1: the summary

`<mark> swarm <elapsed> │ N run │ N queued │ N done │ N fail │ …`

| Segment | Meaning |
|---------|---------|
| mark | A spinner (one braille frame per second, taken from the theme) while anything is live; `✓` (green) when everything settled well; `⊘` (muted) when work was cancelled and nothing failed; `✕` (red) when something failed or timed out. `●` replaces the spinner under reduced motion. |
| `swarm <elapsed>` | The longest elapsed of the rows shown; while something is live it is at least the swarm's own clock from `live.json` (`swarm.elapsed_s`). |
| `N run` | Running, starting and handing-off agents. Accent colour when above 0. |
| `N wait` | Agents waiting on another agent. Shown only when above 0. |
| `N queued` | Admitted by the scheduler, no slot yet. |
| `N done` | Completed. Green when above 0. |
| `N fail` | Failed. Red when above 0. |
| `N timeout` | Timed out. Shown only when above 0. Red. |
| `N stall` | Running but silent past `SWARM_STALL_AFTER_S` (default 180 s). Shown only when above 0. Yellow. |
| `N cancel` | Cancelled. Shown only when above 0. |
| `slots R/L` | Running over the slot limit (`effective_parallel`, else `max_parallel`, from `live.json`). Shown only when `live.json` supplies a limit. |
| `telemetry unavailable` | The live source is unreadable, malformed or stale (see section 2, state `error`). |

When the row does not fit, segments are dropped by priority, never by position: the segment with the largest priority number goes first (on a tie the later one on screen), the others keep their order and place. Row 1 priorities: mark, `swarm` and elapsed 0 (the last to go), `N run` 1, `N fail`, `N timeout`, `N stall` and `telemetry unavailable` 2, `N queued` 3, `N wait` and `N done` 4, `N cancel` 5, `slots R/L` 6; the token chips (below) are in the same pool with priorities 0 to 11. Row 2: the agent rows in display order (the first is the last to go), `+N more` first. A lone segment still too wide is clipped with `…`. Between the left and the right group there are at least two columns. Under 12 columns, or when the theme lacks a powerline piece or throws, the row is plain: segments joined by ` │ `, no caps, still exactly as wide as the terminal; a theme with a transparent bar background draws no caps. Compare the widths of any capture below.

### Row 1, right: token savings

The right-hand end of row 1 shows the measured token savings of this session, as chips. The producer is the `omp-token-opt` plugin of claude-ultrathink: it emits `token-opt:savings` (a v1 snapshot) on omp's event bus and writes the same document to `$HOME/.omp/agent/token-opt/live.json` (`HOME` first, else the user's home directory). The swarm bar subscribes at the first event of an interactive session and restores the last snapshot from that `live.json` once (at most 1 MiB; a missing, unreadable or malformed file restores nothing). A snapshot has to pass the v1 checks (`v` is 1, a session id of 1 to 128 characters without `/`, count fields are non-negative safe integers, `savedTokens` equals `baselineTokens - currentTokens`, at most 200 `points`, each with `saved = baseline - current`, optional measurements absent or integers in range, `cacheHitPct` at most 100, flags 0 or 1); any invalid optional field rejects the whole snapshot.

- **Session filtering.** The bar reads its own session id on every event, and the id is read before the `live.json` restore. Once it is known a snapshot of another session is ignored: the bar keeps showing this session's last snapshot, so another session's snapshots neither replace it nor blank the chips. A restored `live.json` of another session is not adopted. When the id changes (a switched or resumed session) the stored snapshot of the old session is dropped. Before the id is known any valid snapshot is shown.
- **No snapshot, no chips.** Nothing is invented: without a snapshot row 1 has no right-hand side, and an optional chip appears only when its measurement exists. `before <n> · now <n>` and `saved <n>` are the only chips every snapshot carries.
- **Fresh snapshots.** A new valid snapshot is drawn at once, or by one trailing render when the last render was less than 250 ms ago.
- Provider cache tokens and billed totals are never folded into `saved`; the provider's hit rate is `cache`.

Chips, left to right on screen (least important at the left, `saved` always right-most); counts print as `999`, `1.2k`, `1.2M`:

| Chip | Shown when the snapshot has | Drop priority |
|------|-----------------------------|---------------|
| `graph ready` (dim) | `graphReady` 1 | 11 |
| `bill <n> · out <n>` (dim) | `providerInput` and/or `providerOutput` (each half only when present) | 10 |
| `idle <n>ms` | `lastIdleMs` | 9 |
| `payload <n>c` | `lastPayloadChars` | 8 |
| `query <n>ms` | `lastQueryMs` | 7 |
| `rtk +<n>` | `rtkSavedTokens` | 6 |
| `cost <n>c` | `costCents` | 5 |
| `turn <n>ms` | `lastTurnMs` | 4 |
| `before <n> · now <n>` (dim) | always (`baselineTokens`, `currentTokens`) | 3 |
| `prefix moved` / `tools moved` (warning, one chip each) | `prefixChanged` 1 / `toolsChanged` 1 | 2 |
| `ttft <n>ms` | `ttftMs` | 2 |
| `cache <n>%` | `cacheHitPct` | 1 |
| `saved <n>` (green when above 0, else dim) | always (`savedTokens`) | 0 |

The chips compete with the summary segments for the same columns: a narrow row loses `graph ready`, the bill, `idle`, `payload` and the other low-priority chips first, and `saved` is the last chip to go; the chips with priority 0 to 2 even outlast `N queued`, `N done` and `slots R/L` (see the 80-column capture, where only `N run` and `N fail` remain next to them). Captures of session `demo-session` with a full snapshot (every optional measurement present): one active swarm at 80 and 120 columns, then an idle swarm, which keeps the chips because the session's savings do not depend on the swarm, at 120 and at 200 columns, where the whole list fits.

Active, width 80:

<!-- capture:tokens-active@80 -->
```text
◖ ● swarm 5m00s │ 3 run │ 0 fail          ttft 640ms │ cache 87% │ saved 48.2k ◗
◖ ● a05-backend  4m12s │ ● a06-frontend  1m35s │ ● a07-data    14s │ +1 more   ◗
```
<!-- /capture -->

Active, width 120:

<!-- capture:tokens-active@120 -->
```text
◖ ● swarm 5m00s │ 3 run │ 1 queued │ 0 done │ 0 fail   before 61.4k · now 13.2k │ ttft 640ms │ cache 87% │ saved 48.2k ◗
◖ ● a05-backend  4m12s │ ● a06-frontend  1m35s │ ● a07-data    14s │ ○ queued a08-qa     0s                            ◗
```
<!-- /capture -->

Idle, width 120:

<!-- capture:tokens-idle@120 -->
```text
◖ ○ swarm idle    rtk +4.1k │ cost 12c │ turn 3200ms │ before 61.4k · now 13.2k │ ttft 640ms │ cache 87% │ saved 48.2k ◗
◖ no subagents running                                                                                                 ◗
```
<!-- /capture -->

Idle, width 200:

<!-- capture:tokens-idle@200 -->
```text
◖ ○ swarm idle  graph ready │ bill 9.4k · out 2.1k │ idle 1200ms │ payload 156523c │ query 85ms │ rtk +4.1k │ cost 12c │ turn 3200ms │ before 61.4k · now 13.2k │ ttft 640ms │ cache 87% │ saved 48.2k ◗
◖ no subagents running                                                                                                                                                                                 ◗
```
<!-- /capture -->

A snapshot with only the required counts shows only those chips, no zeros for what was never measured:

<!-- capture:tokens-minimal@80 -->
```text
◖ ○ swarm idle                          before 61.4k · now 13.2k │ saved 48.2k ◗
◖ no subagents running                                                         ◗
```
<!-- /capture -->

A snapshot of another session (here `another-session`, while this one is `demo-session`) is not shown:

<!-- capture:tokens-foreign@80 -->
```text
◖ ● swarm 5m00s │ 3 run │ 1 queued │ 0 done │ 0 fail │ slots 3/4               ◗
◖ ● a05-backend  4m12s │ ● a06-frontend  1m35s │ ● a07-data    14s │ +1 more   ◗
```
<!-- /capture -->

### Row 2: the agents

`<glyph> [word] <agent> <elapsed> [activity]`, rows separated by `│`.

| Glyph and word | State | Colour |
|----------------|-------|--------|
| spinner (`●` when still) | running | accent |
| spinner + `start` | starting: spawned, nothing reported yet | accent |
| `○ queued` | queued | dim |
| `◌ wait` | waiting on another agent | dim |
| `⇄ handoff` | handing a handover to the next agent | accent |
| `✓` | completed | green |
| `✕ FAIL` | failed (the activity column carries the error) | red |
| `⏱ TIMEOUT` | timed out | red |
| `! STALL` | running but silent | yellow |
| `⊘ cancelled` | cancelled | muted |

State is never colour alone: every state has a distinct glyph, and every state that is not self-evident also has a word. With colour off the bar still reads the same (`●` is used for every spinner in the plain fallback).

- `agent` is clipped to 18 columns, `elapsed` is right-aligned in 6 columns.
- `~` after the elapsed (`1m07s~`) marks a **detached** specialist: A01 started it as a background job and its `task` call has returned, so no frame and no `live.json` row vouches for it any more. It is still running (the glyph, the state and the ticking elapsed are those of a running row); the `~` says "last known running, not confirmed". It goes when a `live.json` row for the same task lists the specialist, and the row ends by one of the three rules in section 9.
- `activity` is the current tool or intent (`edit swarm/handover.py`), the error of a failed run, `retry 2/3` while the model retries, or `blocked: …` when retries ran out. It is redacted by a port of `swarm/redact.py` (tokens, keys, `Authorization:` and `Cookie:` headers, URL credentials, `NAME=value` and `--password hunter2` style flags, quoted `"password": "x"` / `'password': 'x'` fields and PEM blocks become `[REDACTED]`; both implementations run the shared vectors in `tests/fixtures/redact_cases.json`). A benign assignment or flag never hides a later one (`user=alice,password=x`, `--mode a;--password x`: the scan steps past the benign separator and keeps looking), while a credential name masks its whole value, commas included (`SECRET=a,b,c`), and a credential name swallowed inside that value keeps its own value masked too (`password=a,token: b`). Token shapes (GitHub, `sk-`, AWS, JWT, ...) are masked after the name passes, so a token-shaped prefix never shortens a credential name's value (`SECRET=ghp_<token>,second` masks all of it); `tests/fixtures/redact_generated.json` pins 6000 seeded option lists for both implementations. A name is a credential by its tokens (split on `_ - .` and camelCase): `token`, `secret`, `password`, `credential`, ..., and `key` or `seed` after `api`, `access`, `secret`, `private`, `signing`, `ssh`, `encryption`, `master`, `auth`, `session`, `license`, `client`, `ed25519`, `hmac`, `jwt`, `handoff` or `signer` (so `SWARM_ED25519_KEY`, `SWARM_SIGNING_KEY` and `SUBSTRATE_HANDOFF_KEY` are masked, while `KEYBOARD`, `--key-length`, `sort_key`, `random_seed` and `SWARM_ED25519_PUBLIC_KEY` are not); a name ending in `file` or `path` (`SWARM_ED25519_KEY_FILE`) is a location and stays visible. An unquoted value runs to whitespace, `&` or `;`, so a comma-joined credential list is masked whole. The text is then stripped of control and ANSI characters and clipped. Its budget is 40 columns for up to 2 rows, 24 for up to 4 and 16 beyond that; if the row would not fit it drops to 12 and then to none before a whole row is dropped.
- Redaction is best effort, not a guarantee. Beyond the carriers above, both implementations mask vendor tokens (GitHub, Stripe, Google, SendGrid, Slack, npm, GitLab, DigitalOcean, Hugging Face, AWS key ids), `<vendor>_KEY=` values that look like credentials (`STRIPE_KEY=`, Azure `AccountKey=`), `NAME    = value` aligned with up to 32 blanks, command-line credentials (`curl -u user:pass`, `mysql -pSECRET`, `sshpass -p`, `docker login -p`, `redis-cli -a`, `sqlcmd -P`, `-passin pass:`; `docker run -u 1000:1000` and `-p 8080:80` are left alone), re-serialised JSON pairs (`{\"password\":\"x\"}`), Kubernetes `- name: DB_PASSWORD` / `value:` pairs and YAML block scalars (`password: |`). `sanitizeText` removes bidi marks, zero-width characters and controls before redacting, so a secret split by them cannot re-join after the strip. On the Python side `runlog.emit` keeps `run_id`, `handover_id`, `task_id`, `parent_id` and `correlation_id` as written (identifiers are not free text), so a task id such as `sk-onboarding-flow-v2` keeps its activity and timeout state. Not covered: JSON `{"name": "DB_PASSWORD", "value": "x"}` pairs, a quote inside a quoted value, and any secret without a recognisable name or shape.
- Order: failed, timed out, stalled, running (starting, handoff), waiting, queued, cancelled, done; ties by longest elapsed, then by creation order. At most 12 rows compete for the line.
- Overflow: as many rows as fit are drawn, then `+N more` counts the rest. The count is exact (`N` is rows left out, never an estimate); the `overflow` captures show it.

### Timers

`formatElapsed` (the TypeScript twin of `swarm/clock.py format_elapsed`, pinned by `tests/fixtures/elapsed_cases.json`): `59s`, `4m12s`, `1h05m`. Negative, `NaN` and non-numbers print `0s`. Elapsed is extrapolated with the consumer's own monotonic clock (`performance.now()`), never `Date.now()`, so a wall-clock jump or an NTP step moves nothing. A settled row freezes at its final value. A row's elapsed never goes backwards when the preferred source changes.

## 2. The states

The bar is one of seven states (`BarState`). Each is shown below at widths 40, 80 and 120: always two rows, each exactly as wide as the width. The captures use reduced motion (`●`) so they are stable; section 4 shows the animated spinner. Row 1's right-hand side is empty in them because no token snapshot exists; the token captures are in section 1.

### State `idle`

Nothing is live, nothing lingers and there is no telemetry error. The bar stays mounted and says so: row 1 is the dim `○ swarm idle` (with the token chips on its right when a snapshot exists), row 2 the dim `no subagents running`. The status text is cleared.

Width 40:

<!-- capture:idle@40 -->
```text
◖ ○ swarm idle                         ◗
◖ no subagents running                 ◗
```
<!-- /capture -->

Width 80:

<!-- capture:idle@80 -->
```text
◖ ○ swarm idle                                                                 ◗
◖ no subagents running                                                         ◗
```
<!-- /capture -->

Width 120:

<!-- capture:idle@120 -->
```text
◖ ○ swarm idle                                                                                                         ◗
◖ no subagents running                                                                                                 ◗
```
<!-- /capture -->

### State `loading`

A `task` call was seen (or a lifecycle `started` frame arrived) but no subagent has reported yet: every live row is `starting`.

Width 40:

<!-- capture:loading@40 -->
```text
◖ ● swarm 2s │ 3 run │ 0 fail          ◗
◖ ● start a05-backend     2s │ +2 more ◗
```
<!-- /capture -->

Width 80:

<!-- capture:loading@80 -->
```text
◖ ● swarm 2s │ 3 run │ 0 queued │ 0 done │ 0 fail                              ◗
◖ ● start a05-backend     2s │ ● start a06-frontend     2s │ +1 more           ◗
```
<!-- /capture -->

Width 120:

<!-- capture:loading@120 -->
```text
◖ ● swarm 2s │ 3 run │ 0 queued │ 0 done │ 0 fail                                                                      ◗
◖ ● start a05-backend     2s │ ● start a06-frontend     2s │ ● start a07-data     2s                                   ◗
```
<!-- /capture -->

### State `active`

At least one run is live and at least one has reported. Three running, one queued; the activity column appears when a row has room.

Width 40:

<!-- capture:active@40 -->
```text
◖ ● swarm 5m00s │ 3 run │ 0 fail       ◗
◖ ● a05-backend  4m12s │ +3 more       ◗
```
<!-- /capture -->

Width 80:

<!-- capture:active@80 -->
```text
◖ ● swarm 5m00s │ 3 run │ 1 queued │ 0 done │ 0 fail │ slots 3/4               ◗
◖ ● a05-backend  4m12s │ ● a06-frontend  1m35s │ ● a07-data    14s │ +1 more   ◗
```
<!-- /capture -->

Width 120:

<!-- capture:active@120 -->
```text
◖ ● swarm 5m00s │ 3 run │ 1 queued │ 0 done │ 0 fail │ slots 3/4                                                       ◗
◖ ● a05-backend  4m12s │ ● a06-frontend  1m35s │ ● a07-data    14s │ ○ queued a08-qa     0s                            ◗
```
<!-- /capture -->

### State `active, one row per state`

Every state of the glyph table at once: failed, timed out, stalled, running, waiting, queued, handing off, cancelled and done. The ordering of row 2 is visible.

Width 40:

<!-- capture:every-state@40 -->
```text
◖ ● swarm 10m00s │ 3 run │ 1 fail      ◗
◖ ✕ FAIL a05-backend  1m11s │ +8 more  ◗
```
<!-- /capture -->

Width 80:

<!-- capture:every-state@80 -->
```text
◖ ● swarm 10m00s │ 3 run │ 1 wait │ 1 queued │ 1 fail │ 1 timeout │ 1 stall    ◗
◖ ✕ FAIL a05-backend  1m11s │ ⏱ TIMEOUT a06-frontend 10m00s │ +7 more          ◗
```
<!-- /capture -->

Width 120:

<!-- capture:every-state@120 -->
```text
◖ ● swarm 10m00s │ 3 run │ 1 wait │ 1 queued │ 1 done │ 1 fail │ 1 timeout │ 1 stall │ 1 cancel │ slots 3/4            ◗
◖ ✕ FAIL a05-backend  1m11s │ ⏱ TIMEOUT a06-frontend 10m00s │ ! STALL a07-data  3m10s │ ● a08-qa  1m06s │ +5 more      ◗
```
<!-- /capture -->

### State `active, in-session fan-out`

The main flow: the main session spawns the orchestrator (A01), and A01 spawns specialists inside its own session. A specialist's own progress never reaches the main session, but A01's progress frames carry A01's in-flight `task` call (`inflightTaskDetails`), so every specialist shows as its own row with its own timer. Here A01 has spawned four: a05-backend and a06-frontend run, a08-qa has not started, a15-docs already returned. The scheduler's `live.json` knows a05-backend and a08-qa by task id (`T7f3a-be`, `T7f3a-qa`), which their assignment text names, so each is one row, not two: the Task Store supplies the identity and state (a08-qa is `wait`, not `queued`), the frame supplies the activity. The summary counts every specialist once (3 run: A01, a05-backend, a06-frontend).

Width 40:

<!-- capture:in-session@40 -->
```text
◖ ● swarm 5m05s │ 3 run │ 0 fail       ◗
◖ ● a01-orchestrator  2m35s │ +4 more  ◗
```
<!-- /capture -->

Width 80:

<!-- capture:in-session@80 -->
```text
◖ ● swarm 5m05s │ 3 run │ 1 wait │ 0 queued │ 1 done │ 0 fail │ slots 3/4      ◗
◖ ● a01-orchestrator  2m35s task │ ● a05-backend  1m07s edit swarm/… │ +3 more ◗
```
<!-- /capture -->

Width 120:

<!-- capture:in-session@120 -->
```text
◖ ● swarm 5m05s │ 3 run │ 1 wait │ 0 queued │ 1 done │ 0 fail │ slots 3/4                                              ◗
◖ ● a01-orchestrator  2m35s │ ● a05-backend  1m07s │ ● a06-frontend    53s │ ◌ wait a08-qa    10s │ ✓ a15-docs    33s  ◗
```
<!-- /capture -->

### State `active, background specialists`

Most specialists are not blocking: omp runs them as background jobs (`async.enabled`, on by default). A01's `task` call sends one update with `details.async` (`state: "running"`) and returns; A01 then works on with its own tools, and its next frame carries no in-flight details although the specialists keep running. The bar does not call a row `cancelled` for that. It keeps the row running, the elapsed keeps ticking from the bar's own clock, and the row carries a `~` after the elapsed (detached). Here A01 spawned a05-backend and a06-frontend, its call returned, and it is now running bash. How a detached row ends is in section 9.

Width 40:

<!-- capture:detached@40 -->
```text
◖ ● swarm 2m35s │ 3 run │ 0 fail       ◗
◖ ● a01-orchestrator  2m35s │ +2 more  ◗
```
<!-- /capture -->

Width 80:

<!-- capture:detached@80 -->
```text
◖ ● swarm 2m35s │ 3 run │ 0 queued │ 0 done │ 0 fail                           ◗
◖ ● a01-orchestrator  2m35s │ ● a05-backend 1m07s~ │ ● a06-frontend   53s~     ◗
```
<!-- /capture -->

Width 120:

<!-- capture:detached@120 -->
```text
◖ ● swarm 2m35s │ 3 run │ 0 queued │ 0 done │ 0 fail                                                                   ◗
◖ ● a01-orchestrator  2m35s bash python… │ ● a05-backend 1m07s~ edit swarm/… │ ● a06-frontend   53s~ Reading the…      ◗
```
<!-- /capture -->

### State `overflow`

More rows than the width allows: `+N more` counts what is left out (nine rows exist; the narrower the bar, the fewer fit).

Width 40:

<!-- capture:overflow@40 -->
```text
◖ ● swarm 5m00s │ 6 run │ 1 fail       ◗
◖ ✕ FAIL a05-backend  1m11s │ +8 more  ◗
```
<!-- /capture -->

Width 80:

<!-- capture:overflow@80 -->
```text
◖ ● swarm 5m00s │ 6 run │ 2 queued │ 0 done │ 1 fail │ slots 6/8               ◗
◖ ✕ FAIL a05-backend  1m11s │ ● a06-frontend  5m00s │ +7 more                  ◗
```
<!-- /capture -->

Width 120:

<!-- capture:overflow@120 -->
```text
◖ ● swarm 5m00s │ 6 run │ 2 queued │ 0 done │ 1 fail │ slots 6/8                                                       ◗
◖ ✕ FAIL a05-backend  1m11s │ ● a06-frontend  5m00s │ ● a07-data  4m20s │ ● a08-qa  3m40s │ +5 more                    ◗
```
<!-- /capture -->

### State `completed`

Everything settled well; the mark is `✓` and the rows stay for the done linger.

Width 40:

<!-- capture:completed@40 -->
```text
◖ ✓ swarm 3m23s │ 0 run │ 0 fail       ◗
◖ ✓ a07-data  3m23s │ +2 more          ◗
```
<!-- /capture -->

Width 80:

<!-- capture:completed@80 -->
```text
◖ ✓ swarm 3m23s │ 0 run │ 0 queued │ 3 done │ 0 fail │ slots 0/4               ◗
◖ ✓ a07-data  3m23s │ ✓ a06-frontend  2m22s │ ✓ a05-backend  1m01s             ◗
```
<!-- /capture -->

Width 120:

<!-- capture:completed@120 -->
```text
◖ ✓ swarm 3m23s │ 0 run │ 0 queued │ 3 done │ 0 fail │ slots 0/4                                                       ◗
◖ ✓ a07-data  3m23s │ ✓ a06-frontend  2m22s │ ✓ a05-backend  1m01s                                                     ◗
```
<!-- /capture -->

### State `failed`

Everything settled, at least one failed or timed out; the mark is `✕`, and these rows stay for the (longer) fail linger.

Width 40:

<!-- capture:failed@40 -->
```text
◖ ✕ swarm 10m00s │ 0 run │ 1 fail      ◗
◖ ✕ FAIL a05-backend  1m11s │ +3 more  ◗
```
<!-- /capture -->

Width 80:

<!-- capture:failed@80 -->
```text
◖ ✕ swarm 10m00s │ 0 run │ 0 queued │ 2 done │ 1 fail │ 1 timeout │ slots 0/4  ◗
◖ ✕ FAIL a05-backend  1m11s │ ⏱ TIMEOUT a06-frontend 10m00s │ +2 more          ◗
```
<!-- /capture -->

Width 120:

<!-- capture:failed@120 -->
```text
◖ ✕ swarm 10m00s │ 0 run │ 0 queued │ 2 done │ 1 fail │ 1 timeout │ slots 0/4                                          ◗
◖ ✕ FAIL a05-backend  1m11s │ ⏱ TIMEOUT a06-frontend 10m00s │ ✓ a07-data  3m23s │ ✓ a08-qa  1m28s                      ◗
```
<!-- /capture -->

### State `error`

`live.json` is stale, malformed or unreadable and there are no rows to show: the bar says so instead of lying with old rows. Row 1 is `swarm: telemetry unavailable` in the warning colour (with the token chips when a snapshot exists), row 2 the same placeholder as idle.

Width 40:

<!-- capture:error@40 -->
```text
◖ swarm: telemetry unavailable         ◗
◖ no subagents running                 ◗
```
<!-- /capture -->

Width 80:

<!-- capture:error@80 -->
```text
◖ swarm: telemetry unavailable                                                 ◗
◖ no subagents running                                                         ◗
```
<!-- /capture -->

Width 120:

<!-- capture:error@120 -->
```text
◖ swarm: telemetry unavailable                                                                                         ◗
◖ no subagents running                                                                                                 ◗
```
<!-- /capture -->

### State `error with rows`

Telemetry is unavailable but other sources (here the parent's own `task` row) still show rows: the summary carries `telemetry unavailable`.

Width 40:

<!-- capture:error-rows@40 -->
```text
◖ ● swarm 5s │ 1 run │ 0 fail          ◗
◖ ● start a05-backend     5s           ◗
```
<!-- /capture -->

Width 80:

<!-- capture:error-rows@80 -->
```text
◖ ● swarm 5s │ 1 run │ 0 queued │ 0 done │ 0 fail │ telemetry unavailable      ◗
◖ ● start a05-backend     5s                                                   ◗
```
<!-- /capture -->

Width 120:

<!-- capture:error-rows@120 -->
```text
◖ ● swarm 5s │ 1 run │ 0 queued │ 0 done │ 0 fail │ telemetry unavailable                                              ◗
◖ ● start a05-backend     5s                                                                                           ◗
```
<!-- /capture -->


## 3. Settled rows linger

A settled row stays visible for `SWARM_BAR_LINGER_S` (default 20 s) after it settled; failed and timed-out rows stay for `SWARM_BAR_FAIL_LINGER_S` (default 600 s) so a failure is not missed. When the last row goes, the bar returns to its idle rows (it is not unmounted) and the status text is cleared. A new wave (a live run appears while nothing else is live) drops the previous wave's settled rows, failed ones included.

Edges of both windows, drawn at width 80. The 19 s capture still shows the done rows; at 20 s the bar is idle. Likewise the failed rows at 599 s and 600 s.

<!-- capture:linger:done+0s@80 -->
```text
◖ ✓ swarm 3m23s │ 0 run │ 0 queued │ 3 done │ 0 fail │ slots 0/4               ◗
◖ ✓ a07-data  3m23s │ ✓ a06-frontend  2m22s │ ✓ a05-backend  1m01s             ◗
```
<!-- /capture -->
<!-- capture:linger:done+19s@80 -->
```text
◖ ✓ swarm 3m23s │ 0 run │ 0 queued │ 3 done │ 0 fail │ slots 0/4               ◗
◖ ✓ a07-data  3m23s │ ✓ a06-frontend  2m22s │ ✓ a05-backend  1m01s             ◗
```
<!-- /capture -->
<!-- capture:linger:done+20s@80 -->
```text
◖ ○ swarm idle                                                                 ◗
◖ no subagents running                                                         ◗
```
<!-- /capture -->
<!-- capture:linger:failed+599s@80 -->
```text
◖ ✕ swarm 10m00s │ 0 run │ 0 queued │ 0 done │ 1 fail │ 1 timeout │ slots 0/4  ◗
◖ ✕ FAIL a05-backend  1m11s │ ⏱ TIMEOUT a06-frontend 10m00s                    ◗
```
<!-- /capture -->
<!-- capture:linger:failed+600s@80 -->
```text
◖ ○ swarm idle                                                                 ◗
◖ no subagents running                                                         ◗
```
<!-- /capture -->

A `live.json` already settled and older than the fail linger when the session starts is history: it is not shown at all. A live snapshot older than three heartbeats (`3 × SWARM_HEARTBEAT_S`, 30 s by default) when first read, or one that stops updating while it is being watched, is not painted live: the bar shows `telemetry unavailable` instead, and the marker goes when the file is updated again or ages past the fail linger.

When `live.json` was written by the in-session Task Store (`"producer": "derived"`, which `scripts/orch_status.py --transition` and the status reports write), the 30 s rule does not apply: that file changes only when a task changes state, so a quiet file is a swarm that is still working, not a dead one. The `3 × SWARM_HEARTBEAT_S` rule stays for the scheduler's document (`scripts/swarm_run.py`, rewritten about once a second). A derived document older than that therefore still feeds its rows to the bar, both at first read and while watched; only the fail linger (history) still hides a file that old at session start.

## 4. Motion

The activity spinner advances once a second (the elapsed values tick at the same rate). `SWARM_BAR_REDUCED_MOTION=1` (exactly `1`) pins every spinner to `●`; the elapsed values still tick.

Animated (default): one agent, four consecutive seconds.

<!-- capture:spinner:animated+0s@80 -->
```text
◖ ⠋ a05-backend  4m12s edit swarm/handover.py                                  ◗
```
<!-- /capture -->
<!-- capture:spinner:animated+1s@80 -->
```text
◖ ⠙ a05-backend  4m13s edit swarm/handover.py                                  ◗
```
<!-- /capture -->
<!-- capture:spinner:animated+2s@80 -->
```text
◖ ⠹ a05-backend  4m14s edit swarm/handover.py                                  ◗
```
<!-- /capture -->
<!-- capture:spinner:animated+3s@80 -->
```text
◖ ⠸ a05-backend  4m15s edit swarm/handover.py                                  ◗
```
<!-- /capture -->

Reduced motion:

<!-- capture:spinner:reduced+0s@80 -->
```text
◖ ● a05-backend  4m12s edit swarm/handover.py                                  ◗
```
<!-- /capture -->
<!-- capture:spinner:reduced+1s@80 -->
```text
◖ ● a05-backend  4m13s edit swarm/handover.py                                  ◗
```
<!-- /capture -->
<!-- capture:spinner:reduced+2s@80 -->
```text
◖ ● a05-backend  4m14s edit swarm/handover.py                                  ◗
```
<!-- /capture -->
<!-- capture:spinner:reduced+3s@80 -->
```text
◖ ● a05-backend  4m15s edit swarm/handover.py                                  ◗
```
<!-- /capture -->

The bar itself ticks (polls `live.json`, prunes, redraws) once a second only while something is live or a turn or tool call is in flight; otherwise it wakes once, at the moment the first settled row expires, and keeps only the idle watcher of section 7 (one `stat` of `live.json` every `SWARM_BAR_IDLE_POLL_MS`, 2 s by default, no redraw unless the file changed).

## 5. The secondary `swarm` status segment

Besides the bar, the extension sets `setStatus("swarm", …)`, which omp renders key-sorted inside the shared status segment (bottom of the screen, see the verified capture below). It is a count of what needs attention: running, waiting, queued and failed (failed includes timed out). It is cleared when none of these is non-zero, so a bar that shows only settled-ok rows has no status text. Text per scenario of section 2:

<!-- capture:status -->
```text
idle         (status cleared)
loading      swarm: 3 run
active       swarm: 3 run, 1 queued
every-state  swarm: 3 run, 1 wait, 1 queued, 2 fail
overflow     swarm: 6 run, 2 queued, 1 fail
completed    (status cleared)
failed       swarm: 2 fail
error        (status cleared)
error-rows   swarm: 1 run
in-session   swarm: 3 run, 1 wait
detached     swarm: 3 run
```
<!-- /capture -->

## 6. Headless `-p` and `--mode json`

There is no widget without a TUI. The extension prints one stderr line per event, never to stdout, so piped output stays clean: one when a run is first seen (its state then) and one when it settles. A run first seen already settled prints once, as its settle. Agent text on the line is sanitized (one line, no control characters, secrets redacted). From the real controller, for one scripted swarm (one agent completes, one fails):

<!-- capture:headless -->
```text
[swarm] a05-backend starting 0s
[swarm] a06-frontend starting 0s
[swarm] a06-frontend failed 41s
[swarm] a05-backend completed 1m05s
```
<!-- /capture -->

Format: `[swarm] <agent> <state> <elapsed>` with `<state>` one of `waiting`, `queued`, `starting`, `running`, `handing_off`, `completed`, `failed`, `cancelled`, `timed_out`.

## 7. What feeds the bar

Three sources, merged per run (a terminal state from any source beats everything; otherwise the richest and freshest wins; the parent's own `task` row speaks only when nothing richer has):

1. omp's event bus, `pi.events`, channels `task:subagent:progress` (status `pending|running|completed|failed|aborted`, duration, current tool or intent, tool count, retry state) and `task:subagent:lifecycle` (`started|completed|failed|aborted`). This is what omp's own `task` tool emits for subagents the *main* session runs. A subagent's session is created without the parent's event bus, so the specialists an orchestrator (A01) spawns inside its own session never emit their own frames here; they reach the bar through A01's frames instead (next paragraph).
2. `<swarm_dir>/live.json` (schema `swarm.live.v1`, `swarm/schemas/swarm.live.v1.json`), written atomically by the scheduler (`scripts/swarm_run.py`, `swarm/telemetry.py`) about once a second. `<swarm_dir>` is `$SWARM_DIR`, else `<git toplevel>/.swarm`, else `<cwd>/.swarm`. While something is live or a turn or tool call is in flight the bar polls it once a second by mtime and size; while idle, an unref'd watcher does only an mtime and size `stat` every `SWARM_BAR_IDLE_POLL_MS` (default 2 s) and runs the same read and ingestion only when the file appeared, changed or disappeared (it never runs next to the 1 Hz ticker, and is not created when `SWARM_BAR=off` or in subagent, headless and RPC sessions). A file over 1 MiB is malformed. Producer elapsed values are as of the write; the bar adds its own monotonic time since it read them. A new `run_id` for a known `task_id` is a retry: a new run. This source alone carries `queued`, `waiting`, `handing_off`, `stalled`, `timed_out` and the slot limit.
   One task is one row: `live.json` keeps every attempt of a task (`T.a1` completed, `T.a1.r1` rework running, `T.a2` retry) and sorts running rows before completed ones, so the bar merges only the newest attempt per `task_id` (higher attempt, then higher rework index, then the earlier row). An older completed attempt never settles the row of a newer running one, and the summary counts the task once.
3. The parent session's own `task` tool call and result, the last resort: a row appears the moment the call is seen (state `loading`) and settles when the call returns. A call something else blocked never returns a result; at `agent_end` such rows become `cancelled` (not a failure of the swarm).

**Specialists A01 spawns in-session.** omp's executor copies every update of a subagent's own `task` call into that subagent's progress as `inflightTaskDetails` (`results[]` plus one `progress[]` entry per spawn: agent, status, assignment, current tool and intent, tool count, duration), and clears it when the call ends. Every A01 frame the bar receives is therefore also a snapshot of all its specialists, and each `progress[]` entry (and each settled `results[]` entry the progress list no longer holds) becomes its own row under A01's row, keyed by A01's row and the entry's id, so three lanes of the same agent are three rows. Rules:

- Elapsed is the consumer's own clock: first sight minus the producer's `durationMs`; the earliest estimate wins, so a repeated snapshot (A01's frames for its own activity re-carry the same stale entry) never shortens it, and `Date.now` is never read. A settled entry shows its exact final duration. A snapshot identical to the last one changes nothing, so a specialist `live.json` flags `! STALL` is not kept "fresh" by A01's frames.
- State maps `pending` to `queued`, `running` to running, `completed` to done, `failed` to failed, `aborted` to cancelled. The activity is the current tool or intent; the count is the tool count.
- **Joined with `live.json`, never shown twice.** A nested row and a `live.json` row nothing else describes are one row when (a) the nested assignment text names the live row's `task_id` (`T<hex>-<node>`, the swarm task-id shape) and the agent matches, or else (b) exactly one unmatched row of that agent is left on each side, both live or both settled, and the text does not name a different task id. The joined row takes the Task Store's identity and state (`waiting`, `handing_off`, `timed_out`, `stalled` survive) and A01's activity; its elapsed is the larger of the two clocks. The join runs after every A01 frame and every `live.json` read, so the order does not matter. Rows that cannot be joined (several lanes of one agent with no task id in the text) stay separate, and the summary then counts that agent's larger side once, never both.
- **One agent key.** The sources spell an agent differently: the Task Store and the in-session `live.json` (`"producer": "derived"`) say `A02`, omp's nested progress and the scheduler's `live.json` say `a02-requirements`. Every comparison between sources (the task-id match, the one-per-side slug join, the twin check of a bus row and a `live.json` row, and the summary's no-double-count rule) uses one canonical key: the slot `aNN`, lower-case, parsed from either spelling; an agent without a slot (`explore`) is its lower-cased name. The row shows the name the bus gave (`a02-requirements`).
- A nested row never settles as `cancelled` just because A01's `task` call returned, was replaced, or its details went away: omp runs most specialists as background jobs (one update with `details.async`, then the call returns), so the row is **detached** (still running, `~` after the elapsed) and settles only on evidence: a `live.json` part with a final state; its parent's end (A01 failed, was cancelled or timed out: `cancelled`; A01 completed: the row is dropped, no state is invented); or the evidence timeout (`SWARM_BAR_DETACHED_S`). A settled row never goes back to running, even if `live.json` later loses it. Settled specialists leave with A01's row (the usual 20 s and 600 s lingers).
- Bounds: at most 64 entries per frame (`progress[]` first), every string sanitized like any other agent text, a malformed payload (not an object, `progress` not an array, entries without an id or status) is ignored, never thrown. Headless sessions print one stderr line per specialist spawn and settle, like any row.

The bar renders only in the top-level interactive session (`ctx.hasUI`, `ctx.mode === "tui"`, no subagent identity); subagents themselves never draw it. The token chips are fed separately from the rows (section 1, "Row 1, right: token savings").

## 8. Switches

| Name | Default | Effect |
|------|---------|--------|
| `SWARM_BAR=off` | on | The bar (no widget at all), its token chips (no `token-opt:savings` subscription, no `live.json` restore), the status segment and the headless lines are all off (read once, at the first event of the session). |
| `SWARM_BAR_LINGER_S` | 20 | Seconds a done or cancelled row stays. |
| `SWARM_BAR_FAIL_LINGER_S` | 600 | Seconds a failed or timed-out row stays. |
| `SWARM_BAR_REDUCED_MOTION=1` | unset | Freezes the spinner on `●`. |
| `SWARM_BAR_IDLE_POLL_MS` | 2000 | Period of the idle `stat` watcher of `live.json`; clamped to 500..30000, anything else falls back to 2000. |
| `SWARM_BAR_DETACHED_S` | 900 | Seconds after the last evidence (an A01 frame naming a detached specialist, or a `live.json` read listing it) before a detached row (`~`) is dropped; clamped to 60..86400, anything else falls back to 900. |
| `SWARM_HEARTBEAT_S` | 10 | Scheduler heartbeat; the bar calls `live.json` stale after three of them. |
| `SWARM_STALL_AFTER_S` | 180 | Scheduler threshold for `! STALL`. |
| `SWARM_DIR` | `<repo>/.swarm` | Where `live.json` is read. |

## 9. Known behaviour worth knowing

- While idle the bar does not poll `live.json`; it `stat`s it every `SWARM_BAR_IDLE_POLL_MS` (2 s) and only a change in mtime or size, or the file appearing or disappearing, wakes the full poll. So a swarm started from another process (a detached `hooks/autonomous_run.py`, a second terminal) is drawn within about two seconds, and a `telemetry unavailable` marker goes when the file is deleted. A file that is already stale or ancient at first sight keeps the old rule: history shows nothing, a swarm that died moments ago shows its single marker. A swarm run through omp's own bash tool is covered as before: a tool call in flight keeps the ticker running, and live rows keep it running after the call returns.
- Rows show the *scheduler's* view. If `live.json` says `running` and the bus says `completed`, the terminal state wins.
- Specialists A01 spawns in-session are seen through A01's frames while its `task` call is in flight: omp coalesces progress to about every 150 ms and does not hand the bar the call's final `results`, so a specialist that finishes just before the call returns stays at its last shown state until a `live.json` row (when the Task Store lists the task) or the parent's end settles it. Specialists those specialists spawn (a second nesting level) are not drawn, and two concurrent `task` calls of A01 share omp's single in-flight slot, so only the latest snapshot is visible; rows the latest snapshot no longer lists are detached.
- **Detached rows.** A nested row is detached when the call that listed it carried `details.async`, or when a later A01 frame no longer lists it (the call returned, or a new call replaced the snapshot). It stays `running`, shows `~`, and its elapsed keeps ticking on the bar's clock. It ends only by: (a) a `live.json` row for it (joined by task id, or by agent when it is the only unmatched row of that agent) reporting a final state, which wins; while the file lists it the `~` is off; (b) its parent A01 row ending: failed, cancelled or timed out settles it `cancelled`, completed drops it; (c) `SWARM_BAR_DETACHED_S` (default 900 s, clamped to 60..86400) without any frame or `live.json` read naming it: it is dropped, not settled. Headless sessions never print a `cancelled` line for a detached row while A01 runs or after it completed.
- A row seen settled never goes back to running: if `live.json` is deleted, goes stale or stops listing a row that it had settled, the row keeps its state and its frozen elapsed until its linger ends.
- When an agent has unjoinable rows on both sides (several lanes, no task id in the assignment text), the summary counts the larger side, so two genuinely distinct specialists of one agent, one known only to `live.json` and one only to A01's frame, are counted once.

## 10. Verified on omp 18.12.1

Checked against the installed omp (`omp --version` prints `omp/18.12.1`) with the extension loaded from this checkout, no model call made. Method: a scratch git workspace under `/tmp` with a hand-written `.swarm/live.json` (valid `swarm.live.v1`: three running agents with different elapsed, one queued, one stalled, one failed); interactive omp started in a detached tmux session (140×40) as

```
omp --profile h4smoke --config overlay.yml --no-extensions \
    -e <repo>/omp/src/index.ts \
    --no-session --no-title --no-skills --no-lsp --no-rules
```

An isolated profile was used so nothing from the user's own configuration (credentials, MCP servers, other extensions) is touched and no prompt can reach a model; `overlay.yml` set `theme.dark: dark-lavender`, `symbolPreset: nerd` and `setupVersion: 2` (skips the first-run login wizard). Nothing was typed into the session. Panes were read with `tmux capture-pane -p` (text) and `-p -e` (ANSI). The ANSI panes are kept as `omp/test/fixtures/omp_pane_t3.ansi` and `omp_pane_t8.ansi`; their colours are the same truecolor SGR sequences the renderer produces in the captures above.

**Loads cleanly.** The TUI came up with the bar mounted; stderr of the omp process was empty (0 bytes) and the profile's log has no extension, load or swarm entry (only provider-discovery warnings for local model servers that are not running).

**Mounted with real data, elapsed ticking** (the file was written once, never rewritten: the values below are the bar's own extrapolation). The bar sits directly above the editor; the `swarm:` status segment is the text on the status row. First capture 3 s after the TUI was up, second 5 s later; the elapsed of every live row, the swarm clock and the spinner frame advanced by 5 s. The rows were drawn by the content-width layout that the full-width bar replaced; what the recording covers is the ticking, not the width:

t+3 s
```text
◖ ⠋ swarm 5m09s │ 4 run │ 1 queued │ 0 done │ 1 fail │ 1 stall │ slots 4/4 ◗
◖ ✕ FAIL a10-security  1m11s │ ! STALL a09-reviewer  3m19s │ ⠋ a05-backend  4m21s │ ⠋ a06-frontend  1m44s │ ⠋ a07-data    23s │ +1 more ◗
   … editor …
   swarm: 4 run, 1 queued, 1 fail      <- status segment
```

t+8 s
```text
◖ ⠴ swarm 5m14s │ 4 run │ 1 queued │ 0 done │ 1 fail │ 1 stall │ slots 4/4 ◗
◖ ✕ FAIL a10-security  1m11s │ ! STALL a09-reviewer  3m24s │ ⠴ a05-backend  4m26s │ ⠴ a06-frontend  1m49s │ ⠴ a07-data    28s │ +1 more ◗
   … editor …
   swarm: 4 run, 1 queued, 1 fail      <- status segment
```

The failed row (`a10-security`, 1m11s) did not move (settled rows are frozen); the `+1 more` row is the queued `a08-qa`.

**Not covered by this first recording:** the full-width layout, the stack with Ultrathink's bar and the token chips (the second recording below covers them), and the idle watcher (a swarm started by another process while omp sits idle, a deleted file clearing the marker), which was not re-run on real omp.

### Second recording: full-width layout, stack order and token chips (omp 18.12.2)

Checked against the installed omp (`omp --version` prints `omp/18.12.2`, Nerd Font symbols, theme `dark-lavender`) with this checkout loaded from the operator's own configuration next to Ultrathink and `omp-token-opt`, in a detached tmux session (170×48, resized while it ran). A throwaway extension that is not part of the repository drove omp's event bus on a timeline: at t+2 s a `substrate:status` and a valid `token-opt:savings` snapshot of this session; at t+6 s three subagents (`task:subagent:lifecycle` `started`, then a `task:subagent:progress` frame every second); at t+18 s, t+24 s and t+30 s a completion, a failure (`rate limited`) and a completion; at t+46 s a snapshot of ANOTHER session; at t+50 s a newer snapshot of this session. Nothing was typed into the session. Panes were read with `tmux capture-pane -p`.

**Order and width.** In every pane the four rows above the editor are, from the top, the two swarm rows, Ultrathink's bar and omp's own bar, contiguous and in that order, and each of them is exactly as wide as the terminal. The window was resized while three agents ran, and every row re-fitted at once, to 170, 120, 90, 80, 70, 60, 50 and 40 columns, with the left cap in the first and the right cap in the last column. The pane at 170 columns (powerline glyphs shown as `◖`, `◗` and `│`):

```text
◖ ⠼ swarm 6s │ 3 run │ 0 queued │ 0 done │ 0 fail       query 120ms │ rtk +2.3k │ cost 12c │ turn 1830ms │ before 64.8k · now 48k │ ttft 420ms │ cache 87% │ saved 16.8k ◗
◖ ⠼ a05-backend     6s edit swarm/handover.py │ ⠼ a06-frontend     6s bash bun test │ ⠼ a08-qa     6s read docs/swarm-bar.md                                             ◗
   … Ultrathink's bar, omp's own bar, editor …
   swarm: 3 run      <- status segment
```

**The chips narrow by priority and `saved` never moves.** Row 1 at each width, left side and right-hand chips (the swarm itself moved on while the window was resized: an agent completed at t+18 s and one failed at t+24 s, so the counts differ between rows):

| columns | summary (left) | token chips (right) |
|--------:|----------------|---------------------|
| 170 | `swarm 6s │ 3 run │ 0 queued │ 0 done │ 0 fail` | `query 120ms │ rtk +2.3k │ cost 12c │ turn 1830ms │ before 64.8k · now 48k │ ttft 420ms │ cache 87% │ saved 16.8k` |
| 120 | `swarm 8s │ 3 run │ 0 queued │ 0 done │ 0 fail` | `before 64.8k · now 48k │ ttft 420ms │ cache 87% │ saved 16.8k` |
| 90 | `swarm 10s │ 3 run │ 0 queued │ 0 fail` | `ttft 420ms │ cache 87% │ saved 16.8k` |
| 80 | `swarm 12s │ 2 run │ 0 fail` | `ttft 420ms │ cache 87% │ saved 16.8k` |
| 70 | `swarm 14s │ 2 run │ 0 fail` | `ttft 420ms │ cache 87% │ saved 16.8k` (a 2-column gap) |
| 60 | `swarm 16s │ 2 run │ 0 fail` | `cache 87% │ saved 16.8k` |
| 50 | `swarm 18s │ 1 run` | `cache 87% │ saved 16.8k` |
| 40 | `swarm 20s │ 1 run` | `saved 16.8k` |

While the swarm was idle, at 170 columns, row 1 read `○ swarm idle` and ten chips (`idle 3000ms` first, `saved 16.8k` last; `bill`/`out` and `graph ready` did not fit), row 2 `no subagents running`.

**Another session's snapshot is ignored.** The snapshot of the other session at t+46 s changed nothing: row 1 kept `saved 16.8k` and `cache 87%`. The newer snapshot of this session at t+50 s moved them to `saved 21.4k` and `cache 91%`.

**The status segment.** The status row under the editor read `swarm: 3 run` while three agents ran, `swarm: 1 run, 1 fail` after the failure, and was empty while the swarm was idle.

**After settling.** The bar stayed mounted. Rows lingered as in section 3: with everything settled, row 1 read `✕ swarm 24s │ 0 run │ 0 queued │ 2 done │ 1 fail` and the failed `a08-qa` row stayed after the completed rows had lingered out.

**Next to Ultrathink's bar.** One prompt planned by Ultrathink on the session's own model (the only model call of the recording) while the same feed ran: both bars kept their rows and their full width while the window was resized during planning, to 120, 90, 70 and back to 170 columns. Ultrathink's Graph-of-Thought panel belongs to Ultrathink's widget, so while its graph stage ran it was drawn between the swarm bar and Ultrathink's bar.

**Not covered:** one host version, one theme and one font preset; the swarm's own `live.json` source was not used in this recording (the first recording covers it).

## 11. Refreshing the captures

After any change to the renderer, from the repository root:

```
bun omp/test/fixtures/bar_states.ts --write      # rewrites bar_states.ansi and every capture block above
cd omp && bun test test/render_states.test.ts test/swarm_bar.test.ts
```

The script needs omp's own `@oh-my-pi/pi-tui` theme sources (looked up under `$HOME/.local/share/reflex/bun/install/global/` and `$HOME/.bun/install/global/` in `node_modules/@oh-my-pi/pi-tui/src/theme/`; override with `OMP_PI_TUI_THEME_DIR`). The test works without them: it then checks the doc against `bar_states.ansi`, and the colour fixture against the renderer only where the theme package is present. The two recordings in section 10 ("Verified on omp 18.12.1" and the second one on 18.12.2) are recorded observations, not asserted.
