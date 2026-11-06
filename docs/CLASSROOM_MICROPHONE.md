# Classroom microphone

The idea and initial prototype are by [Guolin Wang](https://github.com/Guolinn),
whose [Classroom mic](https://github.com/Guolinn/classroom-mic) informed the
requirements. This integration is independently implemented; his source was
not copied (the reference repository has no open-source license).

## In class

0. Turn on **Classroom microphone** in the course settings, under *Running a
   lecture*. It is off by default; while it is off, nobody in the course sees
   the panel, the sound-only page is not found, and no audio is routed.
   Turning it off during a lecture closes every microphone connection at once.
1. Sign in to Agora as the instructor on the iPad and the laptop. Select the same
   course. Normal authentication, including the second factor, applies to both.
2. On the laptop, expand **Sound settings**, open **Open sound-only mode** and select **Start classroom sound**.
   Connect its output to the room speakers. Test them at a low volume. Present
   slides in your preferred app while leaving the sound page open.
3. Leave **Play sound on this device** off on the iPad (under **Sound settings**). Tablets and phones default
   to off; desktop browsers default to on. The preference is saved per browser.
   Browsers require a user gesture before audible playback: the start button
   provides it. An idle page never silently takes over another device's output.
   While the laptop plays a student, the instructor's iPad shows a fixed
   **Mute student** button at the bottom right, including when the lecture
   queues are scrolled. It stops both direct and relayed student audio on the
   laptop without moving playback to the iPad or requiring interaction with
   PowerPoint. Both mute buttons change to a disabled **Muted** state until the
   student restarts. The laptop remains ready for the next microphone turn.
4. Choose a student through any of Agora's queues. They select **Start microphone**
   and grant permission. Once a microphone is allowed, a student whose device has more than
   one (built-in, wired or Bluetooth headset, USB) can pick it under
   **Microphone**, also mid-turn without restarting; the choice is remembered
   on that device. A headset or earbud microphone close to the mouth gives far
   more margin against feedback than a phone held out. Bluetooth microphones
   add delay of their own. If the feedback guard acts, the student is told to
   move the phone closer to their mouth or away from the speakers. The last speaker also keeps permission for follow-ups
   until the newly chosen student starts their microphone.
5. The student selects **Stop microphone** when finished. The instructor can
   mute immediately from either device. Choosing another student or ending
   the lecture stops the previous microphone. After stopping or being muted,
   the last speaker can select **Restart microphone** for a follow-up. Selecting
   someone else immediately stops the old stream; starting the next student's
   microphone removes the previous speaker's restart permission. Ending the
   lecture removes all microphone permission. Audio never restarts automatically.

Choosing **Start classroom sound** on another device transfers output to that
device, clears all prior audio, and requires the student to restart the mic.
Multiple instructor sessions stay signed in; a playback lease is separate from
the login session. Only one student tab may publish at a time. Locking a student
screen or leaving the page releases the microphone. The laptop may play while
slides or another tab are foreground; browsers and operating systems can still
suspend a background page, so disable sleep during class.

## Instructor dashboard

Sound settings, speaker tests, connection details, and the prototype credit sit
in a collapsed **Sound settings** panel on the dashboard. The sound-only page
opens those settings by default. The iPad keeps a mute button available while
scrolling. Queue controls use two columns, and the queue chart is expandable
below the names. In portrait, the two student lists share a row with messages
beneath them.

After attendance closes, the panel lists every wrong-list student who is still
unreviewed. Mark each **In class** or **Not in class** after calling on them;
after the last decision (or immediately when nobody used the wrong list) the
panel disappears and **Take attendance** moves beside **Start/End Lecture**, so
a second roll call in the same lecture takes no space yet stays one tap away. Decisions are saved on the server, survive
a reload, and update other instructor tabs. Results and corrections remain
available on the lecture page.

## Transport and delay

The preferred path sends 20 ms mono PCM frames over a direct WebRTC **data
channel**, with unordered delivery and zero retransmissions. It does not use
WebRTC's adaptive RTP audio jitter buffer or a native audio element. Both direct
and relayed audio use the same AudioWorklet player: it starts with 20 ms,
never grows a jitter cushion, and skips to the newest 20 ms when more than
60 ms accumulates. Brief gaps are preferred to delayed speech. These are
application buffering limits, not a promise about microphone-to-speaker delay.

Capture messages delayed more than 40 ms on the sender's main thread are
skipped. Sender queues above 2 KB cause frames to be dropped; a relay queue
above 8 KB closes the socket to purge TCP backlog. Both the server and receiver
reject frames more than 120 ms old using the existing server-clock estimate.
The playback worklet also checks deadlines, so messages delayed between the
main thread and audio thread cannot replay old speech. Reordered/duplicate
packets and obsolete turn tokens are discarded. Clock estimates are imperfect;
clock RTT is included in diagnostics to help interpret frame ages.

Same-network ICE candidates are tried without third-party STUN. Failure to open
the audio channel in three seconds, a failed connection, or a persistent
disconnection switches to the authenticated same-origin WebSocket relay.
Both endpoints close the old channel and clear playback before the relay runs.
A normal stop gets a short control-message grace period so closing a channel
does not falsely mark the connection as failed. The current output device
remembers that it needed relay until classroom sound is reclaimed. Mixed older
RTP clients and new data-channel clients fall back to their common PCM relay.

PCM is normally 24 kHz (384 kbps plus headers). It uses more bandwidth than Opus.
A congested network may produce more gaps; no transport can guarantee a fixed
end-to-end delay. Physical capture, output hardware, Wi-Fi, and room acoustics
remain outside the application's buffer cap.

### Automatic diagnostics

Active sender and receiver browsers upload a bounded summary approximately
every five seconds and on selected connection/feedback events (at most once a
second). The server advertises this capability; new clients do not send unknown
messages to old backends. Reports are accepted only for the current authorized
pair and turn. Revoked members, observers, stale generations, excessive reports,
arbitrary text, unknown fields, and non-finite values are rejected or ignored.

`classroom_audio` JSON entries in Agora's existing events log include the
server timestamp, course ID, random turn ID, sender/receiver role, transport,
asset version, browser family, context state, receiver buffer and high-water
mark, estimated frame age and RTT, discarded/missing frames, playback gaps,
backlog skips, capture scheduling delay, gain reduction, feedback cuts, and
server relay drop counters. Control changes, turn endings, and disconnect codes
are recorded too. Client readings are diagnostic claims, not permission inputs.
No speech, waveform, student name, user ID, raw user-agent string, microphone
name, device ID, or ICE address is included in these audio records. They use
the existing bounded events-log rotation (normally 10 MB plus five backups).

The sound-device panel still shows live buffer readings and **Copy audio
details**. Those are separate components, not a measured total. The new server
records make routine troubleshooting possible without asking the class to
repeat a session. Existing sessions must reload to use the new transport and
send diagnostics; old sessions cannot be reconstructed retroactively.

## Controlled validation

`tests/classroom_microphone_worklets.cjs` simulates five minutes with recurring
400 ms outages and bursts. It checks the queue cap, expired speech, and silence
after mute. The browser harness also delays real data-channel packets by 250 ms,
stalls the sender's main thread, and verifies the direct path recovers without
building latency. These tests do not require real students or microphones.

The updated synthetic Chromium loopback probe observed approximately 24–33 ms
from an injected software signal to the software output detector in one local
run. This excludes physical microphone, loudspeaker and room latency and is not
a guarantee for students' devices. Run the probe on changes rather than treating
that one measurement as a service-level promise.

## Feedback and noise

Capture requests echo cancellation, noise suppression and voice isolation where
supported. Browser automatic gain control is deliberately off: it raises the
gain whenever the student pauses, which turns up the room and the loudspeakers'
own sound, the loop that howls. The phone then processes the signal before
either transport sends it:

- Band limits of 120 Hz (24 dB/octave) and 7 kHz remove rumble, handling noise
  and hiss that only add level for the loudspeakers to feed back.
- A speech gate follows the room's noise floor and turns everything 24 dB down
  between phrases (300 ms hold, so word endings are kept). Background noise
  and the loudspeakers' ringing are not sent while the student is silent.
- A level control moves only while the student is clearly speaking, aiming at
  -26 dBFS, with no positive boost and at most 12 dB of attenuation. Recovery
  is limited to 1 dB/s while strong input is present; it never increases gain
  during pauses. Quiet students may need to hold the phone closer.
- A feedback guard looks at the spectrum 20 times a second for a narrow peak
  that holds one frequency for 300 ms without fading and without harmonics of
  its own (150 ms for an unmistakable howl). It places up to six narrow cuts,
  starting at 12 dB; a howl that slides to the edge of a cut deepens and widens
  that cut, to at most 30 dB. With the cuts spent, the microphone is turned
  down 3 dB at a time, to at most 12 dB.
- When the phone hears the loudspeakers about as loudly as the student, the
  whole loop is above unity and no notch helps. A sound that holds the gate
  open without the syllable-to-syllable swing of speech for 1.5 seconds turns
  the microphone down 3 dB, to at most 12 dB.
- A peak limiter with no makeup gain and a 150 ms fade-in at the start of each
  turn follows.

All of this resets with each new capture. A sustained sung or whistled note can
also earn a (narrow) cut. `tests/classroom_microphone_acoustics.py` checks this
against a simulated room; it is not a substitute for a real one.

Browser echo cancellation does not get a reference to another device's speaker
output, so it cannot guarantee removal of room feedback. Start at low speaker
volume, keep the phone near the speaker's mouth and away from the loudspeakers,
and mute promptly when a turn ends. Acoustic testing with actual phones, the
instructor's iPad, campus Wi-Fi, and room speakers is still required.
In particular, the tonal feedback guard does not cancel delayed copies of
ordinary speech returning from the laptop. A positive feedback notice on the
sound device is a reason to lower speaker volume, not a claim that echo has
been removed. The separate-device reference limitation is described in the
[W3C echo-cancellation discussion](https://github.com/w3c/mediacapture-extensions/issues/32).

## Deployment and authorization

Apply the `peer_lecture` migrations through `0034_classroommicrophone_relay_preferred`
and `peer_course` through `0090_course_classroom_microphone`,
collect static files, and restart the normal ASGI workers using the project's
deployment procedure. The
feature uses the existing database and Redis channel layer, across workers.
HTTPS/WSS is required on real devices. The new path is
`/ws/<course_id>/microphone/`; the existing Nginx `/ws/` proxy should cover it.

This release adds a server diagnostic handler and must deploy the backend and
frontend together. Do not publish only the frontend: that would leave diagnostic
collection disabled.

For a future browser-only audio update with no backend or protocol changes, the audio
modules can be published to the existing collected-static directory without
restarting workers. Preserve the prior files and their hashes for rollback,
publish dependencies before the entry module, and verify the public HTTPS
responses against the committed files. Record the frontend commit separately
from the running backend release, and keep the server's source checkout current
so a later full release retains the update. Reload existing classroom pages to
fetch the new entry module; running microphone sessions keep their loaded code.

Same-origin cookies, course membership, active account status, and the existing
second-factor middleware authorize sockets. Only instructors can claim output or
mute. Only the chosen student and the last speaker may start audio. Once another
student starts, the last speaker is replaced. The server checks the actual
speaker, current connection, generation,
transport, lecture, and unexpired leases before routing audio or signaling.
Tokens change on every turn, disconnect, output transfer, and mute. Leases renew
every five seconds and expire after 75 seconds, which outlasts the roughly
once-a-minute timers a browser allows a page hidden behind slides; a closed
socket gives up its lease immediately. Each socket may send at most a burst of
40 control messages, then 10 a second; a client that floods is disconnected. Receivers recheck permissions after
cross-worker routing; a queued frame cannot resurrect an obsolete stream.

Audio is not saved to the database, logs, files, or analytics. Relayed audio is
encrypted in transit but passes through the Agora server in memory. WebRTC is
encrypted between the two devices. The database contains only ephemeral control
state and random Channels addresses, not media. After an unclean server restart,
leases expire and fresh connections require explicit user action.

## Classroom acceptance check

- Use two instructor devices and two students: only the chosen student and the
  last speaker may start; permission denial and cancellation leave capture off.
- Stop or mute the speaker and check that **Restart microphone** works. Choose
  someone else, verify the previous speaker can still answer a follow-up, then
  start the next student's microphone and verify the previous button disappears.
- Test the normal direct connection and a Wi-Fi network that blocks direct peers.
  Confirm the connection label switches to **Server relay** without double audio.
- Speak, switch students, mute from the iPad, move output, end the lecture, unplug
  Wi-Fi, and reload. Check that old audio and microphone indicators disappear.
- Simulate a network stall and burst. Old speech should be skipped, not replayed.
- At low classroom volume, measure mouth-to-speaker delay acoustically with a
  clap and a recording made separately for the test. Test feedback at increasing
  levels and compare against the room's usual handheld microphone.
- Test Safari on the actual iPad/iPhones as well as Android and the laptop. Fake
  devices and automated browser tests cannot establish acoustic performance.

## Automated validation

`python tests/classroom_microphone_latency.py` sends synthetic pulses through
the speech chain, direct PCM data channel, and bounded playback in Chromium.
It checks software loopback timing, not physical-device or room latency.

Run the sustained deterministic worklet check with:

```text
node tests/classroom_microphone_worklets.cjs
```

Run the existing Django suite with `python manage.py test`. It includes socket
authorization, instructor output transfer, follow-up restart and expiry, turn
changes, lecture ending, revocation, stale frames, duplicate packets, and relay
isolation.

The simulated-room check renders the processing chain offline in Chromium with
synthetic speech, room noise and a delayed, resonant loudspeaker path:
speech must not be cut, noise must be gated between phrases, and mild and
extreme feedback must be found and brought under control. It also checks
that relay playback catches up after a burst:

```text
python tests/classroom_microphone_acoustics.py
```

The optional browser harness uses its own temporary SQLite database, fictional
accounts, a local ASGI server, in-memory Channels, and Chromium fake microphones:

```text
pip install playwright
playwright install chromium
python tests/classroom_microphone_browser.py
```

Set `PLAYWRIGHT_CHROMIUM_EXECUTABLE` to use an existing Chromium installation.
The harness covers actual WebRTC audio, automatic relay fallback, permission
denial, iPad/desktop defaults, switching students through Agora's selection
endpoint, follow-ups before the next speaker starts, restart after stop/mute,
gain-control settings, starting later turns on the relay, output transfer, obsolete callbacks, injected network/main-thread stalls, and playback backlog
limits. It reports software receiver buffering;
this excludes capture, encoding, network transit, output hardware, and room
acoustics and must not be reported as total latency.
