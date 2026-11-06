export function defaultSoundPreference(nav = navigator) {
  const ipad = /iPad/.test(nav.userAgent) || (nav.platform === 'MacIntel' && nav.maxTouchPoints > 1);
  return !ipad && !/iPhone|iPod|Android|Mobile/.test(nav.userAgent);
}

export function generationBytes(generation) {
  return Uint8Array.from(generation.replaceAll('-', '').match(/../g), hex => parseInt(hex, 16));
}

// Bump with any change to these modules: static files are served without
// cache headers, and a phone must not mix an old module with a new one.
export const VERSION = '20261009c';
const moduleUrl = name => new URL(`./${name}?v=${VERSION}`, import.meta.url);

// RFC 7587: ptime is the packet duration the receiver prefers. Opus defaults
// to 20 ms; asking for 10 ms lets the student's encoder send speech sooner.
// Leave other codecs and media sections alone, and retain Opus loss recovery.
export function lowLatencyAnswer(description) {
  return {
    type: description.type,
    sdp: description.sdp.split(/(?=^m=)/m).map(section => {
      if (!section.startsWith('m=audio ') || !/^a=rtpmap:\d+ opus\/48000\//mi.test(section)) return section;
      return section.replace(/^a=ptime:[^\r\n]*\r?\n/gm, '').replace(/\r?\n$/, '') + '\r\na=ptime:10\r\n';
    }).join(''),
  };
}

// A single tone holding most of the band's energy: a howl already under way.
export function feedbackCandidate(spectrum, sampleRate, fftSize) {
  let power = 0, peak = 0, index = -1;
  const first = Math.ceil(250 * fftSize / sampleRate);
  const last = Math.min(spectrum.length - 1, Math.floor(5000 * fftSize / sampleRate));
  for (let i = first; i <= last; i++) {
    const energy = 10 ** (spectrum[i] / 10);
    power += energy;
    if (energy > peak) { peak = energy; index = i; }
  }
  if (index < 0 || spectrum[index] < -40 || !power) return null;
  const tone = peak + 10 ** (spectrum[index - 1] / 10) + 10 ** (spectrum[index + 1] / 10);
  return tone / power >= 0.85 ? index * sampleRate / fftSize : null;
}

// Finds feedback while it is still building. A howl is a narrow peak that
// holds one frequency without fading and without harmonics of its own; the
// harmonics of speech come in families that glide with the voice's pitch. A
// sustained sung or whistled note can still trigger a (narrow, harmless) cut.
export class FeedbackGuard {
  constructor() { this.tracks = []; }

  update(spectrum, sampleRate, fftSize, now) {
    const bin = sampleRate / fftSize;
    const first = Math.max(7, Math.ceil(150 / bin));
    const last = Math.min(spectrum.length - 8, Math.floor(6000 / bin));
    let top = -Infinity;
    for (let i = first; i <= last; i++) top = Math.max(top, spectrum[i]);
    const dominant = feedbackCandidate(spectrum, sampleRate, fftSize);
    const peaks = [];
    for (let i = first; i <= last; i++) {
      const level = spectrum[i];
      if (level < -55 || level < top - 12 || level < spectrum[i - 1] || level < spectrum[i + 1]) continue;
      let around = 0;
      for (const k of [4, 5, 6]) around += spectrum[i - k] + spectrum[i + k];
      if (level - around / 6 < 15) continue;
      // A voice's harmonics come in families; a howl is a lone tone (unless
      // it is already loud enough for the limiter to add harmonics).
      const howling = dominant !== null && Math.abs(dominant - i * bin) < 2 * bin;
      if (!howling && [2, 3].some(multiple => {
        const at = i * multiple;
        return at + 1 < spectrum.length && Math.max(spectrum[at - 1], spectrum[at], spectrum[at + 1]) > level - 12;
      })) continue;
      const below = spectrum[i - 1], above = spectrum[i + 1], curve = below - 2 * level + above;
      const offset = curve < 0 ? Math.max(-0.5, Math.min(0.5, 0.5 * (below - above) / curve)) : 0;
      peaks.push({ frequency: (i + offset) * bin, level });
    }
    peaks.sort((a, b) => b.level - a.level);
    const kept = [];
    let howl = null;
    for (const peak of peaks.slice(0, 4)) {
      const index = this.tracks.findIndex(track => Math.abs(track.frequency - peak.frequency) <= Math.max(1.5 * bin, 8));
      const track = index < 0 ? { start: now, startLevel: peak.level } : this.tracks.splice(index, 1)[0];
      Object.assign(track, { frequency: peak.frequency, level: peak.level, misses: 0 });
      // An unmistakable howl is cut sooner than a merely suspicious peak.
      const needed = dominant !== null && Math.abs(dominant - peak.frequency) < 2 * bin ? 150 : 300;
      if (howl === null && now - track.start >= needed && track.level >= track.startLevel - 3) howl = track.frequency;
      else kept.push(track);
    }
    // One missed look is forgiven: a howl can dip under a loud syllable.
    for (const track of this.tracks) if (track.misses++ < 1) kept.push(track);
    this.tracks = kept;
    return howl;
  }
}

function microphoneConstraints(deviceId) {
  return {
    video: false,
    audio: {
      ...(deviceId ? { deviceId: { exact: deviceId } } : {}),
      // Browser gain control is off: it turns the room up whenever the
      // student pauses. The capture worklet levels speech instead.
      channelCount: 1, echoCancellation: true, noiseSuppression: true,
      autoGainControl: false, voiceIsolation: true, latency: { ideal: 0.01 },
    },
  };
}

// The microphones this device offers (built-in, headset, USB, Bluetooth...).
// Browsers name them only once the page has been allowed a microphone, so an
// empty list means "not known yet", not "none".
export async function listMicrophones() {
  if (!navigator.mediaDevices?.enumerateDevices) return [];
  const devices = await navigator.mediaDevices.enumerateDevices();
  const inputs = devices.filter(device => device.kind === 'audioinput' && device.label);
  // Chrome lists aliases of a real device as "default" and "communications".
  return inputs.filter(device => !['default', 'communications'].includes(device.deviceId))
    .map(device => ({ deviceId: device.deviceId, label: device.label }));
}

// Filters, feedback cuts and the capture worklet, from a microphone (or, in
// tests, any source node) to the processed signal both transports send.
export class SpeechChain {
  static load(context) {
    return context.audioWorklet.addModule(moduleUrl('microphone-capture.worklet.js'));
  }

  constructor(context, source) {
    this.context = context;
    // Speech needs roughly 120 Hz-7 kHz. Rumble, handling noise and hiss
    // outside it only add level for the loudspeakers to feed back.
    const filters = [['highpass', 120], ['highpass', 120], ['lowpass', 7000]].map(([type, frequency]) => {
      const filter = context.createBiquadFilter();
      filter.type = type; filter.frequency.value = frequency;
      return filter;
    });
    this.notches = Array.from({ length: 6 }, () => {
      const filter = context.createBiquadFilter();
      filter.type = 'peaking'; filter.Q.value = 30; filter.gain.value = 0;
      return { filter, frequency: 0, depth: 0, q: 30 };
    });
    this.analyser = context.createAnalyser();
    this.analyser.fftSize = 2048; this.analyser.smoothingTimeConstant = 0;
    this.spectrum = new Float32Array(this.analyser.frequencyBinCount);
    this.worklet = new AudioWorkletNode(context, 'classroom-capture', { outputChannelCount: [1] });
    this.input = filters[0];
    const chain = [source, ...filters, ...this.notches.map(notch => notch.filter)];
    chain.reduce((from, to) => from.connect(to));
    chain.at(-1).connect(this.worklet);
    // Watch what is left after the cuts, so a howl a cut did not tame is seen.
    chain.at(-1).connect(this.analyser);
    this.guard = new FeedbackGuard();
    this.backoff = 0;
    this.steady = 0;
    this.onFrame = null;
    this.onHowl = null;
    this.worklet.port.onmessage = ({ data }) => {
      if (data.pcm) { this.levelDb = data.levelDb; this.onFrame?.(data); }
      else if (typeof data.steady === 'number') { this.steady = data.steady; this.onHowl?.(); }
    };
  }

  // A different microphone has a different path to the loudspeakers: forget
  // the cuts made for the old one.
  reset() {
    for (const notch of this.notches) {
      Object.assign(notch, { frequency: 0, depth: 0, q: 30 });
      notch.filter.gain.setTargetAtTime(0, this.context.currentTime, 0.02);
    }
    this.guard = new FeedbackGuard();
    this.backoff = this.steady = 0;
    this.worklet.port.postMessage({ reset: true });
  }

  inspect(now) {
    this.analyser.getFloatFrequencyData(this.spectrum);
    const howl = this.guard.update(this.spectrum, this.context.sampleRate, this.analyser.fftSize, now);
    if (howl !== null) { this.suppress(howl); this.onHowl?.(); }
  }

  // Cut a howling frequency. A howl near an existing cut has slid onto its
  // skirt, so that cut gets deeper and wider rather than a neighbour being
  // added. With every notch spent, turn the whole microphone down 3 dB.
  suppress(frequency) {
    const near = this.notches.find(notch => notch.depth < 0 && Math.abs(notch.frequency - frequency) < notch.frequency / 15);
    const notch = near?.depth > -30 ? near : near ? null : this.notches.find(candidate => candidate.depth === 0);
    const now = this.context.currentTime;
    if (notch) {
      if (near) {
        notch.depth = Math.max(-30, notch.depth - 6);
        notch.frequency = (notch.frequency + frequency) / 2;
        notch.filter.frequency.setTargetAtTime(notch.frequency, now, 0.02);
        notch.q = Math.max(8, notch.q / 2);
        notch.filter.Q.setTargetAtTime(notch.q, now, 0.02);
      } else {
        notch.depth = -12;
        notch.frequency = frequency;
        notch.filter.frequency.value = frequency;
        notch.q = notch.filter.Q.value = 30;
      }
      notch.filter.gain.setTargetAtTime(notch.depth, now, 0.02);
    } else if (this.backoff > -12) {
      this.backoff -= 3;
      this.worklet.port.postMessage({ backoff: this.backoff });
    }
  }
}

export class Capture {
  static async open(onFrame, onFailure, deviceId = '') {
    const capture = new Capture();
    try {
      try {
        capture.raw = await navigator.mediaDevices.getUserMedia(microphoneConstraints(deviceId));
      } catch (error) {
        // The chosen microphone was unplugged or is out of range.
        if (!deviceId || !['OverconstrainedError', 'NotFoundError'].includes(error.name)) throw error;
        capture.raw = await navigator.mediaDevices.getUserMedia(microphoneConstraints(''));
        capture.missingDevice = true;
      }
      capture.onFailure = onFailure;
      let source;
      for (const sampleRate of [24000, undefined]) {
        capture.context = new AudioContext(sampleRate ? { latencyHint: 'interactive', sampleRate } : { latencyHint: 'interactive' });
        try {
          await capture.context.resume();
          await SpeechChain.load(capture.context);
          source = capture.source = capture.context.createMediaStreamSource(capture.raw);
          break;
        } catch (error) {
          // Some Firefox versions refuse a microphone at a rate other than the
          // device's own; the server accepts 16, 24, 44.1 and 48 kHz frames.
          capture.context.close().catch(() => {});
          if (!sampleRate || error.name !== 'NotSupportedError') throw error;
        }
      }
      capture.chain = new SpeechChain(capture.context, source);
      const worklet = capture.chain.worklet;
      capture.destination = capture.context.createMediaStreamDestination();
      const silent = capture.context.createGain(); silent.gain.value = 0;
      worklet.connect(capture.destination);
      worklet.connect(silent).connect(capture.context.destination);
      capture.worklet = worklet;
      capture.chain.onFrame = frame => onFrame(frame, capture);
      worklet.onprocessorerror = onFailure;
      capture.raw.getTracks().forEach(track => { track.onended = onFailure; });
      capture.guard = setInterval(() => capture.chain.inspect(performance.now()), 50);
      return capture;
    } catch (error) { capture.close(); throw error; }
  }

  // Swap the microphone under a running turn: the processed track that WebRTC
  // or the relay sends is unchanged, so the connection carries on.
  async useDevice(deviceId) {
    const old = this.raw;
    // iOS ends the previous capture when another starts; that is not a failure.
    old.getTracks().forEach(track => { track.onended = null; });
    let raw;
    try {
      raw = await navigator.mediaDevices.getUserMedia(microphoneConstraints(deviceId));
    } catch (error) {
      old.getTracks().forEach(track => { track.onended = this.onFailure; });
      if (old.getTracks().some(track => track.readyState === 'ended')) this.onFailure?.();
      throw error;
    }
    if (this.closed) { raw.getTracks().forEach(track => track.stop()); return; }
    const source = this.context.createMediaStreamSource(raw);
    source.connect(this.chain.input);
    this.source?.disconnect();
    old.getTracks().forEach(track => track.stop());
    this.raw = raw; this.source = source;
    raw.getTracks().forEach(track => { track.onended = this.onFailure; });
    this.chain.reset();
  }

  close() {
    this.closed = true;
    clearInterval(this.guard);
    if (this.worklet) {
      this.chain.onFrame = null;
      this.worklet.port.onmessage = null;
      this.worklet.onprocessorerror = null;
    }
    this.raw?.getTracks().forEach(track => { track.onended = null; track.stop(); });
    this.destination?.stream.getTracks().forEach(track => track.stop());
    this.context?.close().catch(() => {});
  }
}

export class RelayPlayback {
  constructor(context, destination) {
    this.context = context;
    this.epoch = 0;
    this.lastSequence = -1;
    this.discarded = 0;
    this.received = 0;
    this.stats = { buffered: 0, cushion: 0, underruns: 0, dropped: 0, playing: false };
    this.ready = context.audioWorklet.addModule(moduleUrl('microphone-playback.worklet.js')).then(() => {
      this.node = new AudioWorkletNode(context, 'classroom-playback', { numberOfInputs: 0, outputChannelCount: [1] });
      this.node.port.onmessage = event => { if (event.data.epoch === this.epoch) this.stats = event.data; };
      this.node.connect(destination);
    });
  }

  clear() {
    this.node?.port.postMessage({ clear: true, epoch: ++this.epoch });
    this.stats = { buffered: 0, maxBuffered: 0, cushion: 0, underruns: 0, dropped: 0, expired: 0, playing: false };
    this.received = 0; this.discarded = 0; this.gaps = 0; this.ageMs = null;
    this.lastSequence = -1;
  }

  play(packet, generation, serverNow) {
    if (!this.node || this.context.state !== 'running' || packet.byteLength < 32) return;
    const view = new DataView(packet);
    const token = new Uint8Array(packet, 0, 16);
    if (!generation.every((value, index) => token[index] === value)) return;
    const sequence = view.getUint32(16);
    const captured = view.getFloat64(20);
    const rate = view.getUint32(28);
    if (![16000, 24000, 44100, 48000].includes(rate) || packet.byteLength !== 32 + rate / 50 * 2 || !Number.isFinite(captured)) return;
    if (sequence <= this.lastSequence) { this.discarded++; return; }
    if (this.lastSequence >= 0) this.gaps = (this.gaps || 0) + sequence - this.lastSequence - 1;
    this.lastSequence = sequence;
    const age = serverNow - captured;
    this.ageMs = age;
    if (age > 120 || age < -100) { this.discarded++; return; }
    const samples = new Float32Array(rate / 50);
    for (let i = 0; i < samples.length; i++) samples[i] = view.getInt16(32 + i * 2, true) / 32768;
    const deadline = this.context.currentTime + (120 - Math.max(0, age)) / 1000;
    this.node.port.postMessage({ samples, rate, deadline, epoch: this.epoch }, [samples.buffer]);
    this.received++;
  }
}
