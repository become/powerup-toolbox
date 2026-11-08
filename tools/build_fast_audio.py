import numpy as np
import wave
import subprocess

SAMPLE_RATE = 44100
DURATION = 16.0
TOTAL_SAMPLES = int(SAMPLE_RATE * DURATION)
BPM = 136.0 # High energy fast tempo
BEAT_SEC = 60.0 / BPM
t = np.linspace(0, DURATION, TOTAL_SAMPLES, endpoint=False)

print(f"⚡ Generating 136 BPM High-Adrenaline Kinetic Soundtrack (16s)...")

# 1. PUNCHY KICK
kick = np.zeros(TOTAL_SAMPLES)
kick_len = int(SAMPLE_RATE * 0.18)
kt = np.linspace(0, 0.18, kick_len, endpoint=False)
single_kick = np.sin(2 * np.pi * (160 * np.exp(-kt * 32) + 42) * kt) * np.exp(-kt * 18)

num_beats = int(DURATION / BEAT_SEC)
for b in range(num_beats):
    if b < 4:
        continue
    idx = int(b * BEAT_SEC * SAMPLE_RATE)
    if idx + kick_len < TOTAL_SAMPLES:
        kick[idx:idx + kick_len] += single_kick * 0.85

# 2. FAST DRUM & BASS SNARE & HI-HATS
snare = np.zeros(TOTAL_SAMPLES)
hat = np.zeros(TOTAL_SAMPLES)

snare_len = int(SAMPLE_RATE * 0.14)
snt = np.linspace(0, 0.14, snare_len, endpoint=False)
single_snare = ((np.random.rand(snare_len) * 2 - 1) * 0.7 + np.sin(2 * np.pi * 210 * snt) * 0.3) * np.exp(-snt * 30)

hat_len = int(SAMPLE_RATE * 0.03)
htt = np.linspace(0, 0.03, hat_len, endpoint=False)
single_hat = (np.random.rand(hat_len) * 2 - 1) * np.exp(-htt * 110) * 0.25

# 16th-note rolling hi-hats
sixteenth = BEAT_SEC / 4.0
num_16ths = int(DURATION / sixteenth)
for s in range(num_16ths):
    if s >= 16:
        h_idx = int(s * sixteenth * SAMPLE_RATE)
        if h_idx + hat_len < TOTAL_SAMPLES:
            hat[h_idx:h_idx + hat_len] += single_hat

# Snare on beats 2 & 4
for b in range(num_beats):
    if b >= 4 and (b % 4 == 1 or b % 4 == 3):
        s_idx = int(b * BEAT_SEC * SAMPLE_RATE)
        if s_idx + snare_len < TOTAL_SAMPLES:
            snare[s_idx:s_idx + snare_len] += single_snare

# 3. ROLLING REESE BASS
bass = np.zeros(TOTAL_SAMPLES)
roots = [43.65, 43.65, 34.65, 38.89] # F -> Db -> Eb
for s in range(num_16ths):
    if s < 16:
        continue
    bar = int(s / 16)
    root = roots[bar % len(roots)]
    s_len = int(SAMPLE_RATE * sixteenth * 0.8)
    st_arr = np.linspace(0, sixteenth * 0.8, s_len, endpoint=False)
    single_bass = (np.sin(2 * np.pi * root * st_arr) + 0.4 * np.sin(4 * np.pi * root * st_arr)) * np.exp(-st_arr * 12)
    b_idx = int(s * sixteenth * SAMPLE_RATE)
    if b_idx + s_len < TOTAL_SAMPLES:
        bass[b_idx:b_idx + s_len] += single_bass * 0.4

# 4. MECHANICAL SWITCH CLICKS (Hits on rapid cuts from 3.5s to 11.5s)
sfx = np.zeros(TOTAL_SAMPLES)
# 16 rapid cuts!
cut_times = [3.5, 4.0, 4.5, 5.0, 5.5, 6.0, 6.5, 7.0, 7.5, 8.0, 8.5, 9.0, 9.5, 10.0, 10.5, 11.0]
click_len = int(SAMPLE_RATE * 0.04)
clt = np.linspace(0, 0.04, click_len, endpoint=False)
single_click = (np.sin(2 * np.pi * 3600 * clt) * 0.6 + (np.random.rand(click_len) * 2 - 1) * 0.4) * np.exp(-clt * 140) * 0.8

for c_t in cut_times:
    c_idx = int(c_t * SAMPLE_RATE)
    if c_idx + click_len < TOTAL_SAMPLES:
        sfx[c_idx:c_idx + click_len] += single_click

# Bass Drop Sub-boom at 1.8s
sub_len = int(SAMPLE_RATE * 1.5)
sbt = np.linspace(0, 1.5, sub_len, endpoint=False)
sfx[int(1.8*SAMPLE_RATE):int(1.8*SAMPLE_RATE)+sub_len] += np.sin(2 * np.pi * (100 * np.exp(-sbt * 5) + 32) * sbt) * np.exp(-sbt * 3) * 0.8

# Mix BGM
bgm = (kick * 0.75 + snare * 0.6 + hat * 0.45 + bass * 0.6 + sfx * 0.9)
bgm = np.tanh(bgm * 1.2) * 0.35

bgm_int16 = (bgm * 32767).astype(np.int16)
stereo_bgm = np.column_stack((bgm_int16, bgm_int16))

with wave.open('/tmp/powerup_audio/fast_bgm.wav', 'w') as wf:
    wf.setnchannels(2)
    wf.setsampwidth(2)
    wf.setframerate(SAMPLE_RATE)
    wf.writeframes(stereo_bgm.tobytes())

print("✅ Fast 136 BPM BGM & SFX ready: /tmp/powerup_audio/fast_bgm.wav")
