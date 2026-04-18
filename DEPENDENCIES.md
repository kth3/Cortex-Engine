# Cortex High-Performance Setup (GPU / bf16)

본 문서는 NVIDIA GPU(Ampere 아키텍처 이상) 환경에서 `bf16` 정밀도와 `Flash-Attention`을 활용하여 인덱싱 및 임베딩 속도를 극대화하는 방법을 안내합니다.

## 🚀 왜 이 설정이 필요한가요?

- **속도**: GPU 가속을 통해 수천 개의 파일을 수초 내에 임베딩할 수 있습니다.
- **정밀도 & 효율**: `bf16` 정밀도는 `fp16`보다 수치적 안정성이 높으며, 메모리 사용량을 절반으로 줄여줍니다.
- **최적화**: `Flash-Attention`은 어텐션 연산을 최적화하여 긴 문맥 처리 시 성능 저하를 방지합니다.

---

## 🛠 설치 단계 (GPU 전용)

CPU용 패키지를 먼저 설치하고 교체하는 방식이 아닌, 처음부터 CUDA 환경에 최적화된 패키지를 설치하는 것을 권장합니다.

### 1. PyTorch CUDA 빌드 설치
가상환경 생성 직후, CUDA 12.4 대응 빌드를 가장 먼저 설치합니다.

```bash
.agents/venv/bin/pip install torch==2.5.1 torchvision torchaudio --index-url https://download.pytorch.org/whl/cu124
```

### 2. 나머지 공통 의존성 설치
PyTorch 설치가 완료되면, 프로젝트에 필요한 나머지 의존성을 설치합니다. (이미 CUDA 버전이 설치되어 있으므로 `torch`는 중복 다운로드되지 않고 유지됩니다.)

```bash
.agents/venv/bin/pip install -r .agents/requirements.txt
```

### 3. Flash-Attention 설치 (선택)
Ampere(RTX 30xx) 이상의 GPU에서 `bf16` 가속을 활성화하기 위해 필수적입니다. 빌드 시간 단축을 위해 Pre-built Wheel을 사용합니다.

```bash
# Python 3.12 / CUDA 12.4 환경용 Pre-built Wheel
.agents/venv/bin/pip install https://github.com/Dao-AILab/flash-attention/releases/download/v2.8.3/flash_attn-2.8.3%2Bcu12torch2.5cxx11abiFALSE-cp312-cp312-linux_x86_64.whl
```

---

## 🔍 설정 확인

설치 후 아래 명령을 실행하여 `bf16` 지원 여부를 확인할 수 있습니다.

```bash
.agents/venv/bin/python3 -c "import torch; print(f'CUDA Available: {torch.cuda.is_available()}'); print(f'BF16 Supported: {torch.cuda.is_bf16_supported()}')"
```
