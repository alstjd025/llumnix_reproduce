"""
Inference server 부하 실시간 모니터링.

현재 파서는 vLLM Prometheus metric 이름을 우선 지원한다.
SGlang이 동일 metric을 내지 않으면 기본값 0으로 반환한다.
"""
import threading
import time
import requests
import re
from typing import Dict, Optional
from collections import deque


class LoadMonitor:
    """Inference server 부하를 실시간으로 모니터링"""
    
    def __init__(self, vllm_url: str = "http://localhost:30000", interval: float = 1.0):
        """
        Args:
            vllm_url: inference server URL
            interval: 모니터링 주기 (초)
        """
        self.metrics_url = f"{vllm_url}/metrics"
        self.interval = interval
        
        self.is_running = False
        self.monitor_thread = None
        
        # 최근 상태 저장 (thread-safe)
        self.lock = threading.Lock()
        self.current_state = {
            'running_requests': 0,
            'waiting_requests': 0,
            'kv_cache_usage_pct': 0.0,
            'avg_generation_throughput': 0.0,
            'timestamp': time.time()
        }
        
        # 히스토리 (최근 100개)
        self.history = deque(maxlen=100)
    
    def start(self):
        """모니터링 시작"""
        if self.is_running:
            return
        
        self.is_running = True
        self.monitor_thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self.monitor_thread.start()
        print(f"[LoadMonitor] Started (interval={self.interval}s)")
    
    def stop(self):
        """모니터링 종료"""
        self.is_running = False
        if self.monitor_thread:
            self.monitor_thread.join(timeout=5)
        print("[LoadMonitor] Stopped")
    
    def _monitor_loop(self):
        """모니터링 루프"""
        while self.is_running:
            try:
                state = self._fetch_state()
                
                with self.lock:
                    self.current_state = state
                    self.history.append(state.copy())
            
            except Exception as e:
                # 에러가 나도 계속 시도
                pass
            
            time.sleep(self.interval)
    
    def _fetch_state(self) -> Dict:
        """Inference server 상태 가져오기"""
        try:
            response = requests.get(self.metrics_url, timeout=2)
            response.raise_for_status()
            
            metrics_text = response.text
            
            state = {'timestamp': time.time()}
            
            # Running requests
            match = re.search(r'vllm:num_requests_running\s+([\d.]+)', metrics_text)
            state['running_requests'] = int(float(match.group(1))) if match else 0
            
            # Waiting requests
            match = re.search(r'vllm:num_requests_waiting\s+([\d.]+)', metrics_text)
            state['waiting_requests'] = int(float(match.group(1))) if match else 0
            
            # KV cache usage
            match = re.search(r'vllm:gpu_cache_usage_perc\s+([\d.]+)', metrics_text)
            if match:
                state['kv_cache_usage_pct'] = float(match.group(1)) * 100
            else:
                # blocks로 계산
                blocks_used = re.search(r'vllm:gpu_cache_usage_blocks\s+([\d.]+)', metrics_text)
                blocks_total = re.search(r'vllm:gpu_cache_total_blocks\s+([\d.]+)', metrics_text)
                if blocks_used and blocks_total:
                    used = float(blocks_used.group(1))
                    total = float(blocks_total.group(1))
                    state['kv_cache_usage_pct'] = (used / total * 100) if total > 0 else 0
                else:
                    state['kv_cache_usage_pct'] = 0.0
            
            # Generation throughput
            match = re.search(r'vllm:avg_generation_throughput_toks_per_s\s+([\d.]+)', metrics_text)
            state['avg_generation_throughput'] = float(match.group(1)) if match else 0.0
            
            return state
        
        except Exception as e:
            # 에러시 기본값 반환
            return {
                'timestamp': time.time(),
                'running_requests': 0,
                'waiting_requests': 0,
                'kv_cache_usage_pct': 0.0,
                'avg_generation_throughput': 0.0
            }
    
    def get_current_state(self) -> Dict:
        """현재 서버 상태 반환 (thread-safe)"""
        with self.lock:
            return self.current_state.copy()
    
    def get_average_state(self, last_n: int = 10) -> Dict:
        """최근 N개의 평균 상태 반환"""
        with self.lock:
            recent = list(self.history)[-last_n:]
        
        if not recent:
            return self.get_current_state()
        
        return {
            'running_requests': sum(s['running_requests'] for s in recent) / len(recent),
            'waiting_requests': sum(s['waiting_requests'] for s in recent) / len(recent),
            'kv_cache_usage_pct': sum(s['kv_cache_usage_pct'] for s in recent) / len(recent),
            'avg_generation_throughput': sum(s['avg_generation_throughput'] for s in recent) / len(recent),
        }


if __name__ == "__main__":
    # 테스트
    monitor = LoadMonitor()
    monitor.start()
    
    print("Monitoring for 10 seconds...")
    for i in range(10):
        time.sleep(1)
        state = monitor.get_current_state()
        print(f"[{i+1}] Running: {state['running_requests']}, "
              f"KV Cache: {state['kv_cache_usage_pct']:.1f}%")
    
    avg = monitor.get_average_state()
    print(f"\nAverage: Running={avg['running_requests']:.1f}, "
          f"KV Cache={avg['kv_cache_usage_pct']:.1f}%")
    
    monitor.stop()
