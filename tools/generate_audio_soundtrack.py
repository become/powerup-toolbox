import numpy as np
import wave
import subprocess
import os

SAMPLE_RATE = 44100
DURATION = 46.0
TOTAL_SAMPLES = int(SAMPLE_RATE * DURATION)
BPM = 124.0
BEAT_SEC = 60.0 / BPM
t = np.linspace(0, DURATION, TOTAL_SAMPLES, endpoint=False)

print(f"🎵 Generating 124 BPM Electronic Tech Soundtrack ({DURATION}s)...")

# 1. KICK DRUM
kick = np.zeros(TOTAL_SAMPLES)
kick_len = int(SAMPLE_RATE * 0.22)
kt = np.linspace(0, 0.22, kick_len, endpoint=False)
k_env = np.exp(-kt * 18)
k_freq = 140 * np.exp(-kt * 32) + 42
single_kick = np.sin(2 * np.pi * k_freq * kt) * k_env

num_beats = int(DURATION / BEAT_SEC)
for b in range(num_beats):
    # Drop kick in intro first 2 beats and pause at bridge
    if b < 4 or (32 <= b < 36):
        continue
    start_idx = int(b * BEAT_SEC * SAMPLE_RATE)
    end_idx = min(TOTAL_SAMPLES, start_idx + kick_len)
    kick[start_idx:end_idx] += single_kick[:end_idx - start_idx] * 0.85

# 2. HI-HAT & CLAP
hat = np.zeros(TOTAL_SAMPLES)
clap = np.zeros(TOTAL_SAMPLES)
hat_len = int(SAMPLE_RATE * 0.05)
ht = np.linspace(0, 0.05, hat_len, endpoint=False)
single_hat = (np.random.rand(hat_len) * 2 - 1) * np.exp(-ht * 80) * 0.25

clap_len = int(SAMPLE_RATE * 0.15)
ct = np.linspace(0, 0.15, clap_len, endpoint=False)
single_clap = (np.random.rand(clap_len) * 2 - 1) * np.exp(-ct * 30) * 0.4

for b in range(num_beats):
    if b >= 4:
        # Off-beat hat
        h_idx = int((b + 0.5) * BEAT_SEC * SAMPLE_RATE)
        if h_idx + hat_len < TOTAL_SAMPLES:
            hat[h_idx:h_idx + hat_len] += single_hat
    if b >= 8 and (b % 4 == 1 or b % 4 == 3):
        # Clap on beats 2 & 4
        c_idx = int(b * BEAT_SEC * SAMPLE_RATE)
        if c_idx + clap_len < TOTAL_SAMPLES:
            clap[c_idx:c_idx + clap_len] += single_clap

# 3. ROLLING SYNTH BASS (16th notes)
bass = np.zeros(TOTAL_SAMPLES)
sixteenth = BEAT_SEC / 4.0
num_16ths = int(DURATION / sixteenth)
# Chords: F (43.6Hz) -> Db (34.6Hz) -> Eb (38.9Hz) -> C (32.7Hz)
root_notes = [43.65, 43.65, 34.65, 38.89]

for s in range(num_16ths):
    bar = int(s / 16)
    root = root_notes[bar % len(root_notes)]
    # Octave alternation
    f_oct = root * (2 if s % 2 == 1 else 1)
    s_len = int(SAMPLE_RATE * sixteenth * 0.85)
    st_arr = np.linspace(0, sixteenth * 0.85, s_len, endpoint=False)
    b_env = np.exp(-st_arr * 16)
    single_bass = (np.sin(2 * np.pi * f_oct * st_arr) + 0.4 * np.sin(2 * np.pi * f_oct * 2 * st_arr)) * b_env
    idx = int(s * sixteenth * SAMPLE_RATE)
    if idx + s_len < TOTAL_SAMPLES and (s >= 16):
        bass[idx:idx + s_len] += single_bass * 0.45

# 4. CHORDS & ARPEGGIO (Lush synth pads)
synth = np.zeros(TOTAL_SAMPLES)
chord_freqs = [
    [174.61, 220.00, 261.63, 329.63], # Fmaj7
    [138.59, 174.61, 207.65, 261.63], # Dbmaj7
    [155.56, 196.00, 233.08, 293.66], # Eb
    [130.81, 164.81, 196.00, 246.94]  # C
]
bar_sec = BEAT_SEC * 4.0
num_bars = int(DURATION / bar_sec)

for b_idx in range(num_bars):
    c_f = chord_freqs[b_idx % len(chord_freqs)]
    start_s = int(b_idx * bar_sec * SAMPLE_RATE)
    end_s = min(TOTAL_SAMPLES, int((b_idx + 1) * bar_sec * SAMPLE_RATE))
    bt_slice = t[start_s:end_s] - (b_idx * bar_sec)
    fade_env = np.sin(np.pi * (bt_slice / bar_sec)) ** 0.5
    for freq in c_f:
        synth[start_s:end_s] += (np.sin(2 * np.pi * freq * bt_slice) + 0.3 * np.sin(4 * np.pi * freq * bt_slice)) * fade_env * 0.08

# 5. SFX (Sound Effects)
sfx = np.zeros(TOTAL_SAMPLES)

# Sub-bass impact at 0.5s & 39.5s
for imp_t in [0.5, 39.2]:
    imp_len = int(SAMPLE_RATE * 1.5)
    it = np.linspace(0, 1.5, imp_len, endpoint=False)
    sub_imp = np.sin(2 * np.pi * (80 * np.exp(-it * 4) + 30) * it) * np.exp(-it * 3) * 0.7
    idx = int(imp_t * SAMPLE_RATE)
    if idx + imp_len < TOTAL_SAMPLES:
        sfx[idx:idx + imp_len] += sub_imp

# Laser Target Lock at 12.0s (Face detection)
lt_len = int(SAMPLE_RATE * 0.6)
lt = np.linspace(0, 0.6, lt_len, endpoint=False)
laser = np.sin(2 * np.pi * (1800 - lt * 1600) * lt) * np.exp(-lt * 8) * 0.4
# Double high-tech beep
beep1 = np.sin(2 * np.pi * 1760 * lt[:int(SAMPLE_RATE * 0.08)]) * 0.35
beep2 = np.sin(2 * np.pi * 2349 * lt[:int(SAMPLE_RATE * 0.12)]) * 0.4
laser_idx = int(12.0 * SAMPLE_RATE)
if laser_idx + lt_len < TOTAL_SAMPLES:
    sfx[laser_idx:laser_idx + lt_len] += laser
    sfx[laser_idx + int(0.1*SAMPLE_RATE):laser_idx + int(0.1*SAMPLE_RATE) + len(beep1)] += beep1
    sfx[laser_idx + int(0.2*SAMPLE_RATE):laser_idx + int(0.2*SAMPLE_RATE) + len(beep2)] += beep2

# Sparkle Magic Chime at 21.0s (JSON repair)
chime_idx = int(21.0 * SAMPLE_RATE)
for ci, cf in enumerate([1046.5, 1318.5, 1567.9, 2093.0]): # C6, E6, G6, C7
    c_len = int(SAMPLE_RATE * 0.8)
    ct_arr = np.linspace(0, 0.8, c_len, endpoint=False)
    single_c = np.sin(2 * np.pi * cf * ct_arr) * np.exp(-ct_arr * 6) * 0.25
    pos = chime_idx + int(ci * 0.08 * SAMPLE_RATE)
    if pos + c_len < TOTAL_SAMPLES:
        sfx[pos:pos + c_len] += single_c

# Combine BGM + SFX
bgm_mix = (kick * 0.7 + hat * 0.5 + clap * 0.6 + bass * 0.65 + synth * 0.55 + sfx * 0.75)
# Soft limiter
bgm_mix = np.tanh(bgm_mix * 1.2) * 0.32

# Save BGM to file
bgm_int16 = (bgm_mix * 32767).astype(np.int16)
stereo_bgm = np.column_stack((bgm_int16, bgm_int16))

with wave.open('/tmp/powerup_audio/bgm_track.wav', 'w') as wf:
    wf.setnchannels(2)
    wf.setsampwidth(2)
    wf.setframerate(SAMPLE_RATE)
    wf.writeframes(stereo_bgm.tobytes())

print("✅ BGM & SFX Track generated: /tmp/powerup_audio/bgm_track.wav")
