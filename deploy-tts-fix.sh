#!/bin/bash
# TRIDENT TTS Warm-Start Fix — one-shot deploy script
# Run on trident-01: sudo bash deploy-tts-fix.sh
#
# Fixes slow TTS by loading both Piper voice models once at service startup
# instead of spawning a new piper subprocess (and reloading the model from
# disk) on every single /tts request.
set -e

echo "[ TRIDENT ] Deploying Piper warm-start TTS fix..."

echo "[ 1/5 ] Installing piper-tts into existing venv..."
/opt/trident-voice/venv/bin/pip install --quiet piper-tts

echo "[ 2/5 ] Backing up current voice_service.py..."
cp /opt/trident-voice/voice_service.py /opt/trident-voice/voice_service.py.bak

echo "[ 3/5 ] Writing fixed voice_service.py..."
cat > /opt/trident-voice/voice_service.py << 'PYEOF'
#!/usr/bin/env python3
"""
TRIDENT Voice Processing Service
Exposes HTTP endpoints for STT and TTS
Runs on port 5001 (internal only, not exposed via Nginx)
"""
import os
import sys
import io
import tempfile
import subprocess
import wave
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
import json

WHISPER_MODEL_PATH = "/opt/trident-voice/whisper-models"
VOICES_PATH = "/opt/trident-voice/voices"
PORT = 5001

# Load Whisper model at startup
from faster_whisper import WhisperModel
print("Loading Whisper model...")
whisper_model = WhisperModel("tiny", device="cpu", download_root=WHISPER_MODEL_PATH)
print("Whisper model loaded.")

# ---------------------------------------------------------------------------
# Fixed: previously spawned a new piper subprocess per request, forcing a
# full model reload every time. Now loads both voice models once at startup
# and keeps them resident in memory for fast synthesis.
# ---------------------------------------------------------------------------
from piper import PiperVoice

AVAILABLE_VOICES = {
    "lessac": os.path.join(VOICES_PATH, "en_US-lessac-medium.onnx"),
    "amy": os.path.join(VOICES_PATH, "en_US-amy-medium.onnx"),
}

# Load every configured Piper voice model once into memory. Each PiperVoice
# instance holds its own onnxruntime.InferenceSession and stays resident for
# the lifetime of the process, so subsequent TTS requests reuse it directly
# instead of paying disk-load cost on every call.
PIPER_VOICES = {}
for voice_name, voice_model_path in AVAILABLE_VOICES.items():
    print(f"Loading Piper voice model '{voice_name}' from {voice_model_path} ...")
    PIPER_VOICES[voice_name] = PiperVoice.load(voice_model_path)
    print(f"Piper voice model '{voice_name}' loaded.")

DEFAULT_VOICE = "lessac"


class VoiceHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass  # Suppress default logging

    def do_POST(self):
        if self.path == "/stt":
            self._handle_stt()
        elif self.path.startswith("/tts"):
            self._handle_tts()
        else:
            self.send_response(404)
            self.end_headers()

    def _handle_stt(self):
        """Receive audio file, return transcript"""
        try:
            content_length = int(self.headers.get('Content-Length', 0))
            audio_data = self.rfile.read(content_length)

            with tempfile.NamedTemporaryFile(suffix='.webm', delete=False) as f:
                f.write(audio_data)
                tmp_path = f.name

            # Convert to wav for whisper
            wav_path = tmp_path + ".wav"
            subprocess.run([
                "ffmpeg", "-y", "-i", tmp_path, "-ar", "16000", "-ac", "1", wav_path
            ], capture_output=True, check=True)

            segments, info = whisper_model.transcribe(wav_path, beam_size=5)
            transcript = " ".join([seg.text for seg in segments]).strip()

            os.unlink(tmp_path)
            os.unlink(wav_path)

            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"transcript": transcript}).encode())

        except Exception as e:
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"error": str(e)}).encode())

    def _handle_tts(self):
        """Receive text, return WAV audio.

        Fixed: previously spawned a new piper subprocess per request, forcing
        a full model reload every time. Now uses the pre-loaded, in-memory
        PiperVoice instance (loaded once at startup in PIPER_VOICES) to
        synthesize audio directly via the piper-tts Python API, with no
        subprocess spawn and no CLI model reload per call.
        """
        try:
            content_length = int(self.headers.get('Content-Length', 0))
            body = json.loads(self.rfile.read(content_length))
            text = body.get("text", "")
            voice = body.get("voice", DEFAULT_VOICE)

            # Fall back to the default voice if an unknown voice is requested
            # (preserves the original AVAILABLE_VOICES.get(..., lessac) behavior).
            piper_voice = PIPER_VOICES.get(voice, PIPER_VOICES[DEFAULT_VOICE])

            # Synthesize directly into an in-memory WAV buffer. synthesize_wav()
            # writes standard 16-bit PCM WAV frames to the given wave.Wave_write
            # object and sets the WAV header (sample rate/width/channels) itself
            # from the voice's audio output, mirroring what the old piper CLI's
            # --output_file did, just without touching disk or spawning a process.
            wav_buffer = io.BytesIO()
            with wave.open(wav_buffer, "wb") as wav_file:
                piper_voice.synthesize_wav(text, wav_file)

            audio_data = wav_buffer.getvalue()

            self.send_response(200)
            self.send_header("Content-Type", "audio/wav")
            self.send_header("Content-Length", str(len(audio_data)))
            self.end_headers()
            self.wfile.write(audio_data)

        except Exception as e:
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"error": str(e)}).encode())

if __name__ == "__main__":
    server = HTTPServer(("127.0.0.1", PORT), VoiceHandler)
    print(f"TRIDENT Voice Service running on port {PORT}")
    server.serve_forever()
PYEOF

echo "[ 4/5 ] Restarting trident-voice service..."
systemctl restart trident-voice
sleep 4

echo "[ 5/5 ] Verifying startup..."
journalctl -u trident-voice -n 20 --no-pager

echo ""
echo "[ TRIDENT ] Testing a live TTS request..."

cat > /tmp/tts-fix-test-payload.json << 'JSONEOF'
{"text": "Trident voice service is now online with fast synthesis.", "voice": "lessac"}
JSONEOF

HTTP_CODE=$(curl -s -o /tmp/tts-fix-test.wav -w "%{http_code}" -X POST http://127.0.0.1:5001/tts \
  -H "Content-Type: application/json" \
  --data @/tmp/tts-fix-test-payload.json)

if [ "$HTTP_CODE" = "200" ]; then
  FILE_TYPE=$(file -b /tmp/tts-fix-test.wav)
  echo "[ TRIDENT ] TTS test request succeeded (HTTP 200)"
  echo "[ TRIDENT ] Output file: $FILE_TYPE"
  echo "[ TRIDENT ] TTS FIX DEPLOYED SUCCESSFULLY"
else
  echo "[ TRIDENT ] WARNING: TTS test request returned HTTP $HTTP_CODE"
  echo "[ TRIDENT ] Check logs above for errors. Rollback with:"
  echo "  sudo cp /opt/trident-voice/voice_service.py.bak /opt/trident-voice/voice_service.py && sudo systemctl restart trident-voice"
  exit 1
fi

echo ""
echo "[ TRIDENT ] Rollback command if needed later:"
echo "  sudo cp /opt/trident-voice/voice_service.py.bak /opt/trident-voice/voice_service.py && sudo systemctl restart trident-voice"
