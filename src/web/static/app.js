/* Family History Search — interface (linha do tempo, árvore, revisão e nova busca). */
(function () {
  "use strict";

  const $ = (sel, root = document) => root.querySelector(sel);
  const PAGE = 40;
  const EVENTS = [["all", "Todos"], ["nascimento", "Nascimentos"], ["casamento", "Casamentos"], ["obito", "Óbitos"], ["outro", "Outros"]];
  const STATUSES = [["all", "Todas"], ["pendente", "Pendentes"], ["aprovado", "Aprovadas"], ["rejeitado", "Rejeitadas"]];
  const EVENT_TAG = { nascimento: "Nascimento", casamento: "Casamento", obito: "Óbito", outro: "Sem tipo" };
  const STATUS_TAG = { pendente: "Pendente", aprovado: "Aprovada", rejeitado: "Rejeitada" };
  const REL_TAG = { extraida: "Hipótese", confirmada: "Confirmada", rejeitada: "Rejeitada" };
  const REL_NAME = { filho_de: "filho de", casou_com: "casou com", viuvo_de: "viúvo de", residente_em: "residente em", faleceu_em: "faleceu em" };

  const state = {
    datasets: [], dataset: null, occ: [], review: {}, blob: new Map(),
    view: "timeline", shown: PAGE,
    f: { event: "all", status: "all", periodical: "", q: "", desc: false, person: null },
    tree: null, treeFor: null, treeCtl: null, selected: null,
    search: null, poll: null,
    people: null, personById: new Map(), peopleByOcc: {}, psort: "mentions",
    wide: new Map(),          // trecho ampliado já carregado, por ocorrência
  };

  // ---------------------------------------------------------------- utilidades
  const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fold = s => String(s ?? "").normalize("NFKD").replace(/[̀-ͯ]/g, "").toLowerCase();
  const icon = (id, cls = "icon") => `<svg class="${cls}"><use href="#i-${id}"/></svg>`;
  const plural = (n, one, many) => `${n.toLocaleString("pt-BR")} ${n === 1 ? one : many}`;
  const cap = s => String(s || "").replace(/(^|[\s-])(\p{L})/gu, (m, a, b) => a + b.toUpperCase());

  async function api(path, opts = {}) {
    const res = await fetch(path, { headers: { "Content-Type": "application/json" }, ...opts });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || `erro ${res.status}`);
    return data;
  }

  let toastTimer;
  function toast(msg) {
    const t = $("#toast");
    t.textContent = msg;
    t.classList.add("is-on");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => t.classList.remove("is-on"), 2600);
  }

  /* Destaca os termos num texto. A busca é feita no texto original ("GregO' rio" com o
     apóstrofo de verdade) numa expressão só, do termo mais longo ao mais curto — o nome
     inteiro ganha uma marcação, sem outra aninhada para o sobrenome — e cada pedaço é
     escapado na saída. */
  function highlight(text, terms) {
    const s = String(text ?? "");
    const uniq = [...new Set(terms.filter(t => t && t.trim().length >= 2).map(t => t.trim()))]
      .sort((a, b) => b.length - a.length);
    if (!uniq.length) return esc(s);
    const rx = new RegExp(uniq.map(t => t.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|"), "gi");
    let out = "", last = 0;
    for (const m of s.matchAll(rx)) {
      out += esc(s.slice(last, m.index)) + `<mark>${esc(m[0])}</mark>`;
      last = m.index + m[0].length;
    }
    return out + esc(s.slice(last));
  }

  const statusOf = o => (state.review[o.id] || {}).status || "pendente";
  const relStatus = (o, i) => ((state.review[o.id] || {}).relations || {})[String(i)] || o.relations[i].status || "extraida";

  // ---------------------------------------------------------------- endereço (#/visao/conjunto)
  function readHash() {
    const [, view, name] = location.hash.match(/^#\/(timeline|tree|people)\/?(.*)$/) || [];
    return { view: view || null, name: name ? decodeURIComponent(name) : null };
  }
  function writeHash() {
    if (!state.dataset) return;
    history.replaceState(null, "", `#/${state.view}/${encodeURIComponent(state.dataset.name)}`);
  }

  // ---------------------------------------------------------------- carga
  async function loadDatasets(prefer) {
    state.datasets = await api("/api/datasets");
    $("#picker-btn").disabled = !state.datasets.length;
    renderPicker();
    if (!state.datasets.length) return renderNoData();
    const want = prefer || readHash().name || localStorage.getItem("fhs.dataset");
    const ds = state.datasets.find(d => d.name === want) || state.datasets[0];
    await selectDataset(ds.name);
  }

  async function selectDataset(name) {
    const ds = state.datasets.find(d => d.name === name);
    if (!ds) return;
    state.dataset = ds;
    renderPicker();
    try { localStorage.setItem("fhs.dataset", name); } catch (e) { /* armazenamento indisponível */ }
    const base = `/api/datasets/${encodeURIComponent(name)}`;
    const [data, people] = await Promise.all([api(`${base}/occurrences`), api(`${base}/people`)]);
    state.occ = data.occurrences;
    state.review = data.review || {};
    state.wide = new Map();
    setPeople(people);
    state.blob = new Map(state.occ.map(o => [o.id, fold([
      o.context_excerpt, o.periodical, o.person_anchor && o.person_anchor.name_raw,
      ...(o.people_mentioned || []).map(p => p.name_raw), ...(o.places_mentioned || []).map(p => p.name_raw),
    ].join(" "))]));
    state.f = { event: "all", status: "all", periodical: "", q: $("#q").value.trim(), desc: state.f.desc, person: null };
    state.shown = PAGE;
    state.tree = null;
    state.selected = null;
    closeDrawer();
    renderHead();
    renderFilters();
    renderTimeline();
    renderPeople();
    if (state.view === "tree") loadTree();
    writeHash();
  }

  function setPeople(data) {
    state.people = data;
    state.personById = new Map(data.people.map(p => [p.id, p]));
    state.peopleByOcc = data.by_occurrence || {};
  }

  // nomes como aparecem no texto (com as grafias do OCR), para destacar no trecho
  function personTerms(occId) {
    return (state.peopleByOcc[occId] || []).flatMap(pid =>
      (state.personById.get(pid)?.variants || []).flatMap(v => v.raws));
  }

  // ---------------------------------------------------------------- seletor de buscas (topo)
  const dsTitle = d => d.person ? cap(d.person) : `Família ${cap(d.surname)}`;
  const dsKind = d => d.person ? `Pessoa · ${d.mode || "inteligente"}` : (d.scope === "Busca regional" ? "Família" : d.scope);

  function ago(ts) {
    const d = new Date(ts * 1000), now = new Date();
    const mins = Math.round((now - d) / 60000);
    const hhmm = d.toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" });
    const day = x => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
    const days = Math.round((day(now) - day(d)) / 86400000);
    if (mins < 1) return "agora";
    if (mins < 60) return `há ${mins} min`;
    if (days === 0) return `hoje, ${hhmm}`;
    if (days === 1) return `ontem, ${hhmm}`;
    if (days < 7) return `há ${days} dias`;
    return d.toLocaleDateString("pt-BR", { day: "numeric", month: "short", year: "numeric" });
  }

  const picker = { open: false, active: -1, items: [] };

  function renderPicker() {
    const cur = state.dataset;
    $("#picker-value").textContent = cur ? dsTitle(cur) : "—";
    $("#picker-btn").title = cur ? `${dsTitle(cur)} · ${dsKind(cur)}` : "";
    if (picker.open) renderPickerList();
  }

  function renderPickerList() {
    const q = fold($("#picker-q").value.trim());
    picker.items = state.datasets.filter(d => !q || fold(`${dsTitle(d)} ${dsKind(d)}`).includes(q));
    const cur = state.dataset && state.dataset.name;
    if (picker.active >= picker.items.length) picker.active = picker.items.length - 1;
    $("#picker-list").innerHTML = picker.items.length ? picker.items.map((d, i) => `
      <div class="picker-opt${i === picker.active ? " is-active" : ""}" role="option" id="picker-opt-${i}"
           data-ds="${esc(d.name)}" aria-selected="${d.name === cur}">
        <span class="avatar${d.person ? " is-person" : ""}">${esc(FamilyTree.initials(d.person || d.surname))}</span>
        <span class="opt-main">
          <span class="opt-name">${esc(dsTitle(d))}</span>
          <span class="opt-meta">${esc(dsKind(d))} · ${plural(d.count, "ocorrência", "ocorrências")} · ${esc(ago(d.updated))}</span>
        </span>
        ${d.name === cur ? `<svg class="icon opt-check"><use href="#i-check"/></svg>` : ""}
      </div>`).join("") : `<p class="picker-empty">Nenhuma busca com esse nome.</p>`;
    $("#picker-list").setAttribute("aria-activedescendant", picker.active >= 0 ? `picker-opt-${picker.active}` : "");
    $(`#picker-opt-${picker.active}`)?.scrollIntoView({ block: "nearest" });
  }

  function openPicker() {
    if (picker.open || !state.datasets.length) return;
    picker.open = true;
    $("#picker-q").value = "";
    $("#picker-filter").hidden = state.datasets.length <= 6;
    picker.active = Math.max(0, state.datasets.findIndex(d => d.name === state.dataset?.name));
    renderPickerList();
    $("#picker-menu").hidden = false;
    $("#picker-btn").setAttribute("aria-expanded", "true");
    ($("#picker-filter").hidden ? $("#picker-list") : $("#picker-q")).focus();
  }

  function closePicker(refocus) {
    if (!picker.open) return;
    picker.open = false;
    $("#picker-menu").hidden = true;
    $("#picker-btn").setAttribute("aria-expanded", "false");
    if (refocus) $("#picker-btn").focus();
  }

  function choosePicker(name) {
    closePicker(true);
    if (name && name !== state.dataset?.name) selectDataset(name).catch(err => toast(err.message));
  }

  $("#picker-btn").addEventListener("click", () => (picker.open ? closePicker(true) : openPicker()));
  $("#picker-btn").addEventListener("keydown", e => {
    if (["ArrowDown", "ArrowUp"].includes(e.key)) { e.preventDefault(); openPicker(); }
  });
  $("#picker-list").addEventListener("click", e => {
    const opt = e.target.closest("[data-ds]");
    if (opt) choosePicker(opt.dataset.ds);
  });
  $("#picker-list").addEventListener("mousemove", e => {
    const opt = e.target.closest("[data-ds]");
    const i = opt ? picker.items.findIndex(d => d.name === opt.dataset.ds) : -1;
    if (i >= 0 && i !== picker.active) {
      $(".picker-opt.is-active")?.classList.remove("is-active");
      opt.classList.add("is-active");
      picker.active = i;
    }
  });
  $("#picker-q").addEventListener("input", () => { picker.active = 0; renderPickerList(); });
  $(".picker-new").addEventListener("click", () => closePicker(false));   // o painel de nova busca abre em seguida
  $("#picker-menu").addEventListener("keydown", e => {
    const n = picker.items.length;
    const move = to => { e.preventDefault(); picker.active = n ? (to + n) % n : -1; renderPickerList(); };
    if (e.key === "ArrowDown") move(picker.active + 1);
    else if (e.key === "ArrowUp") move(picker.active - 1);
    else if (e.key === "Home" && e.target.id !== "picker-q") move(0);
    else if (e.key === "End" && e.target.id !== "picker-q") move(n - 1);
    else if (e.key === "Enter" && picker.items[picker.active]) { e.preventDefault(); choosePicker(picker.items[picker.active].name); }
    else if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); closePicker(true); }
    else if (e.key === "Tab") closePicker(false);
  });
  document.addEventListener("pointerdown", e => {
    if (picker.open && !e.target.closest("#picker")) closePicker(false);
  });

  // ---------------------------------------------------------------- cabeçalho
  function renderHead() {
    const ds = state.dataset, occ = state.occ;
    $("#eyebrow").textContent = `${ds.scope} · Hemeroteca Digital Brasileira`;
    $("#title").innerHTML = ds.person ? `<em>${esc(cap(ds.person))}</em>` : `Família <em>${esc(cap(ds.surname))}</em>`;
    const years = occ.map(o => o.year).filter(Boolean);
    const periodicals = new Set(occ.map(o => o.periodical).filter(Boolean));
    const reviewed = occ.filter(o => statusOf(o) !== "pendente").length;
    const approved = occ.filter(o => statusOf(o) === "aprovado").length;
    const stats = [
      ["Ocorrências", occ.length.toLocaleString("pt-BR")],
      ["Periódicos", periodicals.size.toLocaleString("pt-BR")],
      ["Período", years.length ? `${Math.min(...years)}–${Math.max(...years)}` : "—"],
      ["Revisadas", `${reviewed} de ${occ.length}`],
    ];
    $("#stats").innerHTML = stats.map(([k, v]) => `<div><dt>${k}</dt><dd>${esc(v)}</dd></div>`).join("");
    for (const [id, fmt] of [["#export-csv", "csv"], ["#export-json", "json"]]) {
      const a = $(id);
      a.href = `/api/datasets/${encodeURIComponent(ds.name)}/export.${fmt}`;
      a.setAttribute("aria-disabled", approved ? "false" : "true");
      a.title = approved ? `${plural(approved, "ocorrência aprovada", "ocorrências aprovadas")}` : "Aprove ocorrências para exportar";
    }
  }

  function renderNoData() {
    $("#eyebrow").textContent = "Hemeroteca Digital Brasileira";
    $("#title").innerHTML = "Comece por um <em>sobrenome</em>";
    $("#stats").innerHTML = "";
    $("#view-timeline .filters").hidden = true;
    $("#result-count").textContent = "";
    $("#timeline").innerHTML = "";
    const empty = $("#timeline-empty");
    empty.hidden = false;
    empty.innerHTML = `<h3>Nenhuma busca ainda</h3>
      <p>Digite um sobrenome e a ferramenta procura nos jornais da Hemeroteca Digital, recorta os trechos e monta a linha do tempo e a árvore.</p>
      <button class="btn btn-primary" data-action="new-search">${icon("plus")}Nova busca</button>`;
  }

  // ---------------------------------------------------------------- filtros
  function renderFilters() {
    $("#view-timeline .filters").hidden = false;
    const count = key => state.occ.filter(o => key === "all" || (o.event_type || "outro") === key).length;
    $("#f-event").innerHTML = EVENTS.map(([k, label]) => {
      const n = count(k);
      return (k === "all" || n) ? `<button type="button" data-event="${k}" aria-pressed="${state.f.event === k}">${label}<span class="n">${n}</span></button>` : "";
    }).join("");
    $("#f-status").innerHTML = STATUSES.map(([k, label]) =>
      `<button type="button" data-status="${k}" aria-pressed="${state.f.status === k}">${label}</button>`).join("");
    const pers = [...new Set(state.occ.map(o => o.periodical).filter(Boolean))].sort((a, b) => a.localeCompare(b, "pt-BR"));
    $("#f-periodical").innerHTML = `<option value="">Todos os periódicos</option>` +
      pers.map(p => `<option value="${esc(p)}">${esc(p)}</option>`).join("");
    $("#f-periodical").value = state.f.periodical;
    $("#f-periodical").hidden = pers.length < 2;
    $("#f-sort span").textContent = state.f.desc ? "Mais recente primeiro" : "Mais antigo primeiro";
  }

  function filtered() {
    const { event, status, periodical, q, desc, person } = state.f;
    const terms = fold(q).split(/\s+/).filter(Boolean);
    const ofPerson = person ? new Set(state.personById.get(person)?.occurrences || []) : null;
    const rows = state.occ.filter(o =>
      (event === "all" || (o.event_type || "outro") === event) &&
      (status === "all" || statusOf(o) === status) &&
      (!periodical || o.periodical === periodical) &&
      (!ofPerson || ofPerson.has(o.id)) &&
      terms.every(t => state.blob.get(o.id).includes(t)));
    const cmp = (a, b) => {
      for (let i = 0; i < 3; i++) if (a.date_key[i] !== b.date_key[i]) return a.date_key[i] - b.date_key[i];
      return (a.periodical || "").localeCompare(b.periodical || "") || a.physical_page - b.physical_page;
    };
    rows.sort(desc ? (a, b) => cmp(b, a) : cmp);
    return rows;
  }

  // ---------------------------------------------------------------- linha do tempo
  function cardHTML(o, i) {
    const st = statusOf(o);
    const terms = [o.match_text, ...personTerms(o.id), state.dataset.surname, ...state.f.q.split(/\s+/)];
    const people = (state.peopleByOcc[o.id] || []).map(pid => state.personById.get(pid)).filter(Boolean);
    // busca de pessoa: como o nome foi lido nesta página, quando difere do que foi buscado
    const readAs = o.match_text && o.person_query && fold(o.match_text).replace(/\W+/g, " ").trim() !== fold(o.person_query).replace(/\W+/g, " ").trim()
      ? `<span class="read-as" title="Como o nome aparece nesta página">lido como “${esc(o.match_text)}”</span>` : "";
    const nRel = (o.relations || []).length;
    return `<article class="card${i !== undefined && i < 12 ? " reveal" : ""}" data-id="${esc(o.id)}" data-status="${st}"${i !== undefined ? ` style="--i:${i}"` : ""}>
      <div class="card-top">
        <div class="card-when">
          <div><span class="date">${esc(o.date_display)}</span>${o.date_label ? `<span class="date-label">${esc(o.date_label)}</span>` : ""}</div>
          <div class="source" title="${esc(o.periodical)}">${esc(o.periodical || "Periódico sem nome")} · ed. ${esc(o.edition)}, p. ${esc(o.physical_page)}</div>
        </div>
        <div class="actions">
          <button class="icon-btn ok" data-review="aprovado" title="Aprovar" aria-label="Aprovar">${icon("check")}</button>
          <button class="icon-btn no" data-review="rejeitado" title="Rejeitar" aria-label="Rejeitar">${icon("x")}</button>
          ${st !== "pendente" ? `<button class="icon-btn" data-review="pendente" title="Voltar para pendente" aria-label="Voltar para pendente">${icon("undo")}</button>` : ""}
        </div>
      </div>
      <div class="excerpt">${highlight(o.context_excerpt, terms)}</div>
      <div class="card-foot">
        ${people.map(p => `<button class="person-chip" data-person="${esc(p.id)}" title="Ver pessoa">${esc(p.name)}</button>`).join("")}
        <span class="tag tag-${esc(o.event_type || "outro")}">${EVENT_TAG[o.event_type] || "Sem tipo"}</span>
        ${st !== "pendente" ? `<span class="tag tag-${st}">${STATUS_TAG[st]}</span>` : ""}
        ${nRel ? `<span class="tag">${plural(nRel, "relação", "relações")}</span>` : ""}
        ${readAs}
        <span class="spacer"></span>
        <button class="link" data-toggle>${icon("plus")}<span>Ler mais</span></button>
        <a class="link" href="${esc(o.source_url)}" target="_blank" rel="noopener">${icon("external")}Fonte</a>
      </div>
      <div class="details" hidden></div>
    </article>`;
  }

  function detailsHTML(o) {
    const list = a => (a || []).map(p => esc(p.name_raw)).join(", ");
    const rows = [];
    const people = (state.peopleByOcc[o.id] || []).map(pid => state.personById.get(pid)).filter(Boolean);
    if (people.length) rows.push([people.length === 1 ? "Pessoa" : "Pessoas",
      people.map(p => `<button class="person-chip" data-person="${esc(p.id)}">${esc(p.name)}</button>`).join(" ")]);
    if ((o.people_mentioned || []).length) rows.push(["Também citadas", list(o.people_mentioned)]);
    if ((o.places_mentioned || []).length) rows.push(["Lugares", list(o.places_mentioned)]);
    if (o.event_type_evidence) rows.push(["Tipo de evento", `${esc(EVENT_TAG[o.event_type])}, pela palavra “${esc(o.event_type_evidence)}” (${Math.round((o.event_type_confidence || 0) * 100)}%)`]);
    if (o.publication_date) rows.push(["Publicação", esc(o.publication_date)]);
    if (o.column_mix_suspected) rows.push(["Atenção", "o trecho pode misturar colunas vizinhas da página"]);
    const rels = (o.relations || []).map((r, i) => relationHTML(r, relStatus(o, i), { occ: o.id, idx: i })).join("");
    return `<dl class="kv">${rows.map(([k, v]) => `<dt>${k}</dt><dd>${v}</dd>`).join("")}</dl>` +
      (rels ? `<div><p class="section-title">Relações extraídas</p>${rels}</div>` : "");
  }

  function relationHTML(r, st, ref, phrase) {
    const line = phrase || `${esc(r.subject_raw || "?")}<span class="rel-arrow">→</span>${esc(r.target_raw)}`;
    return `<div class="rel" data-occ="${esc(ref.occ)}" data-idx="${ref.idx}">
      <div class="rel-main">
        <div class="rel-line"><span class="rel-type">${esc(REL_NAME[r.relation] || r.relation)}</span>${line}</div>
        <div class="rel-ev">“${esc(r.evidence)}”</div>
        <div class="card-foot" style="margin-top:8px">
          <span class="tag tag-${st}">${REL_TAG[st] || st}</span>
          ${r.confidence != null ? `<span class="node-meta">${Math.round(r.confidence * 100)}%</span>` : ""}
          ${ref.url ? `<a class="link" href="${esc(ref.url)}" target="_blank" rel="noopener">${icon("external")}Fonte</a>` : ""}
        </div>
      </div>
      <div class="rel-actions">
        ${st !== "confirmada" ? `<button class="btn btn-ghost btn-sm" data-rel="confirmada">${icon("check")}Confirmar</button>` : ""}
        ${st !== "rejeitada" ? `<button class="btn btn-ghost btn-sm" data-rel="rejeitada">${icon("x")}Rejeitar</button>` : ""}
        ${st !== "extraida" ? `<button class="icon-btn" data-rel="extraida" title="Desfazer" aria-label="Desfazer">${icon("undo")}</button>` : ""}
      </div>
    </div>`;
  }

  function renderTimeline() {
    const rows = filtered();
    const page = rows.slice(0, state.shown);
    const tl = $("#timeline");
    $("#timeline-empty").hidden = rows.length > 0;
    if (!rows.length) {
      tl.innerHTML = "";
      $("#timeline-empty").innerHTML = `<h3>Nada encontrado</h3><p>Nenhuma ocorrência combina com esses filtros.</p>
        <button class="btn btn-ghost" data-action="clear-filters">Limpar filtros</button>`;
    }
    const person = state.f.person && state.personById.get(state.f.person);
    $("#result-count").innerHTML = esc(rows.length === state.occ.length
      ? plural(rows.length, "ocorrência", "ocorrências")
      : `${plural(rows.length, "ocorrência", "ocorrências")} de ${state.occ.length.toLocaleString("pt-BR")}`) +
      (person ? ` <span class="filter-chip">${esc(person.name)}<button data-action="clear-person" aria-label="Tirar filtro de pessoa">${icon("x")}</button></span>` : "");

    const groups = [];
    for (const o of page) {
      const y = o.date_key[0] || "Sem data";
      if (!groups.length || groups[groups.length - 1].year !== y) groups.push({ year: y, items: [] });
      groups[groups.length - 1].items.push(o);
    }
    const yearCount = new Map();
    rows.forEach(o => yearCount.set(o.date_key[0] || "Sem data", (yearCount.get(o.date_key[0] || "Sem data") || 0) + 1));
    let i = 0;
    tl.innerHTML = groups.map(g => `<section class="year-group">
        <h2 class="year-label">${esc(g.year)}<small>${plural(yearCount.get(g.year), "registro", "registros")}</small></h2>
        <div class="year-items">${g.items.map(o => cardHTML(o, i++)).join("")}</div>
      </section>`).join("");
    $("#more").hidden = rows.length <= state.shown;
    $("#more").textContent = `Mostrar mais ${Math.min(PAGE, rows.length - state.shown)}`;
  }

  function refreshCard(id) {
    const el = $(`.card[data-id="${CSS.escape(id)}"]`);
    const o = state.occ.find(x => x.id === id);
    if (!el || !o) return;
    const open = el.classList.contains("is-open");
    const tmp = document.createElement("div");
    tmp.innerHTML = cardHTML(o);
    const fresh = tmp.firstElementChild;
    el.replaceWith(fresh);
    if (open) toggleCard(fresh, true);
  }

  function toggleCard(card, force) {
    const open = force ?? !card.classList.contains("is-open");
    const o = state.occ.find(x => x.id === card.dataset.id);
    card.classList.toggle("is-open", open);
    const det = $(".details", card);
    if (open) det.innerHTML = detailsHTML(o);
    det.hidden = !open;
    const t = $("[data-toggle]", card);
    t.innerHTML = `${icon(open ? "minus" : "plus")}<span>${open ? "Fechar" : "Ler mais"}</span>`;
    const box = $(".excerpt", card);
    if (open) showWide(box, o);
    else {
      box.classList.remove("is-wide");
      box.innerHTML = highlight(o.context_excerpt, [o.match_text, ...personTerms(o.id), state.dataset.surname, ...state.f.q.split(/\s+/)]);
    }
  }

  // ---------------------------------------------------------------- trecho ampliado
  async function loadWide(id) {
    if (!state.wide.has(id)) {
      state.wide.set(id, api(`/api/datasets/${encodeURIComponent(state.dataset.name)}/occurrences/${encodeURIComponent(id)}/context`)
        .catch(e => { state.wide.delete(id); throw e; }));
    }
    return state.wide.get(id);
  }

  async function showWide(box, o) {
    const terms = [o.match_text, ...personTerms(o.id), state.dataset.surname, ...state.f.q.split(/\s+/)];
    box.classList.add("is-loading", "is-wide");
    try {
      const ctx = await loadWide(o.id);
      if (!box.isConnected) return;
      box.innerHTML = ctx.paragraphs.map(p => `<p>${highlight(p, terms)}</p>`).join("") +
        `<p class="wide-note">${ctx.source === "pdf"
          ? "Trecho ampliado: blocos vizinhos na mesma coluna da página"
          : "Trecho da página (o PDF não está no cache, a ordem das colunas pode se misturar)"}</p>`;
    } catch (e) {
      toast(`Não foi possível ampliar o trecho: ${e.message}`);
    } finally {
      box.classList.remove("is-loading");
    }
  }

  // ---------------------------------------------------------------- revisão
  async function review(body) {
    try {
      const res = await api(`/api/datasets/${encodeURIComponent(state.dataset.name)}/review`, { method: "POST", body: JSON.stringify(body) });
      state.review = res.review;
      return true;
    } catch (e) {
      toast(`Não foi possível salvar: ${e.message}`);
      return false;
    }
  }

  async function reviewOccurrence(id, status) {
    if (!(await review({ occurrence_id: id, status }))) return;
    renderHead();
    if (state.f.status !== "all" && state.f.status !== status) {
      renderTimeline();
    } else refreshCard(id);
    toast({ aprovado: "Ocorrência aprovada", rejeitado: "Ocorrência rejeitada", pendente: "Voltou para pendente" }[status]);
  }

  async function reviewRelation(occId, idx, status) {
    if (!(await review({ occurrence_id: occId, relation_index: idx, status }))) return;
    refreshCard(occId);
    state.tree = null;                         // a árvore usa o status das relações
    if (state.view === "tree") await loadTree(true);
    if (state.selected && $("#drawer").classList.contains("is-open") && state.drawerKind === "node") openNode(state.selected);
    toast({ confirmada: "Relação confirmada", rejeitada: "Relação rejeitada", extraida: "Relação voltou a ser hipótese" }[status]);
  }

  // ---------------------------------------------------------------- árvore
  async function loadTree(keepView) {
    const name = state.dataset && state.dataset.name;
    if (!name) return;
    if (!state.tree || state.treeFor !== name) {
      state.tree = await api(`/api/datasets/${encodeURIComponent(name)}/tree`);
      state.treeFor = name;
    }
    renderTree(keepView);
  }

  function renderTree(keepView) {
    const t = state.tree, stage = $("#tree-stage"), vp = $("#tree-viewport");
    const empty = !t.nodes.length;
    vp.hidden = empty;
    $("#tree-empty").hidden = !empty;
    const nFlags = t.cycles.length + t.flags.length;
    $("#tree-flags").hidden = !nFlags;
    $("#tree-flags span").textContent = plural(nFlags, "sinal para revisar", "sinais para revisar");
    if (empty) {
      $("#tree-empty").innerHTML = `<h3>Nenhum parentesco extraído ainda</h3>
        <p>A árvore é montada a partir de frases como “filho de”, “casou com” e “viúvo de” nos trechos encontrados. Nesta família, nenhuma apareceu com um nome reconhecível dos dois lados.</p>`;
      return;
    }
    FamilyTree.render(stage, t, id => openNode(id));
    if (!state.treeCtl) state.treeCtl = FamilyTree.viewport(vp, stage, k => { $("#zoom-level").textContent = Math.round(k * 100) + "%"; });
    if (state.selected) $(`.node[data-id="${CSS.escape(state.selected)}"]`)?.classList.add("is-selected");
    if (!keepView) requestAnimationFrame(() => state.treeCtl.fit());
  }

  function phraseFor(e, id, label) {
    const other = esc(label(e.source === id ? e.target : e.source));
    if (e.relation === "filho_de") return e.source === id ? `Filho(a) de <b>${other}</b>` : `Pai ou mãe de <b>${other}</b>`;
    if (e.relation === "casou_com") return `Casou com <b>${other}</b>`;
    if (e.relation === "viuvo_de") return e.source === id ? `Viúvo(a) de <b>${other}</b>` : `Cônjuge falecido(a) de <b>${other}</b>`;
    return other;
  }

  function openNode(id) {
    const t = state.tree, node = t.nodes.find(n => n.id === id);
    if (!node) return;
    state.selected = id;
    document.querySelectorAll(".node.is-selected").forEach(n => n.classList.remove("is-selected"));
    $(`.node[data-id="${CSS.escape(id)}"]`)?.classList.add("is-selected");
    const byId = new Map(t.nodes.map(n => [n.id, n.label]));
    const label = x => byId.get(x) || x;
    const edges = t.edges.filter(e => e.source === id || e.target === id);
    const ref = e => ({ occ: e.occurrence_id, idx: e.relation_index, url: e.source_url });
    const facts = node.facts.map(f => `<dt>${esc(f.label)}</dt><dd>${esc(f.value)}</dd>`).join("");
    const body = `
      <p class="section-title">Parentesco</p>
      ${edges.map(e => relationHTML(e, e.status, ref(e), phraseFor(e, id, label))).join("")}
      ${facts ? `<p class="section-title">Outros registros</p><dl class="kv">${facts}</dl>` : ""}
      <p class="section-title">Sobre este nome</p>
      <p style="color:var(--muted);font-size:13px;margin:0">${node.id.startsWith("cluster:")
        ? "Agrupado com outras menções que provavelmente são a mesma pessoa (nome, pessoas em comum, lugar e época). É uma hipótese: confira as fontes."
        : "Nome citado numa relação, sem agrupamento com outras menções. Grafias diferentes podem aparecer como pessoas separadas."}</p>`;
    const personLink = node.person && state.personById.has(node.person)
      ? `<button class="btn btn-ghost btn-sm" data-person="${esc(node.person)}">${icon("people")}Ver todas as menções desta pessoa</button>` : "";
    openDrawer("node", node.bears_surname ? "Leva o sobrenome" : "Pessoa citada", node.label, personLink + body);
  }

  function openFlags() {
    const t = state.tree;
    const cycles = t.cycles.map(c => `<div class="rel"><div class="rel-main"><div class="rel-line">${c.map(esc).join('<span class="rel-arrow">→</span>')}<span class="rel-arrow">→</span>${esc(c[0])}</div>
      <div class="rel-ev">Ciclo de filiação: impossível. Ao menos uma dessas relações está errada.</div></div></div>`).join("");
    const flags = t.flags.map(f => `<div class="rel"><div class="rel-main"><div class="rel-line">${esc(f.filho)}<span class="rel-arrow">→</span>${esc(f.pai_ou_mae)}</div>
      <div class="rel-ev">O filho aparece num jornal de ${f.ano_mais_antigo_filho}, antes do pai ou da mãe (${f.ano_mais_antigo_pai_mae}). Sinal fraco: usa o ano do jornal, não a data de nascimento.</div></div></div>`).join("");
    openDrawer("flags", "Árvore", "Sinais para revisar",
      (cycles ? `<p class="section-title">Ciclos</p>${cycles}` : "") + (flags ? `<p class="section-title">Datas incompatíveis</p>${flags}` : ""));
  }

  // ---------------------------------------------------------------- pessoas
  function renderPeople() {
    const data = state.people;
    if (!data) return;
    const q = fold(state.f.q);
    let list = data.people.filter(p => !q || fold(p.name + " " + p.variants.flatMap(v => v.raws).join(" ")).includes(q));
    const by = {
      mentions: (a, b) => b.mentions - a.mentions || a.name.localeCompare(b.name, "pt-BR"),
      name: (a, b) => a.name.localeCompare(b.name, "pt-BR"),
      year: (a, b) => (a.years ? a.years[0] : 9999) - (b.years ? b.years[0] : 9999) || b.mentions - a.mentions,
    }[state.psort];
    list = list.slice().sort(by);
    document.querySelectorAll("[data-psort]").forEach(b => b.setAttribute("aria-pressed", String(b.dataset.psort === state.psort)));
    const total = data.people.length;
    $("#people-summary").innerHTML = `${plural(total, "pessoa", "pessoas")} com o sobrenome` +
      (data.surname_only ? ` <span class="muted">· ${plural(data.surname_only, "menção", "menções")} só com o sobrenome, sem prenome</span>` : "") +
      (q && list.length !== total ? ` <span class="muted">· ${list.length} na busca</span>` : "");
    $("#people-empty").hidden = list.length > 0;
    if (!list.length) {
      $("#people-empty").innerHTML = total
        ? `<h3>Ninguém com esse nome</h3><p>Nenhuma pessoa combina com a busca.</p>`
        : `<h3>Nenhuma pessoa identificada</h3><p>Os trechos citam o sobrenome, mas sem um prenome reconhecível ao lado.</p>`;
    }
    $("#people-grid").innerHTML = list.map((p, i) => `
      <button class="person-card${i < 12 ? " reveal" : ""}" style="--i:${i}" data-person="${esc(p.id)}">
        <span class="avatar">${esc(FamilyTree.initials(p.name))}</span>
        <span class="person-body">
          <span class="person-name">${esc(p.name)}</span>
          <span class="person-meta">${plural(p.mentions, "menção", "menções")}${p.years ? ` · ${p.years[0] === p.years[1] ? p.years[0] : p.years.join("–")}` : ""}</span>
          <span class="person-tags">
            ${p.titles.map(t => `<span class="tag">${esc(t)}</span>`).join("")}
            ${p.variants.length > 1 ? `<span class="variants-count">${plural(p.variants.length, "grafia", "grafias")}</span>` : ""}
          </span>
        </span>
      </button>`).join("");
  }

  function openPerson(id) {
    const p = state.personById.get(id);
    if (!p) return toast("Essa pessoa não está mais no agrupamento");
    const occs = p.occurrences.map(oid => state.occ.find(o => o.id === oid)).filter(Boolean)
      .sort((a, b) => a.date_key[0] - b.date_key[0] || a.date_key[1] - b.date_key[1] || a.date_key[2] - b.date_key[2]);
    const raws = p.variants.flatMap(v => v.raws);
    const others = state.people.people.filter(x => x.id !== id).sort((a, b) => a.name.localeCompare(b.name, "pt-BR"));
    const stats = [
      ["Menções", p.mentions],
      ["Ocorrências", occs.length],
      ["Período", p.years ? (p.years[0] === p.years[1] ? p.years[0] : p.years.join("–")) : "—"],
      ["Periódicos", p.periodicals],
    ];
    const body = `
      <dl class="stats stats-sm">${stats.map(([k, v]) => `<div><dt>${k}</dt><dd>${esc(v)}</dd></div>`).join("")}</dl>
      ${p.titles.length ? `<p class="person-titles">${p.titles.map(t => `<span class="tag">${esc(t)}</span>`).join(" ")}</p>` : ""}
      <button class="btn btn-ghost btn-sm" data-action="person-timeline" data-id="${esc(id)}">${icon("timeline")}Ver na linha do tempo</button>

      <p class="section-title">Grafias encontradas</p>
      <p class="hint-text">Erros de OCR e formas do nome que foram tratados como a mesma pessoa.</p>
      ${p.variants.map(v => `
        <div class="variant">
          <div class="variant-main">
            <div class="variant-raws">${v.raws.map(r => `<span>${esc(r)}</span>`).join("")}</div>
            <div class="node-meta">${plural(v.count, "vez", "vezes")}</div>
          </div>
          ${p.variants.length > 1 ? `<button class="btn btn-ghost btn-sm" data-split="${esc(v.key)}" title="Separar esta grafia numa pessoa à parte">${icon("split")}Não é esta pessoa</button>` : ""}
        </div>`).join("")}
      <div class="merge-row">
        <select class="select" id="merge-target" aria-label="Juntar com outra pessoa">
          <option value="">Juntar com outra pessoa…</option>
          ${others.map(o => `<option value="${esc(o.variants[0].key)}">${esc(o.name)} (${o.mentions})</option>`).join("")}
        </select>
        <button class="btn btn-ghost btn-sm" data-merge="${esc(p.variants[0].key)}">${icon("merge")}Juntar</button>
      </div>
      <button class="link" data-reset-person="${esc(id)}">${icon("undo")}Desfazer ajustes manuais desta pessoa</button>

      <p class="section-title">Onde aparece</p>
      ${occs.map(o => `
        <article class="mini" data-id="${esc(o.id)}">
          <div class="mini-head"><span class="date">${esc(o.date_display)}</span><span class="source">${esc(o.periodical || "")}</span></div>
          <div class="excerpt">${highlight(o.context_excerpt, raws)}</div>
          <div class="card-foot">
            <button class="link" data-mini-wide>${icon("plus")}<span>Trecho maior</span></button>
            <a class="link" href="${esc(o.source_url)}" target="_blank" rel="noopener">${icon("external")}Fonte</a>
          </div>
        </article>`).join("")}`;
    openDrawer("person", "Pessoa", p.name, body);
    state.drawerPerson = id;
  }

  async function adjustPeople(body, message) {
    try {
      const data = await api(`/api/datasets/${encodeURIComponent(state.dataset.name)}/people`, { method: "POST", body: JSON.stringify(body) });
      setPeople(data);
      state.tree = null;                    // a árvore junta os nós pelas mesmas pessoas
      renderPeople();
      renderTimeline();
      if (state.view === "tree") loadTree(true);
      toast(message);
      return data;
    } catch (e) {
      toast(`Não foi possível ajustar: ${e.message}`);
      return null;
    }
  }

  // pessoa que contém a grafia `key` depois de um ajuste
  const personWithKey = key => state.people.people.find(p => p.variants.some(v => v.key === key));

  // ---------------------------------------------------------------- painel lateral
  function openDrawer(kind, eyebrow, title, html) {
    state.drawerKind = kind;
    $("#drawer-eyebrow").textContent = eyebrow;
    $("#drawer-title").textContent = title;
    $("#drawer-body").innerHTML = html;
    $("#drawer").classList.add("is-open");
    $("#drawer").setAttribute("aria-hidden", "false");
    $("#scrim").hidden = false;
  }
  function closeDrawer() {
    $("#drawer").classList.remove("is-open");
    $("#drawer").setAttribute("aria-hidden", "true");
    $("#scrim").hidden = true;
    state.drawerKind = null;
    if (state.selected) {
      document.querySelectorAll(".node.is-selected").forEach(n => n.classList.remove("is-selected"));
      state.selected = null;
    }
  }

  // ---------------------------------------------------------------- nova busca
  const KIND_HINT = {
    familia: "Todas as pessoas com o sobrenome, para montar a família.",
    pessoa: "Só as menções a uma pessoa: nome e sobrenome.",
  };
  const MODE_HINT = {
    inteligente: "Acha também as grafias do jornal e os erros de leitura: “João Silva” encontra “Joam Silva”, “JOÃO SILVA” e “João Carlos Silva”. Pesquisa o sobrenome e as grafias prováveis dele, por isso pode demorar mais.",
    literal: "Só o nome exatamente como digitado, sem diferença de acentos e maiúsculas: “João Silva” encontra “JOAO SILVA”, mas não “Joam Silva” nem “João Carlos Silva”. Mais rápido e com menos resultados errados.",
  };
  const PLACEHOLDER = { nome: "João", sobrenome: "Silva" };

  // ---------------------------------------------------------------- seletor de estado (lista do memoria.bn)
  function ufSelectHTML(data, value) {
    const cur = data.locais.find(o => o.value === value) || data.locais[0];
    const hint = data.fonte === "memoria.bn"
      ? "Locais oferecidos pelo memoria.bn."
      : "Os 27 estados. Depois da primeira busca, a lista passa a ser a do próprio memoria.bn.";
    return `
      <div class="field">
        <span class="field-label" id="s-uf-label">Estado</span>
        <div class="cselect" id="s-uf-box">
          <input type="hidden" name="uf" id="s-uf" value="${esc(cur ? cur.value : "")}">
          <button type="button" class="cselect-btn" id="s-uf-btn" aria-haspopup="listbox" aria-expanded="false"
                  aria-labelledby="s-uf-label s-uf-btn">
            <span class="cselect-value" id="s-uf-value">${esc(cur ? cur.label : "—")}</span>
            <span class="cselect-sigla" id="s-uf-sigla">${esc(cur ? cur.sigla : "")}</span>
            <svg class="icon picker-chevron"><use href="#i-chevron"/></svg>
          </button>
          <div class="picker-menu cselect-menu" id="s-uf-menu" hidden>
            <div class="picker-filter">
              ${icon("search")}
              <input id="s-uf-q" type="search" placeholder="Filtrar estados" autocomplete="off" aria-label="Filtrar estados">
            </div>
            <div class="picker-list" id="s-uf-list" role="listbox" aria-labelledby="s-uf-label" tabindex="-1"></div>
          </div>
        </div>
        <span class="hint">${hint}</span>
      </div>`;
  }

  const uf = { open: false, active: 0, items: [] };

  function renderUfList() {
    const q = fold($("#s-uf-q").value.trim());
    const value = $("#s-uf").value;
    uf.items = state.locais.locais.filter(o => !q || fold(`${o.label} ${o.sigla} ${o.value}`).includes(q));
    if (uf.active >= uf.items.length) uf.active = uf.items.length - 1;
    $("#s-uf-list").innerHTML = uf.items.length ? uf.items.map((o, i) => `
      <div class="picker-opt${i === uf.active ? " is-active" : ""}" role="option" id="s-uf-opt-${i}"
           data-uf="${esc(o.value)}" aria-selected="${o.value === value}">
        <span class="opt-main"><span class="opt-name">${esc(o.label)}</span></span>
        ${o.sigla ? `<span class="opt-sigla">${esc(o.sigla)}</span>` : ""}
        ${o.value === value ? `<svg class="icon opt-check"><use href="#i-check"/></svg>` : ""}
      </div>`).join("") : `<p class="picker-empty">Nenhum local com esse nome.</p>`;
    $("#s-uf-list").setAttribute("aria-activedescendant", uf.active >= 0 ? `s-uf-opt-${uf.active}` : "");
    $(`#s-uf-opt-${uf.active}`)?.scrollIntoView({ block: "nearest" });
  }

  function openUf() {
    if (uf.open) return;
    uf.open = true;
    $("#s-uf-q").value = "";
    uf.active = Math.max(0, state.locais.locais.findIndex(o => o.value === $("#s-uf").value));
    renderUfList();
    $("#s-uf-menu").hidden = false;
    $("#s-uf-btn").setAttribute("aria-expanded", "true");
    $("#s-uf-q").focus();
  }

  function closeUf(refocus) {
    if (!uf.open) return;
    uf.open = false;
    $("#s-uf-menu").hidden = true;
    $("#s-uf-btn").setAttribute("aria-expanded", "false");
    if (refocus) $("#s-uf-btn").focus();
  }

  function chooseUf(value) {
    const o = state.locais.locais.find(x => x.value === value);
    if (o) {
      $("#s-uf").value = o.value;
      $("#s-uf-value").textContent = o.label;
      $("#s-uf-sigla").textContent = o.sigla;
    }
    closeUf(true);
  }

  function wireUfSelect() {
    $("#s-uf-btn").addEventListener("click", () => (uf.open ? closeUf(true) : openUf()));
    $("#s-uf-btn").addEventListener("keydown", e => {
      if (["ArrowDown", "ArrowUp"].includes(e.key)) { e.preventDefault(); openUf(); }
    });
    $("#s-uf-list").addEventListener("click", e => {
      const opt = e.target.closest("[data-uf]");
      if (opt) chooseUf(opt.dataset.uf);
    });
    $("#s-uf-q").addEventListener("input", () => { uf.active = 0; renderUfList(); });
    $("#s-uf-menu").addEventListener("keydown", e => {
      const n = uf.items.length;
      const move = to => { e.preventDefault(); uf.active = n ? (to + n) % n : -1; renderUfList(); };
      if (e.key === "ArrowDown") move(uf.active + 1);
      else if (e.key === "ArrowUp") move(uf.active - 1);
      else if (e.key === "Enter") { e.preventDefault(); if (uf.items[uf.active]) chooseUf(uf.items[uf.active].value); }
      else if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); closeUf(true); }
      else if (e.key === "Tab") closeUf(false);
    });
  }

  document.addEventListener("pointerdown", e => {
    if (uf.open && !e.target.closest("#s-uf-box")) closeUf(false);
  });

  function setSearchKind(kind) {
    state.searchKind = kind;
    const form = $("#search-form");
    if (!form) return;
    form.elements.kind.value = kind;
    document.querySelectorAll("[data-skind]").forEach(b => b.setAttribute("aria-pressed", String(b.dataset.skind === kind)));
    form.querySelectorAll("[data-only]").forEach(el => { el.hidden = el.dataset.only !== kind; });
    $("#s-names").classList.toggle("single", kind !== "pessoa");
    $("#s-kind-hint").textContent = KIND_HINT[kind];
    (kind === "pessoa" ? $("#s-nome") : $("#s-surname")).focus();
  }

  function setSearchMode(modo) {
    const hint = $("#s-modo-hint");
    if (hint) hint.textContent = MODE_HINT[modo];
  }

  async function openSearch() {
    if (state.search && state.search.state === "running") return renderSearchProgress(true);
    const kind = state.searchKind || "familia";
    if (!state.locais) {
      try { state.locais = await api("/api/locais"); }
      catch (e) {
        state.locais = { locais: [{ value: "SC", label: "Santa Catarina", sigla: "SC" }], padrao: "SC", fonte: "padrao" };
      }
    }
    uf.open = false;
    openDrawer("search", "Hemeroteca Digital", "Nova busca", `
      <div class="seg seg-full" role="group" aria-label="Tipo de busca">
        <button type="button" data-skind="familia" aria-pressed="${kind === "familia"}">Família</button>
        <button type="button" data-skind="pessoa" aria-pressed="${kind === "pessoa"}">Pessoa</button>
      </div>
      <p class="hint-text kind-hint" id="s-kind-hint">${KIND_HINT[kind]}</p>
      <form class="form" id="search-form" novalidate>
        <input type="hidden" name="kind" value="${kind}">
        <div class="row${kind === "pessoa" ? "" : " single"}" id="s-names">
          <div class="field" data-only="pessoa"${kind === "pessoa" ? "" : " hidden"}>
            <label for="s-nome">Nome</label>
            <input class="input" id="s-nome" name="nome" autocomplete="off" placeholder="${PLACEHOLDER.nome}">
          </div>
          <div class="field">
            <label for="s-surname">Sobrenome</label>
            <input class="input" id="s-surname" name="surname" required autocomplete="off" placeholder="${PLACEHOLDER.sobrenome}">
          </div>
        </div>
        <div class="field" data-only="pessoa"${kind === "pessoa" ? "" : " hidden"}>
          <span class="field-label">Como procurar o nome</span>
          <div class="seg seg-full seg-radio" role="radiogroup" aria-label="Como procurar o nome">
            <label><input type="radio" name="modo" value="inteligente" checked>Busca inteligente</label>
            <label><input type="radio" name="modo" value="literal">Literal</label>
          </div>
          <span class="hint" id="s-modo-hint">${MODE_HINT.inteligente}</span>
        </div>
        ${ufSelectHTML(state.locais, state.lastUf || state.locais.padrao)}
        <div class="row">
          <div class="field"><label for="s-ini">De</label><input class="input" id="s-ini" name="ano_inicio" inputmode="numeric" placeholder="1850"></div>
          <div class="field"><label for="s-fim">Até</label><input class="input" id="s-fim" name="ano_fim" inputmode="numeric" placeholder="1950"></div>
        </div>
        <span class="hint" style="font-size:12px;color:var(--muted)">Deixe o período em branco para todos os anos.</span>
        <p class="form-error" id="s-error" hidden></p>
        <button class="btn btn-primary" type="submit">${icon("search")}Buscar</button>
        <div class="callout">O Chrome vai abrir para pesquisar no site da Biblioteca Nacional. Se aparecer uma verificação (CAPTCHA), resolva na janela dele; a busca continua sozinha.</div>
      </form>`);
    wireUfSelect();
    setTimeout(() => (kind === "pessoa" ? $("#s-nome") : $("#s-surname"))?.focus(), 250);
  }

  async function submitSearch(form) {
    const fd = Object.fromEntries(new FormData(form));
    const err = $("#s-error");
    if (fd.kind === "pessoa" && !(fd.nome || "").trim()) { err.textContent = "Informe o nome da pessoa."; err.hidden = false; return; }
    if (!fd.surname.trim()) { err.textContent = "Informe um sobrenome."; err.hidden = false; return; }
    if (fd.kind !== "pessoa") { delete fd.nome; delete fd.modo; }
    state.lastUf = fd.uf;
    try {
      state.search = await api("/api/search", { method: "POST", body: JSON.stringify(fd) });
      renderSearchProgress(true);
      startPolling();
    } catch (e) {
      err.textContent = e.message;
      err.hidden = false;
    }
  }

  function updateIndicator() {
    const s = state.search;
    const ind = $("#search-indicator");
    ind.hidden = !s || s.state === "idle";
    if (ind.hidden) return;
    $("#search-indicator-text").textContent = s.state === "running"
      ? `${cap(s.who || s.surname)} · ${Math.round(s.fraction * 100)}%`
      : `${cap(s.who || s.surname)} · ${{ done: "pronto", cancelled: "cancelada" }[s.state] || "falhou"}`;
  }

  /* `force`: abre o painel de progresso (clique do usuário). Sem `force`, só atualiza o
     painel se ele já estiver aberto — nunca rouba o lugar de outra coisa. */
  function renderSearchProgress(force) {
    const s = state.search;
    updateIndicator();
    if (!s || s.state === "idle") return;
    const open = $("#drawer").classList.contains("is-open");
    const showing = open && state.drawerKind === "search-progress";
    if (!showing && !force) return;
    const status = {
      running: `<button class="btn btn-ghost btn-sm" data-action="cancel-search">${icon("x")}Cancelar busca</button>`,
      done: `<div class="callout ok">Busca concluída.</div><p></p><button class="btn btn-primary" data-action="open-result">Abrir ${esc(cap(s.who || s.surname))}</button>`,
      cancelled: `<div class="callout">Busca cancelada. Os PDFs já baixados ficam guardados; ao buscar de novo, a coleta continua de onde parou.</div><p></p><button class="btn btn-ghost" data-action="new-search-form">Nova busca</button>`,
    }[s.state] || `<div class="callout err">A busca parou com erro. O log completo está em <code>${esc(s.log_path)}</code>.</div><p></p><button class="btn btn-ghost" data-action="new-search-form">Tentar de novo</button>`;
    const html = `
      <p class="progress-text">${esc(s.label)}</p>
      <div class="progress"><span style="transform:scaleX(${s.fraction})"></span></div>
      <p class="progress-eta">${s.eta ? `Cerca de ${esc(s.eta)} restantes nesta etapa` : s.state === "running" ? "Calculando o tempo restante" : ""}</p>
      ${status}
      <pre class="log">${esc(s.log.join("\n")) || "Aguardando o pipeline…"}</pre>`;
    if (showing) $("#drawer-body").innerHTML = html;
    else openDrawer("search-progress", s.kind === "pessoa" ? "Busca de pessoa" : (s.state === "running" ? "Buscando" : "Busca"), cap(s.who || s.surname), html);
  }

  function startPolling() {
    clearInterval(state.poll);
    state.poll = setInterval(async () => {
      try { state.search = await api("/api/search"); } catch (e) { return; }
      renderSearchProgress(false);
      if (state.search.state !== "running") {
        clearInterval(state.poll);
        if (state.search.state === "done") toast(`Busca de ${cap(state.search.who || state.search.surname)} concluída`);
      }
    }, 1200);
  }

  // ---------------------------------------------------------------- visões
  function setView(view) {
    state.view = view;
    document.querySelectorAll(".rail-btn[data-view]").forEach(b => b.classList.toggle("is-active", b.dataset.view === view));
    $("#view-timeline").hidden = view !== "timeline";
    $("#view-tree").hidden = view !== "tree";
    $("#view-people").hidden = view !== "people";
    if (view === "tree" && state.dataset) loadTree().catch(e => toast(e.message));
    if (view === "people") renderPeople();
    writeHash();
  }

  // ---------------------------------------------------------------- eventos
  document.addEventListener("click", ev => {
    const t = ev.target;
    const viewBtn = t.closest("[data-view]");
    if (viewBtn) return setView(viewBtn.dataset.view);
    const act = t.closest("[data-action]");
    if (act) {
      const a = act.dataset.action;
      if (a === "new-search" || a === "new-search-form") { if (a === "new-search-form") state.search = null; return openSearch(); }
      if (a === "show-search") return state.search ? (closeDrawer(), renderSearchProgress(true)) : openSearch();
      if (a === "close-drawer") return closeDrawer();
      if (a === "cancel-search") {
        act.disabled = true;
        return api("/api/search/cancel", { method: "POST" })
          .then(s => { state.search = s; renderSearchProgress(true); clearInterval(state.poll); toast("Busca cancelada"); })
          .catch(e => { act.disabled = false; toast(e.message); });
      }
      if (a === "clear-filters") { state.f = { ...state.f, event: "all", status: "all", periodical: "", q: "", person: null }; $("#q").value = ""; renderFilters(); return renderTimeline(); }
      if (a === "clear-person") { state.f.person = null; state.shown = PAGE; return renderTimeline(); }
      if (a === "person-timeline") {
        state.f = { ...state.f, person: act.dataset.id, event: "all", status: "all", periodical: "", q: "" };
        $("#q").value = "";
        state.shown = PAGE;
        closeDrawer();
        renderFilters();
        renderTimeline();
        setView("timeline");
        return window.scrollTo({ top: $("#view-timeline").offsetTop - 80 });
      }
      if (a === "open-result") {
        const name = state.search.dataset;
        state.search = null;
        $("#search-indicator").hidden = true;
        closeDrawer();
        return loadDatasets(name);
      }
    }
    const ev_ = t.closest("[data-event]");
    if (ev_) { state.f.event = ev_.dataset.event; state.shown = PAGE; renderFilters(); return renderTimeline(); }
    const st = t.closest("[data-status]");
    if (st && st.closest("#f-status")) { state.f.status = st.dataset.status; state.shown = PAGE; renderFilters(); return renderTimeline(); }
    const rv = t.closest("[data-review]");
    if (rv) return reviewOccurrence(rv.closest(".card").dataset.id, rv.dataset.review);
    const rel = t.closest("[data-rel]");
    if (rel) {
      const box = rel.closest(".rel");
      return reviewRelation(box.dataset.occ, Number(box.dataset.idx), rel.dataset.rel);
    }
    const tog = t.closest("[data-toggle]");
    if (tog) return toggleCard(tog.closest(".card"));
    const per = t.closest("[data-person]");
    if (per) return openPerson(per.dataset.person);
    const sk = t.closest("[data-skind]");
    if (sk) return setSearchKind(sk.dataset.skind);
    const ps = t.closest("[data-psort]");
    if (ps) { state.psort = ps.dataset.psort; return renderPeople(); }
    const split = t.closest("[data-split]");
    if (split) {
      const key = split.dataset.split;
      // depois de separar, reabre a pessoa que ficou com as outras grafias
      const keep = state.personById.get(state.drawerPerson)?.variants.find(v => v.key !== key)?.key;
      return adjustPeople({ action: "split", key }, "Grafia separada numa pessoa à parte").then(d => {
        const p = d && keep && personWithKey(keep);
        if (p) openPerson(p.id);
      });
    }
    const merge = t.closest("[data-merge]");
    if (merge) {
      const target = $("#merge-target").value;
      if (!target) return toast("Escolha com quem juntar");
      const a = merge.dataset.merge;
      return adjustPeople({ action: "merge", a, b: target }, "Pessoas juntadas")
        .then(d => d && openPerson(personWithKey(a).id));
    }
    const reset = t.closest("[data-reset-person]");
    if (reset) {
      const p = state.personById.get(reset.dataset.resetPerson);
      if (!p) return;
      const keys = p.variants.map(v => v.key);
      return (async () => {
        for (const key of keys) await adjustPeople({ action: "reset", key }, "Ajustes desfeitos");
        const back = personWithKey(keys[0]);
        if (back) openPerson(back.id);
      })();
    }
    const mini = t.closest("[data-mini-wide]");
    if (mini) {
      const art = mini.closest(".mini");
      const o = state.occ.find(x => x.id === art.dataset.id);
      mini.hidden = true;
      return showWide($(".excerpt", art), o);
    }
    const z = t.closest("[data-zoom]");
    if (z && state.treeCtl) return z.dataset.zoom === "fit" ? state.treeCtl.fit(true) : state.treeCtl.zoom(Number(z.dataset.zoom));
  });

  $("#tree-flags").addEventListener("click", openFlags);
  $("#scrim").addEventListener("click", closeDrawer);
  $("#more").addEventListener("click", () => { state.shown += PAGE; renderTimeline(); });
  $("#f-sort").addEventListener("click", () => { state.f.desc = !state.f.desc; renderFilters(); renderTimeline(); });
  $("#f-periodical").addEventListener("change", e => { state.f.periodical = e.target.value; state.shown = PAGE; renderTimeline(); });
  document.addEventListener("change", e => {
    if (e.target.name === "modo" && e.target.closest("#search-form")) setSearchMode(e.target.value);
  });
  document.addEventListener("submit", e => {
    if (e.target.id === "search-form") { e.preventDefault(); submitSearch(e.target); }
  });

  let qTimer;
  $("#q").addEventListener("input", e => {
    clearTimeout(qTimer);
    qTimer = setTimeout(() => {
      state.f.q = e.target.value.trim();
      state.shown = PAGE;
      if (state.view === "tree" && state.f.q) setView("timeline");
      if (state.dataset) { renderTimeline(); renderPeople(); }
    }, 160);
  });

  document.addEventListener("keydown", e => {
    const typing = /^(INPUT|SELECT|TEXTAREA)$/.test(document.activeElement.tagName);
    if (e.key === "/" && !typing) { e.preventDefault(); $("#q").focus(); }
    if (e.key === "Escape") {
      if ($("#drawer").classList.contains("is-open")) closeDrawer();
      else if (document.activeElement === $("#q")) $("#q").blur();
    }
  });

  // ---------------------------------------------------------------- início
  (async function init() {
    const h = readHash();
    if (h.view) state.view = h.view;
    try {
      await loadDatasets();
      setView(state.view);
      state.search = await api("/api/search");
      updateIndicator();
      if (state.search.state === "running") startPolling();
    } catch (e) {
      toast(`Erro ao carregar: ${e.message}`);
    }
  })();
})();
