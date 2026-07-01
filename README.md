# codex_qwen — codex 에 로컬 Qwen 물리기

OpenAI codex CLI/SDK 를 OpenAI 서버 대신 **로컬 Qwen**(ollama 또는 vLLM)에 물리는
최소 재료. 물리는 법은 백엔드에 따라 둘로 갈린다:

- **ollama → 프록시 불필요.** codex 가 `developer` 롤을 그대로 받아주므로(실측 200)
  격리된 `.codex/config.toml` 에 커스텀 provider 하나만 정의하면 **직결**된다.
- **vLLM → 프록시 필요.** vLLM 은 `developer` 롤을 거부(400)하므로 사이에 `proxy.py`
  를 세워 `developer→system` 만 교정해서 포워드한다.

```
[ollama]  codex ─(/v1/responses)────────────────────────▶ ollama:11434 (Qwen)
[vLLM ]   codex ─(/v1/responses)─▶ proxy.py ─(교정)─▶ vLLM:8000 (Qwen)
```

두 경로 공통의 핵심 두 가지:
1. **커스텀 provider + `wire_api="responses"`** — codex 0.137+ 는 responses 강제.
   provider 를 명시해야 0.142+ 의 `ws://` 트랜스포트로 새지 않는다.
   (`-c openai_base_url` 단독은 codex 0.142 에서 안 됨 — 아래 트러블슈팅.)
2. **격리 `CODEX_HOME`** — 전역 `~/.codex`(훅·trust·로그)를 안 건드리도록
   이 레포의 `.codex/` 를 CODEX_HOME 으로 쓴다.

### 백엔드별 차이 (실측)

| 백엔드 | `/v1/responses` | `developer` 롤 | 프록시 |
|--------|-----------------|----------------|--------|
| ollama | serve 함        | 그냥 받음(200) | **불필요** (직결) |
| vLLM   | serve 함        | **거부(400)**  | **필수** (developer→system 병합) |

응답(usage·function_call·SSE 스트림)은 전부 백엔드가 권위있게 생성한 것을 codex 에
그대로 흘려보낸다. **손으로 짠 스키마 0 개** — codex 버전이 올라가도 잘 안 깨진다.

## 구성

| 파일 | 역할 |
|------|------|
| `.codex/config.toml` | 격리 codex 설정(CODEX_HOME). ollama 직결 provider. **ollama 는 이것만으로 끝.** |
| `proxy.py` | vLLM 용 패스스루 프록시(`developer→system` 교정). 라이브러리+CLI 겸용. |
| `run_codex.py` | 프록시 자동 기동 + codex 실행(주로 vLLM/SDK 경로). CODEX_HOME 격리 포함. |

## 사용법

### 1) 준비

- codex CLI (`codex-cli` 0.137+ — `wire_api=responses` 강제하는 세대)
- 로컬 Qwen 백엔드 중 하나:
  - **ollama**: `ollama serve` + `ollama pull qwen3:0.6b` (엔드포인트 `http://localhost:11434/v1`)
  - **vLLM**: `--served-model-name` 으로 Qwen serve (엔드포인트 `http://HOST:8000/v1`)
- SDK 모드만 추가로: `pip install --prerelease=allow openai-codex`

### 2) ollama — 프록시 없이 격리 config 로 직결 (권장)

> 검증됨: codex-cli 0.142.5 + ollama qwen3:0.6b. 격리 `CODEX_HOME` 로 프롬프트
> "PONG" → codex final "PONG". 전역 `~/.codex` 훅 하나도 안 뜸(격리 확인).

`.codex/config.toml` 의 `model` 을 `ollama list` 의 모델명으로 바꾼 뒤:

```bash
CODEX_HOME="$PWD/.codex" codex exec \
  --skip-git-repo-check --ephemeral \
  --dangerously-bypass-approvals-and-sandbox \
  "이 레포 구조 요약해줘"
```

프록시도, `run_codex.py` 도 필요 없다. config.toml 이 provider(wire_api=responses)를
정의하니 codex 가 ollama `/v1/responses` 로 직결된다.

### 3) vLLM — 프록시를 세우고 직결

vLLM 은 `developer` 롤을 거부하므로 프록시가 필수다.

```bash
# 터미널 A: 프록시 (--model 로 model 주입 → 0.142 "model required" 회피)
python proxy.py --vllm http://10.20.0.9:8000/v1 \
  --model Qwen/Qwen3.6-35B-A3B-FP8 --port 8731

# 터미널 B: config.toml 의 model_provider 를 local_vllm 으로 바꾸거나, -c 로 오버라이드
CODEX_HOME="$PWD/.codex" codex exec \
  -c model_providers.local_vllm.base_url="http://localhost:8731/v1" \
  -c model_providers.local_vllm.wire_api="responses" \
  -c model_provider="local_vllm" \
  -m Qwen/Qwen3.6-35B-A3B-FP8 \
  --skip-git-repo-check --dangerously-bypass-approvals-and-sandbox \
  "이 레포 구조 요약해줘"
```

또는 `run_codex.py` 로 프록시 기동+실행을 한 방에 (CODEX_HOME 격리 자동):

```bash
python run_codex.py \
  --vllm http://10.20.0.9:8000/v1 \
  --model Qwen/Qwen3.6-35B-A3B-FP8 \
  --cwd . --prompt "이 레포 구조 요약해줘" --mode cli
```

### 4) 코드에서 (라이브러리)

```python
from proxy import start_proxy

_, stats = start_proxy("http://localhost:11434/v1", port=8731)
# 이제 codex 를 http://localhost:8731/v1 로 가리키면 됨
# stats["turns"] = 포워드 요청 수, stats["errors"] = 백엔드가 되돌린 에러
```

## codex 를 부르는 인터페이스 두 가지

같은 백엔드(ollama 직결 또는 프록시)를 가리키되 codex 호출 방식만 다르다:

**CLI** — `-c` 로 커스텀 provider 정의(또는 `.codex/config.toml` 에 박아두기):
```
codex exec \
  -c model_providers.local.base_url="http://localhost:8731/v1" \
  -c model_providers.local.wire_api="responses" \
  -c model_provider="local" -m <model> ...
```
> 함정: `-c openai_base_url=...` 만 오버라이드하면 codex 0.142+ 는 기본 provider 의
> 트랜스포트(`ws://`)를 써서 어긋난다. provider 를 명시해야 HTTP responses 로 간다.
> (0.137 에선 openai_base_url 만으로 됐다 — 버전 차이.)

**SDK** — `model_providers` 로 커스텀 provider 주입:
```python
codex.thread_start(
    model=MODEL, model_provider="vllmresp",
    config={"model_providers": {"vllmresp": {
        "name": "vllmresp",
        "base_url": "http://localhost:8731/v1",
        "wire_api": "responses",   # codex 0.137+ 은 responses 강제
    }}}, ...)
```

## 트러블슈팅

| 증상 | 원인 / 해결 |
|------|-------------|
| `developer` 롤 400 | 백엔드(vLLM)가 developer 거부. 프록시를 거치는지 확인 — codex 가 백엔드에 **직결**돼 있으면 발생. `openai_base_url` 이 프록시(localhost:8731)를 가리키는지 확인. |
| `stats["errors"]` 에 400/422 | 프록시는 백엔드 에러를 그대로 codex 로 되돌린다. 이 배열을 보면 백엔드가 실제로 뭘 거부했는지 원문이 보인다. |
| codex 가 함수콜/도구를 안 씀 | 작은 모델(qwen3:0.6b 등)은 tool-use 신뢰성이 낮다. 배선 검증엔 OK, 실제 에이전트 작업엔 큰 Qwen 권장. |
| `openai-codex` import 실패 | prerelease 다. `pip install --prerelease=allow openai-codex`. CLI 모드는 SDK 불필요. |
| `wire_api=chat` 관련 오류 | codex 0.137+ 은 chat 을 드롭하고 responses 강제. 백엔드가 `/v1/responses` 를 serve 하는지 확인(ollama/vLLM 둘 다 지원). |
| `unexpected status 200 OK ... ws://` | codex 0.142+ 가 기본 provider 로 WebSocket 트랜스포트를 시도. **커스텀 provider 를 명시**(`model_providers.local.wire_api="responses"`)하면 HTTP 로 간다. `-c openai_base_url` 단독은 안 됨. |
| `model is required` (ollama 400) | codex 0.142+ 가 responses 바디에 model 을 안 싣는 경우. 프록시 `--model` 로 주입하면 해결(run_codex.py 는 자동 전달). |

## 계보

원본은 `ops_check_engine/research/cx/native_codex.py`(cx 실험용, 채점 로직 혼재).
여기선 재사용 가능한 프록시+러너만 추출했다. 설계 정수는 "스키마를 손으로 짜지
않는다" — 백엔드의 `/v1/responses` 출력을 그대로 패스스루하니 유지보수 표면이 최소.
