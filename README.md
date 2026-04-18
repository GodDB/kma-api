# KMA API Hub Tester

기상청 API 허브 API를 빠르게 테스트하기 위한 Python CLI 스크립트입니다.

## 파일

- `kma_api_test.py`: 엔드포인트 호출, URL 생성, 응답 미리보기, 파일 저장
- `kma_bulk_download.py`: 긴 기간을 여러 구간으로 나눠 반복 다운로드하고 병합 저장
- `kma_client.py`: 공통 HTTP 요청, 인코딩 처리, 재시도, 기간 분할
- `kma_asos.py`: ASOS 다운로드와 CSV 저장 로직
- `kma_app_config.py`: macOS 설정 파일 경로와 authKey 로딩
- `kma_asos_app.py`: Tkinter 기반 macOS GUI 앱
- `settings.example.json`: authKey 설정 예시
- `build_mac_app.sh`: macOS `.app` 빌드 스크립트

## macOS GUI 앱

현재 GUI 앱은 `종관기상관측(ASOS)` 다운로드를 지원합니다. `방재기상관측(AWS)`는 이후 같은 구조에 추가할 수 있게 화면만 준비해두었습니다.

### 지원 기능

- 기간 직접 입력 (`yyyymmddhhmm`)
- `stn` 직접 입력, 기본값 `0`
- 결과를 UTF-8 CSV로 저장
- 장기 조회 시 한 번의 API 요청은 최대 14일까지만 자동 분할
- 요청당 timeout 5분(300초)
- API 요청 실패 시 최대 5회 자동 재시도
- 재시도와 최종 실패 시 실패 이유를 진행 로그에 표시
- 다운로드 중 `다운로드 중지` 버튼으로 작업 중단 가능
- 최종 실패 시 `실패` 상태 표시
- 조회 결과가 없으면 `데이터 없음` 상태 표시

### authKey 설정

앱은 `authKey`를 macOS 설정 파일에서 읽습니다.

설정 파일 경로:

```text
~/Library/Application Support/KMA ASOS Downloader/settings.json
```

처음 실행하면 위 파일이 자동으로 생성됩니다. `auth_key` 값을 실제 기상청 API 허브 키로 바꿔주세요.

```json
{
  "auth_key": "YOUR_KMA_AUTH_KEY"
}
```

### 소스코드로 실행

```bash
python3 kma_asos_app.py
```

만약 `ModuleNotFoundError: No module named '_tkinter'` 오류가 나오면, 먼저 Tk 지원 패키지를 설치해야 합니다.

```bash
brew install python-tk@3.13
```

### macOS .app 빌드

```bash
bash build_mac_app.sh
```

빌드가 끝나면 아래 경로에 앱이 생성됩니다.

```text
dist/KMA ASOS Downloader.app
```

## 준비

1. [기상청 API 허브](https://apihub.kma.go.kr/)에서 회원가입 후 `authKey`를 발급받습니다.
2. 테스트할 API에서 `API 활용신청`을 완료합니다.
3. 호출할 엔드포인트와 요청 파라미터를 확인합니다.

기상청 API 허브 이용 절차와 `authKey` 발급 방식은 [이용안내](https://apihub.kma.go.kr/apiInfo.do)에서 볼 수 있습니다.

## 실행 방법

`authKey`를 환경변수로 넣고 실행하는 방식이 가장 편합니다.

```bash
export KMA_AUTH_KEY="발급받은인증키"
```

이후 엔드포인트와 파라미터만 지정해서 실행합니다.

```bash
python3 kma_api_test.py \
  --endpoint typ01/url/kma_sfctm3.php \
  --param tm1=202308010000 \
  --param tm2=202308012359 \
  --param stn=0 \
  --param help=0 \
  --output asos.txt
```

전체 URL을 그대로 넣어도 됩니다.

```bash
python3 kma_api_test.py \
  --endpoint https://apihub.kma.go.kr/api/typ01/url/amos.php \
  --param tm=202211301200 \
  --param dtm=60 \
  --param stn=0 \
  --param help=1
```

## 자주 쓰는 옵션

- `--auth-key`: 환경변수 대신 인증키를 직접 전달
- `--param KEY=VALUE`: 쿼리 파라미터 추가, 여러 번 사용 가능
- `--param-file params.json`: JSON 파일로 파라미터 로드
- `--output result.txt`: 응답 저장. 텍스트 응답은 UTF-8로 변환해서 저장
- `--preview-lines 20`: 터미널 미리보기 줄 수 지정
- `--dry-run`: 실제 호출 없이 최종 URL만 확인

## 긴 기간 다운로드

지상관측 시간자료처럼 긴 기간을 한 번에 요청하면 마지막 일부 구간만 내려오는 경우가 있어, `kma_bulk_download.py`로 월 단위 분할 다운로드를 권장합니다.

```bash
export KMA_AUTH_KEY="발급받은인증키"

python3 kma_bulk_download.py \
  --endpoint typ01/url/kma_sfctm3.php \
  --start 201501010000 \
  --end 202512312300 \
  --chunk-months 1 \
  --param stn=108 \
  --param help=0 \
  --param disp=1 \
  --output-dir downloads/asos_108_chunks \
  --merged-output downloads/asos_108_2015_2025.txt
```

- `--output-dir`: 월별 응답 파일 저장
- `--merged-output`: 월별 결과를 하나의 UTF-8 텍스트 파일로 병합
- `--skip-existing`: 이미 받은 정상 청크를 재사용해서 이어받기
- `--retries 3 --retry-delay 5`: 일시적인 5xx/네트워크 오류 자동 재시도
- `stn=0`은 전체 지점이라 데이터가 매우 커질 수 있어 주의가 필요합니다.

## 파라미터 파일 예시

```json
{
  "tm1": "202308010000",
  "tm2": "202308012359",
  "stn": "0",
  "help": "0"
}
```

```bash
python3 kma_api_test.py \
  --endpoint typ01/url/kma_sfctm3.php \
  --param-file params.json
```

## 참고 예시

- ASOS 시간자료 예시는 [이 Python 정리 글](https://seogoing.github.io/posts/KMA-APIHUB-1/)에서 확인할 수 있습니다.
- 일부 API는 `help=1`로 호출하면 필드 설명이 함께 내려옵니다.
- 허브에 있는 API마다 필요한 파라미터가 다르므로, 실제 값은 해당 API 상세 페이지의 요청인자를 기준으로 넣어야 합니다.
