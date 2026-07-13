# Vigia do robô SureRadar (loop). Fica ligado o tempo todo e, a cada 3 min,
# verifica se o scraper está rodando. Se NÃO estiver, sobe de novo.
# Inicia sozinho no login (atalho na pasta de Inicialização do Windows).
$dir = "C:\Users\Gustavo Sapper\Pictures\surebet"

while ($true) {
    try {
        $rodando = Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
            Where-Object { $_.CommandLine -like '*scraper_pw*' }
        if (-not $rodando) {
            Start-Process -FilePath "python" -ArgumentList "-u", "scraper_pw.py" `
                -WorkingDirectory $dir `
                -RedirectStandardOutput "$dir\scraper.log" `
                -RedirectStandardError  "$dir\scraper.err.log"
            "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') - robo estava PARADO, religado pelo vigia." |
                Out-File -FilePath "$dir\vigia.log" -Append -Encoding utf8
        }
    } catch {}
    Start-Sleep -Seconds 180
}
