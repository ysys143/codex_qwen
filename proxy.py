#!/usr/bin/env python3
"""codex ↔ vLLM(Qwen) 패스스루 프록시.

codex 는 OpenAI `/v1/responses` 스키마로 요청을 보낸다. 이 요청을 vLLM 이 이미
serve 하는 `/v1/responses` 로 **거의 그대로** 포워드하되, 딱 한 가지 —
`developer` 롤 — 만 고쳐서 vLLM 의 제약을 통과시킨다.

왜 그냥 포워드하면 안 되나:
  - codex 는 시스템 지시를 `developer` 롤 항목으로 실어 보낸다.
  - vLLM 의 `/v1/responses` 는 `developer` 롤을 거부하고, `system` 은 맨 앞
    (instructions)에만 허용한다.
  - 따라서 codex → vLLM 직결 시 developer 롤 400 으로 죽는다.
  - 이 프록시가 developer 텍스트를 instructions(선두 system)에 병합·제거하면
    "developer 거부 + system 은 앞에만" 두 제약이 한 번에 풀린다.

응답(usage·function_call·SSE 스트림)은 전부 vLLM 이 권위있게 생성한 것을 codex 에
그대로 흘려보낸다. 손으로 짠 스키마는 0 개 — 그래서 codex 버전이 올라가도
잘 안 깨진다.

라이브러리로:  from proxy import start_proxy
CLI 로:        python proxy.py --port 8731 --vllm http://HOST:8000/v1
"""
import argparse
import http.server
import json
import threading
import urllib.error
import urllib.request
from typing import Any

DEFAULT_VLLM = "http://localhost:8000/v1"


def _fix_developer(data: dict, model: str = "") -> dict:
    """codex 요청을 백엔드 제약에 맞게 교정.

    (1) `developer` 롤 input 항목을 instructions(선두 system)에 병합·제거
        — vLLM 은 developer 롤을 거부하므로.
    (2) `model` 이 비어 있으면 주입 — codex 0.142+ 는 커스텀 base_url 로 responses
        요청을 보낼 때 바디에 model 을 안 싣는 경우가 있어 백엔드가 400 을 낸다.
    """
    if model and not data.get("model"):
        data["model"] = model
    dev_texts, new_input = [], []
    for item in data.get("input", []) or []:
        if isinstance(item, dict) and item.get("role") == "developer":
            c = item.get("content")
            txt = c if isinstance(c, str) else "".join(
                x.get("text", "") for x in (c or []) if isinstance(x, dict))
            if txt:
                dev_texts.append(txt)
        else:
            new_input.append(item)
    if dev_texts:
        data["instructions"] = ((data.get("instructions", "") + "\n\n" +
                                 "\n\n".join(dev_texts)).strip())
        data["input"] = new_input
    return data


def _make_handler(vllm_base: str, stats: dict, model: str = ""):
    class H(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, format, *args):
            pass  # 조용히 — codex 가 이미 자체 로깅함

        def do_GET(self):
            # codex 의 헬스체크/probe 응답
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", "4")
            self.end_headers()
            self.wfile.write(b'"ok"')

        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length)
            try:
                data = _fix_developer(json.loads(raw or b"{}"), model)
            except Exception:
                data = {}
            stats["turns"] += 1
            new_body = json.dumps(data).encode()

            # vLLM 로 패스스루 (path 그대로 유지)
            path = self.path if self.path.startswith("/v1") else "/v1/responses"
            url = vllm_base.rsplit("/v1", 1)[0] + path
            req = urllib.request.Request(
                url, data=new_body, method="POST",
                headers={"Content-Type": "application/json",
                         "Accept": self.headers.get("Accept", "text/event-stream")})
            try:
                r = urllib.request.urlopen(req, timeout=300)
                body = r.read()
                self.send_response(r.status)
                ct = r.headers.get("Content-Type", "text/event-stream")
                self.send_header("Content-Type", ct)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except urllib.error.HTTPError as e:
                eb = e.read()
                stats.setdefault("errors", []).append(
                    eb.decode("utf-8", "replace")[:300])
                self.send_response(e.code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(eb)))
                self.end_headers()
                self.wfile.write(eb)
            except Exception as ex:
                msg = json.dumps({"error": {"message": str(ex)}}).encode()
                try:
                    self.send_response(500)
                    self.send_header("Content-Length", str(len(msg)))
                    self.end_headers()
                    self.wfile.write(msg)
                except Exception:
                    pass
    return H


def start_proxy(vllm_base: str = DEFAULT_VLLM, port: int = 8731, model: str = ""):
    """프록시를 데몬 스레드로 띄우고 (server, stats) 를 돌려준다.

    codex 는 http://localhost:{port}/v1 을 openai_base_url 로 가리키면 된다.
    model 을 주면 바디에 model 이 없을 때 주입한다(codex 0.142+ 호환).
    stats["turns"] = 포워드한 요청 수, stats["errors"] = 백엔드가 되돌린 에러들.
    """
    stats: dict[str, Any] = {"turns": 0}
    server = http.server.ThreadingHTTPServer(
        ("localhost", port), _make_handler(vllm_base, stats, model))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, stats


def main():
    ap = argparse.ArgumentParser(description="codex ↔ vLLM(Qwen) 패스스루 프록시")
    ap.add_argument("--vllm", default=DEFAULT_VLLM,
                    help=f"vLLM /v1 베이스 URL (기본 {DEFAULT_VLLM})")
    ap.add_argument("--port", type=int, default=8731,
                    help="프록시가 listen 할 로컬 포트 (기본 8731)")
    ap.add_argument("--model", default="",
                    help="바디에 model 이 없을 때 주입할 모델명 (codex 0.142+ 호환)")
    args = ap.parse_args()

    _, stats = start_proxy(args.vllm, args.port, args.model)
    print(f"[proxy] localhost:{args.port}/v1  ->  {args.vllm}  (Ctrl-C 로 종료)")
    try:
        threading.Event().wait()  # 포그라운드 유지
    except KeyboardInterrupt:
        errs = stats.get("errors") or []
        print(f"\n[proxy] 종료. 포워드한 요청 {stats['turns']}건, "
              f"에러 {len(errs)}건")


if __name__ == "__main__":
    main()
