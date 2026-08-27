# Vigia do robô SureRadar (loop). Fica ligado o tempo todo e, a cada 60s:
#   1) consulta a nuvem (/api/robo/estado) pra saber se o robô DEVE rodar
#      (interruptor /ligar /desligar do Telegram) e a IDADE do feed;
#   2) se DESLIGADO -> mata o robô e não religa;
#   3) se LIGADO -> garante que roda; e se virou ZUMBI (processo vivo mas o
#      painel está há muito tempo sem dados) -> mata e sobe de novo.
# Inicia sozinho no login (atalho na pasta de Inicialização do Windows).
$dir = "C:\Users\Gustavo Sapper\Pictures\surebet"
# Caminho COMPLETO do Python (o pythoncore-3.14 é o que TEM Playwright).
$py = "C:\Users\Gustavo Sapper\AppData\Local\Python\pythoncore-3.14-64\python.exe"
if (-not (Test-Path $py)) { $py = "C:\Users\Gustavo Sapper\AppData\Local\Microsoft\WindowsApps\python.exe" }
if (-not (Test-Path $py)) { $py = "python" }

$ESTADO_URL = "https://sureradar.site/api/robo/estado"
# Zumbi: processo vivo mas feed parado há mais de N segundos. 1200s (20 min) fica
# ACIMA do pior caso legítimo (após uma queda longa o robô leva ~12 min resolvendo
# centenas de links das casas antes de mandar o 1º painel) — assim NÃO reinicia no
# meio de uma recuperação normal, só quando realmente travou.
$LIMITE_ZUMBI_SEG = 1200

function Get-Scraper {
    Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
        Where-Object { $_.CommandLine -like '*scraper_pw*' }
}

function Stop-Robo {
    # Mata o robô e SOMENTE o Chromium dele (cmdline com pw_profile) — nunca o Chrome pessoal.
    foreach ($p in @(Get-Scraper)) { Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue }
    Start-Sleep -Milliseconds 400
    $chr = Get-CimInstance Win32_Process -Filter "Name='chrome.exe'" |
        Where-Object { $_.CommandLine -like '*pw_profile*' }
    foreach ($c in $chr) { Stop-Process -Id $c.ProcessId -Force -ErrorAction SilentlyContinue }
}

function Start-Robo($motivo) {
    Start-Process -FilePath $py -ArgumentList "-u", "scraper_pw.py" `
        -WorkingDirectory $dir `
        -RedirectStandardOutput "$dir\scraper.log" `
        -RedirectStandardError  "$dir\scraper.err.log"
    "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') - robo SUBIU pelo vigia ($motivo)." |
        Out-File -FilePath "$dir\vigia.log" -Append -Encoding utf8
}

while ($true) {
    try {
        # 1) Estado na nuvem. Se a nuvem estiver inacessível, assume LIGADO (default
        #    seguro) e SEM idade (não reinicia por zumbi sem dado confiável).
        $ligado = $true
        $idade = $null
        try {
            $e = Invoke-RestMethod -Uri $ESTADO_URL -TimeoutSec 15
            if ($null -ne $e) {
                $ligado = [bool]$e.ligado
                $idade = $e.idade_seg
            }
        } catch { }

        $proc = Get-Scraper

        if (-not $ligado) {
            # Interruptor DESLIGADO pelo Telegram -> garante o robô parado.
            if ($proc) {
                Stop-Robo
                "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') - robo PARADO pelo vigia (interruptor /desligar)." |
                    Out-File -FilePath "$dir\vigia.log" -Append -Encoding utf8
            }
        } else {
            if (-not $proc) {
                Start-Robo "processo ausente"
            } elseif (($null -ne $idade) -and ($idade -gt $LIMITE_ZUMBI_SEG)) {
                # Zumbi: vivo mas o painel está velho -> mata e sobe de novo.
                Stop-Robo
                Start-Robo ("zumbi: feed parado ha " + [int]$idade + "s")
            }
        }
    } catch { }
    Start-Sleep -Seconds 60
}
