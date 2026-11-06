"""Simulated-room checks for the classroom microphone's audio processing.

Requires optional `playwright` + Chromium (see classroom_microphone_browser.py).
Run: python tests/classroom_microphone_acoustics.py

Renders the real processing chain (filters, feedback cuts, gate, level control
and limiter, plus the relay's playback buffer) in an OfflineAudioContext with
synthetic speech, room noise, and a loudspeaker path that feeds the output back
into the microphone through a resonant room. A model of a room is not a room:
this guards against regressions, and real acoustic trials are still required.
"""

import asyncio
import functools
import http.server
import os
from pathlib import Path
import threading

STATIC = Path(__file__).resolve().parents[1] / "peer_lecture" / "static"

SIMULATION = r"""
async ({ seconds, loopDb, speechDb, guard, speech }) => {
  const { SpeechChain } = await import('/lecture/microphone-audio.js');
  const rate = 24000;
  const context = new OfflineAudioContext(1, seconds * rate, rate);
  await SpeechChain.load(context);
  // Voiced speech: harmonics of a pitch that glides, in 4 Hz syllables with pauses.
  const voice = context.createBuffer(1, seconds * rate, rate);
  const samples = voice.getChannelData(0);
  let phase = 0;
  for (let i = 0; i < samples.length; i++) {
    const t = i / rate;
    const pitch = 120 + 30 * Math.sin(2 * Math.PI * 0.7 * t) + 10 * Math.sin(2 * Math.PI * 3.1 * t);
    phase += 2 * Math.PI * pitch / rate;
    let value = 0;
    for (let h = 1; h <= 25; h++) value += Math.sin(h * phase) / h;
    const syllable = Math.max(0, Math.sin(2 * Math.PI * 4 * t)) ** 0.5;
    const talking = speech && t > 1 && (t % 4) < 2.5;
    samples[i] = talking ? value * syllable : 0;
  }
  const noise = context.createBuffer(1, seconds * rate, rate);
  const hiss = noise.getChannelData(0);
  let brown = 0, seed = 123456;
  const random = () => { seed = (1664525 * seed + 1013904223) >>> 0; return seed / 4294967296; };
  for (let i = 0; i < hiss.length; i++) { brown = 0.97 * brown + 0.03 * (random() * 2 - 1); hiss[i] = brown * 4; }
  const decibels = value => 10 ** (value / 20);
  const talk = context.createBufferSource(); talk.buffer = voice;
  const talkLevel = context.createGain(); talkLevel.gain.value = decibels(speechDb) / 0.5;
  const room = context.createBufferSource(); room.buffer = noise;
  const roomLevel = context.createGain(); roomLevel.gain.value = decibels(-52) / 0.25;
  const microphone = context.createGain();
  talk.connect(talkLevel).connect(microphone);
  room.connect(roomLevel).connect(microphone);
  const chain = new SpeechChain(context, microphone);
  let maxGainDb = -Infinity; chain.onFrame = frame => { maxGainDb = Math.max(maxGainDb, frame.levelDb); };
  // Loudspeakers: 40 ms of network and air, a room that rings near 1.3 kHz.
  const air = context.createDelay(1); air.delayTime.value = 0.04;
  const resonance = context.createBiquadFilter();
  resonance.type = 'peaking'; resonance.frequency.value = 1300; resonance.Q.value = 8; resonance.gain.value = 12;
  const loop = context.createGain(); loop.gain.value = decibels(loopDb - 12);
  chain.worklet.connect(air).connect(resonance).connect(loop).connect(microphone);
  chain.worklet.connect(context.destination);
  for (let t = 0.05; t < seconds; t += 0.05) {
    context.suspend(t).then(() => { if (guard) chain.inspect(t * 1000); context.resume(); });
  }
  talk.start(); room.start();
  const out = (await context.startRendering()).getChannelData(0);
  const level = (from, to) => {
    let power = 0;
    for (let i = Math.round(from * rate); i < Math.round(to * rate); i++) power += out[i] * out[i];
    return 10 * Math.log10(power / ((to - from) * rate) + 1e-12);
  };
  const series = [];
  for (let t = 0; t + 0.5 <= seconds; t += 0.5) series.push(level(t, t + 0.5));
  return {
    maxGainDb, peakDb: Math.max(...series), series: series.map(Math.round), loudestDb: Math.max(...series.slice(6)), finallyDb: level(seconds - 3, seconds),
    quietestPauseDb: Math.min(...series.slice(-6)), speakingDb: level(8.5, 10),
    notches: chain.notches.filter(n => n.depth < 0).map(n => [Math.round(n.frequency), n.depth]),
    turnedDown: chain.backoff + chain.steady,
  };
}
"""

PLAYBACK = r"""
async () => {
  const { RelayPlayback, generationBytes } = await import('/lecture/microphone-audio.js');
  const context = new AudioContext({ sampleRate: 48000 }); await context.resume();
  const mute = context.createGain(); mute.gain.value = 0; mute.connect(context.destination);
  const playback = new RelayPlayback(context, mute); await playback.ready;
  const token = generationBytes('12345678-1234-1234-1234-123456789012');
  let sequence = 0;
  const packet = () => {
    const bytes = new ArrayBuffer(992), view = new DataView(bytes);
    new Uint8Array(bytes, 0, 16).set(token); view.setUint32(16, sequence++);
    view.setFloat64(20, Date.now()); view.setUint32(28, 24000);
    for (let i = 0; i < 480; i++) view.setInt16(32 + 2 * i, 3000 * Math.sin(i / 5), true);
    return bytes;
  };
  const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
  // Steady frames, then a Wi-Fi stall that delivers 100 ms at once.
  const start = performance.now();
  let sent = 0;
  const pace = async until => {
    while (performance.now() - start < until) {
      while (sent * 20 < performance.now() - start) { playback.play(packet(), token, Date.now()); sent++; }
      await sleep(5);
    }
  };
  await pace(1000);
  const steady = playback.stats.buffered;
  await sleep(100);
  const burstAt = performance.now() - start;
  await pace(burstAt + 100);
  const afterBurst = playback.stats.buffered;
  await pace(burstAt + 4000);
  const recovered = playback.stats.buffered;
  const result = { steady, afterBurst, recovered, cushion: playback.stats.cushion, underruns: playback.stats.underruns };
  await context.close();
  return result;
}
"""


def serve():
    class Quiet(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass
    handler = functools.partial(Quiet, directory=str(STATIC))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


async def run(port):
    from playwright.async_api import async_playwright
    async with async_playwright() as playwright:
        chromium = await playwright.chromium.launch(
            executable_path=os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE"), headless=True,
            args=["--autoplay-policy=no-user-gesture-required"],
        )
        page = await chromium.new_page()
        await page.goto(f"http://127.0.0.1:{port}/lecture/microphone.css")

        async def simulate(**options):
            return await page.evaluate(SIMULATION, {"seconds": 12, "loopDb": 3, "speechDb": -30, "guard": True, "speech": True, **options})

        clean = await simulate(loopDb=-60)
        print("speech, no loudspeaker path:", clean)
        assert not clean["notches"] and clean["turnedDown"] == 0, "speech alone must not be cut"
        assert clean["speakingDb"] - clean["quietestPauseDb"] > 35, "room noise between phrases must be gated"
        assert -34 < clean["speakingDb"] < -25, "quiet speech remains audible without automatic boost"
        assert clean["maxGainDb"] <= 0, "automatic gain must never amplify quiet speech or speaker return"
        for loop in (3, 20):
            unguarded = await simulate(loopDb=loop, guard=False)
            guarded = await simulate(loopDb=loop)
            print(f"loudspeakers {loop} dB past the howl point, no guard:", unguarded)
            print(f"loudspeakers {loop} dB past the howl point, guarded:", guarded)
            assert unguarded["peakDb"] > -10, "the model should produce feedback without the guard"
            assert any(abs(frequency - 1300) < 120 for frequency, _ in guarded["notches"]), guarded
            if loop == 3:
                assert guarded["peakDb"] < -15, "mild feedback must not get going"
                assert guarded["peakDb"] < unguarded["peakDb"] - 6
                continue
            # Mild feedback never gets going; extreme feedback is brought under
            # control within seconds (when exactly varies with the noise).
            assert guarded["loudestDb"] < unguarded["loudestDb"] - 10, guarded
            assert guarded["finallyDb"] < -35, guarded
        assert guarded["turnedDown"] < 0, "a howl no notch can fix turns the microphone down"
        print("PASS: speech kept, noise gated between phrases, room howl found and cut")
        relay = await page.evaluate(PLAYBACK)
        print("relay buffer (ms):", relay)
        assert relay["afterBurst"] <= 60, relay
        # A stall may cause a gap, but must not grow the queue or cushion.
        assert relay["recovered"] <= 60 and relay["cushion"] == 0, "a burst must not increase the playback cushion"
        print("PASS: relay playback catches up after a Wi-Fi burst")
        await chromium.close()


def main():
    server = serve()
    try:
        asyncio.run(run(server.server_address[1]))
    finally:
        server.shutdown()


if __name__ == "__main__":
    main()
