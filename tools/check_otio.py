"""Opens our OTIO export with the REAL OpenTimelineIO library (needs Python <= 3.12 wheels):
   py -3.12 -m pip install opentimelineio ; py -3.12 tools/check_otio.py <timeline.otio>
   (create one with: python -c "import json;from app.timeline import to_otio;..."  or download GET /api/v1/sessions/{id}/timeline?format=otio)"""
import sys
import opentimelineio as otio

t = otio.adapters.read_from_file(sys.argv[1])
v, a = t.video_tracks()[0], t.audio_tracks()[0]
print(f"OTIO {otio.__version__}: video clips={len(v)}, audio clips={len(a)}, markers={len(t.tracks.markers)}")
for c in v:
    print(f"  {c.name}: source {c.source_range.start_time.to_seconds():.2f}s +{c.source_range.duration.to_seconds():.2f}s",
          [f"speed x{e.time_scalar}" for e in c.effects])
assert len(v) == len(a) and len(v) > 0
print("OK")
