#!/usr/bin/env python3
"""프록시를 띄우고 codex 를 그 프록시에 물려 프롬프트 1개를 실행하는 최소 러너.

codex 를 vLLM(Qwen)에 물리는 방법은 딱 하나 — **openai_base_url 을 로컬 프록시로
가리키는 것**. 그 위에서 codex 를 부르는 인터페이스만 두 가지다:

  --mode cli : `codex exec` 서브프로세스. -c 로 openai_base_url 오버라이드.
               가장 간단. codex CLI 만 있으면 됨.
  --mode sdk : openai-codex 파이썬 SDK. model_providers 로 커스텀 provider 주입.
               세션/스레드를 코드에서 제어할 때.

둘 다 같은 프록시(proxy.py)를 가리킨다. 프록시가 developer 롤만 고쳐 vLLM 로 포워드.

예:
  python run_codex.py --vllm http://10.20.0.9:8000/v1 \\
      --model Qwen/Qwen3.6-35B-A3B-FP8 --cwd . --prompt "이 레포 구조 요약해줘"
"""
import argparse
import os
import subprocess
import tempfile

from proxy import start_proxy

# 격리된 CODEX_HOME — 전역 ~/.codex 를 건드리지 않는다(훅·trust·로그 격리).
ISOLATED_CODEX_HOME = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".codex")


def _codex_env() -> dict:
    env = dict(os.environ)
    env["CODEX_HOME"] = ISOLATED_CODEX_HOME
    return env


def run_cli(port: int, model: str, cwd: str, prompt: str) -> str:
    """`codex exec` 를 프록시에 물려 실행하고 최종 응답 텍스트를 돌려준다."""
    out_file = tempfile.NamedTemporaryFile(
        mode="w", suffix=".txt", delete=False).name
    base = f"http://localhost:{port}/v1"
    # 기본 openai provider 대신 커스텀 provider 를 명시(SDK 와 동일). openai_base_url
    # 만 오버라이드하면 codex 0.142+ 가 기본 provider 의 트랜스포트를 써서 어긋난다.
    cmd = [
        "codex", "exec",
        "-c", 'model_providers.local.name="local"',
        "-c", f'model_providers.local.base_url="{base}"',
        "-c", 'model_providers.local.wire_api="responses"',
        "-c", 'model_provider="local"',
        "-m", model,
        "--skip-git-repo-check", "--ephemeral",
        "--dangerously-bypass-approvals-and-sandbox",
        "-o", out_file, prompt,
    ]
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                       timeout=300, env=_codex_env())
    final = (open(out_file).read().strip() if os.path.exists(out_file) else "")
    if not final:
        final = (r.stdout or "").strip()
    if not final:
        raise RuntimeError(f"codex 출력 없음. stderr: {(r.stderr or '')[-300:]}")
    return final


def run_sdk(port: int, model: str, cwd: str, prompt: str) -> str:
    """openai-codex SDK 로 프록시에 물려 실행. model_providers 로 provider 주입."""
    from openai_codex import ApprovalMode, Codex, Sandbox

    codex = Codex()
    try:
        thread = codex.thread_start(
            model=model,
            model_provider="vllmresp",
            config={"model_providers": {"vllmresp": {
                "name": "vllmresp",
                "base_url": f"http://localhost:{port}/v1",  # <- 핵심 스위치
                "wire_api": "responses",  # codex 0.137+ 은 responses 강제
            }}},
            approval_mode=ApprovalMode.auto_review,
            sandbox=Sandbox.full_access,
            ephemeral=True,
            cwd=cwd,
        )
        result = thread.run(prompt)
        final = getattr(result, "final_response", None)
        if not final:
            raise RuntimeError("codex final_response 없음")
        return str(final)
    finally:
        try:
            codex.close()
        except Exception:
            pass


def main():
    ap = argparse.ArgumentParser(description="codex 를 vLLM(Qwen)에 물려 실행")
    ap.add_argument("--vllm", default="http://localhost:8000/v1",
                    help="vLLM /v1 베이스 URL")
    ap.add_argument("--model", required=True,
                    help="vLLM 이 serve 하는 모델 이름 (예: Qwen/Qwen3.6-35B-A3B-FP8)")
    ap.add_argument("--cwd", default=".", help="codex 가 작업할 디렉토리")
    ap.add_argument("--prompt", required=True, help="codex 에 줄 프롬프트")
    ap.add_argument("--mode", choices=["cli", "sdk"], default="cli")
    ap.add_argument("--port", type=int, default=8731)
    args = ap.parse_args()

    _, stats = start_proxy(args.vllm, args.port, args.model)
    print(f"[proxy] localhost:{args.port}/v1 -> {args.vllm}")

    runner = run_cli if args.mode == "cli" else run_sdk
    final = runner(args.port, args.model, args.cwd, args.prompt)

    print(f"\n[turns={stats['turns']} errors={len(stats.get('errors', []))}]")
    if stats.get("errors"):
        print("[vLLM errors]", stats["errors"][:2])
    print("\n===== codex final =====")
    print(final)


if __name__ == "__main__":
    main()
