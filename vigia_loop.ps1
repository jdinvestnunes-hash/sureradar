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
$LIMITE_ZUMBI_SEG = 2700   # 45 min: ciclo do robo agora e de 30 min (CICLO_MIN) + folga
# Janela de GRACA depois de (re)subir o robo: durante ela o vigia NAO mata por
# zumbi, pra dar tempo do robo fazer a 1a varredura e postar o 1o painel (~12 min).
# Sem isso o vigia matava o robo a cada 60s no meio da varredura -> ele nunca
# terminava -> feed nunca atualizava -> matava de novo (death loop). 900s (15 min)
# fica ACIMA dos ~12 min do pior caso.
$GRACA_POS_START_SEG = 900
# "ha muito tempo": deixa a 1a acao (subir/reiniciar) livre no start do vigia.
$script:ultimoStart = (Get-Date).AddSeconds(-100000)

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
    $script:ultimoStart = Get-Date   # zera a janela de graca: nao matar por zumbi ja no proximo tick
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
                # Zumbi: vivo mas o painel está velho. SÓ mata se já passou a janela de
                # graça desde o último start — senão o robô ainda está fazendo a 1ª
                # varredura (que leva ~12 min) e matá-lo agora recomeça o loop do zero.
                $desdeStart = ((Get-Date) - $script:ultimoStart).TotalSeconds
                if ($desdeStart -gt $GRACA_POS_START_SEG) {
                    Stop-Robo
                    Start-Robo ("zumbi: feed parado ha " + [int]$idade + "s")
                } else {
                    "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') - feed velho ($([int]$idade)s) mas dentro da graca ($([int]$desdeStart)s/$GRACA_POS_START_SEG s) - aguardando 1a varredura." |
                        Out-File -FilePath "$dir\vigia.log" -Append -Encoding utf8
                }
            }
        }
    } catch { }
    Start-Sleep -Seconds 60
}
