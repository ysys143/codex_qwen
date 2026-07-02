"""terminal-bench 용 codex 에이전트 — 로컬 ollama(Qwen/gemma 등) 로 라우팅.

기본 CodexAgent 는 OPENAI_API_KEY 만 컨테이너에 넣고 `codex exec --model X` 를
OpenAI 클라우드로 날린다. 이 서브클래스는 컨테이너 안 `~/.codex/config.toml` 에
커스텀 provider(호스트의 ollama, wire_api=responses)를 주입해서 codex 가 로컬
모델로 가게 한다. codex_qwen 배선(ollama 직결)의 terminal-bench 이식판.

전제:
  - Docker Desktop: 컨테이너 → 호스트는 host.docker.internal 로 도달(ollama 127.0.0.1
    바인딩이어도 Docker Desktop 이 라우팅).
  - ollama 는 codex 의 developer 롤을 그대로 받으므로 프록시 불필요(직결).

사용:
  OPENAI_API_KEY=ollama-dummy PYTHONPATH=. \
  tb run -d terminal-bench-core==0.1.1 -t hello-world \
    --agent-import-path tb_codex_local:CodexLocalAgent \
    -m local/gemma4:12b --n-concurrent 1
"""
import os
import shlex

from terminal_bench.agents.installed_agents.codex.codex_agent import CodexAgent
from terminal_bench.terminal.models import TerminalCommand

# 컨테이너에서 본 호스트 ollama 의 /v1 엔드포인트.
LOCAL_LLM_URL = os.environ.get(
    "TB_LOCAL_LLM_URL", "http://host.docker.internal:11434/v1")


class CodexLocalAgent(CodexAgent):
    @staticmethod
    def name() -> str:
        return "codex-local"

    @property
    def _env(self) -> dict[str, str]:
        # ollama 는 키를 무시하지만 codex/auth.json 은 뭔가 있어야 하므로 더미 허용.
        return {"OPENAI_API_KEY": os.environ.get("OPENAI_API_KEY", "ollama-dummy")}

    def _run_agent_commands(self, instruction: str) -> list[TerminalCommand]:
        # provider 를 `-c` 플래그로 직접 주입(config.toml heredoc 회피). terminal-bench 는
        # 명령 끝에 `; tmux wait -S done` 를 덧붙이는데, heredoc 은 종료 EOF 가 단독 줄이어야
        # 해서 그 sentinel 과 충돌해 셸이 멈춘다. 단일 명령 + -c 플래그면 그 문제가 없다.
        esc = shlex.quote(instruction)
        run = TerminalCommand(
            command=(
                "codex exec "
                "--sandbox danger-full-access "
                "--skip-git-repo-check "
                "-c model_providers.local.name='local' "
                f"-c model_providers.local.base_url='{LOCAL_LLM_URL}' "
                "-c model_providers.local.wire_api='responses' "
                "-c model_provider='local' "
                f"--model {self._model_name} "
                "-- "
                f"{esc}"
            ),
            min_timeout_sec=0.0, max_timeout_sec=float("inf"),
            block=True, append_enter=True)
        return [run]
