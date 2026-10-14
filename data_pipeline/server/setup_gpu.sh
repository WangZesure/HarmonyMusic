#!/usr/bin/env bash
# One-shot GPU-server prep for step 04 (lyric forced-alignment). Safe to re-run.
#   bash setup_gpu.sh
# Uses the Tsinghua pip mirror and the HuggingFace mirror (direct HF is blocked).
set -euo pipefail

CONDA_SH=/root/miniconda3/etc/profile.d/conda.sh
[ -f "$CONDA_SH" ] && source "$CONDA_SH"

export PIP_INDEX_URL="${PIP_INDEX_URL:-https://pypi.tuna.tsinghua.edu.cn/simple}"
export HF_ENDPOINT="${HF_ENDPOINT:-https://hf-mirror.com}"
export HF_HOME="${HF_HOME:-/root/autodl-tmp/hf_cache}"

WORK="${WORK:-/root/autodl-tmp/music}"
mkdir -p "$WORK"/{audio,vocals,lrc} "$HF_HOME"

echo "== disk =="; df -h /root/autodl-tmp | tail -1

echo "== ffmpeg =="
if ! command -v ffmpeg >/dev/null 2>&1; then
  apt-get update -y && apt-get install -y ffmpeg
else
  ffmpeg -version | head -1
fi

echo "== python =="; python -V; pip install --upgrade pip >/dev/null

echo "== torch (CUDA) =="
# Pin torchaudio to whatever torch is installed. The AutoDL base image usually
# ships a working torch+cu12x; blindly upgrading torchaudio pulls a cu13 build
# whose libcudart.so.13 is absent -> broke `import torchaudio`/`stable_whisper`.
TORCH_VER="$(python -c 'import torch;print(torch.__version__.split("+")[0])' 2>/dev/null || true)"
if [ -z "$TORCH_VER" ]; then
  pip install torch torchaudio
else
  python -c "import torchaudio" 2>/dev/null || pip install "torchaudio==${TORCH_VER}"
fi
python -c "import torch, torchaudio; print('torch', torch.__version__, 'cuda', torch.cuda.is_available(), '| torchaudio', torchaudio.__version__)"

echo "== demucs + stable-ts =="
pip install demucs stable-ts

echo "== sentence-transformers (for 08 vector index) =="
# Version dance: transformers 5.x wants hub<1.0; a stray `datasets` pulls an
# incompatible hub and breaks transformers' lazy import. Pin hub, drop datasets.
pip install -q sentence-transformers
pip install -q "huggingface_hub>=0.34,<1.0"
pip uninstall -y datasets >/dev/null 2>&1 || true

echo "== sanity =="
python - <<'EOF'
import torch, demucs, stable_whisper, sentence_transformers
print("OK torch", torch.__version__, "cuda", torch.cuda.is_available())
print("OK sentence-transformers", sentence_transformers.__version__)
EOF
echo "== setup done =="
