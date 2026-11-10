/* Árvore de parentesco: layout por gerações + conectores ortogonais + arrastar/zoom.
 *
 * Entrada (de /api/datasets/<x>/tree): nodes [{id, label, bears_surname, flagged, mentions}],
 * edges [{source, target, relation, status, flagged}], com filho_de = source (filho) -> target
 * (pai/mãe) e casou_com / viuvo_de = cônjuges.
 *
 * Cada grupo de pessoas ligadas entre si vira uma árvore própria. Dentro dela:
 * 1. geração de cada pessoa (pai/mãe = filho - 1; cônjuge = mesma geração);
 * 2. cônjuges lado a lado formam uma "unidade";
 * 3. posição horizontal por mínimos quadrados sem sobreposição (pool adjacent
 *    violators), alternando passadas de cima para baixo (filhos sob os pais) e de
 *    baixo para cima (pais sobre os filhos).
 */
(function () {
  "use strict";

  const W = 140, H = 152;            // cartão (mesmo tamanho de .node no CSS)
  const GAP_SPOUSE = 28, GAP_UNIT = 36, ROW_GAP = 88;
  const COMP_GAP_X = 96, COMP_GAP_Y = 112, LABEL_H = 34, PAD = 56;
  const SKIP = new Set(["sr", "sra", "sr3", "srta", "d", "dr", "dra", "dna", "de", "da", "do", "das", "dos", "e",
                        "viuva", "viuvo", "exmo", "exma", "snr", "sra."]);

  function initials(label) {
    const words = label.normalize("NFKD").replace(/[̀-ͯ]/g, "")
      .replace(/[^\p{L}\s]/gu, " ").split(/\s+/).filter(w => w && !SKIP.has(w.toLowerCase()));
    if (!words.length) return "?";
    const first = words[0][0], last = words.length > 1 ? words[words.length - 1][0] : "";
    return (first + last).toUpperCase();
  }

  // ---------------------------------------------------------------- grafo
  function index(data) {
    const nodes = new Map(data.nodes.map(n => [n.id, n]));
    const parents = new Map(), children = new Map(), spouses = new Map(), kin = new Map();
    const add = (m, k, v) => { if (!m.has(k)) m.set(k, new Set()); m.get(k).add(v); };
    const pairKey = (a, b) => a < b ? a + "\u0000" + b : b + "\u0000" + a;
    for (const e of data.edges) {
      if (!nodes.has(e.source) || !nodes.has(e.target)) continue;
      if (e.relation === "filho_de") { add(parents, e.source, e.target); add(children, e.target, e.source); }
      else { add(spouses, e.source, e.target); add(spouses, e.target, e.source); }
      const k = (e.relation === "filho_de" ? "P" : "S") + pairKey(e.source, e.target);
      const agg = kin.get(k) || { confirmed: true, flagged: false };
      agg.confirmed = agg.confirmed && e.status === "confirmada";
      agg.flagged = agg.flagged || !!e.flagged;
      kin.set(k, agg);
    }
    const get = (m, k) => m.get(k) || new Set();
    return {
      nodes, parents: k => get(parents, k), children: k => get(children, k), spouses: k => get(spouses, k),
      parentLink: (c, p) => kin.get("P" + pairKey(c, p)) || { confirmed: false, flagged: false },
      spouseLink: (a, b) => kin.get("S" + pairKey(a, b)) || { confirmed: false, flagged: false },
    };
  }

  function components(g) {
    const seen = new Set(), comps = [];
    for (const start of g.nodes.keys()) {
      if (seen.has(start)) continue;
      const comp = [], stack = [start];
      seen.add(start);
      while (stack.length) {
        const n = stack.pop();
        comp.push(n);
        for (const m of [...g.parents(n), ...g.children(n), ...g.spouses(n)]) {
          if (!seen.has(m)) { seen.add(m); stack.push(m); }
        }
      }
      comps.push(comp);
    }
    return comps;
  }

  // ---------------------------------------------------------------- layout de um grupo
  function generations(g, comp) {
    // começa por quem não tem pais (topo), percorrendo em largura
    const start = comp.find(n => g.parents(n).size === 0 && g.children(n).size > 0) || comp[0];
    const gen = new Map([[start, 0]]), queue = [start], order = [start];
    while (queue.length) {
      const n = queue.shift(), k = gen.get(n);
      const visit = (m, v) => { if (!gen.has(m)) { gen.set(m, v); queue.push(m); order.push(m); } };
      g.spouses(n).forEach(m => visit(m, k));
      g.children(n).forEach(m => visit(m, k + 1));
      g.parents(n).forEach(m => visit(m, k - 1));
    }
    const min = Math.min(...gen.values());
    for (const [n, v] of gen) gen.set(n, v - min);
    return { gen, order };
  }

  function buildUnits(g, nodesInGen, gen) {
    // cônjuges da mesma geração ficam juntos, em cadeia (A - B - C para recasamentos)
    const seen = new Set(), units = [];
    for (const n of nodesInGen) {
      if (seen.has(n)) continue;
      const group = [], stack = [n];
      seen.add(n);
      while (stack.length) {
        const m = stack.pop();
        group.push(m);
        for (const s of g.spouses(m)) if (!seen.has(s) && gen.get(s) === gen.get(n)) { seen.add(s); stack.push(s); }
      }
      // cadeia: começa por uma ponta (quem tem menos cônjuges no grupo)
      const inGroup = new Set(group);
      const deg = m => [...g.spouses(m)].filter(s => inGroup.has(s)).length;
      let cur = group.slice().sort((a, b) => deg(a) - deg(b))[0];
      const chain = [], used = new Set();
      while (cur && !used.has(cur)) {
        chain.push(cur); used.add(cur);
        cur = [...g.spouses(cur)].find(s => inGroup.has(s) && !used.has(s));
      }
      group.forEach(m => { if (!used.has(m)) chain.push(m); });
      units.push({ members: chain, w: chain.length * W + (chain.length - 1) * GAP_SPOUSE, x: 0 });
    }
    return units;
  }

  // posições por mínimos quadrados respeitando a ordem e o espaçamento
  function pack(units, ideals) {
    const blocks = [];
    units.forEach((u, i) => {
      let b = { items: [i], offsets: [0], width: u.w, sum: ideals[i], n: 1 };
      b.x = b.sum / b.n;
      while (blocks.length) {
        const prev = blocks[blocks.length - 1];
        if (prev.x + prev.width + GAP_UNIT <= b.x) break;
        blocks.pop();
        const shift = prev.width + GAP_UNIT;
        b = {
          items: prev.items.concat(b.items),
          offsets: prev.offsets.concat(b.offsets.map(o => o + shift)),
          width: shift + b.width,
          sum: prev.sum + (b.sum - shift * b.n),
          n: prev.n + b.n,
        };
        b.x = b.sum / b.n;
      }
      blocks.push(b);
    });
    for (const b of blocks) b.items.forEach((i, k) => { units[i].x = b.x + b.offsets[k]; });
  }

  function layoutComponent(g, comp) {
    const { gen, order } = generations(g, comp);
    const maxGen = Math.max(...gen.values());
    const rows = [];
    for (let k = 0; k <= maxGen; k++) rows.push(buildUnits(g, order.filter(n => gen.get(n) === k), gen));

    const pos = new Map();             // id -> x (canto esquerdo)
    const unitOf = new Map();
    const sync = row => row.forEach(u => u.members.forEach((m, j) => { pos.set(m, u.x + j * (W + GAP_SPOUSE)); unitOf.set(m, u); }));
    const center = m => pos.get(m) + W / 2;
    const mean = a => a.reduce((s, v) => s + v, 0) / a.length;

    // ideal de uma unidade a partir dos vizinhos (pais acima ou filhos abaixo)
    function idealFrom(u, related) {
      const wants = [];
      u.members.forEach((m, j) => {
        const r = [...related(m)].filter(x => pos.has(x));
        if (r.length) wants.push(mean(r.map(center)) - (j * (W + GAP_SPOUSE) + W / 2));
      });
      return wants.length ? mean(wants) : null;
    }

    function pass(k, related, reorder) {
      const row = rows[k];
      let last = null;
      const ideals = row.map(u => {
        const v = idealFrom(u, related);
        last = v !== null ? v : (last !== null ? last + 1 : u.x);
        return last;
      });
      if (reorder) {
        const idx = row.map((u, i) => i).sort((a, b) => ideals[a] - ideals[b]);
        const sortedUnits = idx.map(i => row[i]), sortedIdeals = idx.map(i => ideals[i]);
        rows[k] = sortedUnits;
        pack(sortedUnits, sortedIdeals);
      } else {
        pack(row, ideals);
      }
      sync(rows[k]);
    }

    // primeira geração: na ordem de descoberta, compactada
    let acc = 0;
    rows[0].forEach(u => { u.x = acc; acc += u.w + GAP_UNIT; });
    sync(rows[0]);
    const parentsInRow = (k) => m => [...g.parents(m)].filter(p => gen.get(p) === k - 1);
    const childrenInRow = (k) => m => [...g.children(m)].filter(c => gen.get(c) === k + 1);
    for (let k = 1; k <= maxGen; k++) { rows[k].forEach(u => { u.x = 0; }); pass(k, parentsInRow(k), true); }
    for (let it = 0; it < 2; it++) {
      for (let k = maxGen - 1; k >= 0; k--) pass(k, childrenInRow(k), false);
      for (let k = 1; k <= maxGen; k++) pass(k, parentsInRow(k), false);
    }

    const minX = Math.min(...[...pos.values()]);
    const maxX = Math.max(...[...pos.values()]) + W;
    for (const [n, x] of pos) pos.set(n, x - minX);
    return {
      ids: comp, gen, pos, unitOf,
      width: maxX - minX, height: (maxGen + 1) * H + maxGen * ROW_GAP,
      y: n => gen.get(n) * (H + ROW_GAP),
    };
  }

  // ---------------------------------------------------------------- conectores
  function connectors(g, L, ox, oy) {
    const paths = [];
    const X = n => ox + L.pos.get(n), Y = n => oy + L.y(n);
    const cls = (links) => {
      const c = [];
      if (links.some(l => !l.confirmed)) c.push("dashed");
      if (links.some(l => l.flagged)) c.push("flag");
      return c.join(" ");
    };
    const adjacent = (a, b) => L.unitOf.get(a) === L.unitOf.get(b) &&
      Math.abs(L.pos.get(a) - L.pos.get(b)) === W + GAP_SPOUSE;

    // cônjuges
    const done = new Set();
    for (const a of L.ids) for (const b of g.spouses(a)) {
      const key = a < b ? a + "|" + b : b + "|" + a;
      if (done.has(key) || !L.pos.has(b)) continue;
      done.add(key);
      const [l, r] = X(a) < X(b) ? [a, b] : [b, a];
      const c = cls([g.spouseLink(a, b)]);
      if (adjacent(a, b) && Y(a) === Y(b)) {
        paths.push({ d: `M${X(l) + W} ${Y(l) + H / 2}H${X(r)}`, c });
      } else {
        const top = Math.min(Y(l), Y(r)) - 18;
        paths.push({ d: `M${X(l) + W / 2} ${Y(l)}V${top}H${X(r) + W / 2}V${Y(r)}`, c });
      }
    }

    // filiação: agrupa os filhos pelo mesmo conjunto de pais
    const groups = new Map();
    for (const c of L.ids) {
      const ps = [...g.parents(c)].filter(p => L.pos.has(p));
      if (!ps.length) continue;
      const proper = ps.filter(p => L.gen.get(p) === L.gen.get(c) - 1);
      for (const p of ps.filter(p => !proper.includes(p))) {   // geração inconsistente (ciclo): ligação direta
        const mid = (Y(p) + H + Y(c)) / 2;
        paths.push({ d: `M${X(p) + W / 2} ${Y(p) + H}V${mid}H${X(c) + W / 2}V${Y(c)}`, c: cls([g.parentLink(c, p)]) + " flag" });
      }
      if (!proper.length) continue;
      const key = proper.sort().join("|");
      if (!groups.has(key)) groups.set(key, { parents: proper, kids: [] });
      groups.get(key).kids.push(c);
    }
    const lanes = new Map();
    for (const { parents, kids } of groups.values()) {
      const k = L.gen.get(kids[0]);
      const lane = lanes.get(k) || 0;
      lanes.set(k, lane + 1);
      const busY = Y(kids[0]) - ROW_GAP / 2 + (lane % 3) * 7 - 7;
      const links = kids.flatMap(c => parents.map(p => g.parentLink(c, p)));
      const drops = [];
      if (parents.length === 2 && adjacent(parents[0], parents[1])) {
        const [l, r] = X(parents[0]) < X(parents[1]) ? parents : [parents[1], parents[0]];
        const x = (X(l) + W + X(r)) / 2;
        drops.push(x);
        paths.push({ d: `M${x} ${Y(l) + H / 2}V${busY}`, c: cls(links) });
      } else {
        for (const p of parents) {
          const x = X(p) + W / 2;
          drops.push(x);
          paths.push({ d: `M${x} ${Y(p) + H}V${busY}`, c: cls(kids.map(c => g.parentLink(c, p))) });
        }
      }
      const xs = drops.concat(kids.map(c => X(c) + W / 2));
      paths.push({ d: `M${Math.min(...xs)} ${busY}H${Math.max(...xs)}`, c: cls(links) });
      for (const c of kids) {
        paths.push({ d: `M${X(c) + W / 2} ${busY}V${Y(c)}`, c: cls(parents.map(p => g.parentLink(c, p))) });
      }
    }
    return paths;
  }

  // ---------------------------------------------------------------- render
  function render(stage, data, onSelect) {
    const g = index(data);
    const comps = components(g).map(c => layoutComponent(g, c))
      .sort((a, b) => b.ids.length - a.ids.length || b.width - a.width);

    // grupos em linhas, como num mural
    const maxRow = Math.max(1180, ...comps.map(c => c.width));
    let x = 0, y = 0, rowH = 0;
    const placed = comps.map(L => {
      if (x > 0 && x + L.width > maxRow) { x = 0; y += rowH + COMP_GAP_Y; rowH = 0; }
      const at = { L, ox: PAD + x, oy: PAD + y + LABEL_H };
      x += L.width + COMP_GAP_X;
      rowH = Math.max(rowH, L.height + LABEL_H);
      return at;
    });
    const width = PAD * 2 + Math.max(0, ...placed.map(p => p.ox - PAD + p.L.width));
    const height = PAD * 2 + Math.max(0, ...placed.map(p => p.oy - PAD + p.L.height));

    stage.innerHTML = "";
    stage.style.width = width + "px";
    stage.style.height = height + "px";
    const svgNS = "http://www.w3.org/2000/svg";
    const svg = document.createElementNS(svgNS, "svg");
    svg.setAttribute("width", width);
    svg.setAttribute("height", height);
    stage.appendChild(svg);

    const frag = document.createDocumentFragment();
    placed.forEach(({ L, ox, oy }, i) => {
      for (const p of connectors(g, L, ox, oy)) {
        const el = document.createElementNS(svgNS, "path");
        el.setAttribute("d", p.d);
        if (p.c) el.setAttribute("class", p.c);
        svg.appendChild(el);
      }
      const label = document.createElement("div");
      label.className = "family-label";
      label.style.transform = `translate(${ox}px, ${oy - LABEL_H}px)`;
      const n = L.ids.length, gens = Math.max(...L.gen.values()) + 1;
      label.textContent = `Grupo ${i + 1} · ${n} ${n === 1 ? "pessoa" : "pessoas"} · ${gens} ${gens === 1 ? "geração" : "gerações"}`;
      frag.appendChild(label);

      for (const id of L.ids) {
        const node = g.nodes.get(id);
        const el = document.createElement("button");
        el.type = "button";
        el.className = "node" + (node.bears_surname ? " bears" : "") + (node.flagged ? " is-flagged" : "");
        el.dataset.id = id;
        el.style.left = ox + L.pos.get(id) + "px";
        el.style.top = oy + L.y(id) + "px";
        el.innerHTML = (node.flagged ? '<span class="node-badge">Conflito</span>' : "") +
          `<span class="avatar">${initials(node.label)}</span>` +
          `<span class="node-name"></span>` +
          `<span class="node-meta">${node.mentions} ${node.mentions === 1 ? "menção" : "menções"}</span>`;
        el.querySelector(".node-name").textContent = node.label;
        el.title = node.label;
        frag.appendChild(el);
      }
    });
    stage.appendChild(frag);
    stage.onclick = ev => {
      const el = ev.target.closest(".node");
      if (el && !stage.dataset.dragged) onSelect(el.dataset.id);
    };
    return { width, height, groups: comps.length };
  }

  // ---------------------------------------------------------------- arrastar e zoom
  function viewport(vp, stage, onZoom) {
    const s = { k: 1, x: 0, y: 0 };
    const apply = () => { stage.style.transform = `translate(${s.x}px, ${s.y}px) scale(${s.k})`; onZoom(s.k); };
    const zoomAt = (factor, cx, cy) => {
      const k = Math.min(2, Math.max(0.25, s.k * factor));
      s.x = cx - (cx - s.x) * (k / s.k);
      s.y = cy - (cy - s.y) * (k / s.k);
      s.k = k;
      apply();
    };
    let drag = null;
    vp.addEventListener("pointerdown", e => {
      if (e.button !== 0) return;
      drag = { x: e.clientX, y: e.clientY, sx: s.x, sy: s.y, moved: false, id: e.pointerId };
      delete stage.dataset.dragged;
    });
    vp.addEventListener("pointermove", e => {
      if (!drag) return;
      const dx = e.clientX - drag.x, dy = e.clientY - drag.y;
      if (!drag.moved && Math.hypot(dx, dy) < 4) return;
      if (!drag.moved) { drag.moved = true; vp.setPointerCapture(drag.id); vp.classList.add("is-dragging"); }
      s.x = drag.sx + dx; s.y = drag.sy + dy;
      apply();
    });
    const end = () => {
      if (drag && drag.moved) { stage.dataset.dragged = "1"; setTimeout(() => delete stage.dataset.dragged, 0); }
      drag = null;
      vp.classList.remove("is-dragging");
    };
    vp.addEventListener("pointerup", end);
    vp.addEventListener("pointercancel", end);
    vp.addEventListener("wheel", e => {
      e.preventDefault();
      const r = vp.getBoundingClientRect();
      if (e.ctrlKey || e.metaKey) zoomAt(Math.exp(-e.deltaY * 0.0015), e.clientX - r.left, e.clientY - r.top);
      else { s.x -= e.deltaX; s.y -= e.deltaY; apply(); }
    }, { passive: false });

    return {
      zoom(step) { const r = vp.getBoundingClientRect(); zoomAt(step > 0 ? 1.2 : 1 / 1.2, r.width / 2, r.height / 2); },
      // cabe tudo se der para ler; senão ajusta pela largura (mínimo 80%) e o resto é arrastar
      fit(all) {
        const r = vp.getBoundingClientRect();
        const w = stage.offsetWidth, h = stage.offsetHeight;
        if (!w || !h) return;
        const whole = Math.min(1, r.width / w, r.height / h);
        s.k = all ? Math.max(0.25, whole) : Math.max(0.8, Math.min(1, whole >= 0.8 ? whole : r.width / w));
        s.x = w * s.k <= r.width ? (r.width - w * s.k) / 2 : 0;
        s.y = h * s.k <= r.height ? (r.height - h * s.k) / 2 : 0;
        apply();
      },
      focus(id) {
        const el = stage.querySelector(`.node[data-id="${CSS.escape(id)}"]`);
        if (!el) return;
        const r = vp.getBoundingClientRect();
        s.x = r.width / 2 - (el.offsetLeft + W / 2) * s.k;
        s.y = r.height / 2 - (el.offsetTop + H / 2) * s.k;
        apply();
      },
    };
  }

  window.FamilyTree = { render, viewport, initials };
})();
