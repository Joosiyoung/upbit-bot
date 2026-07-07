# 업그레이드 노트 — 백테스트-라이브 정합성 작업

이 문서는 이 프로젝트에 적용 중인 구조 개선 작업의 현황·근거·남은 일을 정리한다.
방법론 원본은 [TRADING_BOT_PLAYBOOK.md](TRADING_BOT_PLAYBOOK.md) 참조.
(작업 시작 2026-07-07. 별도 트레이딩 봇 프로젝트 `d:\Fable`의 설계를 이 봇에 이식하는 중.)

---

## 왜 이 작업을 하는가

이 봇의 Google Sheets '코인' 시트에 쌓인 실시간 시뮬 271건(2026-06~07, 수수료 반영)을
분석한 결과: **백테스트 기대값 +0.15%/거래 vs 실시간 시뮬 -0.45%/거래**.
이 괴리의 구조적 원인은 "백테스터와 라이브가 서로 다른 코드로 같은 판정을 중복 구현"한 것이었다.
→ 판정 로직을 **단일 구현으로 통일**해 괴리 원인을 제거하는 것이 이 작업의 목표.

원칙: **매매 동작을 바꾸지 않는다.** 결과가 동일함을 특성 테스트(characterization test)와
스모크 백테스트로 매번 확인하며 리팩토링만 한다. (라이브가 VPS에서 돌고 있으므로 빅뱅 재작성 금지.)

---

## 완료 (Phase A + B)

### Phase A — 검증 인프라
- **특성 테스트 41개** (`tests/`) — 현재 동작을 스펙으로 고정. 리팩토링 안전망.
  - 실행: `pip install -r requirements-dev.txt && python -m pytest tests`
  - `conftest.py`가 `GOOGLE_SHEETS_ID`를 비운다 — 안 그러면 `core.trader` 임포트만으로
    실제 Google Sheets에 연결되는 부작용이 있음(모듈 임포트 시점 접속).
- **backtest.py 진단 기능 추가**: 청산 사유별 손익 분해, `--split`(in/out-of-sample 분리),
  `--sweep-threshold`(과최적화 검증, OHLCV 캐시로 API 재호출 없음).

### Phase B — 드리프트 제거 (백테스트=라이브 단일 코드)
두 판정 로직이 각각 중복 구현돼 있던 것을 단일 구현으로 통일:

| 구분 | 단일 구현 | 공유하는 곳 |
|---|---|---|
| 청산 판정 | `core/exit_rules.py` (`judge_exit`, `net_profit_pct`) | trader.py + backtest.py |
| 진입 점수 가중치 | `core/scoring.py` (`weighted_score`, 장2.5/중2/단0.5) | data_builder.py 2곳 + backtest.py |

- 청산 회계를 **양방향 수수료 실수령 기준**으로 통일(과거 백테스터는 편도라 트리거가 ~0.1% 어긋남).
  이는 Sheets에 적재되는 `profit_pct`와 동일한 기준.
- 테스트가 `trader.judge_exit is exit_rules.judge_exit` 동일성과 legacy 수식 등가를 보증.
- 스모크 백테스트로 리팩토링 전후 결과 완전 동일 확인(15건, in +2.47% / out +1.61%).

**커밋 상태**: 커밋·`git push` 완료(`52c4b40`, 2026-07-07). VPS `git pull`+재시작 반영 완료.

---

## 남은 일 (우선순위 순)

| # | 항목 | 위험도 | 비고 |
|---|---|---|---|
| 0 | ~~변경분 커밋·VPS 반영~~ ✅ 완료 | — | `52c4b40` push + VPS 반영 (2026-07-07) |
| 1 | **파라미터 재검증** (`--days 180 --split`, threshold 스윕) | 무위험 | 코드 안 바꿈. 지금 바로 권장 |
| 2 | 주식 모듈에 같은 처리 (특성 테스트 + 단일 구현 추출) | 중 | 코인과 동일 패턴 |
| 3 | 전략 추상화 + PaperBroker A/B 시뮬 | 높음 | 라이브 경로 변경. 1번 검증 후 판단 |
| 4 | 상태 SQLite 영속화 (Phase C) | 선택 | `d:\Fable`의 SqliteStore 참고 |

### 0. [완료] 변경분 커밋·VPS 반영
Phase A/B 변경은 `52c4b40`로 커밋·`git push`됐고 VPS `git pull`+`systemctl restart upbit-bot`으로
반영 완료(2026-07-07). 라이브 매매 동작은 바뀌지 않음(리팩토링만).

### 1. [권장·무위험] 파라미터 재검증
이제 백테스터를 신뢰할 수 있으니, 실돈/코드 변경 전에 데이터부터 확인:
```bash
python scripts/backtest.py --days 180 --split 2026-05-01              # 더 긴 기간 in/out
python scripts/backtest.py --days 180 --sweep-threshold 8,10,12,14 --split 2026-05-01
```
- 청산 사유별 분해에서 **매도신호 청산이 손실 경로인지** 확인(짧은 표본에선 승률 28.6%였음).
  손실 주범이면 `--no-signal-exit`로 효과 측정.
- threshold 12~14가 in/out 양쪽에서 일관 우위인지 재확인(30일 표본에선 그랬으나 상승장 편향).

### 2. [선택·중위험] 주식 모듈에 같은 처리 적용
`core/stock/`과 `scripts/stock_backtest.py`는 아직 특성 테스트·단일 구현 정리가 안 됨.
README에 "KIS 페이징 버그 수정 전 데이터 기반, 재검증 필요"로 표시된 파라미터가 있음
(`STOCK_MAX_LOSS_PERCENT`, `STOCK_MAX_HOLD_DAYS`). 코인과 동일 패턴으로:
특성 테스트 → 청산/점수 공유 구현 추출 → 백테스터 진단 기능.

### 3. [선택·큰 작업] 전략 추상화 + PaperBroker 이식 (A/B 시뮬)
`d:\Fable`의 `Strategy` 인터페이스 + `PaperBroker`를 이식해 점수제 전략과 변동성 돌파를
동일 파이프라인에서 A/B 시뮬. **라이브 매매 경로를 실제로 바꾸는 작업이라 위험** — 1번
검증에서 개선 여지가 확인된 뒤에 착수 권장. 지금은 안 함.

### 4. [선택] 상태 영속화 개선 (Phase C)
`bot_state.json` → SQLite(자본 곡선 포함), 재시작 복구 강화. `d:\Fable`의
`data/storage.py`(SqliteStore) 패턴 참고.

---

## 알아둘 것 (검증 중 발견)

- **RSI 특이점**: 손실 봉이 전혀 없는 순수 단조 상승/하락에서 RSI가 100/0이 아니라 50(중립)으로
  계산됨(`avg_loss=0 → NaN → fillna(50)`). 실전 영향 작지만 `test_indicators.py`에 고정해둠.
- **sheets_client 임포트 부작용**: `core.trader` 임포트 시 Sheets에 연결. 테스트는 conftest로
  차단. 향후 lazy-init로 개선 여지.
- **sheets_client 경고**: `worksheet.update()` 인자 순서 deprecation 경고(gspread 6.x). 기능엔 무해.

---

## 파일 지도 (이번 작업으로 추가/변경)

```
core/exit_rules.py        [신규] 청산 판정 단일 구현
core/scoring.py           [신규] 진입 점수 가중치 단일 구현
core/trader.py            [변경] 청산 인라인 → judge_exit 호출
core/data_builder.py      [변경] 가중치 하드코딩 2곳 → weighted_score 호출
scripts/backtest.py       [변경] 로컬 judge_exit 제거→공유 임포트, weighted_score 사용,
                                  --split·--sweep-threshold·청산 분해·OHLCV 캐시 추가
tests/                    [신규] 특성 테스트 41개 (conftest는 외부 서비스 차단)
requirements-dev.txt      [신규] pytest
docs/TRADING_BOT_PLAYBOOK.md  [신규] 방법론 원본
docs/UPGRADE_NOTES.md     [신규] 이 문서
```
