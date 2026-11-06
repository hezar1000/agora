"""Isolated browser acceptance tests. Requires optional `playwright` + Chromium.

Run: DJANGO_ALLOW_INSECURE_LOCAL_DEFAULTS=1 SAML2_INTEGRATION_ENABLED=0
     python tests/classroom_microphone_browser.py

Creates its own temporary database, accounts, ASGI server and in-memory Channels.
It never uses a deployed service or a real microphone. Acoustic/Safari testing
still requires actual devices. Set PLAYWRIGHT_CHROMIUM_EXECUTABLE if needed.
"""

import asyncio
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
from datetime import timedelta


ROOT = Path(__file__).resolve().parents[1]


def main():
    with tempfile.TemporaryDirectory(prefix="agora-microphone-browser-") as temporary:
        directory = Path(temporary)
        (directory / "agora_mic_browser_settings.py").write_text(
            "from mta2.settings import *\n"
            "CHANNEL_LAYERS = {'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}}\n"
            "MFA_ENFORCED = False\n"
            "DEBUG = True\n"
            "SESSION_COOKIE_SECURE = False\n"
            "CSRF_COOKIE_SECURE = False\n"
            "DATABASES = {'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': "
            + repr(str(directory / "browser.sqlite3")) + "}}\n"
        )
        (directory / "agora_mic_browser_app.py").write_text(
            "from mta2.asgi import application as app\n"
            "from django.contrib.staticfiles.handlers import ASGIStaticFilesHandler\n"
            "application = ASGIStaticFilesHandler(app)\n"
        )
        environment = {
            **os.environ,
            "PYTHONPATH": os.pathsep.join([str(ROOT), temporary]),
            "DJANGO_SETTINGS_MODULE": "agora_mic_browser_settings",
            "DJANGO_ALLOW_INSECURE_LOCAL_DEFAULTS": "1",
            "SAML2_INTEGRATION_ENABLED": "0",
            "DJANGO_SQLITE_PATH": str(directory / "browser.sqlite3"),
            "DJANGO_DEBUG": "1",
            "DJANGO_ALLOWED_HOSTS": "localhost,127.0.0.1",
            "DJANGO_CSRF_TRUSTED_ORIGINS": "http://localhost,http://127.0.0.1",
        }
        environment.pop("DATABASE_URL", None)
        subprocess.run([sys.executable, "manage.py", "migrate", "--noinput"], cwd=ROOT, env=environment, stdout=subprocess.DEVNULL, check=True)
        os.environ.pop("DATABASE_URL", None)
        os.environ.update(environment)
        sys.path[:0] = [str(ROOT), temporary]
        import django
        django.setup()
        from django.conf import settings
        from django.contrib.auth.models import User
        from django.test import Client
        from django.utils import timezone
        from peer_course.models import Course, CourseMember, CourseSession
        from peer_course.sessions import course_timezone
        from peer_lecture.models import Lecture
        from peer_lecture.microphone import select_microphone_student

        course = Course.objects.create(displayname="Microphone browser test", browsable=True, archived=False, manual_override=True, classroom_microphone=True)
        teacher = User.objects.create_user("browser-teacher")
        student = User.objects.create_user("browser-student", first_name="Guolin")
        second = User.objects.create_user("browser-second", first_name="Second")
        CourseMember.objects.create(course=course, user=teacher, role="instructor")
        member = CourseMember.objects.create(course=course, user=student, role="student")
        next_member = CourseMember.objects.create(course=course, user=second, role="student", hand_up=True)
        lecture = Lecture.objects.create(course=course)
        now = timezone.localtime(timezone.now(), course_timezone(course))
        CourseSession.objects.create(course=course, session_date=now.date(), start_time_pst=(now - timedelta(minutes=5)).time().replace(tzinfo=None), end_time_pst=(now + timedelta(hours=1)).time().replace(tzinfo=None), is_template=False, ended=False)
        select_microphone_student(course.pk, member, lecture)
        cookies = {}
        for user in [teacher, student, second]:
            client = Client()
            client.force_login(user)
            session = client.session
            session["course_id"] = course.pk
            session.save()
            cookies[user.pk] = client.cookies[settings.SESSION_COOKIE_NAME].value
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        log_path = ROOT / ".cache" / "microphone-browser.log"
        log_path.parent.mkdir(exist_ok=True)
        with log_path.open("w") as log:
            server = subprocess.Popen([sys.executable, "-m", "daphne", "-b", "127.0.0.1", "-p", str(port), "agora_mic_browser_app:application"], cwd=ROOT, env=environment, stdout=log, stderr=log)
            try:
                for _ in range(100):
                    try:
                        with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                            break
                    except OSError:
                        time.sleep(0.1)
                else:
                    raise RuntimeError(f"Local test server did not start; see {log_path}")
                asyncio.run(browser(port, course.pk, teacher.pk, student.pk, second.pk, next_member.pk, cookies))
            finally:
                server.terminate()
                server.wait(timeout=10)


async def browser(port, course, teacher, student, second, next_member, cookies):
    from playwright.async_api import async_playwright
    base = f"http://127.0.0.1:{port}"
    selector = "[data-classroom-microphone]"
    async with async_playwright() as playwright:
        executable = os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE")
        chromium = await playwright.chromium.launch(executable_path=executable, headless=True, args=["--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream"])
        contexts = []
        async def page_for(user, ipad=False):
            options = {"permissions": ["microphone"], "ignore_https_errors": True}
            if ipad:
                options.update(user_agent="Mozilla/5.0 (iPad; CPU OS 18_0 like Mac OS X) AppleWebKit/605.1.15 Safari/604.1", viewport={"width": 1024, "height": 768}, has_touch=True)
            context = await chromium.new_context(**options)
            contexts.append(context)
            await context.add_cookies([{"name": "sessionid", "value": cookies[user], "url": base}])
            page = await context.new_page()
            return page
        async def wait(page, expression):
            await page.wait_for_function(f"() => {{ const mic = document.querySelector('{selector}')?.microphone; return mic && ({expression}); }}", timeout=15000)
        laptop = await page_for(teacher)
        ipad = await page_for(teacher, True)
        phone = await page_for(student)
        other = await page_for(second)
        await laptop.goto(f"{base}/sound/{course}/")
        await ipad.goto(f"{base}/agora_dash/")
        await phone.goto(f"{base}/agora_dash/")
        await other.goto(f"{base}/agora_dash/")
        for page in [laptop, ipad, phone, other]:
            await wait(page, "mic.state !== null")
        assert await laptop.locator("[data-sound]").is_checked()
        assert not await ipad.locator("[data-sound]").is_checked()
        assert not await other.locator("[data-start]").is_visible()
        await laptop.locator("[data-start]").click()
        await wait(laptop, "mic.state.this_output")
        await phone.locator("[data-start]").click()
        await wait(phone, "mic.capture && mic.peer?.connectionState === 'connected'")
        await wait(laptop, "mic.audioChannel?.readyState === 'open' && mic.playback.received > 0")
        await laptop.wait_for_timeout(300)
        direct = await laptop.evaluate(f"""async () => {{
          const mic = document.querySelector('{selector}').microphone;
          return {{received:mic.playback.received, ordered:mic.audioChannel.ordered,
            retries:mic.audioChannel.maxRetransmits, tracks:mic.peer.getReceivers().length,
            native:mic.native.srcObject, buffer:mic.playback.stats.buffered}};
        }}""")
        assert direct['received'] > 0 and not direct['ordered'] and direct['retries'] == 0
        assert direct['tracks'] == 0 and direct['native'] is None and direct['buffer'] <= 60, direct
        print('PASS: real direct PCM data channel, no retransmissions or native audio, short playback buffer', direct)
        # Hold packets for 250 ms and then release them: old speech must be
        # discarded, without switching transport or leaving a persistent delay.
        await phone.evaluate(f"""() => {{
          const mic = document.querySelector('{selector}').microphone;
          const channel = mic.audioChannel, send = channel.send.bind(channel);
          channel.send = packet => setTimeout(() => {{if(channel.readyState === 'open') send(packet);}}, 250);
          setTimeout(() => {{channel.send = send;}}, 1800);
        }}""")
        await laptop.wait_for_timeout(2300)
        await wait(laptop, "mic.playback.discarded > 0 && mic.state.transport === 'direct' && mic.playback.stats.buffered <= 60")
        await phone.evaluate('() => { const end = performance.now() + 300; while(performance.now() < end) {} }')
        await wait(phone, 'mic.senderDropped > 0')
        print('PASS: delayed network packets and a stalled sender skip speech instead of building delay')
        await wait(laptop, "mic.diagnostics.remote?.sampleRate === 24000 && mic.diagnostics.metrics.bufferMs !== null")
        assert 'Direct audio' in await laptop.locator('[data-audio-health]').inner_text()
        details = await laptop.locator('[data-audio-details]').text_content()
        assert 'Student browser: Mozilla/' in details and 'not a measured total' in details, details
        await phone.evaluate(f"() => {{ document.querySelector('{selector}').microphone.noticeFeedback(); }}")
        await laptop.locator('[data-audio-health]').get_by_text('Feedback detected', exact=False).wait_for()
        await phone.evaluate(f"() => {{ document.querySelector('{selector}').microphone.feedbackAt = 0; }}")
        print('PASS: instructor sees receiver timing, student capture diagnostics, and feedback notices without recording audio')
        # The lecture controller stays silent and can stop laptop playback
        # without scrolling back to the microphone panel or taking output over.
        assert not await ipad.locator('[data-sound]').is_checked()
        await ipad.evaluate('() => window.scrollTo(0, document.body.scrollHeight)')
        await ipad.locator('[data-remote-mute]').wait_for(state='visible')
        assert await ipad.locator('[data-remote-mute]').evaluate('(button) => { const r = button.getBoundingClientRect(); return r.top >= 0 && r.bottom <= innerHeight; }')
        assert not await laptop.locator('[data-remote-mute]').is_visible()
        assert await phone.locator('[data-remote-mute]').count() == 0
        await ipad.locator('[data-remote-mute]').click()
        await wait(phone, 'mic.capture === null && !mic.state.this_sender')
        await wait(laptop, 'mic.state.this_output && mic.native.srcObject === null && mic.playback.received === 0')
        await wait(ipad, '!mic.state.streaming')
        for page in (ipad, laptop):
            assert await page.locator('[data-mute]').inner_text() == 'Muted'
            assert await page.locator('[data-mute]').is_disabled()
        assert await ipad.locator('[data-remote-mute]').inner_text() == 'Muted'
        assert await ipad.locator('[data-remote-mute]').is_disabled()
        assert not await ipad.locator('[data-sound]').is_checked()
        await phone.locator('[data-start]').click()
        await wait(phone, "mic.capture && mic.peer?.connectionState === 'connected'")
        await wait(laptop, "mic.audioChannel?.readyState === 'open' && mic.playback.received > 0")
        print('PASS: fixed iPad mute stops direct laptop audio, keeps the laptop output lease, and allows a student follow-up')
        assert await ipad.locator('[data-mute]').inner_text() == 'Mute student'
        assert await ipad.locator('[data-mute]').is_enabled()
        agc = await phone.evaluate(f"() => {{ const track = document.querySelector('{selector}').microphone.capture.raw.getAudioTracks()[0]; return {{requested: track.getConstraints().autoGainControl, applied: track.getSettings().autoGainControl}}; }}")
        # Browser gain control raises the room's level whenever the student
        # pauses; the capture worklet levels speech instead.
        assert agc["requested"] is False and agc["applied"] is not True, agc
        # Choosing another microphone mid-turn keeps the turn and the connection.
        before = await phone.evaluate(f"() => {{ const mic = document.querySelector('{selector}').microphone; return {{generation: mic.state.generation, track: mic.capture.raw.getAudioTracks()[0].id, choices: Array.from(mic.deviceSelect.options).map(o => o.value), shown: !mic.deviceSelect.parentElement.hidden}}; }}")
        chosen = None
        if len(before["choices"]) > 2:
            assert before["shown"], before
            chosen = before["choices"][2]
            await phone.locator("[data-device]").select_option(chosen)
            await wait(phone, f"mic.capture.raw.getAudioTracks()[0].id !== '{before['track']}' && mic.capture.deviceId === '{chosen}'")
            await wait(phone, f"mic.state.this_sender && mic.state.generation === '{before['generation']}'")
            await wait(laptop, "mic.audioChannel?.readyState === 'open' && mic.playback.received > 0")
            print(f"PASS: switching microphones mid-turn keeps the turn and connection ({len(before['choices']) - 1} microphones listed)")
        else:
            print(f"SKIP: microphone switching needs two microphones; this browser lists {before['choices']}")
        await phone.locator('[data-stop]').click()
        await wait(phone, "mic.capture === null && mic.state.last_speaker_user_id === mic.user")
        await wait(laptop, "mic.native.srcObject === null && mic.playback.received === 0")
        assert await phone.locator('[data-start]').inner_text() == 'Restart microphone'
        await phone.locator('[data-start]').click()
        await wait(laptop, "mic.audioChannel?.readyState === 'open' && mic.playback.received > 0")
        if chosen:
            assert await phone.evaluate(f"() => document.querySelector('{selector}').microphone.capture.deviceId") == chosen
        print('PASS: browser gain control is off and stopping offers a working restart (with the chosen microphone)')
        # A transport switch must detach the old data callback before relay plays.
        await laptop.evaluate(f"""() => {{
          const mic = document.querySelector('{selector}').microphone;
          window.obsoleteAudio = mic.audioChannel.onmessage;
          mic.fallback('manual_relay');
        }}""")
        await wait(laptop, "mic.state.transport === 'relay' && mic.playback.received > 0")
        await laptop.evaluate("() => window.obsoleteAudio({data:new ArrayBuffer(32)})")
        await wait(laptop, "mic.peer === null && mic.audioChannel === null && mic.native.srcObject === null")
        await wait(phone, "mic.state.this_sender && mic.state.transport === 'relay' && mic.peer === null")
        await laptop.wait_for_function("() => /Server relay.*Receiving buffer: \\d+ ms/.test(document.querySelector('[data-audio-health]').textContent)")
        assert 'Switched to the server relay' in await laptop.locator('[data-audio-health]').inner_text()
        assert 'Before relay switch' in await laptop.locator('[data-audio-details]').text_content()
        # Keep the notice while proving that subsequent buffer readings update.
        reading = await laptop.evaluate(f"""() => {{
          const mic = document.querySelector('{selector}').microphone;
          const actual = mic.playback.stats.buffered;
          mic.playback.stats.buffered = 42; mic.diagnostics.render();
          const first = mic.diagnostics.health.textContent;
          mic.playback.stats.buffered = 73; mic.diagnostics.render();
          const second = mic.diagnostics.health.textContent;
          mic.playback.stats.buffered = actual; mic.diagnostics.render();
          return {{first, second}};
        }}""")
        assert '42 ms' in reading['first'] and '73 ms' in reading['second'], reading
        print("PASS: manual relay switch stops direct audio; obsolete callbacks cannot restart playback")
        print('PASS: relay notice retains continuously updated buffer readings and the pre-switch direct measurements')
        # Change the selected student through Agora's actual selection endpoint.
        csrf = await ipad.locator("input[name=csrfmiddlewaretoken]").first.input_value()
        response = await ipad.request.post(f"{base}/choose_next/{next_member}/1/", form={"selection_version": "0"}, headers={"X-CSRFToken": csrf})
        assert response.ok, await response.text()
        await wait(phone, "mic.capture === null && !mic.state.this_sender")
        await wait(other, "mic.state.selected_user_id === mic.user")
        await wait(laptop, "mic.native.srcObject === null && mic.playback.received === 0")
        print("PASS: real speaker selection releases the old microphone and all playback")
        assert await phone.locator('[data-start]').is_visible()
        assert await phone.locator('[data-start]').inner_text() == 'Restart microphone'
        await phone.locator('[data-start]').click()
        # This output already needed the relay, so the turn starts on it at once.
        await wait(phone, "mic.state.this_sender && mic.state.transport === 'relay' && mic.peer === null")
        await wait(laptop, "mic.playback.received > 0 && mic.state.last_speaker_user_id !== mic.state.selected_user_id")
        await phone.locator('[data-stop]').click()
        await wait(phone, "mic.capture === null && !mic.state.this_sender")
        print('PASS: the previous speaker can answer a follow-up before the next student starts, straight on the relay')
        await other.evaluate("() => { window.realCapture = navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices); navigator.mediaDevices.getUserMedia = async () => { throw new DOMException('Denied for test', 'NotAllowedError'); }; }")
        await other.locator("[data-start]").click()
        await other.locator("[data-status]").get_by_text("Microphone permission was denied.", exact=False).wait_for()
        await wait(other, "mic.capture === null && !mic.state.this_sender")
        print("PASS: denied microphone permission leaves capture and publishing off")
        # An already-open older sender still offers a native RTP track. The
        # new receiver must negotiate fallback without playing both paths.
        await laptop.locator("[data-sound]").uncheck()
        await wait(laptop, "!mic.state.this_output")
        await laptop.locator("[data-sound]").check()
        await laptop.locator("[data-start]").click()
        await wait(laptop, "mic.state.this_output && mic.state.transport === 'direct'")
        await other.evaluate(f"""() => {{
          navigator.mediaDevices.getUserMedia = window.realCapture;
          const mic = document.querySelector('{selector}').microphone;
          window.currentOffer = mic.offer;
          mic.offer = async function() {{
            const peer = this.makePeer();
            this.capture.destination.stream.getTracks().forEach(track => peer.addTrack(track, this.capture.destination.stream));
            await peer.setLocalDescription(await peer.createOffer());
            this.send('signal', {{signal: {{description:peer.localDescription.toJSON()}}}});
          }};
        }}""")
        await other.locator('[data-start]').click()
        await wait(laptop, "mic.state.transport === 'relay' && mic.playback.received > 0 && mic.native.srcObject === null")
        await other.locator('[data-stop]').click()
        await wait(other, "mic.capture === null")
        await other.evaluate(f"() => {{ document.querySelector('{selector}').microphone.offer = window.currentOffer; }}")
        print('PASS: an older RTP sender falls back to relay with no simultaneous native playback')
        # Restarting sound on the laptop forgets the relay, so direct is tried
        # again; then prove automatic fallback, rather than requesting relay.
        await laptop.locator("[data-sound]").uncheck()
        await wait(laptop, "!mic.state.this_output")
        await laptop.locator("[data-sound]").check()
        await laptop.locator("[data-start]").click()
        await wait(laptop, "mic.state.this_output && mic.state.transport === 'direct'")
        await other.evaluate("() => { navigator.mediaDevices.getUserMedia = window.realCapture; window.RTCPeerConnection = class { constructor() { throw new Error('Peer network blocked for test'); } }; }")
        await other.locator("[data-start]").click()
        await wait(laptop, "mic.state.transport === 'relay' && mic.playback.received > 0")
        await wait(phone, "mic.state.last_speaker_user_id !== mic.user && !mic.maySpeak()")
        assert not await phone.locator('[data-start]').is_visible()
        print("PASS: failed direct connection falls back automatically")
        await ipad.locator("[data-remote-mute]").click()
        await wait(other, "mic.capture === null && !mic.state.this_sender && mic.state.last_speaker_user_id === mic.user")
        await wait(laptop, "mic.state.this_output && mic.playback.received === 0 && mic.native.srcObject === null")
        assert not await ipad.locator('[data-sound]').is_checked()
        print("PASS: mute from the silent iPad stops the student and laptop")
        assert await other.locator('[data-start]').inner_text() == 'Restart microphone'
        await other.locator('[data-start]').click()
        await wait(laptop, "mic.state.transport === 'relay' && mic.playback.received > 0")
        print('PASS: restart after instructor mute works; previous speaker remains ineligible')
        await ipad.locator(".classroom-microphone__settings > summary").click()
        await ipad.locator("[data-sound]").check()
        await ipad.locator("[data-start]").click()
        await wait(ipad, "mic.state.this_output")
        await wait(laptop, "!mic.state.this_output && mic.playback.received === 0")
        # A new output clears the old owner's intent; reconnects don't take it back.
        assert not await phone.evaluate(f"() => document.querySelector('{selector}').microphone.capture !== null")
        print("PASS: output takeover preserves both instructor logins and releases the prior device")
        # Deterministic buffer and processing checks supplement real browser audio.
        result = await laptop.evaluate(r"""async () => {
          const {RelayPlayback, FeedbackGuard, generationBytes, defaultSoundPreference, feedbackCandidate, lowLatencyAnswer} = await import('/static/lecture/microphone-audio.js');
          const context = new AudioContext(); await context.resume();
          const mute = context.createGain(); mute.gain.value = 0; mute.connect(context.destination);
          const playback = new RelayPlayback(context, mute); await playback.ready;
          const token = generationBytes('12345678-1234-1234-1234-123456789012');
          function packet(sequence, age=0) {
            const bytes = new ArrayBuffer(992), view = new DataView(bytes);
            new Uint8Array(bytes, 0, 16).set(token); view.setUint32(16, sequence);
            view.setFloat64(20, Date.now()-age); view.setUint32(28, 24000); return bytes;
          }
          for (let i=0; i<30; i++) playback.play(packet(i), token, Date.now());
          await new Promise(resolve => setTimeout(resolve, 300));
          const capped = playback.stats.buffered <= 60 && playback.stats.dropped > 0;
          playback.play(packet(31, 1000), token, Date.now());
          const staleCleared = playback.received === 30 && playback.discarded > 0;
          const ipadOff = !defaultSoundPreference({userAgent:'Macintosh', platform:'MacIntel', maxTouchPoints:5});
          const quietPreserved = feedbackCandidate(new Float32Array(1024).fill(-80), 24000, 2048) === null;
          const tone = new Float32Array(1024).fill(-90); tone[80] = -10;
          const guarded = feedbackCandidate(tone, 24000, 2048) !== null;
          // A lone tone that holds is a howl; a voice's family of harmonics is not.
          const watch = spectrum => { const guard = new FeedbackGuard(); let found = null; for (let t = 0; t <= 600 && found === null; t += 50) found = guard.update(spectrum, 24000, 2048, t); return found; };
          const howl = new Float32Array(1024).fill(-70); howl[110] = -20; howl[109] = howl[111] = -26;
          const voice = new Float32Array(1024).fill(-90); for (let h = 1; 11 * h < 1024; h++) voice[11 * h] = -20 - 20 * Math.log10(h);
          const howlFound = Math.abs(watch(howl) - 110 * 24000 / 2048) < 12;
          const voiceSpared = watch(voice) === null;
          playback.clear(); await context.close(); return {capped, staleCleared, ipadOff, quietPreserved, guarded, howlFound, voiceSpared};
        }""")
        assert all(result.values()), result
        print("PASS: capped playback backlog, stale-frame purge, iPad desktop identity, feedback detection without cutting speech", result)
        # Real attendance workflow: retain all wrong-list students, then hide
        # the entire feature after the final decision, including after reload.
        await ipad.reload()
        await wait(ipad, 'mic.state !== null')
        mirror = await page_for(teacher)
        await mirror.goto(f'{base}/agora_dash/')
        await mirror.locator('#attendance-start').wait_for()
        await ipad.locator('#attendance-start').click()
        await mirror.locator('#attendance-close').wait_for()
        await ipad.locator('#attendance-close').wait_for()
        state = await (await ipad.request.get(f'{base}/course/{course}/attendance/')).json()
        wrong = next(item['id'] for item in state['lists'] if item['id'] != state['check']['announced_list'])
        paths = {1:'enable', 2:'enable_blue', 3:'enable_red', 4:'enable_yellow'}
        for page in (phone, other):
            token = await page.locator('input[name=csrfmiddlewaretoken]').first.input_value()
            response = await page.request.post(f'{base}/{paths[wrong]}/', headers={'X-CSRFToken':token})
            assert response.ok, await response.text()
        await ipad.locator('#attendance-close').click()
        await ipad.locator('.attendance-panel__row').first.wait_for()
        await mirror.locator('.attendance-panel__row').first.wait_for()
        assert await ipad.locator('.attendance-panel__row').count() == 2
        assert await ipad.locator('#attendance-start').count() == 0
        await ipad.locator('.attendance-panel__row [data-status="confirmed"]').first.click()
        await ipad.wait_for_function("() => document.querySelector('#attendance-panel-body').textContent.includes('1 still to call on')")
        await ipad.reload()
        await ipad.locator('.attendance-panel__row').first.wait_for()
        assert await ipad.locator('#attendance-panel').is_visible()
        await ipad.locator('.attendance-panel__row [data-status="confirmed"]:enabled').click()
        await ipad.locator('#attendance-panel').wait_for(state='hidden')
        await mirror.locator('#attendance-panel').wait_for(state='hidden')
        await ipad.reload()
        await wait(ipad, 'mic.state !== null')
        await ipad.locator('#attendance-panel').wait_for(state='hidden')
        # Roll call with no wrong-list hands disappears as soon as it closes.
        token = await ipad.locator('input[name=csrfmiddlewaretoken]').first.input_value()
        for action in ('start', 'close'):
            response = await ipad.request.post(f'{base}/course/{course}/attendance/{action}/', headers={'X-CSRFToken':token})
            assert response.ok, await response.text()
            if action == 'start':
                await mirror.locator('#attendance-close').wait_for()
        await mirror.locator('#attendance-panel').wait_for(state='hidden')
        await ipad.locator('#attendance-panel').wait_for(state='hidden')
        # Restore the usual teaching arrangement for layout checks: laptop
        # output, silent iPad with synchronized mute controls.
        await ipad.locator('.classroom-microphone__settings > summary').click()
        await ipad.locator('[data-sound]').uncheck()
        await ipad.locator('.classroom-microphone__settings > summary').click()
        await laptop.locator('[data-start]').click()
        await wait(ipad, 'mic.state.output_connected && !mic.state.this_output')
        # At both tablet orientations, audio setup is collapsed and queues
        # start well inside the screen, without horizontal page overflow.
        for width, height, label in ((1024,768,'landscape'), (768,1024,'portrait')):
            await ipad.set_viewport_size({'width':width, 'height':height})
            await ipad.evaluate('window.scrollTo(0, 0)')
            await ipad.wait_for_timeout(200)
            geometry = await ipad.evaluate("""() => ({
                sound: document.querySelector('[data-classroom-microphone]').getBoundingClientRect().height,
                queue: document.querySelector('.lecture-scroll--hands').getBoundingClientRect().top,
                width:document.documentElement.scrollWidth, viewport:innerWidth,
                settings:document.querySelector('.classroom-microphone__settings').open
            })""")
            assert geometry['sound'] < 110 and geometry['queue'] < height - 180, geometry
            assert geometry['width'] <= width and not geometry['settings'], geometry
            await ipad.screenshot(path=str(ROOT / '.cache' / f'ipad-dashboard-{label}.png'))
            print('PASS: compact iPad dashboard', label, geometry)
        print('PASS: attendance stays through wrong-list review and disappears after completion and reload')
        for context in contexts:
            await context.close()
        await chromium.close()


if __name__ == "__main__":
    main()
