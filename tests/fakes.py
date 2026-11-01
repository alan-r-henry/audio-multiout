"""Stand-ins for the COM-backed pieces, so logic can be tested without audio."""

import struct

from winaudio import Endpoint


def endpoint(device_id, name, is_default=False):
    return Endpoint(device_id, name, is_default)


class FakeRenderer:
    """Records what is written; models a device buffer of fixed size."""

    def __init__(self, buffer_frames=4096, frame_bytes=8):
        self.buffer_frames = buffer_frames
        self.frame_bytes = frame_bytes
        self.padding = 0
        self.written = bytearray()
        self.silence_frames = 0
        self.stopped = False
        self.fail_on_write = None

    def padding_frames(self):
        return self.padding

    def write(self, data):
        if self.fail_on_write:
            raise self.fail_on_write
        frames = min(len(data) // self.frame_bytes,
                     self.buffer_frames - self.padding)
        nbytes = frames * self.frame_bytes
        self.written += bytes(memoryview(data)[:nbytes])
        self.padding += frames
        return nbytes

    def write_silence(self, frames):
        frames = max(min(frames, self.buffer_frames - self.padding), 0)
        self.silence_frames += frames
        self.padding += frames
        return frames * self.frame_bytes

    def stop(self):
        self.stopped = True

    def drain(self, frames):
        self.padding = max(0, self.padding - frames)


def floats(*values):
    return struct.pack(f"<{len(values)}f", *values)


def unfloats(data):
    return list(struct.unpack(f"<{len(data) // 4}f", data))
