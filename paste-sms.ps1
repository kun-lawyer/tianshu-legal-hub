# 把剪贴板里的法院短信交给中台处理（双击「粘贴法院短信.cmd」调用）
$ErrorActionPreference = 'Stop'
$config = Get-Content (Join-Path $env:USERPROFILE '.agent-autoloop\config.json') -Raw | ConvertFrom-Json
$text = (Get-Clipboard -Raw)
if (-not $text -or -not $text.Trim()) {
    Write-Host '剪贴板是空的：先在手机或微信里复制法院短信，再双击运行。'
    exit 1
}
$text = $text.Trim()
Write-Host '将要提交的短信：'
Write-Host '----------------------------------------'
Write-Host $text
Write-Host '----------------------------------------'
if ($text -notmatch '法院|案号|传票|送达|立案|开庭|zxfw') {
    Write-Host '提醒：这段文字里没有法院短信常见的关键词。'
}
if ((Read-Host '确认提交？回车提交，输入 n 取消') -eq 'n') { exit 0 }

$sha = [Security.Cryptography.SHA256]::Create()
$hash = -join ($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($text))[0..7] | ForEach-Object { $_.ToString('x2') })
$first = ($text -split "`n")[0]
$body = @{
    id      = "sms-$hash"
    type    = 'sms.received'
    subject = 'sms'
    summary = $first.Substring(0, [Math]::Min(200, $first.Length))
    detail  = $text.Substring(0, [Math]::Min(4000, $text.Length))
} | ConvertTo-Json
$resp = Invoke-RestMethod -Method Post -Uri "$($config.url)/v1/events" `
    -Headers @{ Authorization = "Bearer $($config.source.key)" } `
    -ContentType 'application/json; charset=utf-8' -Body ([Text.Encoding]::UTF8.GetBytes($body))
if ($resp.duplicate) {
    Write-Host '这条短信之前已经提交过，不重复处理。'
} elseif ($resp.routed.Count) {
    Write-Host "已提交（事件 #$($resp.event)），交给「$($resp.routed -join '、')」处理，结果会推到飞书。"
} else {
    Write-Host "已记下（事件 #$($resp.event)），但没有规则接手，请检查 rules.json。"
}
