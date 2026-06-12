"""
restart.bat 에서 호출하는 프로세스 관리 헬퍼
사용법:
  python restart_helper.py kill   -- app.py 프로세스 전체 종료
  python restart_helper.py check  -- 프로세스 수 최종 확인 + HTTP 응답 검사
"""
import subprocess
import sys
import time
import urllib.request

# Windows cp949 환경에서 한글 출력 안전하게 처리
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

PYTHON_EXE = r"C:\Users\jso84\AppData\Local\Python\pythoncore-3.14-64\python.exe"


def get_app_py_pids():
    """Python 프로세스 중 app.py 를 실행 중인 PID 목록 반환"""
    pids = []
    result = subprocess.run(
        ["wmic", "process",
         "where", "CommandLine like '%python%' and CommandLine like '%app.py%'",
         "get", "ProcessId,CommandLine"],
        capture_output=True, text=True, encoding="utf-8", errors="ignore"
    )
    for line in result.stdout.splitlines():
        line = line.strip()
        lower = line.lower()
        # bash, wmic, restart_helper 자체 제외
        if ("python" in lower and "app.py" in lower
                and "bash" not in lower
                and "wmic" not in lower
                and "restart_helper" not in lower):
            parts = line.rsplit(None, 1)
            try:
                pids.append(int(parts[-1]))
            except (ValueError, IndexError):
                pass
    return pids


def get_port_pids(port=5000):
    """포트 LISTENING PID 반환"""
    pids = []
    result = subprocess.run(
        "netstat -aon", shell=True, capture_output=True, text=True
    )
    for line in result.stdout.splitlines():
        if f":{port}" in line and "LISTENING" in line:
            parts = line.split()
            try:
                pid = int(parts[-1])
                if pid not in pids:
                    pids.append(pid)
            except (ValueError, IndexError):
                pass
    return pids


def kill_all():
    targets = list(set(get_app_py_pids() + get_port_pids(5000)))
    if not targets:
        print("  실행 중인 app.py 프로세스 없음")
        return

    print(f"  종료 대상 PID: {targets}")
    for pid in targets:
        r = subprocess.run(
            f"taskkill /PID {pid} /T /F",
            shell=True, capture_output=True,
            text=True, encoding="utf-8", errors="ignore"
        )
        out = r.stdout.strip()
        print(f"  PID {pid}: {out if out else '종료 완료'}")

    time.sleep(2)

    remaining = list(set(get_app_py_pids() + get_port_pids(5000)))
    if remaining:
        print(f"  경고: 잔여 PID {remaining} 재시도")
        for pid in remaining:
            subprocess.run(f"taskkill /PID {pid} /T /F", shell=True,
                           capture_output=True)
        time.sleep(1)
    else:
        print("  모든 app.py 프로세스 종료 확인")


def check():
    ok = False
    for _ in range(6):
        try:
            code = urllib.request.urlopen(
                "http://127.0.0.1:5000/", timeout=2
            ).getcode()
            print(f"서버 상태: HTTP {code}")
            ok = True
            break
        except Exception:
            time.sleep(1)

    if not ok:
        print("서버 응답 없음 - server.log 확인 필요")
        sys.exit(1)

    app_pids = get_app_py_pids()
    if len(app_pids) == 1:
        print(f"프로세스 정상: 단일 실행 중 (PID {app_pids[0]})")
    elif len(app_pids) > 1:
        print(f"경고: 중복 실행 감지 (PID {app_pids}) - restart.bat 재실행 필요")
    else:
        print("경고: app.py 프로세스를 찾을 수 없음")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "kill":
        kill_all()
    elif cmd == "check":
        check()
    else:
        print(f"사용법: python restart_helper.py kill|check")
        sys.exit(1)
