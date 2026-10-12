param(
    [switch]$Remove,
    [switch]$NoPause
)

$ErrorActionPreference = 'Stop'
$variableName = 'HIRA_DISEASE_API_KEY'
$exitCode = 0

try {
    if ($Remove) {
        [Environment]::SetEnvironmentVariable($variableName, $null, 'User')
        Remove-Item "Env:$variableName" -ErrorAction SilentlyContinue
        Write-Host '심평원 API 키 설정을 삭제했습니다.'
    } else {
        Write-Host '공공데이터포털에서 받은 일반 인증키(Encoding 또는 Decoding)를 입력하세요.'
        Write-Host '입력한 글자는 화면에 표시되지 않으며 프로젝트 파일에는 저장하지 않습니다.'
        $secureKey = Read-Host '심평원 API 키' -AsSecureString
        $keyPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureKey)
        try {
            $plainKey = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($keyPointer)
            if ([string]::IsNullOrWhiteSpace($plainKey)) {
                throw 'API 키가 비어 있습니다.'
            }
            [Environment]::SetEnvironmentVariable($variableName, $plainKey.Trim(), 'User')
            $env:HIRA_DISEASE_API_KEY = $plainKey.Trim()
        } finally {
            if ($keyPointer -ne [IntPtr]::Zero) {
                [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($keyPointer)
            }
            $plainKey = $null
        }
        Write-Host ''
        Write-Host '설정이 완료되었습니다.' -ForegroundColor Green
        Write-Host '실행 중인 보험앱을 종료한 뒤 START_APP.ps1로 다시 시작하세요.'
    }
} catch {
    $exitCode = 1
    Write-Host ''
    Write-Host '설정 중 오류가 발생했습니다:' -ForegroundColor Red
    Write-Host $_.Exception.Message -ForegroundColor Red
} finally {
    if (-not $NoPause) {
        Write-Host ''
        Read-Host 'Enter 키를 누르면 창이 닫힙니다'
    }
}

exit $exitCode
