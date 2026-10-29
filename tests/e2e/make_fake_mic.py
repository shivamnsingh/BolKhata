"""Write a synthetic, looping 'microphone' WAV: 1 s silence, 2 s speech-like tone, 7 s silence."""
from __future__ import annotations

import math
import struct
import sys
import wave

RATE = 48000


def build(path: str, loops: int = 8) -> None:
    frames = bytearray()
    for _ in range(loops):
        frames += b"\x00\x00" * int(RATE * 1.0)
        for index in range(int(RATE * 2.0)):
            t = index / RATE
            envelope = 0.55 + 0.45 * math.sin(2 * math.pi * 3 * t)  # syllable-like modulation
            value = envelope * (0.35 * math.sin(2 * math.pi * 180 * t) + 0.2 * math.sin(2 * math.pi * 540 * t))
            frames += struct.pack("<h", int(value * 32767))
        frames += b"\x00\x00" * int(RATE * 7.0)
    with wave.open(path, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(RATE)
        handle.writeframes(bytes(frames))


if __name__ == "__main__":
    build(sys.argv[1])
