from django.db import models

from .plugins import PRESETS, with_defaults


class Clip(models.Model):
    """An uploaded audio file."""

    name = models.CharField(max_length=200)
    file = models.FileField(upload_to='clips/')
    duration = models.FloatField()

    def __str__(self):
        return self.name


class Region(models.Model):
    """A placement of (part of) a clip on a track of the timeline."""

    clip = models.ForeignKey(Clip, on_delete=models.CASCADE, related_name='regions')
    track = models.PositiveSmallIntegerField(default=0)
    start = models.FloatField(default=0)  # position on the timeline, seconds
    offset = models.FloatField(default=0)  # trim-in point within the clip, seconds
    length = models.FloatField()  # trimmed length, seconds
    fx = models.JSONField(default=dict)
    preset = models.CharField(max_length=20, default='none')

    class Meta:
        ordering = ['track', 'start']

    @property
    def rate(self):
        return PRESETS.get(self.preset, PRESETS['none']).get('rate', 1)

    @property
    def span(self):
        """Time the region occupies on the timeline, after any speed change."""
        return self.length / self.rate

    @property
    def end(self):
        return self.start + self.span

    @property
    def effects(self):
        return with_defaults(self.fx)

    def chain(self):
        """Effects to apply, in order: the preset's, then the enabled plugins."""
        preset = PRESETS.get(self.preset, PRESETS['none']).get('chain', [])
        return [*preset, *((name, p) for name, p in self.effects.items() if p['on'])]

    def as_json(self):
        return {
            'id': self.id,
            'src': self.clip.file.url,
            'start': self.start,
            'offset': self.offset,
            'length': self.length,
            'rate': self.rate,
            'chain': self.chain(),
        }


class ChatMessage(models.Model):
    name = models.CharField(max_length=40)
    text = models.CharField(max_length=500)
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created']
