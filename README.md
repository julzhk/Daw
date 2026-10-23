# PoC DAW

A proof-of-concept Digital Audio Workstation, built to test whether this stack can handle
an interactive audio app: **Django + Datastar (django-datastar, datastar-py) + Tailwind CSS + daisyUI**.
htmx handled the upload at first. It has since been replaced by Datastar, so Datastar now does all
the client–server interaction.

## Run it

```console
uv sync
uv run python manage.py migrate
uv run python manage.py runserver
```

Open http://127.0.0.1:8000. Tailwind, daisyUI and Datastar load from CDNs, so there's no build step.

## User stories covered

1. **Upload a clip.** As a musician, I can choose an audio file (WAV, MP3, OGG, FLAC…) and it uploads straight away, appearing in my clip library with its duration.
2. **Arrange clips.** I can drag a clip from the library onto one of three tracks at any time position, and drag regions to move them in time or between tracks.
3. **Play the arrangement.** I can press Play to hear every region on every track mixed together, and watch the playhead move. I can press Stop or rewind to the start.
4. **Scrub.** I can drag along the ruler to move the playhead and hear the audio under it as I drag. If playback was running, it resumes from where I let go.
5. **Clip (trim and split).** I can select a region and trim its start (trim in) and its length, or split it in two at the playhead. Each region shows a waveform of the part that will play.
6. **Apply effect plugins.** For a selected region, I can turn on Gain, Low-pass, High-pass and Echo, and adjust each one's parameters. Changes are saved and heard right away, even during playback.
7. **Pick a filter preset.** I can choose one preset per region with radio buttons: None, Echo, Slower, Faster, Telephone or Underwater. Slower and Faster change the region's speed, pitch and length on the timeline.
8. **Persist.** I can reload the page and find my clips and arrangement as I left them.
9. **Chat with everyone.** I can set a display name and send messages that everyone with the page open sees straight away. I can see how many people are online.
10. **Edit together.** When someone else uploads a clip, or adds, moves, trims, splits, deletes or changes the effects on a region, my screen updates immediately. If I have that region open, my inspector reloads it.

Not covered (on purpose): streaming or recording, multiple projects, user accounts, undo, zoom, snapping, a master bus or mixer, rendering to a file, and loop playback.

## How the pieces fit

| Concern | Technology | Where |
|---|---|---|
| Upload (multipart form → library patch) | **Datastar** `@post` with `contentType: 'form'`, falling back to a plain form post without JS | `index.html` form, `views.upload` |
| Arrange / select / trim / split / effects (server round trips) | **Datastar** `@post/@get/@delete` actions, responses sent as SSE `patch-elements` / `patch-signals` via `datastar-py` | `_lanes.html`, `_inspector.html`, `views.py` |
| CSRF on Datastar requests | **django-datastar** middleware + `{% datastar_csrf %}` | `settings.py`, `index.html` |
| Client state (playhead, playing, drag) | Datastar signals | `<body data-signals>` |
| Audio playback, scrubbing, effects | Web Audio API | `static/studio/engine.js` |
| Waveforms | `<daw-wave>` custom element (shadow DOM, so morphing doesn't touch its canvas) | `engine.js` |
| Styling / components | Tailwind (browser build) + daisyUI | templates |

The server is the source of truth for the arrangement. Each edit re-renders `#lanes`, including a
`<script id="arrangement">` JSON blob that the audio engine reads, and then sends
`daw.refresh()` so the engine re-syncs if it's playing.

Effect plugins are declared once in `studio/plugins.py`, which drives the controls and the
validation. Each one has a matching Web Audio implementation under the same name in `engine.js`.
To add a plugin, add an entry in both places. Filter presets (`PRESETS` in the same file) are
pure server-side data: a playback rate plus a fixed chain of existing plugins, so adding a preset
needs no JavaScript.

## Collaboration

Each tab opens one long-lived Datastar stream on load (`data-init="@get('/events')"`, see
`views.events`). `studio/hub.py` keeps a queue per connected tab. After any change, the view
responds to the person who made it as before, and also puts the same patches on every other
tab's queue:

- **Timeline changes** send the re-rendered tracks, plus a small element patched into `#sync`
  whose `data-init` reloads the inspector only on tabs whose `$selected` is that region. The
  server doesn't need to track who is looking at what.
- **Uploads** send the new library to everyone.
- **Chat messages** are saved, then prepended to `#chat-log` on every tab.
- **Presence** (the "N online" badge) is re-sent when a tab connects or disconnects.

Each tab gets a random `cid` signal when the page renders. Every Datastar request carries it, so
the server can leave out whoever made the change.

**Limits of this proof of concept:**

- **One server process only.** The hub lives in memory. Several processes or servers would need
  a shared pub/sub (Redis, Postgres `LISTEN/NOTIFY` or Django Channels).
- **One thread per open tab.** Each stream holds a thread on `runserver` (WSGI). An ASGI server
  with async views would be needed beyond a handful of users.
- **Browser connection limit.** Over HTTP/1.1, browsers allow about 6 connections per site, and
  each tab's stream uses one. Many tabs of the same site in one browser can stall. HTTP/2 removes
  this.
- **Last write wins.** Two people editing the same region at once overwrite each other. There's
  no locking, merging or conflict warning.
- **Remote edits restart playback.** If you're playing when someone else edits, your playback
  restarts from the current position, as it does for your own edits.
- **Slow presence updates.** Closed tabs are detected by a keepalive every 5 s, so they can take
  about 10 s to drop off the online count.
- **No identity.** Display names are free text, not accounts.

## Feasibility notes

- **Expected to work, still to confirm in a browser:** Editing should feel responsive with
  server-rendered HTML and Datastar morphing (slider changes post with a 150 ms debounce). This hasn't been measured
  by hand yet.
- **The real DAW lives in the browser:** Audio has to run client-side in the Web Audio API. The
  hypermedia stack handles state, persistence and UI, and a small JS engine is unavoidable. The
  boundary here is small: four functions in, two window events out.
- **Version pin:** `django-datastar` 0.2.0 requires Django < 6.1, so this project pins Django 6.0.x.
- **daisyUI** is a CSS component library, not a set of web components. The one real web component
  here is the hand-written `<daw-wave>`.
- **Limits you'd hit next:** Each edit restarts playback, which causes a small audible glitch.
  Sample-accurate live parameter changes would mean keeping persistent Web Audio nodes instead of
  rebuilding the graph. Dragging uses native HTML5 drag-and-drop, which is coarse and gives no
  live preview.

See [docs/datastar.md](docs/datastar.md) for why the project uses Datastar, with its pros, cons, future potential and obstacles.

See [docs/audio.md](docs/audio.md) for how audio playback works, with its pros, cons, future potential and obstacles.

See [docs/adr/0001-ui-stack.md](docs/adr/0001-ui-stack.md) for the UI stack decision, comparing this project, the NiceGUI version and other options.

See [docs/adr/0002-collaboration.md](docs/adr/0002-collaboration.md) for the decisions behind the collaboration features.
