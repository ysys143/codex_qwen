# codex_qwen — codex 에 로컬 Qwen 물리기

OpenAI **codex**(CLI/SDK)를 OpenAI 클라우드 대신 **내 로컬 모델**(ollama·vLLM 위의
Qwen/gemma 등)에 붙여 쓰는 가장 짧은 방법을 담은 가이드입니다.

**이 가이드로 할 수 있는 것**
- codex 를 ollama 로컬 모델에 붙여 바로 코딩 시키기 (5분)
- GPU 서버(vLLM)에 붙이기
- codex 를 [terminal-bench](https://github.com/laude-institute/terminal-bench) 같은 벤치에 로컬 모델로 돌리기

**이런 분께**: codex 는 써봤지만 로컬/사내 모델에 물려본 적 없는 분. 명령은 전부
복사-붙여넣기로 동작하도록 적었고, 각 단계마다 "이렇게 나오면 성공"을 붙였습니다.

---

## 5분 빠른시작 — ollama

가장 쉬운 경로입니다. **ollama 는 프록시가 필요 없어서** config 파일 하나만 고치면 됩니다.

### 준비물
- **codex CLI** — 터미널에서 `codex --version` 이 나오면 OK (0.137 이상 권장)
- **ollama + 모델 하나** — 예: `ollama pull gemma4:12b` (또는 가진 모델 아무거나)
  - `ollama list` 로 설치된 모델 이름을 확인해 두세요.

> 팁: 코딩(파일 쓰기·명령 실행)까지 시키려면 **도구 호출(tool-calling)을 학습한 모델**이
> 필요합니다. `gemma4:12b` 급은 됩니다. `qwen3:0.6b` 같은 초소형은 배선 확인엔 되지만
> 실제 코딩은 잘 못합니다(뒤 "더 알아보기" 참고).

### 1) 모델 이름 넣기
이 레포의 `.codex/config.toml` 을 열어 `model` 값을 `ollama list` 의 이름으로 바꿉니다:

```toml
model = "gemma4:12b"          # <- 여기를 내 모델 이름으로
model_provider = "local_ollama"

[model_providers.local_ollama]
name = "Local Ollama"
base_url = "http://localhost:11434/v1"
wire_api = "responses"
```

### 2) codex 실행 (격리 모드)
레포 폴더에서:

```bash
CODEX_HOME="$PWD/.codex" codex exec \
  --skip-git-repo-check --ephemeral \
  --dangerously-bypass-approvals-and-sandbox \
  "Reply with exactly the single word: PONG"
```

### 3) 성공 확인
마지막에 이렇게 나오면 성공입니다:

```
PONG
```

이제 프롬프트만 바꾸면 됩니다. 예를 들어 실제 코딩:

```bash
CODEX_HOME="$PWD/.codex" codex exec \
  --skip-git-repo-check --ephemeral \
  --dangerously-bypass-approvals-and-sandbox \
  -C /tmp/work \
  "Create fizzbuzz.py with a fizzbuzz(n) function. Use the shell to write it."
```

> **왜 `CODEX_HOME="$PWD/.codex"` 인가?** 전역 `~/.codex`(내 개인 codex 설정·훅·로그)를
> 안 건드리려는 격리 장치입니다. 이 레포의 `.codex/` 를 codex 의 집으로 지정해서, 실험이
> 개인 환경을 오염시키지 않습니다. (실측: 이렇게 하면 개인 훅이 하나도 안 딸려 나옵니다.)

---

## 원리 한 컷

codex 는 요청을 OpenAI `/v1/responses` 형식으로 보냅니다. 로컬 백엔드가 이 엔드포인트를
서빙하면, codex 가 그쪽을 바라보게만 하면 됩니다.

```
[ollama]        codex ─(/v1/responses)──────────────────▶ ollama:11434
[vLLM · 직결]    codex ─(/v1/responses)──────────────────▶ vLLM:8000      ← 서버 chat_template 패치 시(검증됨)
[vLLM · 프록시]  codex ─(/v1/responses)─▶ proxy.py ─(교정)─▶ vLLM:8000      ← 서버를 못 고칠 때만
```

어느 경로든 두 가지만 지키면 됩니다:

1. **커스텀 provider + `wire_api="responses"`** — codex 0.137+ 는 responses API 를
   강제합니다. provider 를 명시해야 최신 codex(0.142+)가 엉뚱한 `ws://` 연결로 새지 않습니다.
2. **격리 `CODEX_HOME`** — 위에서 본, 개인 환경 보호 장치.

**vLLM 도 사실 프록시 없이 됩니다.** codex 는 시스템 지시를 `developer` 역할로 보내는데,
ollama 는 그냥 받지만(실측 200) vLLM 은 거부합니다(400). 이 거부는 vLLM 서버가 쓰는
chat_template 한 줄에서 나므로, **서버를 직접 관리한다면 template 을 고쳐 프록시 없이
직결**할 수 있습니다(llm-test 에서 developer→200 검증). 프록시는 **서버를 못 건드릴 때**
(예: 공유 vLLM)를 위한 대안으로, `developer`→`system` 만 교정해 전달합니다.
(근거·방법은 맨 아래 "더 알아보기".)

---

## vLLM(GPU 서버)에 붙이기

vLLM 이 `developer` 를 거부할 때 길은 둘입니다:
- **(A) 서버를 직접 관리한다면** — `--chat-template` 로 developer 분기를 추가하면 프록시
  없이 직결됩니다(방법·검증은 맨 아래 "더 알아보기"). 가장 깔끔한 근본 해결입니다.
- **(B) 서버를 못 건드리면**(공유 vLLM 등) — 아래처럼 `proxy.py`(동봉)를 세웁니다.
  요청의 `developer`->`system` 만 고쳐 전달하는 얇은 중계기입니다.

아래는 (B) 프록시 경로입니다.

```bash
# 터미널 A — 프록시 기동 (--model 로 모델명 주입: 0.142 의 "model required" 회피)
python proxy.py --vllm http://<서버IP>:8000/v1 \
  --model Qwen/Qwen3.6-35B-A3B-FP8 --port 8731

# 터미널 B — codex 를 프록시로 보냄
CODEX_HOME="$PWD/.codex" codex exec \
  -c model_providers.local_vllm.base_url="http://localhost:8731/v1" \
  -c model_providers.local_vllm.wire_api="responses" \
  -c model_provider="local_vllm" \
  -m Qwen/Qwen3.6-35B-A3B-FP8 \
  --skip-git-repo-check --dangerously-bypass-approvals-and-sandbox \
  "이 레포 구조 요약해줘"
```

**한 방에 하기** — `run_codex.py` 가 프록시 기동 + codex 실행 + CODEX_HOME 격리를 알아서 합니다:

```bash
python run_codex.py \
  --vllm http://<서버IP>:8000/v1 \
  --model Qwen/Qwen3.6-35B-A3B-FP8 \
  --cwd . --prompt "이 레포 구조 요약해줘" --mode cli
```

> 성공하면 `[turns=N errors=0]` 과 함께 codex 의 최종 답이 출력됩니다. `errors` 에 숫자가
> 뜨면 백엔드가 되돌린 원문 에러이니 그걸 보고 원인을 잡으세요.

---

## SDK 로 붙이기 (선택)

CLI 대신 `openai-codex` 파이썬 SDK 로 부르고 싶다면:

```bash
# 설치 (uv 권장) — prerelease 입니다
uv venv --python 3.11 .venv
uv pip install --python .venv/bin/python --prerelease=allow openai-codex

# 실행
.venv/bin/python run_codex.py \
  --vllm http://localhost:11434/v1 \
  --model gemma4:12b \
  --cwd . --prompt "이 레포 구조 요약해줘" --mode sdk
```

> **주의**: SDK 는 **자체 codex 바이너리를 번들**합니다(현재 `openai-codex-cli-bin 0.137`).
> 시스템 `codex`(0.142+)와 버전이 달라 동작이 미묘하게 다를 수 있습니다. CLI 모드는 시스템
> codex 를, SDK 모드는 번들 codex 를 씁니다.

라이브러리로 프록시만 띄우고 싶다면:

```python
from proxy import start_proxy
_, stats = start_proxy("http://localhost:11434/v1", port=8731)
# 이제 codex 를 http://localhost:8731/v1 로 가리키면 됩니다.
```

---

## 터미널 벤치에 돌리기 (선택)

`tb_codex_local.py`(동봉)는 codex 를 로컬 ollama 모델로
[terminal-bench](https://github.com/laude-institute/terminal-bench) 에 돌리는 어댑터입니다.

```bash
uv tool install terminal-bench
tb datasets download -d terminal-bench-core==0.1.1 --output-dir /tmp/tbcore

# Colima 를 쓴다면(Docker Desktop 아님) 소켓 경로를 알려줘야 합니다
export DOCKER_HOST="unix://$HOME/.colima/default/docker.sock"

OPENAI_API_KEY=ollama-dummy PYTHONPATH=. tb run \
  -p /tmp/tbcore -t hello-world \
  --agent-import-path tb_codex_local:CodexLocalAgent \
  -m local/gemma4:12b --n-concurrent 1 --no-livestream
```

> `-m local/gemma4:12b` 의 `local/` 접두는 형식상 필요(codex 는 `/` 뒤만 모델명으로 씀 ->
> `gemma4:12b`). ollama `list` 의 이름으로 바꾸세요.

**실측 결과** (codex-cli + gemma4:12b, Mac/Colima):

| 태스크 | 난이도 | 결과 |
|--------|--------|------|
| hello-world | easy | PASS (~1분, 2/2) |
| heterogeneous-dates | medium | PASS (~10분, 3/3) — 단 `--global-agent-timeout-sec 3600` 필요 |

> heterogeneous-dates 는 기본 360초 캡에선 정답을 다 계산하고도 파일 저장 직전에 시간이
> 끊깁니다. 캡만 늘리면 통과합니다. 로컬 12B 의 추론 속도가 병목이지 역량 문제가 아닙니다.

자주 만나는 함정은 아래 "문제 해결" 표에 있습니다.

---

## 문제 해결

증상이 보이면 오른쪽대로 해보세요.

| 증상 | 이렇게 하세요 |
|------|-------------|
| `developer` 롤 **400** | 백엔드가 vLLM 인데 프록시를 안 거쳤을 때. codex 가 프록시(`localhost:8731`)를 보게 했는지 확인. (ollama 는 이 에러가 안 납니다.) |
| `unexpected status 200 OK ... ws://` | codex 0.142+ 가 기본 provider 로 WebSocket 을 시도한 것. **커스텀 provider 를 명시**(`model_providers.<name>.wire_api="responses"`)하세요. `-c openai_base_url` **단독은 안 됩니다.** |
| `model is required` (ollama 400) | codex 0.142+ 가 바디에 모델명을 안 실은 경우. 프록시에 `--model` 을 주면 주입해 줍니다(`run_codex.py` 는 자동). |
| `wire_api=chat` 관련 오류 | codex 0.137+ 는 chat 을 드롭하고 responses 만 씁니다. 백엔드가 `/v1/responses` 를 서빙하는지 확인(ollama·vLLM 둘 다 지원). |
| codex 가 파일/도구를 안 씀 | 모델이 너무 작습니다(예: `qwen3:0.6b`). 배선 확인엔 되지만 실제 코딩엔 도구 호출을 학습한 큰 모델(gemma4:12b 급)을 쓰세요. |
| `openai-codex` import 실패 | prerelease 라 `uv pip install --prerelease=allow openai-codex` 필요. CLI 모드는 SDK 없이 됩니다. |
| `docker ... Connection aborted, FileNotFoundError` (terminal-bench) | **Colima** 사용 시 발생. `export DOCKER_HOST="unix://$HOME/.colima/default/docker.sock"`. |
| `Template file not found: codex-setup.sh.j2` (terminal-bench) | 어댑터는 템플릿을 자기 폴더에서 찾습니다. 동봉된 `codex-setup.sh.j2` 가 `tb_codex_local.py` 옆에 있어야 합니다. |

---

## 이 레포에 뭐가 있나

| 파일 | 역할 |
|------|------|
| `.codex/config.toml` | 격리 codex 설정(CODEX_HOME). **ollama 는 이 파일만 고치면 끝.** |
| `proxy.py` | vLLM 용 프록시(`developer->system` 교정). 라이브러리+CLI 겸용. |
| `run_codex.py` | 프록시 기동 + codex 실행(CLI/SDK), CODEX_HOME 격리 포함. |
| `tb_codex_local.py` + `codex-setup.sh.j2` | terminal-bench 어댑터. |

---

## 더 알아보기

<details>
<summary><b>vLLM 은 왜 developer 롤을 거부하나 — 원인과 두 해법 (실증)</b></summary>

codex 는 시스템 지시를 `developer` 역할로 보내는데 vLLM 은 400 `"Unexpected message
role."` 로 거부합니다. 실 vLLM(Qwen3.6, 0.22.0)에서 추적한 결과, 이 거부는 서버 파이썬
코드가 아니라 모델의 Jinja chat template 에서 납니다:

```jinja
{%- if message.role == "system" %} ...
{%- elif message.role == "user" %} ...
{%- elif message.role == "assistant" %} ...
{%- elif message.role == "tool" %} ...
{%- else %}{{- raise_exception('Unexpected message role.') }}   <- developer 가 여기로
```

400 바디의 문자열(`Unexpected message role.`, 마침표까지)이 Qwen 템플릿의 `raise_exception`
과 정확히 일치합니다. 거부가 template 계층이라 두 가지로 고칠 수 있습니다:

- **해법 A — 엣지 프록시 (이 레포)**: `developer->system` 을 요청단에서 relabel.
  vLLM 무변경, 모델·버전 무관, 요청 단위 격리. `developer` 없는 요청엔 no-op.
- **해법 B — 서버측 `--chat-template`**: developer 분기를 추가한 템플릿으로 vLLM 기동.
  ```jinja
  {%- elif message.role == "developer" %}
      {{- '<|im_start|>system\n' + content + '<|im_end|>' + '\n' }}
  ```
  ```bash
  vllm serve <model> --chat-template /path/to/qwen_dev_patched.jinja ...
  ```

**검증** (llm-test A100 VM, Qwen3.6-27B-FP8):

| 항목 | 결과 |
|------|------|
| 패치 전 developer 롤 | **400** `"Unexpected message role."` |
| 실 템플릿 + transformers 렌더러(=vLLM) | 원본->raise / 패치->렌더 |
| 기존 트래픽(system/user/assistant/tool) | 패치 전후 **바이트 동일 -> 영향 0** |
| `--chat-template` 적용·재기동 후 developer 롤 | **200**, 모델이 developer 지시 수신 |

**어느 걸 쓰나**: **공유 vLLM**(여러 서비스가 함께 쓰는 서버)이면 재기동·전역 변경이 없는
프록시(A)가 안전합니다. **전용 인스턴스**면 근본해결인 `--chat-template`(B)가 깔끔합니다.
(참고: 최신 vLLM `chat_utils` 는 developer 를 인지하므로 업그레이드도 서버측 해법입니다.)

</details>

<details>
<summary><b>계보 — 이 코드는 어디서 왔나</b></summary>

원본은 `ops_check_engine/research/cx/native_codex.py`(cx 실험용, 채점 로직 혼재)입니다.
여기선 재사용 가능한 프록시+러너만 추출했습니다. 설계 정수는 **"스키마를 손으로 짜지
않는다"**는 것입니다. 백엔드의 `/v1/responses` 출력을 그대로 패스스루하니, codex 가 버전업해도 잘
깨지지 않습니다.

</details>
