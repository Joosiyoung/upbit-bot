"""
서버 시작 스크립트 — 중복 실행 방지
기존 포트 5000 프로세스를 종료하고 서버를 시작합니다.
"""
import subprocess
import socket
import time
import sys
import os


PORT = 5000


def kill_existing():
    """포트 5000을 점유 중인 프로세스를 모두 종료"""
    result = subprocess.run(
        ["netstat", "-ano"],
        capture_output=True, text=True, encoding="cp949", errors="ignore"
    )
    pids = set()
    for line in result.stdout.splitlines():
        if f":{PORT}" in line and "LISTENING" in line:
            parts = line.split()
            if parts:
                pids.add(parts[-1])

    if not pids:
        return

    print(f"기존 서버 프로세스 종료 중: PID {', '.join(pids)}")
    for pid in pids:
        subprocess.run(["taskkill", "/F", "/PID", pid],
                       capture_output=True)

    # 포트 해제 대기
    for _ in range(10):
        time.sleep(0.5)
        try:
            with socket.create_connection(("127.0.0.1", PORT), timeout=1):
                pass  # 아직 점유 중
        except OSError:
            break  # 포트 해제됨


def is_port_free():
    try:
        with socket.create_connection(("127.0.0.1", PORT), timeout=1):
            return False
    except OSError:
        return True


if __name__ == "__main__":
    kill_existing()

    if not is_port_free():
        print(f"오류: 포트 {PORT}를 해제하지 못했습니다.")
        sys.exit(1)

    print(f"서버 시작 중... http://127.0.0.1:{PORT}")
    # scripts/ 안에 있으므로 프로젝트 루트(상위 폴더)로 이동 후 app.py 실행
    os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    os.execv(sys.executable, [sys.executable, "app.py"])
