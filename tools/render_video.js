const puppeteer = require('puppeteer-core');
const fs = require('fs');
const path = require('path');
const { execSync } = require('child_process');

const CHROME_PATH = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const FRAMES_DIR = '/tmp/powerup_frames';
const TOTAL_FRAMES = 400; // 16 seconds at 25 fps
const FPS = 25;
const AUDIO_TRACK = '/tmp/powerup_audio/pure_music_audio.m4a';
const OUT_VIDEO = path.join(__dirname, '../website/assets/product_intro_1080p.mp4');

async function main() {
  console.log('⚡ Starting PowerUp Toolbox 16s Ultra Fast-Paced Reel Production...');
  
  if (fs.existsSync(FRAMES_DIR)) {
    fs.rmSync(FRAMES_DIR, { recursive: true, force: true });
  }
  fs.mkdirSync(FRAMES_DIR, { recursive: true });

  const browser = await puppeteer.launch({
    executablePath: CHROME_PATH,
    headless: 'new',
    args: [
      '--no-sandbox',
      '--disable-setuid-sandbox',
      '--disable-gpu',
      '--window-size=1920,1080'
    ]
  });

  const page = await browser.newPage();
  await page.setViewport({ width: 1920, height: 1080, deviceScaleFactor: 1 });

  const studioUrl = 'http://localhost:18765/website/video_generator.html';
  console.log(`📡 Connecting to Studio: ${studioUrl}`);
  await page.goto(studioUrl, { waitUntil: 'networkidle0' });

  await page.waitForFunction(() => window.assetsReady === true, { timeout: 15000 });
  console.log('✅ All assets loaded successfully in studio!');

  console.log(`🎬 Capturing ${TOTAL_FRAMES} frames at 1920x1080 25fps...`);
  const startTime = Date.now();

  for (let i = 0; i < TOTAL_FRAMES; i++) {
    await page.evaluate((frameIdx) => {
      window.seekFrame(frameIdx);
    }, i);

    const frameNum = String(i).padStart(4, '0');
    const framePath = path.join(FRAMES_DIR, `frame_${frameNum}.png`);

    const canvasElement = await page.$('#stageCanvas');
    await canvasElement.screenshot({ path: framePath, type: 'png' });

    if (i % 50 === 0 || i === TOTAL_FRAMES - 1) {
      const elapsed = ((Date.now() - startTime) / 1000).toFixed(1);
      const progress = ((i / TOTAL_FRAMES) * 100).toFixed(0);
      console.log(`   Frame ${i}/${TOTAL_FRAMES} (${progress}%) - ${elapsed}s elapsed`);
    }
  }

  await browser.close();
  console.log('🎥 All 400 frames captured! Encoding H.264 MP4 with synchronized Fast Audio track...');

  const ffmpegCmd = `/opt/homebrew/bin/ffmpeg -y -framerate ${FPS} -i "${FRAMES_DIR}/frame_%04d.png" -i "${AUDIO_TRACK}" -c:v libx264 -preset fast -pix_fmt yuv420p -crf 18 -c:a aac -b:a 192k -shortest -movflags +faststart "${OUT_VIDEO}"`;
  console.log(`Executing: ${ffmpegCmd}`);
  execSync(ffmpegCmd, { stdio: 'inherit' });

  const stats = fs.statSync(OUT_VIDEO);
  const sizeMB = (stats.size / (1024 * 1024)).toFixed(2);
  console.log(`🎉 16s Fast-Paced Reel created successfully: ${OUT_VIDEO} (${sizeMB} MB)`);
}

main().catch(err => {
  console.error('❌ Video generation failed:', err);
  process.exit(1);
});
