import { Capture, RelayPlayback, defaultSoundPreference, generationBytes, listMicrophones, lowLatencyAnswer, VERSION } from './microphone-audio.js?v=20261009c';
import { AudioDiagnostics } from './microphone-diagnostics.js?v=20261009d';

class ClassroomMicrophone {
  constructor(panel) {
    this.panel = panel;
    this.instructor = panel.dataset.instructor === 'true';
    this.user = Number(panel.dataset.user);
    this.checkbox = panel.querySelector('[data-sound]');
    this.status = panel.querySelector('[data-status]');
    this.startButton = panel.querySelector('[data-start]');
    this.stopButton = panel.querySelector('[data-stop]');
    this.state = null;
    this.sequence = 0;
    this.clockOffset = 0;
    this.bestRtt = Infinity;
    this.capture = null;
    this.peer = null;
    this.pendingStart = false;
    this.serial = 0;
    this.wantsOutput = false;
    this.outputReady = false;
    this.unloading = false;
    this.native = panel.querySelector('audio');
    this.diagnostics = new AudioDiagnostics(this, VERSION);
    if (this.checkbox) {
      let preference = defaultSoundPreference();
      try { const saved = localStorage.getItem('agora.classroomSound'); if (saved !== null) preference = saved === 'on'; } catch (_) {}
      this.checkbox.checked = preference;
      this.checkbox.addEventListener('change', () => {
        try { localStorage.setItem('agora.classroomSound', this.checkbox.checked ? 'on' : 'off'); } catch (_) {}
        if (!this.checkbox.checked) this.releaseOutput();
        this.render();
      });
      this.startButton.addEventListener('click', () => this.enableOutput());
      panel.querySelector('[data-mute]').addEventListener('click', () => this.send('mute'));
      // The iPad controls the lecture while the laptop is behind PowerPoint.
      // Keep a remote mute within reach even when the queues are scrolled.
      this.remoteMute = document.createElement('button');
      this.remoteMute.type = 'button';
      this.remoteMute.className = 'btn btn-danger';
      this.remoteMute.dataset.remoteMute = '';
      this.remoteMute.textContent = 'Mute student audio';
      this.remoteMute.setAttribute('aria-live', 'polite');
      this.remoteMute.style.cssText = 'display:none;position:fixed;right:16px;bottom:calc(16px + env(safe-area-inset-bottom, 0px));min-height:48px;z-index:1030;box-shadow:0 2px 8px #0003;';
      this.remoteMute.hidden = true;
      this.remoteMute.addEventListener('click', () => this.send('mute'));
      document.body.append(this.remoteMute);
      panel.querySelector('[data-volume]').addEventListener('input', event => {
        const volume = Number(event.target.value) / 100;
        this.native.volume = volume;
        if (this.outputGain) this.outputGain.gain.value = volume;
      });
      panel.querySelector('[data-test]').addEventListener('click', () => this.testSpeaker());
    } else {
      this.startButton.addEventListener('click', () => this.startCapture());
      this.stopButton.addEventListener('click', () => this.stopCapture(true));
      this.deviceSelect = panel.querySelector('[data-device]');
      this.deviceSelect.addEventListener('change', () => this.chooseDevice());
      navigator.mediaDevices?.addEventListener?.('devicechange', () => this.listDevices());
      this.listDevices();
    }
    window.addEventListener('pagehide', () => {
      this.unloading = true;
      clearInterval(this.diagnostics.timer);
      if (this.state?.this_output) this.releaseOutput();
      this.stopCapture(true); this.socket?.close();
    });
    // A screen locking must release a student microphone; slides on another
    // tab must be allowed to keep the instructor's sound-only page running.
    document.addEventListener('visibilitychange', () => {
      if (!this.instructor && document.hidden) this.stopCapture(true);
    });
    this.connect();
    this.heartbeat = setInterval(() => {
      if (this.state?.this_output || this.state?.this_sender) this.send('heartbeat', { ping: Date.now() });
    }, 5000);
    this.watchdog = setInterval(() => {
      // Only the speaker and the sound device get heartbeat replies. Everyone
      // else is legitimately quiet while someone speaks, so must not reconnect.
      const endpoint = this.state?.this_sender || this.state?.this_output;
      if (endpoint && Date.now() - this.lastState > 12000) {
        this.stopCapture(false); this.stopPlayback(); this.stopPeer(); this.socket?.close();
      }
    }, 1000);
    // Available for connection diagnostics and browser integration tests.
    panel.microphone = this;
  }

  connect() {
    const scheme = location.protocol === 'https:' ? 'wss:' : 'ws:';
    const socket = new WebSocket(`${scheme}//${location.host}/ws/${this.panel.dataset.course}/microphone/`);
    this.socket = socket;
    socket.binaryType = 'arraybuffer';
    socket.onopen = () => { if (this.socket === socket) this.retries = 0; };
    socket.onmessage = event => {
      if (this.socket !== socket) return;
      if (event.data instanceof ArrayBuffer) {
        if (this.state?.this_output && this.state.transport === 'relay') this.playback?.play(event.data, this.token, Date.now() + this.clockOffset);
        return;
      }
      let message;
      try { message = JSON.parse(event.data); } catch (_) { return; }
      if (message.type === 'state') this.applyState(message);
      if (message.type === 'signal') this.signal(message).catch(() => {
        if (this.state?.generation === message.generation) this.fallback();
      });
    };
    socket.onclose = event => {
      if (this.socket !== socket) return;
      this.serial++; this.pendingStart = false;
      this.stopCapture(false); this.stopPeer(); this.stopPlayback();
      this.state = null; this.render();
      this.status.textContent = event.code === 4403 ? 'Microphone access is unavailable. Sign in again or contact your instructor.' : 'Audio disconnected. Reconnecting…';
      if (this.unloading || [4400, 4403].includes(event.code)) return;
      // Back off with jitter so a server restart is not met by every phone in
      // the room reconnecting in the same instant.
      this.retries = (this.retries || 0) + 1;
      const delay = Math.min(30000, 1000 * 2 ** Math.min(this.retries - 1, 5));
      setTimeout(() => this.connect(), delay * (0.5 + Math.random()));
    };
    socket.onerror = () => { this.status.textContent = 'Audio connection interrupted.'; };
  }

  send(type, extra = {}) {
    if (this.socket?.readyState === WebSocket.OPEN) {
      this.socket.send(JSON.stringify({ type, generation: this.state?.generation, ...extra }));
      return true;
    }
    return false;
  }

  applyState(state) {
    this.lastState = Date.now();
    const rtt = typeof state.ping === 'number' ? Date.now() - state.ping : null;
    if (rtt !== null && rtt >= 0 && rtt < this.bestRtt) {
      this.bestRtt = rtt;
      this.clockOffset = state.server_time - (state.ping + Date.now()) / 2;
    } else if (this.bestRtt === Infinity) this.clockOffset = state.server_time - Date.now();
    const changed = this.state && (this.state.generation !== state.generation || this.state.transport !== state.transport);
    if (this.state?.generation !== state.generation) {
      this.diagnostics.beforeRelay = null; this.diagnostics.message = ''; this.senderDropped = 0;
    }
    const outputLost = this.state?.this_output && !state.this_output;
    const streamStopped = this.state?.streaming && !state.streaming;
    if (changed || outputLost || streamStopped) { this.stopPeer(); this.stopPlayback(); }
    this.state = state;
    this.token = generationBytes(state.generation);
    if (state.reply_to === 'start') this.pendingStart = false;
    if (this.capture && !state.this_sender && !this.pendingStart) this.stopCapture(false);
    if (this.pendingStart && !this.maySpeak()) this.stopCapture(true);
    if (state.this_output && !this.wantsOutput) this.send('release_output');
    if (state.this_sender && this.capture && state.transport === 'direct' && !this.peer) {
      this.offer().catch(() => { if (this.state?.generation === state.generation) this.fallback(); });
    }
    // Reconnect does not automatically reclaim sound from a different device.
    if (state.reply_to === 'claim_output' && !state.this_output) this.wantsOutput = false;
    if (outputLost) this.wantsOutput = false;
    // After a dropped connection, take the speakers back without a walk to the
    // laptop -- but only if no other device has claimed them meanwhile.
    if (this.wantsOutput && this.outputReady && !state.output_connected && state.reply_to !== 'claim_output'
        && this.outputContext?.state === 'running') {
      this.send('claim_output', { ping: Date.now() });
    }
    this.render();
  }

  maySpeak() {
    return this.state?.selected_user_id === this.user || this.state?.last_speaker_user_id === this.user;
  }

  render() {
    const state = this.state;
    this.startButton.disabled = !state || this.pendingStart;
    if (this.instructor) {
      this.startButton.hidden = Boolean(state?.this_output) || !this.checkbox.checked;
      this.startButton.disabled ||= !this.checkbox.checked;
      this.checkbox.disabled = !state;
      // All instructor devices render the server's audio state, including
      // mute from the iPad and a student restarting for a follow-up.
      for (const button of [this.panel.querySelector('[data-mute]'), this.remoteMute]) {
        button.disabled = !state?.streaming;
        button.textContent = state?.streaming ? 'Mute student' : state?.last_speaker_user_id ? 'Muted' : 'Microphone off';
        button.classList.toggle('btn-danger', Boolean(state?.streaming));
        button.classList.toggle('btn-default', !state?.streaming);
        button.title = state?.streaming ? 'Stop student audio on the classroom sound device' : 'The student can restart their microphone for a follow-up.';
      }
      this.remoteMute.hidden = !state?.last_speaker_user_id || !state?.output_connected || state.this_output;
      this.remoteMute.style.display = this.remoteMute.hidden ? 'none' : '';
      this.status.textContent = state?.this_output ?
        (state.streaming ? `${state.last_speaker_name} is speaking through this device.` : 'Classroom sound is ready on this device.') :
        (state?.output_connected ? (state.streaming ? `${state.last_speaker_name} is speaking · sound on another device.` : 'Sound ready on another device.') : this.checkbox.checked ? 'Tap Start classroom sound to use this device’s speakers.' : 'Sound is off on this device.');
      if (state?.selected_name && !state.streaming) this.status.textContent += ` Waiting for ${state.selected_name}.`;
      if (state?.this_output && this.outputContext && this.outputContext.state !== 'running') {
        this.startButton.hidden = false;
        this.status.textContent = 'Sound on this device was paused by the browser. Tap Start classroom sound to resume.';
      }
    } else {
      const eligible = this.maySpeak();
      const followup = state?.last_speaker_user_id === this.user;
      this.startButton.textContent = followup ? 'Restart microphone' : 'Start microphone';
      this.startButton.hidden = !eligible || Boolean(this.capture);
      this.startButton.disabled ||= !state?.output_connected;
      this.stopButton.hidden = !this.capture && !this.pendingStart;
      const feedback = this.capture && Date.now() - (this.feedbackAt || 0) < 8000;
      this.status.textContent = feedback ? 'The room speakers are feeding back into your microphone. Hold your phone closer to your mouth and away from the speakers, or choose a headset microphone.' :
        this.capture?.missingDevice ? 'Your chosen microphone was not found, so your default microphone is on. Select Stop microphone when you finish.' :
        this.capture ? 'Your microphone is on. Select Stop microphone when you finish.' :
        eligible ? (state.output_connected ?
          (followup ? 'You can restart your microphone for a follow-up until another student speaks.' : 'You’ve been chosen. You can turn on your microphone to speak.') :
          'Waiting for the instructor’s sound device. You can turn on your microphone when it is ready.') :
        'Your microphone is off. It becomes available when your instructor chooses you.';
    }
    this.panel.querySelector('[data-connection]').textContent = state?.streaming ?
      (state.transport === 'relay' ? 'Server relay' : (!state.this_output && this.instructor) || this.peer?.connectionState === 'connected' ? 'Direct audio' : 'Connecting direct audio…') : 'Idle';
    this.diagnostics.render();
  }

  async prepareOutput() {
    if (!this.outputContext) {
      this.outputContext = new AudioContext({ latencyHint: 'interactive' });
      this.outputGain = this.outputContext.createGain(); this.outputGain.gain.value = 0.5;
      this.outputGain.connect(this.outputContext.destination);
      this.playback = new RelayPlayback(this.outputContext, this.outputGain);
      // Sleep or another app can suspend audio without a word; say so rather
      // than claim a student is being heard.
      this.outputContext.onstatechange = () => { this.playback?.clear(); this.render(); };
    }
    await this.outputContext.resume();
    await this.playback.ready;
    this.outputGain.gain.value = Number(this.panel.querySelector('[data-volume]').value) / 100;
    // Native WebRTC playback is kept separate from Web Audio for Safari.
    this.native.volume = Number(this.panel.querySelector('[data-volume]').value) / 100;
    // An empty audio element's play promise can remain pending until a track
    // arrives. Prime it in this gesture without blocking the output claim.
    this.native.play().catch(() => {});
    this.outputReady = true;
  }

  async enableOutput() {
    try {
      if (!this.checkbox.checked) return;
      await this.prepareOutput();
      this.wantsOutput = true;
      this.send('claim_output', { ping: Date.now() });
    } catch (_) { this.status.textContent = 'Could not start audio. Check this device’s sound settings and try again.'; }
  }

  releaseOutput() {
    this.wantsOutput = false;
    this.stopPeer(); this.stopPlayback();
    if (this.state?.this_output) this.send('release_output');
  }

  stopPlayback() {
    this.native.pause(); this.native.srcObject = null;
    this.playback?.clear();
    this.testTone?.stop(); this.testTone = null;
  }

  stopPeer() {
    clearTimeout(this.peerTimeout);
    clearTimeout(this.disconnectTimeout);
    clearTimeout(this.channelCloseTimeout);
    this.diagnostics.resetPeer();
    if (this.audioChannel) { this.audioChannel.onmessage = this.audioChannel.onclose = null; this.audioChannel.close(); this.audioChannel = null; }
    if (this.peer) {
      this.peer.ontrack = null; this.peer.onicecandidate = null; this.peer.onconnectionstatechange = null;
      this.peer.close(); this.peer = null;
    }
    this.candidates = [];
  }

  preferredDevice() {
    try { return localStorage.getItem('agora.classroomMicrophone') || ''; } catch (_) { return ''; }
  }

  async listDevices() {
    let devices = [];
    try { devices = await listMicrophones(); } catch (_) {}
    const select = this.deviceSelect;
    // Shown even with a single microphone, so a student can see what this
    // device offers; earbuds or a headset appear here once connected. Names
    // are unknown until a microphone has been allowed.
    select.parentElement.hidden = !devices.length;
    if (!devices.length) return;
    const wanted = this.capture?.deviceId ?? this.preferredDevice();
    select.replaceChildren(new Option('Default microphone', ''),
      ...devices.map(device => new Option(device.label, device.deviceId)));
    select.value = devices.some(device => device.deviceId === wanted) ? wanted : '';
  }

  async chooseDevice() {
    const deviceId = this.deviceSelect.value;
    try { localStorage.setItem('agora.classroomMicrophone', deviceId); } catch (_) {}
    const capture = this.capture;
    if (!capture) return;
    this.deviceSelect.disabled = true;
    try {
      await capture.useDevice(deviceId);
      capture.deviceId = deviceId;
      this.feedbackAt = 0; this.render();
    } catch (_) {
      if (this.capture === capture) this.status.textContent = 'Could not switch to that microphone. Your previous microphone is still on.';
    } finally {
      this.deviceSelect.disabled = false;
    }
  }

  noticeFeedback() {
    this.feedbackAt = Date.now();
    this.diagnostics.upload('feedback');
    this.render();
    clearTimeout(this.feedbackTimer);
    this.feedbackTimer = setTimeout(() => this.render(), 8000);
  }

  async startCapture() {
    if (this.pendingStart || this.capture || !this.maySpeak() || !this.state.output_connected) return;
    const serial = ++this.serial;
    const generation = this.state.generation;
    this.pendingStart = true; this.render();
    try {
      const deviceId = this.preferredDevice();
      const capture = await Capture.open((frame, owner) => this.frame(frame, owner), () => {
        this.stopCapture(true); this.status.textContent = 'Microphone interrupted. Select Start microphone to try again.';
      }, deviceId);
      capture.deviceId = capture.missingDevice ? '' : deviceId;
      capture.chain.onHowl = () => { if (this.capture === capture) this.noticeFeedback(); };
      if (serial !== this.serial || generation !== this.state?.generation || document.hidden) {
        capture.close(); this.pendingStart = false; this.render(); return;
      }
      this.capture = capture;
      this.feedbackAt = 0;
      if (!this.send('start', { ping: Date.now() })) this.stopCapture(false);
      // Names of the microphones become available once one is allowed.
      this.listDevices();
    } catch (error) {
      if (serial !== this.serial) return;
      this.pendingStart = false; this.render();
      this.status.textContent = error.name === 'NotAllowedError' ? 'Microphone permission was denied. Allow it in your browser and try again.' :
        !window.isSecureContext ? 'Microphones require a secure HTTPS connection.' : 'Could not open your microphone. Check browser permissions and try again.';
    }
  }

  stopCapture(tellServer) {
    // Every student's tab switch and screen lock comes through here; only a
    // page that had (or was asking for) the microphone has anything to stop.
    const active = Boolean(this.capture || this.pendingStart || this.state?.this_sender);
    if (active) this.diagnostics.upload('stop');
    this.serial++; this.pendingStart = false;
    this.capture?.close(); this.capture = null;
    this.stopPeer();
    if (tellServer && active) this.send('stop');
    this.render();
  }

  frame(frame, capture) {
    if (capture === this.capture) capture.frameLagMs = Math.max(0, (capture.context.currentTime - frame.time) * 1000 - 20);
    if (capture !== this.capture || !this.state?.this_sender) return;
    const direct = this.state.transport === 'direct';
    const channel = direct ? this.audioChannel : this.socket;
    if (direct ? channel?.readyState !== 'open' : channel?.readyState !== WebSocket.OPEN) return;
    // A stalled main thread must not send the worklet messages it missed.
    if (capture.frameLagMs > 40 || channel.bufferedAmount > 2000) {
      this.senderDropped = (this.senderDropped || 0) + 1;
      if (!direct && channel.bufferedAmount > 8192) {
        this.diagnostics.upload('send_backlog'); this.stopCapture(true); this.socket.close();
      }
      return;
    }
    const packet = new ArrayBuffer(32 + frame.pcm.byteLength);
    new Uint8Array(packet, 0, 16).set(this.token);
    const view = new DataView(packet);
    view.setUint32(16, this.sequence++);
    view.setFloat64(20, Date.now() + this.clockOffset - Math.max(0, (capture.context.currentTime - frame.time) * 1000));
    view.setUint32(28, capture.context.sampleRate);
    new Uint8Array(packet, 32).set(new Uint8Array(frame.pcm));
    try { channel.send(packet); } catch (_) { this.senderDropped = (this.senderDropped || 0) + 1; }
  }

  attachAudio(channel, peer) {
    if (this.audioChannel || channel.label !== 'agora-pcm-v1' || channel.ordered || channel.maxRetransmits !== 0) { channel.close(); return; }
    this.audioChannel = channel; channel.binaryType = 'arraybuffer';
    const current = () => this.peer === peer && this.audioChannel === channel && this.state?.transport === 'direct';
    channel.onopen = () => { if (current()) { clearTimeout(this.peerTimeout); this.diagnostics.upload('connected'); this.render(); } };
    channel.onmessage = ({data}) => {
      if (current() && this.state.this_output && this.wantsOutput && data instanceof ArrayBuffer) {
        this.playback?.play(data, this.token, Date.now() + this.clockOffset);
      }
    };
    channel.onclose = () => {
      // A normal stop closes media before its control update reaches the peer.
      this.channelCloseTimeout = setTimeout(() => { if (current()) this.fallback('channel_closed'); }, 500);
    };
  }

  makePeer() {
    const generation = this.state.generation;
    const peer = new RTCPeerConnection({ iceServers: [] });
    this.peer = peer; this.candidates = [];
    const current = () => this.peer === peer && this.state?.generation === generation && this.state.transport === 'direct';
    peer.ondatachannel = event => {
      if (!current() || !this.state.this_output) { event.channel.close(); return; }
      if (event.channel.label === 'agora-pcm-v1') this.attachAudio(event.channel, peer);
      else this.diagnostics.attach(event.channel, peer);
    };
    peer.onicecandidate = event => {
      if (current() && event.candidate) this.send('signal', { signal: { candidate: event.candidate.toJSON() } });
    };
    // An old browser tab offers an RTP track: use the common PCM relay,
    // never run native playback alongside the new bounded PCM player.
    peer.ontrack = () => { if (current()) this.fallback('legacy_peer'); };
    peer.onconnectionstatechange = () => {
      if (!current()) return;
      if (peer.connectionState === 'connected') { clearTimeout(this.disconnectTimeout); }
      if (peer.connectionState === 'failed') this.fallback('connection_failed');
      if (peer.connectionState === 'disconnected') this.disconnectTimeout = setTimeout(() => { if (current() && peer.connectionState !== 'connected') this.fallback('disconnected'); }, 1500);
      this.render();
    };
    // Peers on one network connect in about a second. Waiting longer only
    // leaves the room in silence; the server remembers a fallback, so later
    // turns on this output start on the relay.
    this.peerTimeout = setTimeout(() => { if (current() && this.audioChannel?.readyState !== 'open') this.fallback('connect_timeout'); }, 3000);
    return peer;
  }

  async offer() {
    const peer = this.makePeer();
    this.attachAudio(peer.createDataChannel('agora-pcm-v1', { ordered: false, maxRetransmits: 0 }), peer);
    this.diagnostics.attach(peer.createDataChannel('agora-audio-status', { ordered: false, maxRetransmits: 0 }), peer);
    const offer = await peer.createOffer();
    if (this.peer !== peer) return;
    await peer.setLocalDescription(offer);
    if (this.peer === peer) this.send('signal', { signal: { description: peer.localDescription.toJSON() } });
  }

  async signal(message) {
    if (message.generation !== this.state?.generation || this.state.transport !== 'direct') return;
    if (!this.state.this_output && !this.state.this_sender) return;
    const signal = message.signal;
    let peer = this.peer;
    if (!peer) {
      if (!this.state.this_output || !this.wantsOutput || !this.outputReady) return;
      peer = this.makePeer();
    }
    if (signal.candidate) {
      if (peer.remoteDescription) await peer.addIceCandidate(signal.candidate);
      else this.candidates.push(signal.candidate);
      return;
    }
    if (signal.description) {
      if (this.state.this_output && signal.description.type !== 'offer') return;
      if (this.state.this_sender && signal.description.type !== 'answer') return;
      await peer.setRemoteDescription(signal.description);
      if (this.peer !== peer) return;
      for (const candidate of this.candidates.splice(0)) await peer.addIceCandidate(candidate);
      if (signal.description.type === 'offer') {
        const answer = await peer.createAnswer();
        try {
          await peer.setLocalDescription(lowLatencyAnswer(answer));
        } catch (_) {
          // Keep audio working in browsers that reject a packet-time preference.
          if (this.peer !== peer) return;
          await peer.setLocalDescription(answer);
        }
        if (this.peer === peer) this.send('signal', { signal: { description: peer.localDescription.toJSON() } });
      }
    }
  }

  fallback(reason = 'manual_relay') {
    if (this.state?.transport !== 'direct' || (!this.state.this_sender && !this.state.this_output)) return;
    this.diagnostics.upload(reason);
    this.diagnostics.message = 'Switched to the server relay.';
    if (this.state.this_output) this.diagnostics.beforeRelay = { ...this.diagnostics.metrics };
    // Stop the old path synchronously, before requesting the other transport.
    this.stopPeer(); this.stopPlayback(); this.send('relay');
  }

  async testSpeaker() {
    if (!this.state?.this_output) { this.status.textContent = 'Start classroom sound on this device before testing its speakers.'; return; }
    await this.prepareOutput();
    this.testTone?.stop();
    const tone = this.outputContext.createOscillator();
    const gain = this.outputContext.createGain(); gain.gain.value = 0.08;
    tone.frequency.value = 440;
    tone.connect(gain).connect(this.outputGain);
    this.testTone = tone;
    tone.onended = () => { tone.disconnect(); gain.disconnect(); if (this.testTone === tone) this.testTone = null; };
    tone.start(); tone.stop(this.outputContext.currentTime + 0.3);
  }
}

document.querySelectorAll('[data-classroom-microphone]').forEach(panel => new ClassroomMicrophone(panel));
