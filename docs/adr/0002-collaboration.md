# ADR 0002: Collaboration (shared chat and simultaneous timeline editing)

- **Status:** Accepted for the proof of concept; production changes proposed below
- **Date:** 2026-10-22
- **Related:** [ADR 0001](0001-ui-stack.md) (UI stack). Implementation: `studio/hub.py`,
  `views.events`, `views.share*`, `views.chat`, `index.html`. Behaviour summary: the README's
  Collaboration section.

## Context

ADR 0001 chose Django + Datastar partly because server push (SSE) offers "the most direct route to
live collaboration". This ADR records how collaboration was built to test that claim: an
all-users chat, plus everyone editing the one shared timeline at the same time. It also records
which shortcuts are acceptable only in a proof of concept.

Constraints carried over from earlier work:

- The server is the single source of truth for the arrangement. Views already render HTML
  fragments (`#lanes`, `#inspector`, `#library`) and send them as Datastar SSE patches.
- Each browser's audio engine plays whatever arrangement JSON its page currently holds
  (`docs/audio.md`).
- The prototype runs on Django's WSGI `runserver`, in one process.

### Decision drivers

1. Least new technology and code: reuse Datastar, the existing views and the templates.
2. Changes by others visible within about a second.
3. Behaviour that's predictable when two people touch the same thing.
4. A clear path from the proof of concept to multi-process production.

## Decisions

### 1. Transport: one long-lived Datastar SSE stream per tab

Each page opens `@get('/events')` on load (`data-init` on a hidden element, `retryMaxCount: 1000`).
The server keeps the response open and writes Datastar events to it as they happen.

| Option | Pros | Cons |
|---|---|---|
| **Datastar SSE stream** *(chosen)* | No new client code. Server pushes the same `patch-elements` / `patch-signals` events the views already produce. Plain HTTP: works through proxies, reconnects automatically. Client→server traffic stays ordinary Datastar requests. | One-way only (that's fine here: changes go up as normal requests). Each open stream holds a server thread under WSGI. Browsers allow about 6 HTTP/1.1 connections per site, and each tab's stream uses one. |
| WebSockets (Django Channels) | Two-way, efficient for frequent small messages. | Needs ASGI, Channels and a channel layer. A second message protocol next to Datastar, plus client JS to apply messages. |
| Polling (`data-on-interval`) | Simplest server, stateless. | Latency equals the polling interval. Wasted requests while nothing changes. Re-sends whole fragments. |
| WebRTC / peer-to-peer | Low latency, no server fan-out. | Overkill. Still needs a server for persistence and as the source of truth. Complex signalling. |

### 2. Fan-out: an in-memory hub with one queue per tab

`studio/hub.py` maps each tab's client ID to a `queue.Queue`. `broadcast(events, exclude=)` puts
the same list of events on every other queue. The stream view blocks on its queue and writes
whatever arrives.

| Option | Pros | Cons |
|---|---|---|
| **In-memory hub** *(chosen for the PoC)* | About 40 lines, no infrastructure, easy to reason about. | **Single process only.** Lost on restart. Doesn't work with multiple workers or servers. |
| Redis pub/sub | Standard choice: fast, works across processes and servers. | Extra service to run. Messages aren't stored, so a client that's briefly disconnected misses them. |
| Postgres `LISTEN/NOTIFY` | Uses the existing database (if Postgres). Fires as part of database transactions. | Payload size limit (8 KB) means sending IDs, not HTML. Needs a listener connection per process. |
| Django Channels layer | Integrated groups and ASGI consumers. | Brings in the whole Channels stack (see decision 1). |

### 3. Payload: server-rendered HTML, the same patches the editor receives

After a change, a view sends the person who made it the usual response. It also broadcasts
`patch_lanes()` (re-rendered tracks plus `daw.refresh()`) to everyone else, or the re-rendered
library for uploads, or a single prepended message for chat.

| Option | Pros | Cons |
|---|---|---|
| **Re-rendered HTML fragments** *(chosen)* | Zero client merge logic. Every client converges on the server's state with each patch. Reuses the existing templates. | Re-sends all of `#lanes` for every change. The whole drop response, tracks plus inspector, was about 11 KB with one region in testing. Rendering happens per change, not per client, so it's cheap, but it grows with arrangement size. |
| JSON change operations ("region 5 moved to 2.0 s") | Small messages. Enables animation and finer-grained updates. | Client-side state and merge code: the SPA complexity ADR 0001 avoided. |
| "Something changed" + each client re-fetches | Tiny broadcast. Each client fetches its own view. | Thundering herd of requests after every change. Extra round trip of latency. |

### 4. No echo to the sender: a client ID signal

The index view gives each tab a random `cid` signal. Datastar includes signals in every JSON
request, so `share()` reads `cid` and leaves that client out. The sender already has the update
from its own response, and getting it twice would reset its inspector mid-edit.

Exception: uploads use Datastar's form mode, which sends no signals. The library is broadcast to
everyone, including the uploader, which is harmless because the patch is identical.

### 5. Inspector sync without tracking who views what

When a region changes, others receive a tiny element patched into `#sync`:
`<div data-init="$selected == ID && @get('/regions/ID')">`. Only tabs whose own `$selected`
matches re-fetch the inspector. If the region was deleted, `GET /regions/ID` now returns an
empty inspector instead of 404.

| Option | Pros | Cons |
|---|---|---|
| **Conditional `data-init` snippet** *(chosen)* | Server stays stateless about selection. Uses only Datastar features. | Costs one extra request per affected viewer. Logic hidden in an HTML string. |
| Server tracks each client's selection | Can push exactly the right inspector. | Needs selection updates sent to the server, plus more per-client server state. |
| Don't sync the inspector | Simplest. | Stale inspector. The viewer's next slider move would silently overwrite the other person's change. |

### 6. Conflicts: last write wins

Every edit saves the full set of region values from the editor's signals. If two people edit the
same region, whoever saves last overwrites the other, and the earlier change is lost without
warning. Inspector sync (decision 5) narrows the window: after someone else's change, your
inspector shows the new values before your next edit.

| Option | Pros | Cons |
|---|---|---|
| **Last write wins** *(chosen for the PoC)* | No extra code. Predictable. Matches how the views already work. | Lost updates when edits overlap. No feedback about who changed what. |
| Field-level updates (send only the changed field) | Removes most conflicts: one person trims while another adds an effect. | Needs signal diffing or per-control endpoints. |
| Soft locks / "being edited by…" indicators | Prevents conflicts socially. Uses the presence data already available. | Lock lifetime and cleanup when tabs close. More UI. |
| CRDT/OT (for example Yjs) | Real concurrent editing with merging. | A client-side document model: contradicts the server-authoritative design of ADR 0001. Large addition. |

### 7. Presence: connection count, with keepalive-based cleanup

The "N online" badge is the number of open streams, re-broadcast on connect and disconnect. Under
WSGI a closed connection is only noticed when a write fails, so the stream writes an SSE comment
(`: keepalive`) every 5 s. A closed tab drops off after one or two keepalives, about 10 s.

### 8. Identity: free-text display names

Each tab starts with a random "Guest NNNN" name bound to the `userName` signal. It's sent with
chat posts and cut to 40 characters. Messages are rendered with Django auto-escaping, and the
tests confirmed that HTML is displayed as text. There are no accounts and no authentication.

### 9. Chat: persisted, prepended, newest at the bottom

`ChatMessage` rows (name, text up to 500 characters, time) are saved, and the last 50 render with
the page. New messages are broadcast to **everyone, including the sender**, with
`mode='prepend'` into `#chat-log`. Because that list uses `flex-col-reverse`, prepending puts the
newest message at the bottom and keeps the view scrolled there without any JS. The sender's
response just clears `chatText`.

### 10. Playback: remote edits re-sync audio

Broadcast track updates include `daw.refresh()`, so a listener who's playing hears others' edits
straight away. As with your own edits, this restarts playback from the current position and causes
a short glitch.

## Consequences

### Positive

- Collaboration took about 120 lines of Python, about 25 lines of template and **no new
  JavaScript**. It confirms ADR 0001's claim that Datastar's SSE model suits server push.
- Every client converges on the server's state with each update. There's no client-side merge
  logic to get wrong.
- Collaboration is optional: without the stream the app works exactly as a single-user editor.

### Negative / accepted for the PoC

- Single process, in-memory, one thread per tab: not deployable beyond a demo.
- Lost updates under concurrent edits of the same region (last write wins).
- Whole-track re-renders for every change, sent to every client.
- Remote edits interrupt playback for anyone listening.
- No accounts, rooms or projects: everyone shares one timeline and one chat.
- No rate limiting or moderation on chat.

### Verified

On an isolated server with two simulated tabs (curl streams):
- Edits reached the other tab but not the sender.
- Inspector reload snippets reached only the other tab.
- Uploads and chat reached both, with chat HTML escaped.
- Presence counts rose and fell, and fetching a deleted region returned an empty inspector.

**Not verified in a real browser:** whether Datastar re-runs a `data-init` element patched into
`#sync`, reconnection after a server restart, and the 6-connection limit.

## Reconsider when

1. **More than one server process** is needed: replace `hub.py` with Redis pub/sub (or
   `LISTEN/NOTIFY`), keeping the same `broadcast()` interface.
2. **More than a handful of concurrent users:** move streams to ASGI with async views, so an open
   stream no longer holds a thread.
3. **Lost updates are reported by users:** move to field-level updates first, then soft locks.
   Consider a CRDT only if real simultaneous editing of the *same* region is required.
4. **Arrangements grow to hundreds of regions:** broadcast per-region patches instead of all of
   `#lanes`.

## Follow-up work (for production)

1. Projects/rooms: key hub groups and chat by project, with authenticated users instead of free
   text names.
2. Redis-backed hub plus ASGI deployment (Daphne/Uvicorn), served over HTTP/2 to avoid the
   per-site connection limit.
3. Field-level region updates, plus an "edited by <name>" indicator on regions and in the inspector.
4. Smooth remote updates for listeners: apply parameter changes without restarting playback
   (see `docs/audio.md`, "Glitch-free live edits").
5. Chat rate limiting and length checks on the server (length is already enforced).
6. Browser tests (Playwright) with two browser contexts, covering the unverified items above.
