#!/usr/bin/env python3
"""
Download CTranslate2 Whisper Turbo Model
This script downloads the required model for the enhanced ASR service.
"""

import os
import sys
from pathlib import Path
import subprocess

def check_ctranslate2():
    """Check if ctranslate2 is installed"""
    try:
        import ctranslate2
        print("✅ ctranslate2 is installed")
        return True
    except ImportError:
        print("❌ ctranslate2 is not installed")
        print("   Install with: pip3 install ctranslate2")
        return False

def download_model():
    """Download the Whisper Turbo CTranslate2 model"""
    print("🔍 Downloading Whisper Turbo CTranslate2 model...")
    
    # Create models directory
    models_dir = Path("models")
    models_dir.mkdir(exist_ok=True)
    
    # Model path
    model_path = models_dir / "whisper-turbo"
    
    if (model_path / "model.bin").exists():
        print("✅ Model already exists at models/whisper-turbo/")
        return True
    
    print("📥 Downloading model (this may take a while)...")
    
    try:
        # Use ctranslate2 to download the model
        result = subprocess.run([
            sys.executable, "-c", """
import ctranslate2
from faster_whisper import WhisperModel

print("Downloading Whisper Turbo model...")
model = WhisperModel("openai/whisper-large-v3-turbo", download_root="models")
print("Model downloaded successfully!")
"""
        ], capture_output=True, text=True)
        
        if result.returncode == 0:
            print("✅ Model downloaded successfully!")
            return True
        else:
            print(f"❌ Download failed: {result.stderr}")
            return False
            
    except Exception as e:
        print(f"❌ Error downloading model: {e}")
        return False

def verify_model():
    """Verify the downloaded model"""
    print("🔍 Verifying model...")
    
    model_path = Path("models/whisper-turbo")
    required_files = ["model.bin", "config.json", "tokenizer.json"]
    
    for file in required_files:
        if not (model_path / file).exists():
            print(f"❌ Missing required file: {file}")
            return False
    
    print("✅ Model verification successful")
    return True

def main():
    print("🚀 Whisper Turbo Model Downloader")
    print("=" * 40)
    
    # Check dependencies
    if not check_ctranslate2():
        sys.exit(1)
    
    # Download model
    if not download_model():
        print("❌ Failed to download model")
        sys.exit(1)
    
    # Verify model
    if not verify_model():
        print("❌ Model verification failed")
        sys.exit(1)
    
    print("\n🎉 Model download complete!")
    print("📁 Model location: models/whisper-turbo/")
    print("🔧 You can now run the ASR service with: ./start_asr.sh")

if __name__ == "__main__":
    main()
