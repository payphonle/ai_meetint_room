# 下载五个助手的官方 logo 到 web/logos/（apple-touch-icon 优先，favicon 兜底）
$ErrorActionPreference = "Continue"
New-Item -ItemType Directory -Force -Path "web\logos" | Out-Null
$ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120"
$sites = @(
    @("doubao", "https://www.doubao.com"),
    @("deepseek", "https://chat.deepseek.com"),
    @("qianwen", "https://www.qianwen.com"),
    @("chatglm", "https://chatglm.cn"),
    @("yuanbao", "https://yuanbao.tencent.com")
)
foreach ($s in $sites) {
    $key = $s[0]; $base = $s[1]; $done = $false
    foreach ($path in @("/apple-touch-icon.png", "/favicon.ico", "/favicon.png")) {
        if ($done) { break }
        $dest = "web\logos\$key.png"
        curl.exe -s -L -A $ua -o $dest --max-time 20 "$base$path"
        $len = 0
        if (Test-Path $dest) { $len = (Get-Item $dest).Length }
        if ($len -gt 500) {
            Write-Host "${key} <- ${path} ($([Math]::Round($len/1KB,1)) KB)"
            $done = $true
        }
    }
    if (-not $done) { Write-Host "${key}: FAILED" }
}
