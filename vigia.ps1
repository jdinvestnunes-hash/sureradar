# Vigia do robô SureRadar.
# Roda a cada 3 min (via Tarefa Agendada). Se o scraper NÃO estiver rodando,
# sobe de novo (desacoplado, com log). Se já estiver rodando, NÃO faz nada.
$dir = "C:\Users\Gustavo Sapper\Pictures\surebet"

$rodando = Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
    Where-Object { $_.CommandLine -like '*scraper_pw*' }

if (-not $rodando) {
    Start-Process -FilePath "python" -ArgumentList "-u", "scraper_pw.py" `
        -WorkingDirectory $dir `
        -RedirectStandardOutput "$dir\scraper.log" `
        -RedirectStandardError  "$dir\scraper.err.log"
    "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') - robo estava PARADO, religado." |
        Out-File -FilePath "$dir\vigia.log" -Append -Encoding utf8
}
