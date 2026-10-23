# ADR 0001: UI stack for the web DAW

- **Status:** Proposed
- **Date:** 2026-10-22
- **Evidence:** two working prototypes with identical features. `Daw/` (Django + Datastar) and
  `DawNiceGUI/` (NiceGUI). See each project's README, `docs/datastar.md` and `docs/audio.md`.

## Context

We want a browser-based Digital Audio Workstation. Users upload audio clips (no streaming),
arrange them on tracks, play, scrub, trim, split, and apply effect plugins and presets. The team
works mainly in Python.

The prototypes established one thing that holds for **every** option below: **audio runs in the
browser's Web Audio API, in JavaScript.** Playback, scrubbing and effects can't round-trip to a
server. Both prototypes share the same ~200-line engine (`engine.js`). So the decision isn't
"JavaScript or not". It's **where the arrangement state and the editing UI live, and how they
stay in sync with the audio engine.**

### Decision drivers

1. Speed of development for a Python-focused team.
2. How rich the timeline interaction can get: smooth dragging, zoom, live previews.
3. How easily the UI talks to the audio engine.
4. Testability.
5. Scaling, deployment, and a path to collaboration.
6. Ecosystem maturity and hiring.

## Options considered

1. **Django + Datastar** (+ htmx, Tailwind, daisyUI). *Prototyped.* htmx was later removed (follow-up 4).
2. **NiceGUI** (Python UI over a WebSocket; Quasar/Vue components). *Prototyped.*
3. **Django or FastAPI JSON API + React SPA**, with libraries such as wavesurfer.js and Tone.js.
4. **API + Svelte/SvelteKit SPA** (Vue or Solid are similar).
5. **Django + htmx + Alpine.js**: hypermedia without Datastar.
6. **Reflex**: Python compiled to a React front end, with state on the server.
7. **Desktop shell (Tauri or Electron) around one of the web UIs.**

Native desktop DAW frameworks (for example JUCE in C++) were left out. They don't meet the
"web application" goal and need a different team.

## Pros and cons

| Option | Pros | Cons |
|---|---|---|
| **1. Django + Datastar** *(prototyped)* | Server-rendered templates with little client code (31 attribute expressions across 4 templates, 157 template lines). One SSE response can patch HTML, client state and run a script, so a drop updates tracks, inspector and audio in one round trip. Built-in client signals handle playhead, play state and selection without extra libraries. Django brings an ORM, admin, auth, migrations and CSRF. Plain HTTP: stateless, cacheable, scales like any Django app. SSE streams fit future collaboration and job progress. No build step. | Logic in HTML attribute strings can't be linted or type-checked. A drag-and-drop bug got through unnoticed this way. Failed requests show nothing by default. Every signal is sent on every request. `django-datastar` is a young community package that forced Django < 6.1. The syntax changed recently and older examples are wrong. Browser behaviour can only be tested with a real-browser tool (none written yet). Re-rendering whole regions of the page won't scale to large arrangements without finer-grained patches. Long-lived streams need ASGI plus pub/sub. Few developers know it. |
| **2. NiceGUI** *(prototyped)* | Everything in Python: layout, components and handlers, with no templates or HTML expressions. Fastest to write UI code. Rich Quasar components ready to use (sliders, toggles, upload, notifications). Built-in simulated-user test harness, so 3 tests cover upload, drag-drop, move, trim, split, presets and delete without a browser. These tests found a real bug. Easy calls both ways between Python and JS (`run_javascript`, `emitEvent`). | Every interaction goes over a stateful WebSocket. High-frequency UI (playhead, scrubbing) had to move into hand-written JS anyway. Server keeps live state per open tab, so horizontal scaling needs sticky sessions and memory per client. Refreshing a handler's own element breaks `ui.run_javascript` (found and fixed); `refresh()` runs in the background. Less control over markup and styling (Quasar look). No ORM/auth/admin included (prototype uses a JSON file). Smaller ecosystem and community than Django or React. |
| **3. API + React SPA** | The industry-default way to build rich interactive editors. Full client-side control: smooth drag previews, zoom, optimistic updates, undo/redo, keyboard shortcuts. Huge ecosystem: wavesurfer.js (waveforms, regions), Tone.js (Transport scheduling, effects), state libraries, component kits. TypeScript gives type safety across UI and audio code. Mature testing (Vitest, Testing Library, Playwright). Easiest to hire for. | Two codebases and two languages. Needs JS/TS expertise the team may not have. The API layer (serializers, endpoints, client-side caching) must be built and versioned. State is duplicated between client and server and has to be kept in sync. A build toolchain (Vite, npm) to maintain. Slowest start: likely more code for the same feature set than either prototype (not measured). React's rendering model needs care to keep 60 fps timelines from re-rendering everything. |
| **4. API + Svelte / SvelteKit** | Same capabilities as React, with less boilerplate and fine-grained reactivity, which suits 60 fps playhead and timeline updates. Smaller bundles. Works with the same audio libraries (wavesurfer.js, Tone.js). TypeScript support. | Same two-codebase and API costs as React. Smaller ecosystem and hiring pool than React. Svelte 5's model is recent, so some community material is out of date. |
| **5. Django + htmx + Alpine.js** | Mature, widely known hypermedia approach with a big community and many Django examples (`django-htmx`). Alpine handles client state (playhead, selection). Both are small libraries with stable APIs. | Two libraries to coordinate instead of one. Multiple updates per response need out-of-band swaps plus separate client-state wiring, which Datastar does natively. No built-in server push (needs the SSE extension or polling). Same untestable-attribute-logic problem as Datastar. The prototype already used htmx for upload and it added little. |
| **6. Reflex** | Pure Python like NiceGUI, but produces a React front end. React components can be wrapped and used. Includes a database layer and deployment tooling. | Same stateful-WebSocket and per-client-state scaling concerns as NiceGUI. A compile step to Next.js adds build complexity and slower iteration. Wrapping custom components (waveform, audio engine bridge) needs JS knowledge anyway. Not prototyped, so unproven for this use case. |
| **7. Tauri / Electron wrapper** | Desktop install, local file access, offline use. Tauri can call Rust for native audio and file work. Can reuse any of the web UIs above. | An addition to, not a replacement for, a UI stack choice. Adds packaging, signing and update channels per OS. Electron is large. Native audio through Tauri means a Rust audio engine, which is a large undertaking. Only worth it if desktop use becomes a requirement. |

### Comparison against the decision drivers

| Driver | 1. Datastar | 2. NiceGUI | 3. React | 4. Svelte | 5. htmx + Alpine | 6. Reflex |
|---|---|---|---|---|---|---|
| Development speed (Python team) | High | **Highest** | Low | Low–medium | High | High |
| Timeline interaction richness | Medium | Medium | **High** | **High** | Medium | Medium–high |
| Bridge to the audio engine | Good (expressions call JS, window events) | Good (`run_javascript`, `emitEvent`) | **Native** (same language) | **Native** | Good | Fair (wrapping needed) |
| Testability | Weak until browser tests exist | **Strong** (simulated user) | Strong | Strong | Weak until browser tests exist | Medium |
| Scaling / deployment | **Stateless HTTP** | Stateful WebSocket, sticky sessions | Stateless API | Stateless API | **Stateless HTTP** | Stateful WebSocket |
| Collaboration path | **SSE push built in** | WebSocket built in, single process | Add WebSockets/CRDTs | Add WebSockets/CRDTs | Add SSE extension | WebSocket built in |
| Ecosystem / hiring | Small | Small | **Largest** | Medium | Large | Small |

Ratings for options 3–6 are informed estimates. Only options 1 and 2 were built.

## Decision (proposed)

**Continue with Django + Datastar for the next phase, and drop htmx.** Keep the audio engine as
a separate JS module behind a small API, so the UI layer can be replaced without touching it.

### Rationale

- It fits a Python team: the arrangement, validation and persistence logic stay in Django,
  which also provides the database, auth and admin a real product needs.
- It keeps the server as the single source of truth over stateless HTTP. That's simpler to run
  and scale than NiceGUI's or Reflex's per-client WebSocket state.
- Its SSE model is the most direct route to the most valuable future features: live
  collaboration and server jobs such as export and analysis.
- Both prototypes showed that the hard, interactive part of a DAW (the audio engine) is
  JavaScript whatever the UI stack. The UI framework mostly decides how editing state reaches it.
  Datastar handled that with very little code.
- htmx duplicated what Datastar already does (Datastar can post multipart forms), so removing
  it leaves one request model, one CSRF mechanism and one attribute vocabulary.

### Why not the others (for now)

- **NiceGUI** was the fastest and most testable, and should stay the choice for **internal tools
  and quick experiments**. Per-client server state and WebSocket scaling make it a weaker
  base for a multi-user product.
- **React / Svelte** are the strongest for rich timeline editing, but they double the codebase
  and need skills the team doesn't have today. They're the planned escape route (see below),
  not the starting point.
- **htmx + Alpine** is a reasonable, more conservative hypermedia alternative, but needs two
  libraries for what Datastar does in one.
- **Reflex** brings NiceGUI's scaling concerns plus a compile step, and was not proven here.
- **Tauri / Electron** only becomes relevant if a desktop app is required.

### Conditions to reconsider

Revisit this ADR if any of these happen:

1. **Timeline interactions need client-side editing:** live drag previews, zoom with smooth
   rendering, multi-select, undo/redo, keyboard-driven editing. Then build the **timeline as a
   React or Svelte "island"** in the Datastar page, talking to the same audio engine, before
   considering a full SPA rewrite.
2. Datastar or `django-datastar` stops tracking new Django or Datastar releases. Fallback: use
   Datastar's own `headers` option for CSRF and the official `datastar-py` SDK alone.
3. Full-page re-renders cause noticeable latency with realistic arrangements (hundreds of regions)
   even after switching to per-region patches.
4. The team's skills shift toward TypeScript.

## Consequences

- **Accepted risks:** logic in attribute expressions, a small ecosystem, the Django version cap,
  and an unfamiliar model for new developers.
- **Required follow-up work:**
  1. Add browser tests (for example Playwright) for the Datastar UI and the audio engine. Render
     audio offline with `OfflineAudioContext` so audio tests give repeatable results.
  2. Show failed Datastar requests to the user (handle fetch error events globally).
  3. Replace whole-region re-renders with per-region patches.
  4. ~~Move the upload from htmx to Datastar and remove htmx.~~ Done 2026-10-22.
  5. Plan ASGI plus pub/sub (for example Redis) before building collaboration. A single-process
     collaboration proof of concept (shared chat, simultaneous timeline editing) now exists. It
     uses an in-memory hub and one SSE stream per tab, and confirms that the approach works.
  6. Keep the audio engine framework-independent (`window.daw` API plus events), so a later
     React/Svelte timeline island can reuse it unchanged.
