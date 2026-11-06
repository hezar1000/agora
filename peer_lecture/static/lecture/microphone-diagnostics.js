// Bounded timing summaries are logged by the server; never audio or device IDs.
// Browser counters describe parts of the path, never mouth-to-speaker delay.
const finite = value => typeof value === 'number' && Number.isFinite(value) && value >= 0;
const ms = value => finite(value) ? `${Math.round(value)} ms` : 'not reported';

export class AudioDiagnostics {
  constructor(microphone, version) {
    this.mic = microphone; this.version = version;
    this.version = version;
    this.remote = null;
    this.metrics = {};
    this.message = '';
    this.health = document.createElement('p');
    this.health.dataset.audioHealth = '';
    this.health.style.fontWeight = '600';
    this.details = document.createElement('details');
    const summary = document.createElement('summary'); summary.textContent = 'Audio details';
    this.text = document.createElement('pre');
    this.text.dataset.audioDetails = '';
    this.text.style.cssText = 'white-space:pre-wrap;font-size:12px;margin:8px 0;';
    const copy = document.createElement('button');
    copy.type = 'button'; copy.className = 'btn btn-default'; copy.textContent = 'Copy audio details';
    copy.onclick = async () => {
      try { await navigator.clipboard.writeText(this.text.textContent); copy.textContent = 'Copied'; }
      catch (_) { copy.textContent = 'Select and copy the details above'; }
    };
    this.relayButton = document.createElement('button');
    this.relayButton.type = 'button'; this.relayButton.className = 'btn btn-default';
    this.relayButton.textContent = 'Try server relay';
    this.relayButton.style.marginLeft = '8px';
    this.relayButton.title = 'Use this if direct audio sounds delayed. The student microphone stays on.';
    this.relayButton.onclick = () => {
      if (!this.mic.state?.this_output || this.mic.state.transport !== 'direct') return;
      this.message = 'Trying the server relay to bypass direct audio buffering.';
      this.mic.fallback();
    };
    this.details.append(summary, this.text, copy, this.relayButton);
    microphone.panel.querySelector('[data-diagnostics]').append(this.health, this.details);
    this.timer = setInterval(() => this.tick(), 1000);
    this.render();
  }

  resetPeer() {
    if (this.channel) { this.channel.onmessage = null; this.channel.close(); }
    this.channel = null;
    this.remote = null;
    this.metrics = {};
    this.playoutPrevious = null;
  }

  attach(channel, peer) {
    if (channel.label !== 'agora-audio-status' || this.channel) { channel.close(); return; }
    this.channel = channel;
    channel.onmessage = event => {
      if (peer !== this.mic.peer || !this.mic.state?.this_output || typeof event.data !== 'string' || event.data.length > 2048) return;
      if (Date.now() - (this.remote?.at || 0) < 250) return;
      let data;
      try { data = JSON.parse(event.data); } catch (_) { return; }
      if (!data || typeof data !== 'object') return;
      // Treat the student's report as untrusted, including for automatic recovery:
      // only local receiver statistics may trigger a transport switch.
      this.remote = {};
      for (const key of ['captureMs', 'graphMs', 'frameLagMs', 'sampleRate', 'cuts', 'reductionDb']) {
        this.remote[key] = finite(data[key]) && data[key] <= 100000 ? data[key] : null;
      }
      this.remote.feedback = data.feedback === true;
      this.remote.browser = typeof data.browser === 'string' ? data.browser.slice(0, 300) : 'not reported';
      this.remote.at = Date.now();
      this.render();
    };
  }

  captureReport() {
    const capture = this.mic.capture;
    if (!capture) return null;
    const settings = capture.raw.getAudioTracks()[0]?.getSettings() || {};
    return {
      captureMs: finite(settings.latency) ? settings.latency * 1000 : null,
      graphMs: capture.context.baseLatency * 1000,
      frameLagMs: capture.frameLagMs ?? null,
      sampleRate: capture.context.sampleRate,
      cuts: capture.chain.notches.filter(notch => notch.depth < 0).length,
      levelDb: capture.chain.levelDb,
      reductionDb: -Math.max(-12, capture.chain.backoff + capture.chain.steady),
      feedback: Date.now() - (this.mic.feedbackAt || 0) < 8000,
      browser: navigator.userAgent,
    };
  }

  async tick() {
    const mic = this.mic;
    if (this.busy || mic.unloading) return;
    this.busy = true;
    try {
      const report = this.captureReport();
      if (report && this.channel?.readyState === 'open' && this.channel.bufferedAmount < 2048) {
        this.channel.send(JSON.stringify(report));
      }
      const peer = mic.peer;
      if (peer && mic.state?.this_output && peer.connectionState === 'connected') {
        const stats = await peer.getStats();
        if (mic.peer !== peer || mic.unloading) return;
        const values = Array.from(stats.values());
        const transport = values.find(stat => stat.type === 'transport' && stat.selectedCandidatePairId);
        const pair = transport && stats.get(transport.selectedCandidatePairId);
        this.metrics = {
          bufferMs: mic.playback?.stats.buffered ?? null,
          roundTripMs: finite(pair?.currentRoundTripTime) ? pair.currentRoundTripTime * 1000 : null,
        };

      }
    } catch (_) {
      // Missing statistics or a closing peer must never interrupt working audio.
    } finally { this.busy = false; this.upload('periodic'); this.render(); }
  }

  upload(event) {
    const mic = this.mic, now = Date.now();
    if (!mic.state?.diagnostics || !mic.state.streaming || (!mic.state.this_sender && !mic.state.this_output)) return;
    if (now - (this.lastUpload || 0) < (event === 'periodic' ? 5000 : 1000)) return;
    const capture = this.captureReport();
    const playback = mic.state.this_output ? mic.playback : null;
    const ua = navigator.userAgent;
    const report = {
      version: this.version, event,
      browser: /Firefox/.test(ua) ? 'firefox' : /Edg/.test(ua) ? 'edge' : /Chrome|CriOS/.test(ua) ? 'chromium' : /Safari/.test(ua) ? 'safari' : 'other',
      context: (mic.capture?.context || mic.outputContext)?.state,
      hidden: document.hidden,
      bufferMs: playback?.stats.buffered, peakBufferMs: playback?.stats.maxBuffered,
      frameAgeMs: playback?.ageMs, roundTripMs: this.metrics.roundTripMs,
      clockRttMs: finite(mic.bestRtt) ? mic.bestRtt : null,
      captureMs: capture?.captureMs, graphMs: capture?.graphMs, frameLagMs: capture?.frameLagMs,
      outputMs: mic.outputContext?.outputLatency * 1000,
      received: playback?.received, discarded: playback?.discarded, gaps: playback?.gaps,
      underruns: playback?.stats.underruns, skipped: playback?.stats.dropped, expired: playback?.stats.expired,
      levelDb: capture?.levelDb,
      senderDropped: mic.senderDropped || 0, cuts: capture?.cuts, reductionDb: capture?.reductionDb,
      sendQueueBytes: mic.audioChannel?.bufferedAmount ?? mic.socket?.bufferedAmount,
    };
    if (mic.socket?.bufferedAmount > 4096) return;
    if (mic.send('diagnostics', {report})) this.lastUpload = now;
  }

  render() {
    const mic = this.mic;
    const active = mic.state?.streaming && (mic.state.this_output || mic.state.this_sender);
    this.health.hidden = this.details.hidden = !active;
    if (!active) { this.message = ''; this.beforeRelay = null; return; }
    const relay = mic.state.transport === 'relay';
    this.relayButton.hidden = !mic.state.this_output || relay;
    const buffer = mic.playback?.stats.buffered;
    const capture = this.captureReport() || (this.remote && Date.now() - this.remote.at < 5000 ? this.remote : null);
    const feedback = capture?.feedback;
    const type = relay ? 'Server relay' : 'Direct audio';
    // A recovery notice must not hide the new transport's live readings.
    this.health.textContent = `${type}${mic.state.this_output ? ` · Receiving buffer: ${ms(buffer)}` : ''}`;
    if (this.message) this.health.textContent += ` · ${this.message}`;
    if (feedback) this.health.textContent += ' · Feedback detected: lower the speaker volume and move the microphone closer to your mouth.';
    const lines = [
      `Audio version: ${this.version}`,
      `Connection: ${type}`,
      `Receiving buffer: ${mic.state.this_output ? ms(buffer) : 'shown on the sound device'}`,
      `Direct network round trip: ${ms(this.metrics.roundTripMs)}`,
      `Received frame age (clock estimate): ${ms(mic.playback?.ageMs)}`,
      `Skipped frames: ${mic.playback?.discarded || 0}; missing frames: ${mic.playback?.gaps || 0}`,
      `Playback gaps: ${mic.playback?.stats.underruns || 0}; backlog skips: ${mic.playback?.stats.dropped || 0}`,
      `AudioContext output delay (browser estimate): ${ms(mic.outputContext?.outputLatency * 1000)}`,
      `Microphone capture delay (browser estimate): ${ms(capture?.captureMs)}`,
      `Microphone processing buffer: ${ms(capture?.graphMs)}`,
      `Microphone frame delivery delay: ${ms(capture?.frameLagMs)}`,
      `Microphone processing rate: ${capture?.sampleRate ?? 'not reported'} Hz`,
      `Feedback cuts: ${capture?.cuts ?? 'not reported'}; gain reduction: ${capture?.reductionDb ?? 'not reported'} dB`,
      `Student browser: ${capture?.browser ?? 'not reported (reload both devices to share direct-audio diagnostics)'}`,
      `This browser: ${navigator.userAgent}`,
      'These are separate parts of the audio path, not a measured total. Unknown values are not zero. Hardware and room delay may be unreported.',
      mic.state.diagnostics ? 'Timing and connection summaries are saved for troubleshooting. No speech, microphone names, or device IDs are recorded.' : 'This server does not yet save audio diagnostics.',
    ];
    if (relay && this.beforeRelay) lines.splice(3, 0,
      `Before relay switch — receiving buffer: ${ms(this.beforeRelay.bufferMs)}; network round trip: ${ms(this.beforeRelay.roundTripMs)}`);
    this.text.textContent = lines.join('\n');
  }
}
