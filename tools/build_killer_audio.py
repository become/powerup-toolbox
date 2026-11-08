import numpy as np
import wave
import subprocess
import os

SAMPLE_RATE = 44100
DURATION = 41.5
TOTAL_SAMPLES = int(SAMPLE_RATE * DURATION)
BPM = 120.0
BEAT_SEC = 60.0 / BPM
t = np.linspace(0, DURATION, TOTAL_SAMPLES, endpoint=False)

print(f"🎵 Generating 120 BPM Clean Tech Soundtrack for 41.5s...")

# 1. KICK DRUM
kick = np.zeros(TOTAL_SAMPLES)
kick_len = int(SAMPLE_RATE * 0.2)
kt = np.linspace(0, 0.2, kick_len, endpoint=False)
single_kick = np.sin(2 * np.pi * (130 * np.exp(-kt * 28) + 40) * kt) * np.exp(-kt * 16)

num_beats = int(DURATION / BEAT_SEC)
for b in range(num_beats):
    if b < 4:
        continue
    start_idx = int(b * BEAT_SEC * SAMPLE_RATE)
    end_idx = min(TOTAL_SAMPLES, start_idx + kick_len)
    kick[start_idx:end_idx] += single_kick[:end_idx - start_idx] * 0.75

# 2. HI-HAT & CLAP
hat = np.zeros(TOTAL_SAMPLES)
clap = np.zeros(TOTAL_SAMPLES)
hat_len = int(SAMPLE_RATE * 0.04)
ht = np.linspace(0, 0.04, hat_len, endpoint=False)
single_hat = (np.random.rand(hat_len) * 2 - 1) * np.exp(-ht * 90) * 0.2

clap_len = int(SAMPLE_RATE * 0.12)
ct = np.linspace(0, 0.12, clap_len, endpoint=False)
single_clap = (np.random.rand(clap_len) * 2 - 1) * np.exp(-ct * 32) * 0.35

for b in range(num_beats):
    if b >= 4:
        h_idx = int((b + 0.5) * BEAT_SEC * SAMPLE_RATE)
        if h_idx + hat_len < TOTAL_SAMPLES:
            hat[h_idx:h_idx + hat_len] += single_hat
    if b >= 8 and (b % 4 == 1 or b % 4 == 3):
        c_idx = int(b * BEAT_SEC * SAMPLE_RATE)
        if c_idx + clap_len < TOTAL_SAMPLES:
            clap[c_idx:c_idx + clap_len] += single_clap

# 3. SYNTH BASS & ARPEGGIO
bass = np.zeros(TOTAL_SAMPLES)
sixteenth = BEAT_SEC / 4.0
num_16ths = int(DURATION / sixteenth)
roots = [43.65, 43.65, 34.65, 38.89]

for s in range(num_16ths):
    if s < 16:
        continue
    bar = int(s / 16)
    root = roots[bar % len(roots)]
    f_oct = root * (2 if s % 2 == 1 else 1)
    s_len = int(SAMPLE_RATE * sixteenth * 0.8)
    st_arr = np.linspace(0, sixteenth * 0.8, s_len, endpoint=False)
    single_bass = np.sin(2 * np.pi * f_oct * st_arr) * np.exp(-st_arr * 14)
    idx = int(s * sixteenth * SAMPLE_RATE)
    if idx + s_len < TOTAL_SAMPLES:
        bass[idx:idx + s_len] += single_bass * 0.35

# 4. SFX: Mechanical Switch Clicks during rapid browsing (5.5s to 13.5s)
sfx = np.zeros(TOTAL_SAMPLES)
click_times = [5.4, 6.1, 6.8, 7.5, 8.2, 8.9, 9.6, 10.3, 11.0, 11.7, 12.4, 13.1]
click_len = int(SAMPLE_RATE * 0.035)
clt = np.linspace(0, 0.035, click_len, endpoint=False)
single_click = (np.sin(2 * np.pi * 3200 * clt) * 0.5 + (np.random.rand(click_len) * 2 - 1) * 0.5) * np.exp(-clt * 120) * 0.6

for ct_val in click_times:
    c_idx = int(ct_val * SAMPLE_RATE)
    if c_idx + click_len < TOTAL_SAMPLES:
        sfx[c_idx:c_idx + click_len] += single_click

# Sparkle Chime at 21.0s (JSON Repair)
chime_idx = int(21.0 * SAMPLE_RATE)
for ci, cf in enumerate([1046.5, 1318.5, 1567.9, 2093.0]):
    c_len = int(SAMPLE_RATE * 0.7)
    ct_arr = np.linspace(0, 0.7, c_len, endpoint=False)
    pos = chime_idx + int(ci * 0.07 * SAMPLE_RATE)
    if pos + c_len < TOTAL_SAMPLES:
        sfx[pos:pos + c_len] += np.sin(2 * np.pi * cf * ct_arr) * np.exp(-ct_arr * 7) * 0.22

# Sub-bass boom at 1.8s
sub_len = int(SAMPLE_RATE * 1.5)
sbt = np.linspace(0, 1.5, sub_len, endpoint=False)
sfx[int(1.8*SAMPLE_RATE):int(1.8*SAMPLE_RATE)+sub_len] += np.sin(2 * np.pi * (90 * np.exp(-sbt * 4) + 32) * sbt) * np.exp(-sbt * 3) * 0.6

# Combine BGM
bgm = (kick * 0.6 + hat * 0.4 + clap * 0.5 + bass * 0.5 + sfx * 0.8)
bgm = np.tanh(bgm * 1.1) * 0.25 # Clean, comfortable volume

bgm_int16 = (bgm * 32767).astype(np.int16)
stereo_bgm = np.column_stack((bgm_int16, bgm_int16))

with wave.open('/tmp/powerup_audio/clean_bgm.wav', 'w') as wf:
    wf.setnchannels(2)
    wf.setsampwidth(2)
    wf.setframerate(SAMPLE_RATE)
    wf.writeframes(stereo_bgm.tobytes())

print("✅ Clean BGM & Clicks generated: /tmp/powerup_audio/clean_bgm.wav")

# Now mix with single continuous voiceover
cmd = """/opt/homebrew/bin/ffmpeg -y \
  -i /tmp/powerup_audio/clean_bgm.wav \
  -i /tmp/powerup_audio/full_voiceover.mp3 \
  -filter_complex "[0:a]volume=0.22[bgm];[1:a]volume=1.5[vo];[bgm][vo]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[aout]" \
  -map "[aout]" -c:a aac -b:a 192k /tmp/powerup_audio/clean_master_audio.m4a 2>/dev/null
"""
subprocess.run(cmd, shell=True, check=True)
print("🎉 Master audio track with single continuous Azure Neural voice mixed: /tmp/powerup_audio/clean_master_audio.m4a")
