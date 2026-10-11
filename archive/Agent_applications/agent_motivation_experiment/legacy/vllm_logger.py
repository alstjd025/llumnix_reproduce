"""
vLLM 서버 로그 파싱 모듈
서버 로그에서 request별 prefill/decode 시간 추출

추가 기능:
- export_log_path를 지정하면, tail로 읽은 라인을 해당 파일로도 그대로 저장하여
  request rate(run)별로 서버 로그를 분리 저장할 수 있습니다.
"""

import re
import time
from typing import Dict, Optional
from threading import Thread, Lock
from collections import defaultdict


class VLLMLogParser:
    """
    vLLM 서버 로그를 실시간으로 파싱하여 메트릭 추출
    """

    def __init__(
        self,
        log_file_path: str,
        export_log_path: Optional[str] = None,
        start_at_end: bool = True,
    ):
        """
        Args:
            log_file_path: vLLM 서버 로그 파일 경로 (server.log)
            export_log_path: (optional) 이 run 구간 로그를 따로 저장할 파일 경로
            start_at_end: True면 파서 시작 시점의 "파일 끝"부터 tail 시작
        """
        self.log_file_path = log_file_path
        self.export_log_path = export_log_path
        self.start_at_end = start_at_end

        self.metrics = defaultdict(dict)
        self.is_running = False
        self.parser_thread = None

        self._export_f = None
        self._export_lock = Lock()

    def start(self):
        """로그 파싱 스레드 시작"""
        if self.is_running:
            return

        self.is_running = True
        self.parser_thread = Thread(target=self._parse_loop, daemon=True)
        self.parser_thread.start()

        if self.export_log_path:
            print(f"[VLLMLogParser] Export enabled: {self.export_log_path}")
        print(f"[VLLMLogParser] Started parsing {self.log_file_path}")

    def stop(self):
        """로그 파싱 스레드 종료"""
        self.is_running = False
        if self.parser_thread:
            self.parser_thread.join(timeout=5)

        # export 파일 닫기
        with self._export_lock:
            if self._export_f is not None:
                try:
                    self._export_f.flush()
                    self._export_f.close()
                except Exception:
                    pass
                self._export_f = None

    def _open_export(self):
        if not self.export_log_path:
            return
        # append 모드로 열어서 run별로 누적 저장 가능(원하면 run별 새 파일로 지정)
        self._export_f = open(self.export_log_path, "a", buffering=1)

    def _export_line(self, line: str):
        if not self.export_log_path:
            return
        with self._export_lock:
            if self._export_f is None:
                self._open_export()
            try:
                self._export_f.write(line)
            except Exception:
                # export 실패해도 파서는 계속 동작
                pass

    def _parse_loop(self):
        """로그 파일을 tail -f 방식으로 읽으면서 파싱"""
        try:
            with open(self.log_file_path, "r") as f:
                # 시작 위치 결정
                if self.start_at_end:
                    f.seek(0, 2)  # end of file
                else:
                    f.seek(0, 0)  # beginning

                while self.is_running:
                    line = f.readline()
                    if not line:
                        time.sleep(0.1)
                        continue

                    # ✅ run별 로그 분리 저장
                    self._export_line(line)

                    # ✅ 메트릭 파싱
                    self._parse_line(line)

        except FileNotFoundError:
            print(f"[VLLMLogParser] Log file not found: {self.log_file_path}")
        except Exception as e:
            print(f"[VLLMLogParser] Error: {e}")

    def _parse_line(self, line: str):
        """로그 라인 파싱"""
        request_id_match = re.search(r"request[_\s]id[=:\s]+([a-zA-Z0-9\-]+)", line)
        if not request_id_match:
            return

        request_id = request_id_match.group(1)

        prompt_tokens_match = re.search(r"prompt[_\s]tokens[=:\s]+(\d+)", line)
        if prompt_tokens_match:
            self.metrics[request_id]["prompt_tokens"] = int(prompt_tokens_match.group(1))

        output_tokens_match = re.search(r"output[_\s]tokens[=:\s]+(\d+)", line)
        if output_tokens_match:
            self.metrics[request_id]["output_tokens"] = int(output_tokens_match.group(1))

        gen_time_match = re.search(r"generation[_\s]time[=:\s]+([\d.]+)", line)
        if gen_time_match:
            self.metrics[request_id]["generation_time"] = float(gen_time_match.group(1))

        prefill_match = re.search(r"prefill[_\s]time[=:\s]+([\d.]+)", line)
        if prefill_match:
            self.metrics[request_id]["prefill_time"] = float(prefill_match.group(1))

        decode_match = re.search(r"decode[_\s]time[=:\s]+([\d.]+)", line)
        if decode_match:
            self.metrics[request_id]["decode_time"] = float(decode_match.group(1))

    def get_metrics(self, request_id: str) -> Optional[Dict]:
        """특정 request_id의 메트릭 반환"""
        return self.metrics.get(request_id)

    def clear_metrics(self, request_id: str):
        """메트릭 삭제 (메모리 정리)"""
        if request_id in self.metrics:
            del self.metrics[request_id]


class MockVLLMLogParser:
    """로그 파싱이 불가능할 경우 사용하는 Mock"""

    def start(self):
        print("[MockVLLMLogParser] Log parsing disabled (using client-side metrics only)")

    def stop(self):
        pass

    def get_metrics(self, request_id: str) -> None:
        return None

    def clear_metrics(self, request_id: str):
        pass


if __name__ == "__main__":
    import tempfile
    import os

    temp_log = tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".log")
    temp_log.write("INFO: Received request abc123: prompt_tokens=150\n")
    temp_log.write("INFO: Finished request abc123: output_tokens=512, generation_time=10.5\n")
    temp_log.flush()
    temp_log.close()

    export_path = temp_log.name + ".export"

    parser = VLLMLogParser(temp_log.name, export_log_path=export_path, start_at_end=False)
    parser.start()

    time.sleep(1)

    metrics = parser.get_metrics("abc123")
    print(f"Parsed metrics: {metrics}")

    parser.stop()

    with open(export_path, "r") as f:
        print("Exported log:")
        print(f.read())

    os.unlink(temp_log.name)
    os.unlink(export_path)
