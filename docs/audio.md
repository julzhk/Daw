# Audio playback in the PoC DAW

This note explains how the PoC turns an arrangement into sound, why it's built that way, what
works, what doesn't, and what stands in the way of taking it further. All audio code is in
`studio/static/studio/engine.js` (~190 lines), which uses the browser's
[Web Audio API](https://developer.mozilla.org/en-US/docs/Web/API/Web_Audio_API). The server never
processes audio. It only stores files and reads their duration with `mutagen` on upload.

## The pieces

| Piece | What it is | Where |
|---|---|---|
| Arrangement data | A `<script id="arrangement">` JSON list, re-rendered with every `#lanes` patch. Each region has `src`, `start`, `offset`, `length`, `rate` and `chain` (a list of `[plugin, params]`). | `_lanes.html`, `Region.as_json` |
| Buffer cache | `load(url)`: fetches the whole file once, decodes it with `decodeAudioData`, and caches the resulting promise per URL | `engine.js` |
| Effect plugins | `gain`, `lowpass`, `highpass`, `delay`: each a small factory that returns `{input, output}` Web Audio nodes | `engine.js` `PLUGINS`, matching `studio/plugins.py` |
| Presets | Server-side data: a playback `rate` plus a fixed chain of the plugins above, combined into each region's `chain` | `studio/plugins.py` `PRESETS`, `Region.chain` |
| Transport API | `window.daw.play(from)`, `stop()`, `scrub(t)`, `refresh()` called from Datastar expressions | `engine.js`, `index.html` |
| Playhead feedback | `daw-tick` (position in seconds) and `daw-ended` window events, picked up by `data-on:daw-tick__window` | `engine.js`, `index.html` |
| Waveforms | `<daw-wave>` custom element: draws peaks of the visible part of a clip from the same cached buffer | `engine.js` |

## What happens when you press Play

1. The Play button's expression sets `$playing = true` and calls `daw.play($playhead)`.
2. `play()` stops any previous session and creates a session token. It creates the
   `AudioContext` if needed and resumes it (browsers only allow audio after a user gesture).
3. It reads the arrangement JSON from the page and waits for every region's buffer to be loaded
   and decoded. If Stop was pressed meanwhile, the token no longer matches and nothing plays.
4. It creates a master `GainNode` (`out`) connected to the speakers and picks a start time
   `t0` 50 ms in the future, to give the scheduler a safe margin.
5. For each region it works out how much of the region lies before the playhead, then:
   - creates an `AudioBufferSourceNode` with the region's `playbackRate`;
   - builds the effect chain (preset effects first, then enabled plugins) and connects it to `out`;
   - schedules it with `start(when, offsetInClip, durationInClip)`. Regions that start later
     are scheduled in advance, and regions entirely before the playhead are skipped.
6. A `requestAnimationFrame` loop works out the position from the **audio clock**
   (`ctx.currentTime - t0`), not the wall clock, and emits `daw-tick` each frame. Datastar
   updates `$playhead`, which moves the red line and the time readout.
7. When the position passes the end of the last region, the engine stops and emits
   `daw-ended`, which sets `$playing = false`.

**Stop** calls `stop()` on every source and disconnects `out`, which also cuts off echo tails.

### Timeline time and clip time

`offset` and `length` are measured in the clip's own time. A region takes up
`length / rate` seconds on the timeline. Slower (0.75×) makes a 3 s clip take 4 s, and Faster
(1.5×) makes it take 2 s. The engine converts positions on the timeline into the clip
(`offset + skip * rate`) when scheduling and scrubbing. The server uses the same conversion
when splitting (`views.split`), so a cut lands at the same sound you hear.

### Scrubbing

Dragging the ruler calls `daw.scrub(t)`, at most every 50 ms. For each region under the
playhead it plays an 80 ms **grain** from that point, with a 10 ms fade-in and a fade-out to
avoid clicks. It's the "tape rocking" effect at its simplest. If you were playing, the
transport stops while you drag and resumes from where you let go.

### Edits during playback

Every server response that re-renders the lanes also tells the browser to run
`daw.refresh()`. If the engine is playing, `refresh()` restarts playback from the current
position, rebuilding the whole audio graph from the new arrangement. That's how trim,
effect and preset changes become audible right away.

## Why this approach

1. **Audio has to run in the browser.** Playback, scrubbing and live effects need sound within
   milliseconds. Round-tripping to a server is out of the question, so the Web Audio API is
   the only realistic choice in a web page.
2. **Web Audio does the hard parts.** Sample-accurate scheduling, resampling, the biquad
   filters, delay lines and mixing all run natively on the browser's audio thread. The engine
   only connects built-in nodes. No custom DSP was needed for anything in this PoC.
3. **The audio engine just follows the server's data.** The engine keeps no arrangement state of
   its own. It re-reads the JSON the server rendered, so there's only one source of truth
   (the database) and nothing to reconcile.
4. **A small API to the UI.** Four functions in, two events out. The engine knows nothing
   about Datastar, and Datastar knows nothing about Web Audio.
5. **Upload, not streaming.** Whole files are uploaded and decoded once. That keeps
   scheduling exact (every sample is in memory) and matches the PoC brief.

## Pros

- **Sample-accurate timing.** Regions are scheduled on the audio clock, so tracks stay locked
  together regardless of what the page or the network is doing.
- **Smooth even if the page is busy.** Audio renders on a separate thread. If the page
  stutters, only the playhead animation suffers, not the sound.
- **Accurate playhead.** It's computed from `ctx.currentTime`, the clock that actually drives
  the audio, so it doesn't drift.
- **Effects are cheap to add.** A plugin is a factory of a few lines plus a registry entry in
  Python. Presets need no JavaScript at all.
- **Waveforms for free.** The decoded buffer used for playback is also used to draw waveforms,
  so each file is downloaded and decoded once.
- **No dependencies.** Plain browser APIs, no audio library, no build step.

## Cons and limitations

- **Edits restart playback.** `refresh()` tears down and rebuilds the whole graph, so every
  slider movement during playback causes a small audible jump. It also restarts after edits
  that change nothing audible, such as moving a region somewhere else on the timeline.
- **Every play rebuilds everything.** Each press of Play creates new source and effect nodes
  for every region, and waits for every buffer to be decoded first. That's fine for a handful
  of regions, but it gets slow for large arrangements.
- **Memory.** Decoded audio is uncompressed 32-bit float: about **21 MB per stereo minute**
  at 44.1 kHz, more at 48 kHz. Buffers are cached for the whole session and never freed, even
  after a clip is deleted.
- **No protection against clipping when mixing.** Tracks are summed with no master limiter,
  so several loud clips together can exceed full scale and distort at the output.
- **Speed changes alter pitch.** Slower and Faster use `playbackRate`, like a tape machine.
  There's no time-stretching that keeps the pitch.
- **Echo tails are cut off.** Stopping, or reaching the end of the last region, disconnects
  everything immediately, so an echo can't ring out past the end.
- **Scrubbing is basic.** Grains are dry (no effects), always play forwards, and play straight
  to the speakers.
- **No latency compensation.** The playhead shows the scheduled time, not the time sound
  actually reaches the speakers (`outputLatency`), so on high-latency devices (Bluetooth
  especially) the line runs slightly ahead of what you hear.
- **Format support depends on the browser.** The server accepts anything `mutagen` can read,
  but playback depends on the browser's `decodeAudioData`. A file can upload fine and then
  fail to play in a browser that can't decode it, with no error shown.
- **Waveforms show only one channel.** They're drawn from the first channel, on the main
  thread, by sampling roughly 64 points per pixel. That's approximate but fast.
- **The plugin registry is duplicated.** Plugin names and parameters are defined in Python and
  implemented in JS. Nothing checks that the two match.
- **Not tested by ear by me.** The engine passes a syntax check, and its server-side inputs
  (timing maths, presets, splitting) are covered by Django test-client checks, but I couldn't
  listen to playback while building it.

## Future potential

- **Glitch-free live edits.** Keep a persistent graph per region and change parameters with
  `AudioParam.setTargetAtTime()` instead of rebuilding. Effect and preset changes would then
  apply smoothly while playing. Only structural changes (moving, trimming) would need to
  reschedule a single source.
- **Export a mix.** An `OfflineAudioContext` can render the same graph faster than real time
  into a buffer and encode it as WAV in the browser. It's a small addition that reuses the
  engine almost unchanged.
- **Deterministic tests.** The same offline rendering makes audio testable: render a known
  arrangement and compare samples or levels in a headless browser.
- **Custom DSP.** `AudioWorklet` (optionally with WebAssembly) allows effects Web Audio
  doesn't provide: pitch-preserving time-stretch, reverse, distortion, compression,
  convolution reverb with impulse responses.
- **A plugin standard.** [Web Audio Modules](https://www.webaudiomodules.com/) (WAM 2) is a
  community standard for loadable web audio plugins with their own UIs. It could replace the
  hand-written registry.
- **Mixer and master bus.** Per-track gain and pan, plus a `DynamicsCompressorNode` as a
  limiter on `out`, would address the clipping risk.
- **Server-generated waveform peaks.** Computing peaks on upload (for example with numpy or
  the `audiowaveform` tool) would let the page draw waveforms without downloading and decoding
  whole files, and would allow decoding only the parts about to play.
- **Recording and MIDI.** `getUserMedia` plus `MediaRecorder` (recording) and Web MIDI
  (controllers) are available in the same API family.

## Obstacles to going further

- **Scheduling large arrangements.** Scheduling every region at once and decoding everything
  up front doesn't scale. A real DAW needs a look-ahead scheduler that creates sources a
  second or two before they're due, and on-demand decoding with buffers freed when no longer
  needed.
- **Memory limits.** Uncompressed audio in browser memory caps the size of a project,
  especially on phones and tablets. Long projects would need chunked decoding or streaming
  from the server.
- **Browser differences.** Autoplay rules, codec support and audio quirks (notably in mobile
  Safari) differ between browsers and need testing on each target.
- **Precise timing for real work.** Sample-accurate editing during playback, latency
  compensation for recording, and tight sync with MIDI are all possible in Web Audio but need
  careful engineering. This PoC does none of them.
- **Keeping server and client in agreement.** Timing maths (speed, splitting) and plugin
  definitions exist on both sides. As features grow, either shared definitions or tests that
  cross-check Python and JS will be needed to prevent drift.
- **Running heavy effects in the browser.** Complex effects in an `AudioWorklet` must finish
  within each audio block (a few milliseconds) or the sound drops out. WebAssembly helps, but
  it's specialist work.

## Verdict

The Web Audio API is fully capable of this PoC's needs: multi-track playback, trimming,
splitting, speed changes, effect chains and audible scrubbing, all with built-in nodes and
under 200 lines of code. The current engine's simplicity, which rebuilds everything on each play
and each edit, is the main thing to replace. Moving to a persistent graph with a look-ahead
scheduler is a known, well-documented path. Offline rendering for export and tests is the
cheapest next win. Memory use and browser differences, rather than raw capability, are the real
limits for larger projects.
