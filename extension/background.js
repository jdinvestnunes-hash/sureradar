// SureRadar Bridge — service worker.
// Recebe as surebets raspadas pelo content script e envia ao painel local.
// O service worker da extensão fala com o localhost sem os bloqueios de
// "conteúdo misto" / "rede privada" que travam o fetch da página.

// Produção: o painel no ar. Para testar local, troque por http://localhost:8000/api/ingest
const SAAS_URL = "https://sureradar.site/api/ingest";

chrome.runtime.onMessage.addListener((msg) => {
  if (msg && msg.tipo === "ingest") {
    fetch(SAAS_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ records: msg.records }),
    })
      .then((r) => r.json())
      .then((j) => console.log("[SureRadar] painel respondeu:", j))
      .catch((e) => console.warn("[SureRadar] erro ao enviar:", e));
  }
  return false;
});
