# Выгрузка пользователей AD в JSON для синхронизации с A-Tracker.
# Запускать на машине в домене (под пользователем с доступом к AD).
# Результат: ad_export.json в папке scripts/ — скопируйте в data/ и укажите AD_EXPORT_PATH.

$BaseDN = "OU=CorpUsers,DC=ovp,DC=ru"
# Только включённые пользователи (objectClass=user, не отключённые)
$Filter = "ObjectClass -eq 'user' -and Enabled -eq `$true"
$Props = @("cn", "mail", "SamAccountName", "objectSid")

$users = Get-ADUser -SearchBase $BaseDN -SearchScope Subtree -Filter $Filter -Properties $Props -ErrorAction Stop

$result = @()
foreach ($u in $users) {
    $sid = if ($u.objectSid) { $u.objectSid.Value } else { $null }
    $result += [PSCustomObject]@{
        cn             = $u.cn
        mail           = $u.mail
        sAMAccountName = $u.SamAccountName
        objectSid      = $sid
    }
}

$json = ConvertTo-Json -InputObject $result -Depth 3
$outPath = Join-Path $PSScriptRoot "ad_export.json"
$json | Out-File -FilePath $outPath -Encoding UTF8
Write-Host "Exported $($result.Count) users to $outPath"
Write-Host "Copy to project: data/ad_export.json and set config AD_EXPORT_PATH = 'data/ad_export.json'"
