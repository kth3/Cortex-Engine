# Cortex High-Performance Setup (GPU / bf16)

본 문서는 NVIDIA GPU(Ampere 아키텍처 이상) 환경에서 `bf16` 정밀도와 `Flash-Attention`을 활용하여 인덱싱 및 임베딩 속도를 극대화하는 방법을 안내합니다.

GPU가 없거나 저사양이면 이 문서의 extra 설치를 건너뜁니다. 기본 설치만으로도 Cortex는 동작하며, 임베딩은 CPU 또는 사용 가능한 장치에서 실행됩니다.

## 왜 이 설정이 필요한가요?

- **속도**: GPU 가속을 통해 수천 개의 파일을 수초 내에 임베딩할 수 있습니다.
- **정밀도 & 효율**: `bf16` 정밀도는 `fp16`보다 수치적 안정성이 높으며, 메모리 사용량을 절반으로 줄여줍니다.
- **최적화**: `Flash-Attention`은 어텐션 연산을 최적화하여 긴 문맥 처리 시 성능 저하를 방지합니다.

---

## 설치 (uv 기반, 선택 extra)

`pyproject.toml`의 `[project.optional-dependencies]`에 GPU 가속 extra(`gpu-accel`)가 선언되어 있습니다.
**torch CUDA wheel**은 `[tool.uv.sources]`에 의해 CUDA 12.4 빌드가 설치됩니다. `flash-attn` extra는 Linux 환경에서만 적용되므로 Windows에서는 기본 설치 경로를 사용하고, GPU 가속 검증은 WSL/Linux에서 진행합니다.

```bash
# GPU 가속 의존성 포함 전체 동기화
uv sync --extra gpu-accel
```

> **참고**: 위 명령어 한 줄로 PyTorch CUDA 12.4 빌드 + Flash-Attention 프리컴파일 wheel이 모두 설치됩니다.
> 별도의 `pip install --index-url` 이나 수동 wheel 다운로드가 필요하지 않습니다.

---

## 설정 확인

설치 후 아래 명령을 실행하여 `bf16` 지원 여부를 확인할 수 있습니다.

```bash
uv run python -c "import torch; print(f'CUDA Available: {torch.cuda.is_available()}'); print(f'BF16 Supported: {torch.cuda.is_bf16_supported()}')"
```
