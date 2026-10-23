# Datastar in the PoC DAW

This note explains how and why the PoC uses [Datastar](https://data-star.dev), what worked,
what didn't, and what stands in the way of taking it further. It's based on the code in this
repository (Datastar 1.0.4, `datastar-py` 1.0.3, `django-datastar` 0.2.0).

## What Datastar does here

Datastar handles all client–server interaction. htmx handled the upload form at first and was
removed once Datastar's form mode proved enough (see ADR 0001).

| Interaction | Datastar feature | Code |
|---|---|---|
| Upload a clip | `@post('/clips/upload', {contentType: 'form', retry: 'error', retryMaxCount: 0})` on the form's `change`; `data-indicator` drives the spinner; a `datastar-fetch` listener resets the form or alerts on failure | `index.html`, `views.upload` |
| Drop a clip or region onto a track | `data-on:drop` → `@post('/timeline/drop')` | `_lanes.html`, `views.drop` |
| Select a region | `data-on:click` → `@get('/regions/<id>')` | `_lanes.html`, `views.region` |
| Edit trim, track, effects, preset | `data-bind` inputs + `data-on:input__debounce.150ms` → `@post` | `_inspector.html` |
| Split / delete | `@post(.../split)`, `@delete(...)` | `_inspector.html`, `views.split` |
| Server response | SSE `patch-elements` (re-render `#lanes`, `#inspector`), `patch-signals` (load region values into inputs), `execute-script` (`daw.refresh()`) | `views.patch_lanes`, `views.patch_inspector` |
| Client-only state | Signals: `$playhead`, `$playing`, `$selected`, `$_grab`, `$_scrubbing` | `<body data-signals>` in `index.html` |
| Playhead, transport, scrubbing | `data-text`, `data-style:left`, `data-class`, `data-on:pointer*` | `index.html` |
| Talking to the audio engine | Expressions call `daw.play()`/`stop()`/`scrub()`; the engine answers with window events picked up by `data-on:daw-tick__window` | `index.html`, `engine.js` |
| CSRF | `django-datastar` middleware + `{% datastar_csrf %}` adds `X-CSRFToken` to unsafe Datastar requests | `settings.py`, `index.html` |

The pattern: **the server owns the arrangement, the browser owns time.** Edits go to Django,
which saves them and sends back re-rendered HTML. Anything that changes 60 times a second
(the playhead, scrubbing, playback) stays in signals and the Web Audio engine and never touches
the server.

## Why Datastar

1. **Server-rendered UI with very little JavaScript.** The arrangement, inspector and library
   are Django templates. Apart from the audio engine (`engine.js`, ~190 lines, needed with any
   stack), the app's client code is short attribute expressions. There's no build step, bundler
   or client-side state store.
2. **One response can do several things.** An SSE response can carry any number of events, in
   order. A drop sends back the new lanes, the inspector's signal values, the inspector HTML and
   an instruction for the audio engine to re-sync, all in one round trip (`views.drop`). With
   htmx this would need out-of-band swaps plus a separate mechanism for client state and
   scripts.
3. **Reactive client state built in.** htmx has no client-state model. The playhead, play/stop
   button text, selection highlight and slider labels are Datastar signals with declarative
   bindings, so no Alpine.js or similar is needed alongside.
4. **Two-way binding for forms.** `data-bind` keeps the inspector's sliders, toggles, select and
   radio buttons in sync with signals. Datastar sends all signals as JSON with each request,
   and the server reads them with `read_signals(request)`. No form serialisation code needed.
5. **Morphing keeps the page stable.** `patch-elements` morphs rather than replaces, so focus
   and scroll position survive while `#lanes` re-renders under a slider being dragged.
6. **Small and self-contained.** A single script from a CDN: about 33 KB, or 13 KB gzipped.

## Pros (as experienced building this)

- **Fast to build.** Every feature (drop, select, trim, split, effects, presets) was a view that
  returns SSE events plus a template. Adding the filter presets needed no new Datastar code at
  all, just one more bound radio group.
- **Server-side logic stays in Python.** Clamping, split arithmetic, speed and timeline
  conversion, and preset validation all live in `views.py` and `models.py` and are tested with
  Django's test client.
- **A clean boundary with custom JS.** Expressions can call global functions and listen for
  custom window events, so the audio engine plugs in with four functions and two events.
- **Works with web components.** `<daw-wave>` gets its attributes updated by morphing and redraws
  itself. Keeping its canvas in shadow DOM stops morphing from deleting it.
- **CSRF is handled.** `django-datastar` attaches Django's token to unsafe requests without
  exempting views from CSRF protection. A request without the token returns 403.

## Cons and problems encountered

- **Logic in HTML attribute strings.** Expressions like the drop handler in `_lanes.html` are
  JavaScript inside attributes: no linting, type checking, autocompletion or unit tests. The
  first drag-and-drop bug (not putting data in `dataTransfer`) went unnoticed because nothing
  checks these strings. Typos fail silently at runtime.
- **Failures are silent by default.** In Datastar 1.0.4, a non-200 response (500, 403) just ends
  the request. No error event fires, and nothing is shown. The upload works around this with
  `retry: 'error', retryMaxCount: 0`, which turns the first HTTP error into a `retries-failed`
  fetch event (dispatched on `document`) without posting again. Other actions still fail
  silently. Upload failures were hard to diagnose earlier in this project for the same reason.
- **Every signal is sent on every request.** `@post('/timeline/drop')` also sends the whole
  `region` object, including all effect settings. That's fine at this size, but it grows with
  client state. Prefixing a signal with `_` keeps it local, but you have to remember to.
- **Unofficial, young Django integration.** `django-datastar` is a small community package
  (0.2.0). It required pinning **Django < 6.1**. Its CSRF bridge works by wrapping
  `window.fetch`, so it depends on Datastar internals and must be re-checked on each upgrade.
- **Recent syntax churn.** Datastar's attribute syntax changed between release candidates and
  1.0 (for example `data-on-click` → `data-on:click`). Older tutorials and examples don't
  match the current version.
- **Re-rendering whole regions of the page.** Every slider tweak re-renders all of `#lanes`.
  Cheap for a few regions, but it won't scale to hundreds. Patching single regions by ID would
  fix it, at the cost of more targeted server code.
- **Overlap with htmx (resolved).** The PoC first used htmx for the upload, which meant a second
  request model, a second CSRF setup and a second attribute vocabulary for no functional gain.
  It's now replaced by Datastar's form mode.
- **Not involved in audio.** Datastar can't help with sample-accurate or real-time work. Any DAW
  on this stack will have a substantial JS audio engine whose state must be kept in sync with
  the server's. Here that's done crudely: playback restarts after every edit, which causes an
  audible glitch.

## Future potential

- **Live collaboration** (*now prototyped, see the README's Collaboration section*). Datastar is built around long-lived SSE streams. Several people could
  open the same project, and every edit could push `patch-elements` to all of them. This fits
  Datastar better than polling or WebSocket-based alternatives.
- **Streaming server jobs.** Mixdown or export to a file, waveform peak generation, loudness
  analysis or tempo detection could run as background jobs that stream progress bars and
  results into the page as they finish.
- **Optimistic UI.** Signals could move a region instantly while dragging, with the server
  confirming or correcting it, removing the round-trip latency from the feel of editing.
- **Server-defined plugins.** The presets already show this: new effect combinations are pure
  server-side data. The same approach could deliver user-saved presets or per-project effect
  racks with no client changes.

## Obstacles to going further

- **Production serving of SSE.** The PoC's responses are short. Long-lived streams (collaboration,
  job progress) need ASGI (Daphne/Uvicorn), async views and a pub/sub layer (Redis, Django
  Channels or Postgres `LISTEN/NOTIFY`) to broadcast patches. Under WSGI each open stream ties
  up a worker.
- **Keeping the audio engine in sync.** The audio engine reads a JSON copy of the arrangement
  that's re-sent with every lanes patch. A real DAW needs incremental updates to a persistent
  Web Audio graph instead of rebuilding it. That's client-side JS work, not Datastar's.
- **Testing.** Datastar expressions can only be tested in a real browser (for example
  Playwright). That test suite doesn't exist yet. None of the browser-side behaviour in this PoC
  is covered by automated tests.
- **Dependency risk.** Relying on a small community Django package (and its Django version cap)
  needs either a contribution to keep it current or a fallback. A fallback is easy: the CSRF
  header can be added with Datastar's own `headers` option, and `datastar-py`, the official SDK,
  covers the rest.
- **Pricing and licensing.** Check which plugins are in the free bundle and which are in
  Datastar's paid Pro offering before designing around a feature.
- **Team familiarity.** The model (signals, SSE events, morphing) is unfamiliar to most
  developers, and the mix of attribute expressions and templates needs agreed conventions:
  where logic lives, which signals are local, and how errors are shown.

## Verdict

Datastar is a good fit for a DAW's **editing UI**: arranging, inspecting, presets and
persistence. It kept nearly all logic in Django with very little client code. It isn't a
solution for the **audio** side, which will always be a dedicated JS engine. The success of a
real product on this stack depends on how cleanly that engine and the server-rendered UI stay in
sync. Its strongest future argument is server-pushed updates (collaboration, background
rendering), and that's where the main infrastructure cost (ASGI plus pub/sub) also lies.
