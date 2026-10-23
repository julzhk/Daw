import queue
import uuid

import mutagen
from datastar_py.django import DatastarResponse, ServerSentEventGenerator as SSE, read_signals
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.template.loader import render_to_string
from django.views.decorators.http import require_http_methods, require_POST
from django_datastar import is_datastar

from . import hub
from .models import ChatMessage, Clip, Region
from .plugins import PLUGINS, PRESETS, with_defaults

TRACKS = 3
PPS = 50  # timeline zoom: pixels per second
MIN_LENGTH = 0.05


def lanes_context():
    regions = Region.objects.select_related('clip')
    return {
        'tracks': [(i, [r for r in regions if r.track == i]) for i in range(TRACKS)],
        'arrangement': [r.as_json() for r in regions],
        'pps': PPS,
    }


def patch_lanes():
    # Re-render the tracks, then let the audio engine re-sync if it is playing.
    return [
        SSE.patch_elements(render_to_string('studio/_lanes.html', lanes_context())),
        SSE.execute_script('daw.refresh()'),
    ]


def patch_inspector(region=None):
    html = render_to_string('studio/_inspector.html', {
        'region': region, 'plugins': PLUGINS, 'presets': PRESETS, 'tracks': range(TRACKS),
    })
    # Signals go first so the inspector's data-bind inputs pick up the values.
    if region:
        events = [SSE.patch_signals({
            'selected': region.id,
            'region': {
                'track': region.track,
                'start': region.start,
                'offset': region.offset,
                'length': region.length,
                'fx': region.effects,
                'preset': region.preset,
            },
        })]
    else:
        events = [SSE.patch_signals({'selected': 0})]
    return [*events, SSE.patch_elements(html)]


def patch_library(error=None):
    return SSE.patch_elements(render_to_string('studio/_library.html', {'clips': Clip.objects.all(), 'error': error}))


def patch_presence():
    return SSE.patch_elements(f'<span id="presence" class="badge badge-sm badge-success">{hub.count()} online</span>')


def patch_reload_if_inspecting(region_id):
    """Make clients that have this region open in the inspector re-fetch it.

    Inserts a fresh element whose data-init only fires the request when the
    client's own $selected matches; the server doesn't track who is inspecting what.
    """
    url = reverse('region', args=[region_id])
    return SSE.patch_elements(
        f'<div data-init="$selected == {region_id} && @get(\'{url}\')"></div>', selector='#sync', mode='inner',
    )


def share(request, events):
    """Send events to every other connected client (not the one making this request)."""
    hub.broadcast(events, exclude=(read_signals(request) or {}).get('cid'))


def share_timeline(request, region_id):
    share(request, [*patch_lanes(), patch_reload_if_inspecting(region_id)])


def index(request):
    return render(request, 'studio/index.html', {
        **lanes_context(),
        'cid': uuid.uuid4().hex,
        'guest_name': f'Guest {uuid.uuid4().int % 10000:04}',
        'chat_messages': ChatMessage.objects.all()[:50],
        'clips': Clip.objects.all(),
        'timeline_seconds': 60,
        'ruler_marks': range(0, 60, 5),
    })


@require_POST
def upload(request):
    """Datastar endpoint (form mode): store an uploaded file and patch the library."""
    error = None
    f = request.FILES.get('file')
    if not f:
        error = 'Choose a file first.'
    else:
        try:
            info = mutagen.File(f)
        except mutagen.MutagenError:
            info = None
        f.seek(0)
        if info is None or not getattr(info.info, 'length', 0):
            error = f'Could not read audio from {f.name}.'
        else:
            Clip.objects.create(name=f.name, file=f, duration=info.info.length)
    if not is_datastar(request):
        return redirect('index')  # plain form post, e.g. if Datastar failed to load
    if not error:
        # Form-mode requests carry no signals, so the uploader gets this too (harmlessly).
        hub.broadcast([patch_library()])
    return DatastarResponse(patch_library(error))


@require_POST
def drop(request):
    """Datastar endpoint: a clip or region was dropped onto a track."""
    signals = read_signals(request)
    kind, _, pk = str(signals.get('drag', '')).partition(':')
    if kind not in ('clip', 'region') or not pk.isdigit():
        return DatastarResponse()  # something other than a clip or region was dropped
    start = max(0.0, round(float(signals['dropAt']), 2))
    track = min(max(int(signals['dropTrack']), 0), TRACKS - 1)
    if kind == 'clip':
        clip = get_object_or_404(Clip, pk=pk)
        region = Region.objects.create(clip=clip, track=track, start=start, length=clip.duration)
    else:
        region = get_object_or_404(Region, pk=pk)
        region.track, region.start = track, start
        region.save()
    share_timeline(request, region.id)
    return DatastarResponse([*patch_lanes(), *patch_inspector(region)])


@require_http_methods(['GET', 'POST', 'DELETE'])
def region(request, pk):
    """Datastar endpoint: select (GET), edit (POST) or delete a region."""
    if request.method == 'GET':
        # The region may have been deleted by another user meanwhile.
        return DatastarResponse(patch_inspector(Region.objects.select_related('clip').filter(pk=pk).first()))
    region = get_object_or_404(Region.objects.select_related('clip'), pk=pk)
    if request.method == 'DELETE':
        region.delete()
        share_timeline(request, pk)
        return DatastarResponse([*patch_lanes(), *patch_inspector(None)])

    data = read_signals(request)['region']
    duration = region.clip.duration
    region.track = min(max(int(data['track']), 0), TRACKS - 1)
    region.start = max(0.0, float(data['start']))
    region.offset = min(max(0.0, float(data['offset'])), duration - MIN_LENGTH)
    region.length = min(max(MIN_LENGTH, float(data['length'])), duration - region.offset)
    region.fx = with_defaults(data.get('fx', {}))
    region.preset = data.get('preset') if data.get('preset') in PRESETS else 'none'
    region.save()
    share_timeline(request, region.id)
    return DatastarResponse(patch_lanes())


@require_POST
def split(request, pk):
    """Datastar endpoint: cut a region in two at the playhead."""
    region = get_object_or_404(Region, pk=pk)
    at = float(read_signals(request)['playhead'])
    if not region.start + MIN_LENGTH < at < region.end - MIN_LENGTH:
        return DatastarResponse()
    cut = (at - region.start) * region.rate  # in clip time
    Region.objects.create(
        clip=region.clip, track=region.track, start=at,
        offset=region.offset + cut, length=region.length - cut, fx=region.fx, preset=region.preset,
    )
    region.length = cut
    region.save()
    share_timeline(request, region.id)
    return DatastarResponse([*patch_lanes(), *patch_inspector(region)])


@require_POST
def chat(request):
    """Datastar endpoint: post a chat message to everyone."""
    signals = read_signals(request)
    text = str(signals.get('chatText', '')).strip()[:500]
    if text:
        name = str(signals.get('userName', '')).strip()[:40] or 'Anonymous'
        message = ChatMessage.objects.create(name=name, text=text)
        html = render_to_string('studio/_chat_message.html', {'message': message})
        # The log is flex-col-reverse, so prepending shows the newest message at the bottom.
        hub.broadcast([SSE.patch_elements(html, selector='#chat-log', mode='prepend')])
    return DatastarResponse(SSE.patch_signals({'chatText': ''}))


def events(request):
    """Datastar endpoint: one long-lived SSE stream per open tab, fed by the hub."""
    client_id = str((read_signals(request) or {}).get('cid', ''))

    def stream():
        q = hub.connect(client_id)
        try:
            hub.broadcast([patch_presence()])
            while True:
                try:
                    yield from q.get(timeout=5)
                except queue.Empty:
                    # An SSE comment; writing it is how a closed connection gets noticed.
                    yield ': keepalive\n\n'
        finally:
            hub.disconnect(client_id, q)
            hub.broadcast([patch_presence()])

    return DatastarResponse(stream())
