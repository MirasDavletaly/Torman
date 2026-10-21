"use strict";

const $ = (selector) => document.querySelector(selector);
const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[char]));
const paths = {
  sparkles:'<path d="m12 3 2.5 6.5L21 12l-6.5 2.5L12 21l-2.5-6.5L3 12l6.5-2.5L12 3Z"/><path d="m20 2 .6 1.4L22 4l-1.4.6L20 6l-.6-1.4L18 4l1.4-.6L20 2Z"/>',
  grid:'<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/>',
  clock:'<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  settings:'<path d="m9 3-.6 2.2-2 .9-2.1-.6-2 3.5 1.6 1.7v2.5L2.3 15l2 3.5 2.1-.6 2 .9L9 21h4l.6-2.2 2-.9 2.1.6 2-3.5-1.6-1.8v-2.5L19.7 9l-2-3.5-2.1.6-2-.9L13 3Z"/><circle cx="11" cy="12" r="3"/>',
  database:'<ellipse cx="12" cy="5" rx="8" ry="3"/><path d="M4 5v7c0 4 16 4 16 0V5M4 12v7c0 4 16 4 16 0v-7"/>',
  chevron:'<path d="m9 5 7 7-7 7"/>',
  sliders:'<path d="M4 6h5m4 0h7M4 12h10m4 0h2M4 18h3m4 0h9"/><circle cx="11" cy="6" r="2"/><circle cx="16" cy="12" r="2"/><circle cx="9" cy="18" r="2"/>',
  shield:'<path d="M12 3 4 6v6c0 5 8 9 8 9s8-4 8-9V6l-8-3Z"/><path d="m8 12 3 3 5-6"/>',
  arrow:'<path d="M4 12h15m-6-6 6 6-6 6"/>',
  upload:'<path d="M12 16V3m-5 5 5-5 5 5M4 15v5a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-5"/>',
  file:'<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8l-6-6Z"/><path d="M14 2v6h6M8 13h8M8 17h8M11 11v8"/>',
  search:'<circle cx="10.5" cy="10.5" r="7.5"/><path d="m16 16 5 5"/>',
  download:'<path d="M12 3v13m-5-5 5 5 5-5M4 16v4h16v-4"/>',
  check:'<path d="m5 12 4 4L19 6"/>'
};
function icon(name) { return `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${paths[name] || paths.file}</svg>`; }
document.querySelectorAll("[data-icon]").forEach(node => node.innerHTML = icon(node.dataset.icon));

let state = {catalog:null, settings:{}, active_job:null};
let currentView = "search", currentResult = null, historyItems = [], currentJob = null;
let uploadState = null, previewVersion = 0, catalogPage = 1, catalogVersion = 0, toastTimer, filterTimer, pollTimer;
const date = (value) => new Date(value).toLocaleString("ru-RU", {day:"numeric",month:"short",hour:"2-digit",minute:"2-digit"});
const count = (value) => Number(value).toLocaleString("ru-RU");

async function api(url, options = {}) {
  const headers = {...options.headers};
  if (options.method && options.method !== "GET") headers["X-Torman-Request"] = "1";
  if (options.body && !(options.body instanceof FormData)) {
    headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(options.body);
  }
  let response;
  try { response = await fetch(url, {...options, headers}); }
  catch { throw new Error("Нет связи с программой. Убедитесь, что окно Torman открыто, и обновите страницу."); }
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || "Не удалось выполнить действие.");
  return data;
}

function toast(message, error = false) {
  clearTimeout(toastTimer);
  $("#toast").textContent = message;
  $("#toast").className = `toast${error ? " error" : ""}`;
  toastTimer = setTimeout(() => $("#toast").classList.add("hidden"), error ? 10000 : 5000);
}

async function refreshState() {
  state = await api("/api/state");
  $("#nav-count").textContent = count(state.catalog?.page_count || 0);
  const badge = $("#connection-badge");
  badge.classList.toggle("connected", state.settings.has_key);
  badge.innerHTML = `<span class="status-dot"></span><span>${state.settings.has_key ? "Gemini настроен" : "Подключить Gemini"}</span>${icon("chevron")}`;
  renderCatalogCard();
}

function documentUrl(catalogId) {
  return `/api/catalog/document?catalog_id=${encodeURIComponent(catalogId)}`;
}

function renderCatalogCard() {
  const c = state.catalog;
  if (c && c.source_type !== "pdf") {
    $("#catalog-card").innerHTML = `<div class="card-topline"><h2>Прежний каталог сохранён</h2></div><p>Для подбора в этой версии загрузите PDF. Прежний исходный файл можно скачать в разделе «Мой каталог».</p><button class="button primary wide" data-action="upload">${icon("upload")}Загрузить PDF</button>`;
    return;
  }
  $("#catalog-card").innerHTML = c
    ? `<div class="card-topline"><h2>Активный PDF-каталог</h2><span class="mini-label">СОХРАНЁН</span></div><div class="catalog-stats"><div><strong>${count(c.page_count)}</strong><span>страниц</span></div><div><strong>${(c.size_bytes/1024/1024).toLocaleString("ru-RU",{maximumFractionDigits:1})}</strong><span>МБ</span></div></div><div class="catalog-file">${esc(c.filename)}</div><div class="catalog-meta">Загружен ${esc(date(c.saved_at))}</div><a class="pdf-source-link" href="${documentUrl(c.id)}" target="_blank" rel="noopener noreferrer">Открыть PDF ↗</a><span class="saved-note">${icon("check")}Доступен при следующем запуске</span><div class="index-note">${state.index?.ready ? state.index.records ? `Подготовлен для быстрого поиска · ${count(state.index.records)} записей` : "Товары не распознаны. Загрузите каталог или прайс-лист." : state.index?.completed ? `Подготовка сохранена: ${state.index.completed} из ${state.index.total} частей` : "Первый подбор подготовит PDF. Следующие будут быстрее."}</div><button class="button subtle wide" data-action="upload">${icon("upload")}Заменить PDF</button>`
    : `<div class="card-topline"><h2>Ваш каталог</h2><span class="mini-label">PDF</span></div><div class="upload-illustration">${icon("file")}</div><h3>Начнём с вашего каталога</h3><p>Загрузите PDF с товарами.<br>Подойдут текстовые файлы и сканы.</p><button class="button subtle wide" data-action="upload">${icon("upload")}Загрузить PDF</button><span class="formats">PDF · до 20 МБ · до 300 страниц</span>`;
}

function renderEmpty() {
  $("#results").innerHTML = `<div class="empty-results"><div class="results-heading"><h2>Три шага к подходящему товару</h2><span class="empty-badge">ВСЁ В ОДНОМ МЕСТЕ</span></div><div class="steps"><article class="step"><div class="step-number"><span>01</span>${icon("file")}</div><h3>Загрузите ваш PDF</h3><p>Каталог, прайс-лист или скан.<br>Документ сохранится на компьютере.</p></article><article class="step"><div class="step-number"><span>02</span>${icon("sliders")}</div><h3>Опишите, что нужно</h3><p>Укажите важные характеристики<br>и ограничения обычными словами.</p></article><article class="step"><div class="step-number"><span>03</span>${icon("sparkles")}</div><h3>Сравните варианты</h3><p>Получите объяснение выбора<br>и ссылки на страницы PDF.</p></article></div></div>`;
}

function pageCards(pages, url) {
  return `<div class="pdf-pages">${pages.map(page=>`<article class="pdf-page"><div class="pdf-page-heading"><strong>Страница ${page.number}</strong><a href="${esc(url)}#page=${page.number}" target="_blank" rel="noopener noreferrer">Открыть в PDF ↗</a></div>${page.text ? `<p class="pdf-page-text">${esc(page.text.slice(0,1400))}${page.text.length>1400||page.truncated?"…":""}</p>` : '<p class="pdf-page-empty">Текстовый предпросмотр недоступен. Откройте страницу в PDF. При подборе Gemini получит её вместе с изображениями.</p>'}</article>`).join("")}</div>`;
}

async function showView(view) {
  currentView = view;
  document.querySelectorAll(".view").forEach(node=>node.classList.toggle("hidden", node.id !== `view-${view}`));
  document.querySelectorAll("[data-view]").forEach(node=>node.classList.toggle("active", node.dataset.view === view));
  $("#breadcrumb-title").textContent = {search:"Подбор товаров",catalog:"Мой каталог",history:"История подборов"}[view];
  try {
    if (view === "catalog") await loadCatalog();
    if (view === "history") await loadHistory();
  } catch(error) { toast(error.message, true); }
}

async function loadCatalog() {
  const version = ++catalogVersion;
  const data = await api(`/api/catalog?q=${encodeURIComponent($("#catalog-filter").value)}&page=${catalogPage}`);
  if (version !== catalogVersion) return;
  const catalog=state.catalog, isPdf=catalog?.source_type==="pdf";
  $("#catalog-description").textContent = isPdf ? `${catalog.filename} · ${count(catalog.page_count)} страниц · полный документ сохранён` : "Загрузите PDF-каталог с товарами и характеристиками.";
  $("#original-download").classList.toggle("hidden", !catalog);
  $("#pdf-open").classList.toggle("hidden", !isPdf);
  if(isPdf) $("#pdf-open").href=documentUrl(catalog.id);
  $("#catalog-notes").textContent = isPdf
    ? "Ниже — доступный текст страниц. Для просмотра таблиц и изображений откройте PDF. Поиск здесь работает только по извлечённому тексту; быстрый подбор Gemini использует сохранённые распознанные характеристики."
    : catalog ? "Здесь сохранён файл из прежней версии. Его можно скачать. Для нового подбора загрузите PDF." : "";
  $("#catalog-table").innerHTML = data.pages.length
    ? pageCards(data.pages,documentUrl(catalog.id))
    : `<div class="empty-state"><h3>${catalog ? "Нет страниц для текстового просмотра" : "Каталог пока пуст"}</h3><p>${catalog ? "Попробуйте другой запрос или откройте исходный PDF." : "Выберите PDF. Он останется доступен после перезапуска программы."}</p></div>`;
  const size=data.page_size;
  $("#page-description").textContent = data.total ? `${(catalogPage-1)*size+1}–${Math.min(catalogPage*size,data.total)} из ${count(data.total)} страниц` : "0 страниц";
  $("#page-prev").disabled=catalogPage<=1;
  $("#page-next").disabled=catalogPage*size>=data.total;
}

function openUpload() {
  uploadState=null;
  ++previewVersion;
  $("#upload-content").innerHTML=`<p class="modal-intro">Выберите PDF-каталог, прайс-лист или скан с товарами. Его не нужно переводить в таблицу: Gemini прочитает сам документ.</p><button class="upload-drop" id="drop-zone" data-action="choose-file">${icon("upload")}<strong>Выберите PDF или перетащите сюда</strong><span>PDF · до 20 МБ · до 300 страниц</span></button><p class="field-help">Загрузка и сохранение выполняются на компьютере. При первом подборе Gemini распознает PDF и сохранит результат. Затем отправляются только характеристики и новый запрос.</p>`;
  if(!$("#upload-dialog").open) $("#upload-dialog").showModal();
  const drop=$("#drop-zone");
  drop.addEventListener("dragover",event=>{event.preventDefault();drop.classList.add("dragover");});
  drop.addEventListener("dragleave",()=>drop.classList.remove("dragover"));
  drop.addEventListener("drop",event=>{event.preventDefault();if(event.dataTransfer.files[0])processFile(event.dataTransfer.files[0]);});
}

async function processFile(file) {
  if(!file.name.toLowerCase().endsWith(".pdf")) return toast("Выберите файл в формате PDF.",true);
  if(file.size>20*1024*1024) return toast("Максимальный размер PDF — 20 МБ.",true);
  const version=++previewVersion;
  $("#upload-content").innerHTML='<div class="busy-text"><span class="spinner"></span>Читаем страницы PDF…</div>';
  try {
    const form=new FormData();form.append("file",file);
    const data=await api("/api/upload",{method:"POST",body:form});
    if(version!==previewVersion||!$("#upload-dialog").open)return;
    uploadState=data;
    const url=`/api/upload/${encodeURIComponent(data.id)}/document`;
    $("#upload-content").innerHTML=`<div class="upload-filename">${icon("file")}<strong>${esc(data.filename)}</strong></div><div class="pdf-upload-summary"><span><strong>${count(data.page_count)}</strong> страниц</span><span>${(data.size_bytes/1024/1024).toLocaleString("ru-RU",{maximumFractionDigits:1})} МБ</span><a href="${url}" target="_blank" rel="noopener noreferrer">Открыть PDF ↗</a></div><p class="preview-caption">Текстовый предпросмотр первых ${data.pages.length} страниц. Оформление и изображения доступны в самом PDF.</p><div class="pdf-upload-preview">${pageCards(data.pages,url)}</div>${data.warnings.map(w=>`<p class="field-help">${esc(w)}</p>`).join("")}${state.catalog?'<p class="replace-warning">После сохранения этот PDF заменит текущий каталог. Исходные файлы на компьютере не изменятся.</p>':""}<div class="modal-actions"><button class="button subtle" data-action="choose-file">Другой файл</button><button id="commit-upload" class="button primary">Сохранить PDF</button></div>`;
    $("#commit-upload").addEventListener("click",commitUpload);
  }catch(error){
    if(version===previewVersion&&$("#upload-dialog").open)$("#upload-content").innerHTML=`<div class="error-box">${esc(error.message)}<br><button class="button subtle" data-action="choose-file">Выбрать другой файл</button></div>`;
  }
}

async function commitUpload() {
  $("#commit-upload").disabled=true;
  try{
    await api("/api/upload/commit",{method:"POST",body:{upload_id:uploadState.id}});
    $("#upload-dialog").close();
    currentResult=null;catalogPage=1;
    $("#catalog-filter").value="";
    await refreshState();renderEmpty();
    if(currentView==="catalog")await loadCatalog();
    toast("PDF сохранён. При следующем запуске загружать его заново не нужно.");
  }catch(error){toast(error.message,true);if($("#commit-upload"))$("#commit-upload").disabled=false;}
}

function openSettings() {
  $("#api-key").value="";
  $("#api-key").placeholder=state.settings.has_key ? "Ключ уже сохранён. Введите новый для замены." : "Вставьте API-ключ";
  $("#key-help").textContent=state.settings.has_key ? "Оставьте поле пустым, чтобы сохранить текущий ключ." : "Ключ вводится здесь, его не нужно отправлять вместе с программой.";
  $("#model").value=state.settings.model || "gemini-3.8-flash";
  $("#remember-key").checked=state.settings.can_remember && (!state.settings.has_key || state.settings.remembered);
  $("#remember-key").disabled=!state.settings.can_remember;
  $("#settings-feedback").textContent="";
  $("#settings-dialog").showModal();
}

async function saveSettings() {
  const key = $("#api-key").value.trim();
  await api("/api/settings", {method:"POST",body:{model:$("#model").value.trim(), ...(key ? {key} : {}),remember:$("#remember-key").checked}});
  $("#api-key").value="";
  await refreshState();
  $("#api-key").placeholder=state.settings.has_key ? "Ключ сохранён" : "Вставьте API-ключ";
}

$("#settings-form").addEventListener("submit", async event=>{
  event.preventDefault();$("#save-settings").disabled=true;
  try { await saveSettings();$("#settings-dialog").close();toast("Настройки Gemini сохранены."); }
  catch(error) { $("#settings-feedback").className="form-feedback error";$("#settings-feedback").textContent=error.message; }
  finally { $("#save-settings").disabled=false; }
});

$("#test-connection").addEventListener("click",async()=>{
  const button=$("#test-connection");button.disabled=true;$("#save-settings").disabled=true;
  $("#settings-feedback").className="form-feedback";$("#settings-feedback").textContent="Сохраняем настройки и проверяем подключение…";
  try {await saveSettings();await api("/api/settings/test",{method:"POST",body:{}});$("#settings-feedback").textContent="Подключение работает. Gemini готов к подбору товаров.";}
  catch(error){$("#settings-feedback").className="form-feedback error";$("#settings-feedback").textContent=error.message;}
  finally{button.disabled=false;$("#save-settings").disabled=false;}
});

function queryCount() {$("#query-count").textContent=`${$("#query").value.length} / 3000`;}
$("#query").addEventListener("input",queryCount);
$("#query").addEventListener("keydown",event=>{if(event.key==="Enter" && (event.ctrlKey||event.metaKey)) $("#search-form").requestSubmit();});

$("#search-form").addEventListener("submit",async event=>{
  event.preventDefault();
  if(currentJob) return;
  if(state.catalog?.source_type!=="pdf") {toast("Сначала загрузите PDF-каталог с товарами.");openUpload();return;}
  const query=$("#query").value.trim();
  if(query.length<3) return toast("Опишите нужный товар и его характеристики.",true);
  $("#search-button").disabled=true;
  try {
    const job=await api("/api/search",{method:"POST",body:{query,direct:$("#direct-search").checked}});
    if(job.result) {currentResult=job.result;renderResults(currentResult);finishJob();return;}
    currentResult=null;$("#results").innerHTML="";
    currentJob=job.id;$("#search-progress").classList.remove("hidden");
    $("#progress-title").textContent="Подбираем товары";
    $("#progress-detail").textContent="Подготовка каталога…";
    $("#progress-bar").removeAttribute("value");
    await pollJob();
  } catch(error) {toast(error.message,true);$("#search-button").disabled=false;if(!state.settings.has_key)openSettings();}
});

async function pollJob() {
  if(!currentJob) return;
  clearTimeout(pollTimer);
  try {
    const job=await api(`/api/jobs/${currentJob}`);
    if(job.status==="done") {
      currentResult=job.result;renderResults(currentResult);finishJob();refreshState().catch(()=>{});return;
    }
    if(job.status==="error") {finishJob();renderSearchError(job.error);refreshState().catch(()=>{});return;}
    $("#progress-title").textContent=job.message;
    $("#progress-detail").textContent=`Готово частей: ${job.completed} из ${job.total}. Первая подготовка PDF занимает больше времени; затем используется сохранённый каталог.`;
    $("#progress-bar").max=Math.max(1,job.total);
    if(job.total>1)$("#progress-bar").value=job.completed;
    else $("#progress-bar").removeAttribute("value");
  } catch(error) {toast(error.message,true);finishJob();return;}
  pollTimer=setTimeout(pollJob,450);
}

function finishJob() {currentJob=null;$("#search-progress").classList.add("hidden");$("#search-button").disabled=false;}

function renderSearchError(message) {
  $("#results").innerHTML=`<div class="results-heading"><h2>Не удалось завершить подбор</h2></div><div class="error-box search-error" role="alert"><p>${esc(message)}</p><button class="button subtle" data-action="retry-search">Повторить подбор</button></div>`;
}

function renderResults(result) {
  const exact=result.matches.filter(m=>m.status==="exact").length;
  const emptyCatalog=result.mode==="fast" && result.indexed_records===0;
  const isPdf=result.source_type==="pdf";
  const isOld=!state.catalog||state.catalog.id!==result.catalog_id;
  const unit=isPdf ? `PDF: ${count(result.page_count)} страниц` : `Проверено ${count(result.analyzed)} товаров`;
  const timing=result.duration_ms!==undefined ? `${(result.duration_ms/1000).toLocaleString("ru-RU",{maximumFractionDigits:1})} с` : "";
  const speed=result.cached ? "Из сохранённого результата · без обращения к Gemini" : result.mode==="fast" ? `По подготовленному каталогу · ${count(result.indexed_records)} записей · ${timing}` : result.mode==="direct" ? `Проверка по исходному PDF · ${timing}` : "";
  const warnings=(result.unreadable_pages||[]).length ? `<p class="notice pdf-warning">Gemini не смог прочитать страницы: ${result.unreadable_pages.map(esc).join(", ")}. Подбор может быть неполным. Проверьте эти страницы в оригинале.</p>` : "";
  $("#results").innerHTML=`<div class="results-heading"><div><h2>${result.matches.length?"Варианты из вашего каталога":emptyCatalog?"В этом PDF не распознаны товары":"Gemini не предложил подходящих вариантов"}</h2><p>${unit} · ${esc(result.model)}${result.matches.length?` · подходящих: ${exact}, требуют уточнения: ${result.matches.length-exact}`:""}</p></div>${result.matches.length?`<button class="button subtle" data-action="export-results">${icon("download")}Скачать CSV</button>`:""}</div><div class="query-summary">Запрос: ${esc(result.query)}${speed?`<span class="search-timing">${esc(speed)}</span>`:""}</div>${isOld?'<p class="notice pdf-warning">Это результат по прежнему каталогу. Текущий файл изменился; ссылки на исходный PDF этого подбора недоступны.</p>':""}${warnings}${result.matches.length?`<div class="results-grid">${result.matches.map((m,index)=>{
    const values=m.product.values,entries=Object.entries(values),title=values[result.title_column]||"Товар из каталога";
    const visible=[...new Set([...m.evidence,...Object.keys(values)])].slice(0,6);
    const pages=m.product.pages||[];
    const source=isPdf
      ? `${esc(result.filename)} · страницы: ${pages.join(", ")}`
      : `${esc(result.filename)} · ${esc(result.sheet)} · строка ${m.product.row}`;
    const links=isPdf&&!isOld ? `<div class="pdf-source-links">${pages.map(page=>`<a href="${documentUrl(result.catalog_id)}#page=${page}" target="_blank" rel="noopener noreferrer">PDF, стр. ${page} ↗</a>`).join("")}</div>` : "";
    return `<article class="result-card"><div class="result-top"><span class="result-rank">ВАРИАНТ ${String(index+1).padStart(2,"0")}</span><span class="match-status ${m.status==="uncertain"?"uncertain":""}">${m.status==="exact"?"Подходит по оценке Gemini":"Нужно уточнить"}</span></div><h3>${esc(title)}</h3><p class="result-reason">${esc(m.reason)}</p><dl class="attributes">${visible.map(k=>`<dt>${esc(k)}</dt><dd class="${m.evidence.includes(k)?"highlight":""}">${esc(values[k])||"Не указано"}</dd>`).join("")}</dl>${m.missing.length?`<p class="missing">Нужно уточнить: ${m.missing.map(esc).join("; ")}</p>`:""}${entries.length>6?`<details><summary>Все характеристики (${entries.length})</summary><dl class="attributes">${entries.map(([k,v])=>`<dt>${esc(k)}</dt><dd>${esc(v)||"Не указано"}</dd>`).join("")}</dl></details>`:""}${isPdf&&m.source_excerpt?`<details><summary>Фрагмент, распознанный Gemini</summary><blockquote class="source-excerpt">${esc(m.source_excerpt)}</blockquote></details>`:""}<span class="row-source">${source}</span>${links}</article>`;
  }).join("")}</div><p class="field-help">${isPdf?"Характеристики и фрагменты распознаны Gemini из PDF. Откройте указанные страницы и проверьте важные параметры. Для повторного чтения документа включите «Проверить по исходному PDF».":"Характеристики сохранены из прежнего каталога. Объяснение выбора составлено Gemini."}</p>`:emptyCatalog?'<div class="notice">Gemini не распознал товарных записей в документе. Загрузите каталог или прайс-лист с названиями товаров и характеристиками. Программа мероприятия или обычный текст не дадут вариантов для подбора.<br><button class="button subtle" data-action="upload">Загрузить каталог товаров</button></div>':'<div class="notice">Проверьте условия и читаемость PDF. Попробуйте уточнить название товара или смягчить одно из ограничений.</div>'}`;
}

async function loadHistory() {
  historyItems=(await api("/api/history")).items;
  $("#history-list").innerHTML=historyItems.length ? historyItems.map((r,i)=>`<button class="history-item" data-history="${i}"><span class="icon-tile">${icon("clock")}</span><div><strong>${esc(r.query)}</strong><p>${esc(date(r.created_at))} · ${esc(r.filename)} · ${r.source_type==="pdf"?`${count(r.page_count)} страниц`:`${count(r.analyzed)} товаров`}</p></div><span>${r.matches.length} вариантов</span>${icon("chevron")}</button>`).join("") : '<div class="panel empty-state"><h3>Здесь появятся ваши подборы</h3><p>Загрузите PDF и опишите нужный товар.<br>Результат сохранится вместе с запросом.</p><button class="button subtle" data-view="search">Перейти к подбору</button></div>';
}

function exportResults() {
  if(!currentResult)return;
  const fields=[...new Set(currentResult.matches.flatMap(m=>Object.keys(m.product.values)))];
  const cell=value=>`"${String(value??"").replace(/^[\s]*[=+@-]/,match=>"'"+match).replace(/"/g,'""')}"`;
  const sourceHeader=currentResult.source_type==="pdf"?"Страницы PDF":"Строка каталога";
  const rows=[[sourceHeader,"Результат Gemini","Объяснение","Нужно уточнить",...fields],...currentResult.matches.map(m=>[m.product.pages?m.product.pages.join(", "):m.product.row,m.status==="exact"?"Подходит":"Нужно уточнить",m.reason,m.missing.join("; "),...fields.map(k=>m.product.values[k]||"")])];
  const blob=new Blob(["\ufeff"+rows.map(r=>r.map(cell).join(";")).join("\r\n")],{type:"text/csv;charset=utf-8"});
  const url=URL.createObjectURL(blob),a=document.createElement("a");a.href=url;a.download="Torman-подбор.csv";a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
}

document.addEventListener("click",async event=>{
  const target=event.target.closest("button,[data-action]");if(!target)return;
  if(target.dataset.view) return showView(target.dataset.view);
  if(target.dataset.close) return $("#"+target.dataset.close).close();
  if(target.dataset.example) {$("#query").value=($("#query").value.trim()+" "+target.dataset.example).trim().slice(0,3000);queryCount();$("#query").focus();return;}
  if(target.dataset.history!==undefined) {currentResult=historyItems[Number(target.dataset.history)];await showView("search");$("#query").value=currentResult.query;queryCount();renderResults(currentResult);return;}
  switch(target.dataset.action) {
    case "settings":openSettings();break;
    case "upload":openUpload();break;
    case "choose-file":$("#file-input").value="";$("#file-input").click();break;
    case "export-results":exportResults();break;
    case "retry-search":$("#search-form").requestSubmit();break;
    case "cancel-search":if(currentJob)try{await api(`/api/jobs/${currentJob}/cancel`,{method:"POST",body:{}});toast("Останавливаем подбор после текущего обращения к Gemini.");}catch(error){toast(error.message,true);}break;
  }
});

$("#file-input").addEventListener("change",event=>{if(event.target.files[0])processFile(event.target.files[0]);});
$("#catalog-filter").addEventListener("input",()=>{clearTimeout(filterTimer);catalogPage=1;filterTimer=setTimeout(()=>loadCatalog().catch(e=>toast(e.message,true)),200);});
$("#page-prev").addEventListener("click",()=>{catalogPage--;loadCatalog().catch(e=>toast(e.message,true));});
$("#page-next").addEventListener("click",()=>{catalogPage++;loadCatalog().catch(e=>toast(e.message,true));});
$("#upload-dialog").addEventListener("close",()=>{++previewVersion;});

(async()=>{
  renderEmpty();
  try {
    await refreshState();
    if(state.active_job){currentJob=state.active_job;$("#search-progress").classList.remove("hidden");$("#search-button").disabled=true;pollJob();}
  }catch(error){toast(error.message,true);}
})();
