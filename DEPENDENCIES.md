# Cortex High-Performance Setup (GPU / bf16)

본 문서는 NVIDIA GPU(Ampere 아키텍처 이상) 환경에서 `bf16` 정밀도와 `Flash-Attention`을 활용하여 인덱싱 및 임베딩 속도를 극대화하는 방법을 안내합니다.

## 🚀 왜 이 설정이 필요한가요?

- **속도**: GPU 가속을 통해 수천 개의 파일을 수초 내에 임베딩할 수 있습니다.
- **정밀도 & 효율**: `bf16` 정밀도는 `fp16`보다 수치적 안정성이 높으며, 메모리 사용량을 절반으로 줄여줍니다.
- **최적화**: `Flash-Attention`은 어텐션 연산을 최적화하여 긴 문맥 처리 시 성능 저하를 방지합니다.

---

## 🛠 설치 (uv 기반 — 단일 명령어)

`pyproject.toml`의 `[dependency-groups]`에 GPU 가속 패키지(flash-attn 포함)가 선언되어 있습니다.
**torch CUDA wheel**은 `[tool.uv.sources]`에 의해 자동으로 올바른 CUDA 12.4 빌드가 설치됩니다.

```bash
# GPU 가속 의존성 포함 전체 동기화 (단일 명령어)
uv sync --project .agents --group gpu-accel
```

> **참고**: 위 명령어 한 줄로 PyTorch CUDA 12.4 빌드 + Flash-Attention 프리컴파일 wheel이 모두 설치됩니다.
> 별도의 `pip install --index-url` 이나 수동 wheel 다운로드가 필요하지 않습니다.

---

## 🔍 설정 확인

설치 후 아래 명령을 실행하여 `bf16` 지원 여부를 확인할 수 있습니다.

```bash
uv run --project .agents python -c "import torch; print(f'CUDA Available: {torch.cuda.is_available()}'); print(f'BF16 Supported: {torch.cuda.is_bf16_supported()}')"
```
