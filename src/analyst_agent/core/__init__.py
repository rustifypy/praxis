"""What every other package needs and none of them owns: settings, the database file, the
event wire format, the chat model, the job runner, the subprocess wrapper.

**A module belongs here only if it imports nothing from this project except its siblings.**
That is the whole membership rule, and `tests/test_layering.py` enforces it — without it
this becomes the package anything may be dropped into, and the tiers above stop meaning
anything.
"""
