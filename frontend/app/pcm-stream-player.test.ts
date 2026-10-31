import { describe, expect, it, vi } from "vitest";
import { PcmStreamPlayer } from "./pcm-stream-player";

class FakeSource {
  buffer: AudioBuffer | null = null;
  onended: (() => void) | null = null;
  startedAt: number | null = null;
  stopped = false;
  start(at: number) { this.startedAt = at; }
  stop() { this.stopped = true; }
  connect() { return this; }
  disconnect() {}
  end() { this.onended?.(); }
}

function fakeContext() {
  const sources: FakeSource[] = [];
  const context = {
    currentTime: 1,
    destination: {},
    createBuffer: (_channels: number, frames: number, sampleRate: number) => {
      const data = new Float32Array(frames);
      return { duration: frames / sampleRate, getChannelData: () => data, data };
    },
    createBufferSource: () => {
      const source = new FakeSource();
      sources.push(source);
      return source;
    },
    getOutputTimestamp: () => ({ contextTime: 1, performanceTime: 500 }),
  };
  return { context: context as unknown as AudioContext, sources };
}

describe("PcmStreamPlayer", () => {
  it("plays valid chunks once in sequence and finishes after the final source ends", async () => {
    const { context, sources } = fakeContext();
    const started = vi.fn();
    const complete = vi.fn();
    const player = new PcmStreamPlayer(context, { onPlaybackStart: started, onPlaybackComplete: complete });
    player.start();
    const first = player.append(0, new Blob([new Uint8Array([0, 64, 0, 128])]));
    const second = player.append(1, new Blob([new Uint8Array([255, 127])]));
    player.finish();
    await Promise.all([first, second]);

    expect(sources).toHaveLength(2);
    expect(sources[0].startedAt).toBe(1.03);
    expect(sources[1].startedAt).toBeGreaterThan(sources[0].startedAt!);
    expect(started).toHaveBeenCalledTimes(1);
    expect(complete).not.toHaveBeenCalled();
    sources[0].end();
    expect(complete).not.toHaveBeenCalled();
    sources[1].end();
    expect(complete).toHaveBeenCalledTimes(1);
  });

  it("rejects missing or duplicate sequence numbers", async () => {
    const { context } = fakeContext();
    const player = new PcmStreamPlayer(context, { onPlaybackStart: vi.fn(), onPlaybackComplete: vi.fn() });
    player.start();
    await expect(player.append(1, new Blob([new Uint8Array([0, 0])]))).rejects.toThrow("sequence mismatch");
    player.stop();
  });

  it("cancels scheduled sources safely on interruption or disconnect", async () => {
    const { context, sources } = fakeContext();
    const complete = vi.fn();
    const player = new PcmStreamPlayer(context, { onPlaybackStart: vi.fn(), onPlaybackComplete: complete });
    player.start();
    await player.append(0, new Blob([new Uint8Array([0, 0])]));
    player.stop();
    expect(sources[0].stopped).toBe(true);
    expect(complete).not.toHaveBeenCalled();
  });
});
