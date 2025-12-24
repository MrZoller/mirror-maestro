// Topology graph (instances + aggregated mirror flows)
// Depends on: apiRequest(), showMessage(), state (from app.js), and d3 (loaded via CDN).

(function () {
  const topology = {
    initialized: false,
    raw: null, // {nodes, links}
    svg: null,
    rootG: null,
    simulation: null,
    resizeObserver: null,
    lastRenderKey: "",
    selected: null, // {type: 'node'|'link', data}
  };

  function byId(id) {
    return document.getElementById(id);
  }

  function esc(s) {
    return (s ?? "").toString().replace(/[&<>"']/g, (m) => {
      const map = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;" };
      return map[m] || m;
    });
  }

  function getFilters() {
    const showPull = !!byId("topology-show-pull")?.checked;
    const showPush = !!byId("topology-show-push")?.checked;
    const includeDisabled = !!byId("topology-show-disabled")?.checked;
    return { showPull, showPush, includeDisabled };
  }

  function setPanel(title, subtitle, bodyHtml) {
    const t = byId("topology-panel-title");
    const st = byId("topology-panel-subtitle");
    const b = byId("topology-panel-body");
    if (t) t.textContent = title;
    if (st) st.textContent = subtitle;
    if (b) b.innerHTML = bodyHtml;
  }

  function ensureD3() {
    if (typeof window.d3 === "undefined") {
      throw new Error("D3 failed to load (check network / CSP).");
    }
  }

  function canvasSize() {
    const el = byId("topology-canvas");
    if (!el) return { width: 900, height: 520 };
    const r = el.getBoundingClientRect();
    return { width: Math.max(500, Math.floor(r.width)), height: Math.max(420, Math.floor(r.height)) };
  }

  async function fetchTopology() {
    const pairSel = byId("topology-pair-filter");
    const pairId = (pairSel?.value || "").toString().trim();
    const warnHours = parseInt((byId("topology-stale-warn-hours")?.value || "24").toString(), 10);
    const errHours = parseInt((byId("topology-stale-error-hours")?.value || "168").toString(), 10);
    const warnS = Number.isFinite(warnHours) ? Math.max(0, warnHours) * 3600 : 24 * 3600;
    const errS = Number.isFinite(errHours) ? Math.max(0, errHours) * 3600 : 168 * 3600;
    const neverAsError = !!byId("topology-never-succeeded-error")?.checked;

    const params = new URLSearchParams();
    if (pairId) params.set("instance_pair_id", pairId);
    params.set("stale_warning_seconds", String(warnS));
    params.set("stale_error_seconds", String(errS));
    params.set("never_succeeded_level", neverAsError ? "error" : "warning");

    const qs = params.toString() ? `?${params.toString()}` : "";
    topology.raw = await apiRequest(`/api/topology${qs}`);
  }

  function statusBadgeHtml(status) {
    const s = (status || "unknown").toString().toLowerCase();
    const cls =
      s === "failed"
        ? "badge-danger"
        : s === "finished"
          ? "badge-success"
          : s === "updating"
            ? "badge-warning"
            : s === "pending"
              ? "badge-warning"
              : "badge-info";
    return `<span class="badge ${cls}">${esc(s)}</span>`;
  }

  function healthPillHtml(health, staleness, neverSucceeded) {
    const h = (health || "unknown").toString().toLowerCase();
    const dot = healthDotClass(h);
    const st = (staleness || "unknown").toString().toLowerCase();
    const never = !!neverSucceeded;
    const extra = never ? `<span class="pill" style="margin-left:8px">never</span>` : st && st !== "ok" ? `<span class="pill" style="margin-left:8px">${esc(st)}</span>` : "";
    return `<span class="pill"><span class="dot ${dot}"></span><strong>${esc(h)}</strong></span>${extra}`;
  }

  function buildLinkMirrorsQuery({ sourceId, targetId, direction }) {
    const pairId = (byId("topology-pair-filter")?.value || "").toString().trim();
    const includeDisabled = !!byId("topology-show-disabled")?.checked;
    const warnHours = parseInt((byId("topology-stale-warn-hours")?.value || "24").toString(), 10);
    const errHours = parseInt((byId("topology-stale-error-hours")?.value || "168").toString(), 10);
    const warnS = Number.isFinite(warnHours) ? Math.max(0, warnHours) * 3600 : 24 * 3600;
    const errS = Number.isFinite(errHours) ? Math.max(0, errHours) * 3600 : 168 * 3600;
    const neverAsError = !!byId("topology-never-succeeded-error")?.checked;

    const params = new URLSearchParams();
    params.set("source_instance_id", String(sourceId));
    params.set("target_instance_id", String(targetId));
    params.set("mirror_direction", String(direction));
    if (pairId) params.set("instance_pair_id", pairId);
    params.set("include_disabled", includeDisabled ? "true" : "false");
    params.set("stale_warning_seconds", String(warnS));
    params.set("stale_error_seconds", String(errS));
    params.set("never_succeeded_level", neverAsError ? "error" : "warning");
    params.set("limit", "200");
    params.set("offset", "0");
    return params.toString();
  }

  window.openMirrorsForPair = async function openMirrorsForPair(pairId) {
    try {
      const tabBtn = document.querySelector(`.tab[data-tab="mirrors-tab"]`);
      if (tabBtn) tabBtn.click();

      if (typeof window.loadPairs === "function") {
        await window.loadPairs();
      }
      const sel = document.getElementById("pair-selector");
      if (sel) {
        sel.value = String(pairId);
        sel.dispatchEvent(new Event("change", { bubbles: true }));
        sel.scrollIntoView({ behavior: "smooth", block: "center" });
      }
    } catch (e) {
      showMessage(`Failed to open Mirrors tab: ${e?.message || e}`, "error");
    }
  };

  function filteredGraph() {
    const raw = topology.raw || { nodes: [], links: [] };
    const f = getFilters();

    const links = (raw.links || [])
      .map((l) => {
        const dir = (l.mirror_direction || "").toString().toLowerCase();
        const shownCount = f.includeDisabled ? l.mirror_count : l.enabled_count;
        return { ...l, mirror_direction: dir, shown_count: shownCount };
      })
      .filter((l) => {
        if (l.shown_count <= 0) return false;
        if (l.mirror_direction === "pull" && !f.showPull) return false;
        if (l.mirror_direction === "push" && !f.showPush) return false;
        return true;
      });

    // Keep nodes that exist + have at least one visible edge.
    const used = new Set();
    links.forEach((l) => {
      used.add(l.source);
      used.add(l.target);
    });
    const nodes = (raw.nodes || []).filter((n) => used.has(n.id) || (raw.nodes || []).length <= 1);

    return { nodes, links };
  }

  function linkColor(dir) {
    return dir === "push" ? "rgba(252, 109, 38, 0.95)" : "rgba(31, 117, 203, 0.92)";
  }

  function healthSeverity(h) {
    const x = (h || "").toString().toLowerCase();
    if (x === "error") return 3;
    if (x === "warning") return 2;
    if (x === "ok") return 1;
    return 0;
  }

  function healthDotClass(h) {
    const x = (h || "").toString().toLowerCase();
    if (x === "error") return "topology-dot-error";
    if (x === "warning") return "topology-dot-warning";
    if (x === "ok") return "topology-dot-ok";
    return "topology-dot-unknown";
  }

  function healthStrokeColor(h) {
    const x = (h || "").toString().toLowerCase();
    if (x === "error") return "rgba(221, 43, 14, 0.95)";
    if (x === "warning") return "rgba(222, 126, 0, 0.95)";
    if (x === "ok") return "rgba(16, 133, 72, 0.95)";
    return "rgba(107, 114, 128, 0.70)";
  }

  function linkWidth(count) {
    const c = Math.max(1, Number(count || 1));
    // 1..8-ish
    return Math.min(8, 1.2 + Math.log2(c) * 1.1);
  }

  function nodeRadius(n, io) {
    const total = (io.in || 0) + (io.out || 0);
    return Math.max(10, Math.min(22, 10 + Math.sqrt(total)));
  }

  function computeNodeIO(nodes, links) {
    const io = new Map();
    nodes.forEach((n) => io.set(n.id, { in: 0, out: 0 }));
    links.forEach((l) => {
      const src = io.get(l.source) || { in: 0, out: 0 };
      const tgt = io.get(l.target) || { in: 0, out: 0 };
      src.out += l.shown_count;
      tgt.in += l.shown_count;
      io.set(l.source, src);
      io.set(l.target, tgt);
    });
    return io;
  }

  function computeNodeHealth(nodes, links) {
    // Prefer server-provided node health, but fall back to link-derived health for older payloads.
    const out = new Map();
    nodes.forEach((n) => {
      if (n && n.health) {
        out.set(n.id, { health: (n.health || "unknown").toString().toLowerCase(), last: n.last_successful_update || null });
      } else {
        out.set(n.id, { health: "unknown", last: null });
      }
    });

    const sev = (h) => healthSeverity(h);
    links.forEach((l) => {
      const sid = typeof l.source === "object" && l.source ? l.source.id : l.source;
      const tid = typeof l.target === "object" && l.target ? l.target.id : l.target;
      const h = (l.health || "unknown").toString().toLowerCase();
      const last = l.last_successful_update || null;
      [sid, tid].forEach((id) => {
        if (!out.has(id)) out.set(id, { health: "unknown", last: null });
        const cur = out.get(id);
        if (sev(h) > sev(cur.health)) cur.health = h;
        if (last) {
          if (!cur.last || Date.parse(last) > Date.parse(cur.last)) cur.last = last;
        }
        out.set(id, cur);
      });
    });
    return out;
  }

  function render() {
    ensureD3();
    const canvas = byId("topology-canvas");
    const loading = byId("topology-loading");
    if (!canvas) return;
    if (loading) loading.classList.add("hidden");

    const { width, height } = canvasSize();
    const { nodes, links } = filteredGraph();
    const io = computeNodeIO(nodes, links);
    const nodeHealth = computeNodeHealth(nodes, links);

    const renderKey = JSON.stringify({
      w: width,
      h: height,
      nodes: nodes.map((n) => n.id),
      links: links.map((l) => [l.source, l.target, l.mirror_direction, l.shown_count]),
    });

    // Clear & re-render for simplicity (graph sizes are typically small).
    canvas.innerHTML = "";

    const svg = window.d3
      .select(canvas)
      .append("svg")
      .attr("width", width)
      .attr("height", height)
      .attr("viewBox", `0 0 ${width} ${height}`)
      .style("display", "block");

    topology.svg = svg;

    // Markers + filters
    const defs = svg.append("defs");
    const mk = (id, color) => {
      defs
        .append("marker")
        .attr("id", id)
        .attr("viewBox", "0 -5 10 10")
        .attr("refX", 18)
        .attr("refY", 0)
        .attr("markerWidth", 7)
        .attr("markerHeight", 7)
        .attr("orient", "auto")
        .append("path")
        .attr("d", "M0,-5L10,0L0,5")
        .attr("fill", color);
    };
    mk("arrow-pull", linkColor("pull"));
    mk("arrow-push", linkColor("push"));

    // Glow filters for health states (applied as SVG filter).
    const addGlow = (id, color) => {
      const f = defs.append("filter").attr("id", id).attr("x", "-50%").attr("y", "-50%").attr("width", "200%").attr("height", "200%");
      f.append("feDropShadow").attr("dx", 0).attr("dy", 0).attr("stdDeviation", 3.2).attr("flood-color", color).attr("flood-opacity", 0.45);
      f.append("feDropShadow").attr("dx", 0).attr("dy", 0).attr("stdDeviation", 1.8).attr("flood-color", color).attr("flood-opacity", 0.35);
    };
    addGlow("glow-error", "rgba(221, 43, 14, 1)");
    addGlow("glow-warning", "rgba(222, 126, 0, 1)");

    const rootG = svg.append("g");
    topology.rootG = rootG;

    const zoom = window.d3
      .zoom()
      .scaleExtent([0.4, 2.6])
      .on("zoom", (event) => rootG.attr("transform", event.transform));

    svg.call(zoom);

    // Background hint
    rootG
      .append("text")
      .attr("x", 14)
      .attr("y", 22)
      .attr("fill", "rgba(17, 24, 39, 0.35)")
      .attr("font-size", 12)
      .text("drag nodes • scroll to zoom • click for details");

    const linkG = rootG.append("g").attr("stroke-linecap", "round");
    const labelG = rootG.append("g");
    const nodeG = rootG.append("g");

    // D3 wants mutable objects for simulation
    const simNodes = nodes.map((n) => ({ ...n }));
    const simLinks = links.map((l) => ({ ...l }));

    // Tooltip (absolute div overlay)
    const tooltip = document.createElement("div");
    tooltip.className = "topology-tooltip hidden";
    canvas.appendChild(tooltip);

    const setTooltip = (html, x, y) => {
      tooltip.innerHTML = html;
      tooltip.classList.remove("hidden");
      const pad = 12;
      const rect = canvas.getBoundingClientRect();
      // position relative to canvas
      let left = x + pad;
      let top = y + pad;
      // clamp within canvas
      const maxLeft = rect.width - 360;
      const maxTop = rect.height - 140;
      left = Math.max(10, Math.min(left, maxLeft));
      top = Math.max(10, Math.min(top, maxTop));
      tooltip.style.left = `${left}px`;
      tooltip.style.top = `${top}px`;
    };
    const hideTooltip = () => {
      tooltip.classList.add("hidden");
      tooltip.innerHTML = "";
    };

    const linkSel = linkG
      .selectAll("line")
      .data(simLinks, (d) => `${d.source}-${d.target}-${d.mirror_direction}`)
      .join("line")
      .attr("stroke", (d) => linkColor(d.mirror_direction))
      .attr("stroke-width", (d) => linkWidth(d.shown_count))
      .attr("stroke-opacity", 0.75)
      .attr("stroke-dasharray", (d) => (healthSeverity(d.health) === 2 ? "7 6" : null))
      .attr("filter", (d) => (healthSeverity(d.health) === 3 ? "url(#glow-error)" : healthSeverity(d.health) === 2 ? "url(#glow-warning)" : null))
      .attr("marker-end", (d) => `url(#arrow-${d.mirror_direction})`)
      .style("cursor", "pointer")
      .on("mousemove", (event, d) => {
        const srcId = typeof d.source === "object" && d.source ? d.source.id : d.source;
        const tgtId = typeof d.target === "object" && d.target ? d.target.id : d.target;
        const src = nodes.find((n) => n.id === srcId);
        const tgt = nodes.find((n) => n.id === tgtId);
        const counts = d.status_counts || {};
        const last = d.last_successful_update ? new Date(d.last_successful_update).toLocaleString() : "Never";
        const stale = (d.staleness || "").toString().toLowerCase();
        const staleAge = Number.isFinite(d.staleness_age_seconds) ? `${Math.round(d.staleness_age_seconds / 3600)}h` : "n/a";
        const neverCount = Number.isFinite(d.never_succeeded_count) ? d.never_succeeded_count : 0;
        const health = (d.health || "unknown").toString();
        const dot = healthDotClass(health);
        const html = `
          <div class="row" style="align-items:center">
            <div><strong>${esc(src?.name || `#${srcId}`)} → ${esc(tgt?.name || `#${tgtId}`)}</strong></div>
            <div class="pill"><span class="dot ${dot}"></span>${esc(health)}</div>
          </div>
          <div class="muted" style="margin-top:6px">${esc(String(d.shown_count))} ${esc(d.mirror_direction)} mirror(s)</div>
          <div class="muted" style="margin-top:6px">Last success: ${esc(last)}</div>
          <div class="muted" style="margin-top:6px">Staleness: ${esc(stale || "n/a")} (${esc(staleAge)})</div>
          <div class="muted" style="margin-top:6px">Never succeeded: ${esc(String(neverCount))}</div>
          <div class="muted" style="margin-top:8px">Status: ${Object.entries(counts).map(([k,v]) => `${esc(k)}=${esc(String(v))}`).join(", ") || "n/a"}</div>
        `;
        setTooltip(html, event.offsetX, event.offsetY);
      })
      .on("mouseout", () => hideTooltip())
      .on("click", (event, d) => {
        event.stopPropagation();
        topology.selected = { type: "link", data: d };
        showLinkDetails(d, nodes);
        highlightSelection();
      });

    const linkLabelSel = labelG
      .selectAll("text")
      .data(simLinks, (d) => `${d.source}-${d.target}-${d.mirror_direction}`)
      .join("text")
      .attr("fill", "rgba(17, 24, 39, 0.62)")
      .attr("font-size", 11)
      .attr("text-anchor", "middle")
      .attr("pointer-events", "none")
      .text((d) => `${d.shown_count} ${d.mirror_direction}`);

    const nodeSel = nodeG
      .selectAll("g")
      .data(simNodes, (d) => d.id)
      .join((enter) => {
        const g = enter.append("g").style("cursor", "grab");

        g.append("circle")
          .attr("r", (d) => nodeRadius(d, io.get(d.id) || { in: 0, out: 0 }))
          .attr("fill", "rgba(255, 255, 255, 0.92)")
          .attr("stroke", (d) => healthStrokeColor((nodeHealth.get(d.id)?.health || d.health || "unknown")))
          .attr("stroke-width", 2.2)
          .attr("filter", (d) => {
            const h = (nodeHealth.get(d.id)?.health || d.health || "unknown").toString().toLowerCase();
            return healthSeverity(h) === 3 ? "url(#glow-error)" : healthSeverity(h) === 2 ? "url(#glow-warning)" : null;
          });

        g.append("circle")
          .attr("r", (d) => Math.max(4, nodeRadius(d, io.get(d.id) || { in: 0, out: 0 }) - 6))
          .attr("fill", "rgba(17, 24, 39, 0.06)")
          .attr("stroke", "rgba(17, 24, 39, 0.08)")
          .attr("stroke-width", 1);

        g.append("text")
          .attr("text-anchor", "middle")
          .attr("dy", (d) => nodeRadius(d, io.get(d.id) || { in: 0, out: 0 }) + 14)
          .attr("fill", "rgba(17, 24, 39, 0.86)")
          .attr("font-size", 12)
          .attr("font-weight", 600)
          .text((d) => d.name);

        return g;
      })
      .call(
        window.d3
          .drag()
          .on("start", (event, d) => {
            if (!event.active) topology.simulation.alphaTarget(0.3).restart();
            d.fx = d.x;
            d.fy = d.y;
          })
          .on("drag", (event, d) => {
            d.fx = event.x;
            d.fy = event.y;
          })
          .on("end", (event, d) => {
            if (!event.active) topology.simulation.alphaTarget(0);
            d.fx = null;
            d.fy = null;
          })
      )
      .on("click", (event, d) => {
        event.stopPropagation();
        topology.selected = { type: "node", data: d };
        showNodeDetails(d, nodes, links);
        highlightSelection();
      });

    nodeSel
      .on("mousemove", (event, d) => {
        const h = (nodeHealth.get(d.id)?.health || d.health || "unknown").toString().toLowerCase();
        const dot = healthDotClass(h);
        const lastIso = nodeHealth.get(d.id)?.last || d.last_successful_update || null;
        const last = lastIso ? new Date(lastIso).toLocaleString() : "Never";
        const stale = (d.staleness || "").toString().toLowerCase();
        const staleAge = Number.isFinite(d.staleness_age_seconds) ? `${Math.round(d.staleness_age_seconds / 3600)}h` : "n/a";
        const neverCount = Number.isFinite(d.never_succeeded_count) ? d.never_succeeded_count : 0;
        const html = `
          <div class="row" style="align-items:center">
            <div><strong>${esc(d.name)}</strong></div>
            <div class="pill"><span class="dot ${dot}"></span>${esc(h)}</div>
          </div>
          <div class="muted" style="margin-top:6px">${esc(String(d.mirrors_in || 0))} in • ${esc(String(d.mirrors_out || 0))} out</div>
          <div class="muted" style="margin-top:6px">Last success: ${esc(last)}</div>
          <div class="muted" style="margin-top:6px">Staleness: ${esc(stale || "n/a")} (${esc(staleAge)})</div>
          <div class="muted" style="margin-top:6px">Never succeeded: ${esc(String(neverCount))}</div>
        `;
        setTooltip(html, event.offsetX, event.offsetY);
      })
      .on("mouseout", () => hideTooltip());

    svg.on("click", () => {
      topology.selected = null;
      hideTooltip();
      setPanel("Details", "Click a node or link", '<div class="text-muted">No selection.</div>');
      highlightSelection();
    });

    const sim = window.d3
      .forceSimulation(simNodes)
      .force(
        "link",
        window.d3
          .forceLink(simLinks)
          .id((d) => d.id)
          .distance((d) => 120 + Math.max(0, 16 - Math.log2(Math.max(1, d.shown_count))) * 10)
          .strength(0.35)
      )
      .force("charge", window.d3.forceManyBody().strength(-520))
      .force("center", window.d3.forceCenter(width / 2, height / 2))
      .force("collide", window.d3.forceCollide().radius((d) => nodeRadius(d, io.get(d.id) || { in: 0, out: 0 }) + 10))
      .force("x", window.d3.forceX(width / 2).strength(0.06))
      .force("y", window.d3.forceY(height / 2).strength(0.06));

    topology.simulation = sim;

    const ticked = () => {
      linkSel
        .attr("x1", (d) => d.source.x)
        .attr("y1", (d) => d.source.y)
        .attr("x2", (d) => d.target.x)
        .attr("y2", (d) => d.target.y);

      nodeSel.attr("transform", (d) => `translate(${d.x},${d.y})`);

      linkLabelSel
        .attr("x", (d) => (d.source.x + d.target.x) / 2)
        .attr("y", (d) => (d.source.y + d.target.y) / 2 - 6);
    };

    sim.on("tick", ticked);

    // In demo screenshot mode (file:// with injected data), pre-tick the simulation
    // so the graph is visible and stable before Playwright captures the page.
    const isDemo = (window?.location?.protocol || "") === "file:" && !!window.__TOPOLOGY_DEMO_DATA__;
    if (isDemo) {
      try {
        for (let i = 0; i < 260; i++) sim.tick();
        ticked();
        sim.stop();
      } catch (e) {
        // Ignore demo-only stabilization errors; rendering will still proceed.
      }
    }

    function highlightSelection() {
      const sel = topology.selected;
      linkSel.attr("stroke-opacity", (d) => {
        if (!sel) return 0.75;
        if (sel.type !== "link") return 0.35;
        return d === sel.data ? 0.95 : 0.22;
      });

      nodeSel.selectAll("circle").attr("stroke", (d) => {
        if (!sel) return "rgba(17, 24, 39, 0.22)";
        if (sel.type !== "node") return "rgba(17, 24, 39, 0.14)";
        return d.id === sel.data.id ? "rgba(252, 109, 38, 0.95)" : "rgba(17, 24, 39, 0.14)";
      });
      nodeSel.selectAll("circle").attr("stroke-width", (d, i) => {
        if (i !== 0) return 1; // inner circle
        if (!sel) return 1.2;
        if (sel.type !== "node") return 1.1;
        return d.id === sel.data.id ? 2.2 : 1.1;
      });
    }

    // Default panel (summary) when nothing selected.
    const totalEdges = links.length;
    const totalMirrorsShown = links.reduce((acc, l) => acc + l.shown_count, 0);
    setPanel(
      "Topology",
      `${nodes.length} instance(s), ${totalEdges} link(s)`,
      `
        <div style="display:grid; gap:10px">
          <div><strong>Mirrors shown</strong><div class="text-muted">${esc(String(totalMirrorsShown))}</div></div>
          <div><strong>Legend</strong><div class="text-muted">Edge labels show: <code>count direction</code></div></div>
        </div>
      `
    );

    // Demo mode: auto-select the busiest link for nicer screenshots.
    if (isDemo && simLinks.length) {
      const best = simLinks.slice().sort((a, b) => (b.shown_count || 0) - (a.shown_count || 0))[0];
      if (best) {
        topology.selected = { type: "link", data: best };
        showLinkDetails(best, nodes);
        highlightSelection();
      }
    }

    topology.lastRenderKey = renderKey;
  }

  function showNodeDetails(node, nodes, links) {
    const n = nodes.find((x) => x.id === node.id) || node;
    const outgoing = links.filter((l) => l.source === n.id);
    const incoming = links.filter((l) => l.target === n.id);

    const listEdges = (arr, label) => {
      if (!arr.length) return `<div class="text-muted">No ${label} links.</div>`;
      const rows = arr
        .slice()
        .sort((a, b) => b.shown_count - a.shown_count)
        .map((l) => {
          const otherId = label === "outgoing" ? l.target : l.source;
          const other = nodes.find((x) => x.id === otherId);
          const otherName = other ? other.name : `#${otherId}`;
          return `<div style="display:flex; justify-content:space-between; gap:12px">
            <div><span class="badge badge-info">${esc(l.mirror_direction)}</span> ${esc(otherName)}</div>
            <div><strong>${esc(String(l.shown_count))}</strong></div>
          </div>`;
        })
        .join('<div style="height:10px"></div>');
      return `<div style="display:grid; gap:10px">${rows}</div>`;
    };

    const health = (n.health || "unknown").toString().toLowerCase();
    const dot = healthDotClass(health);
    const last = n.last_successful_update ? new Date(n.last_successful_update).toLocaleString() : "Never";
    const stale = (n.staleness || "unknown").toString().toLowerCase();
    const staleAge = Number.isFinite(n.staleness_age_seconds) ? `${Math.round(n.staleness_age_seconds / 3600)}h` : "n/a";
    const neverCount = Number.isFinite(n.never_succeeded_count) ? n.never_succeeded_count : 0;
    const counts = n.status_counts || {};
    const fmtCounts = Object.keys(counts).length
      ? Object.entries(counts)
          .sort((a, b) => b[1] - a[1])
          .map(([k, v]) => `<span class="pill" style="margin-right:6px">${esc(k)}: <strong>${esc(String(v))}</strong></span>`)
          .join("")
      : '<span class="text-muted">n/a</span>';

    setPanel(
      n.name,
      "GitLab instance",
      `
        <div style="display:grid; gap:14px">
          <div class="row" style="display:flex; justify-content:space-between; align-items:center">
            <div class="pill"><span class="dot ${dot}"></span><strong>${esc(health)}</strong></div>
            <div class="text-muted">Last success: ${esc(last)}</div>
          </div>
          <div class="text-muted">Staleness: <strong>${esc(stale)}</strong> <span class="text-muted">(${esc(staleAge)})</span></div>
          <div class="text-muted">Never succeeded mirrors: <strong>${esc(String(neverCount))}</strong></div>
          <div>
            <div class="text-muted">URL</div>
            <div><code>${esc(n.url)}</code></div>
          </div>
          <div class="grid-2">
            <div><div class="text-muted">Mirrors out</div><div><strong>${esc(String(n.mirrors_out || 0))}</strong></div></div>
            <div><div class="text-muted">Mirrors in</div><div><strong>${esc(String(n.mirrors_in || 0))}</strong></div></div>
            <div><div class="text-muted">Pairs out</div><div><strong>${esc(String(n.pairs_out || 0))}</strong></div></div>
            <div><div class="text-muted">Pairs in</div><div><strong>${esc(String(n.pairs_in || 0))}</strong></div></div>
          </div>
          <div>
            <div class="text-muted" style="margin-bottom:6px">Status breakdown</div>
            <div>${fmtCounts}</div>
          </div>
          <div>
            <div class="text-muted">Outgoing</div>
            ${listEdges(outgoing, "outgoing")}
          </div>
          <div>
            <div class="text-muted">Incoming</div>
            ${listEdges(incoming, "incoming")}
          </div>
        </div>
      `
    );
  }

  function showLinkDetails(link, nodes) {
    const srcId = typeof link.source === "object" && link.source ? link.source.id : link.source;
    const tgtId = typeof link.target === "object" && link.target ? link.target.id : link.target;
    const src = nodes.find((n) => n.id === srcId) || { name: `#${srcId}` };
    const tgt = nodes.find((n) => n.id === tgtId) || { name: `#${tgtId}` };
    const dir = (link.mirror_direction || "").toLowerCase();
    const countShown = link.shown_count ?? link.mirror_count;
    const health = (link.health || "unknown").toString().toLowerCase();
    const dot = healthDotClass(health);
    const last = link.last_successful_update ? new Date(link.last_successful_update).toLocaleString() : "Never";
    const stale = (link.staleness || "unknown").toString().toLowerCase();
    const staleAge = Number.isFinite(link.staleness_age_seconds) ? `${Math.round(link.staleness_age_seconds / 3600)}h` : "n/a";
    const neverCount = Number.isFinite(link.never_succeeded_count) ? link.never_succeeded_count : 0;
    const counts = link.status_counts || {};
    const fmtCounts = Object.keys(counts).length
      ? Object.entries(counts)
          .sort((a, b) => b[1] - a[1])
          .map(([k, v]) => `<span class="pill" style="margin-right:6px">${esc(k)}: <strong>${esc(String(v))}</strong></span>`)
          .join("")
      : '<span class="text-muted">n/a</span>';

    setPanel(
      `${src.name} → ${tgt.name}`,
      `${dir} link`,
      `
        <div style="display:grid; gap:14px">
          <div class="row" style="display:flex; justify-content:space-between; align-items:center">
            <div class="pill"><span class="dot ${dot}"></span><strong>${esc(health)}</strong></div>
            <div class="text-muted">Last success: ${esc(last)}</div>
          </div>
          <div class="text-muted">Staleness: <strong>${esc(stale)}</strong> <span class="text-muted">(${esc(staleAge)})</span></div>
          <div class="text-muted">Never succeeded mirrors: <strong>${esc(String(neverCount))}</strong></div>
          <div class="grid-2">
            <div><div class="text-muted">Mirrors shown</div><div><strong>${esc(String(countShown))}</strong></div></div>
            <div><div class="text-muted">Pairs</div><div><strong>${esc(String(link.pair_count || 0))}</strong></div></div>
          </div>
          <div class="grid-2">
            <div><div class="text-muted">Enabled</div><div><strong>${esc(String(link.enabled_count || 0))}</strong></div></div>
            <div><div class="text-muted">Disabled</div><div><strong>${esc(String(link.disabled_count || 0))}</strong></div></div>
          </div>
          <div>
            <div class="text-muted" style="margin-bottom:6px">Status breakdown</div>
            <div>${fmtCounts}</div>
          </div>
          <div>
            <div class="text-muted" style="margin-bottom:6px">Mirrors in this link</div>
            <div id="topology-link-mirrors">
              <div class="text-muted">Loading…</div>
            </div>
          </div>
          <div class="text-muted" style="font-size:0.92rem">
            Arrow is always drawn <strong>source → target</strong>. Direction indicates how GitLab is configured:
            <strong>push</strong> = source pushes, <strong>pull</strong> = target pulls.
          </div>
        </div>
      `
    );

    // Fetch drilldown mirrors and render inline
    const isFileDemo = (window?.location?.protocol || "") === "file:";
    if (isFileDemo) {
      try {
        const host = document.getElementById("topology-link-mirrors");
        if (!host) return;
        const demo = window.__TOPOLOGY_DEMO_LINK_MIRRORS__;
        const key = `${srcId}:${tgtId}:${dir}`;
        const mirrors = (demo && (demo[key] || demo.default)) || [];
        if (!mirrors.length) {
          host.innerHTML = `<div class="text-muted">No mirrors found for this link.</div>`;
          return;
        }

        const rows = mirrors
          .slice(0, 200)
          .map((m) => {
            const last = m.last_successful_update ? new Date(m.last_successful_update).toLocaleString() : "Never";
            const enabled = m.enabled ? `<span class="badge badge-success">Enabled</span>` : `<span class="badge badge-warning">Disabled</span>`;
            const health = healthPillHtml(m.health, m.staleness, m.never_succeeded);
            const status = statusBadgeHtml(m.last_update_status);
            return `
              <tr>
                <td><strong>${esc(m.instance_pair_name || "Pair")}</strong><div class="text-muted">#${esc(String(m.instance_pair_id || ""))}</div></td>
                <td><code>${esc(m.source_project_path)}</code></td>
                <td><code>${esc(m.target_project_path)}</code></td>
                <td>${health}</td>
                <td>${status}</td>
                <td>${enabled}</td>
                <td class="text-muted">${esc(last)}</td>
              </tr>
            `;
          })
          .join("");

        host.innerHTML = `
          <div class="text-muted" style="margin-bottom:8px">Showing ${esc(String(mirrors.length))} mirror(s)</div>
          <table>
            <thead>
              <tr>
                <th>Pair</th>
                <th>Source</th>
                <th>Target</th>
                <th>Health</th>
                <th>Status</th>
                <th>Enabled</th>
                <th>Last Success</th>
              </tr>
            </thead>
            <tbody>${rows}</tbody>
          </table>
        `;
      } catch (e) {
        // ignore demo rendering errors
      }
      return;
    }
    topology._linkReqId = (topology._linkReqId || 0) + 1;
    const reqId = topology._linkReqId;

    (async () => {
      try {
        const q = buildLinkMirrorsQuery({ sourceId: srcId, targetId: tgtId, direction: dir });
        const res = await apiRequest(`/api/topology/link-mirrors?${q}`);
        if (reqId !== topology._linkReqId) return;

        const host = document.getElementById("topology-link-mirrors");
        if (!host) return;

        const mirrors = res?.mirrors || [];
        if (!mirrors.length) {
          host.innerHTML = `<div class="text-muted">No mirrors found for this link.</div>`;
          return;
        }

        const rows = mirrors
          .slice(0, 200)
          .map((m) => {
            const last = m.last_successful_update ? new Date(m.last_successful_update).toLocaleString() : "Never";
            const enabled = m.enabled ? `<span class="badge badge-success">Enabled</span>` : `<span class="badge badge-warning">Disabled</span>`;
            const health = healthPillHtml(m.health, m.staleness, m.never_succeeded);
            const status = statusBadgeHtml(m.last_update_status);
            const openBtn = `<button class="btn btn-secondary btn-small" type="button" onclick="openMirrorsForPair(${Number(m.instance_pair_id)})">Open</button>`;
            return `
              <tr>
                <td><strong>${esc(m.instance_pair_name)}</strong><div class="text-muted">#${esc(String(m.instance_pair_id))}</div></td>
                <td><code>${esc(m.source_project_path)}</code></td>
                <td><code>${esc(m.target_project_path)}</code></td>
                <td>${health}</td>
                <td>${status}</td>
                <td>${enabled}</td>
                <td class="text-muted">${esc(last)}</td>
                <td>${openBtn}</td>
              </tr>
            `;
          })
          .join("");

        host.innerHTML = `
          <div class="text-muted" style="margin-bottom:8px">Showing ${esc(String(mirrors.length))} mirror(s)</div>
          <table>
            <thead>
              <tr>
                <th>Pair</th>
                <th>Source</th>
                <th>Target</th>
                <th>Health</th>
                <th>Status</th>
                <th>Enabled</th>
                <th>Last Success</th>
                <th></th>
              </tr>
            </thead>
            <tbody>${rows}</tbody>
          </table>
        `;
      } catch (e) {
        if (reqId !== topology._linkReqId) return;
        const host = document.getElementById("topology-link-mirrors");
        if (host) {
          host.innerHTML = `<div class="message message-error">Failed to load mirrors for this link: ${esc(e?.message || e)}</div>`;
        }
      }
    })();
  }

  async function refresh() {
    try {
      const canvas = byId("topology-canvas");
      const loading = byId("topology-loading");
      if (canvas && loading) {
        canvas.innerHTML = "";
        loading.classList.remove("hidden");
        canvas.appendChild(loading);
      }
      await fetchTopology();
      render();
    } catch (e) {
      console.error("Failed to load topology:", e);
      setPanel("Topology", "Error", `<div class="message message-error">Failed to load topology: ${esc(e.message || e)}</div>`);
      showMessage(`Failed to load topology: ${e.message || e}`, "error");
    }
  }

  function bindUIOnce() {
    if (topology.initialized) return;
    topology.initialized = true;

    const refreshBtn = byId("topology-refresh-btn");
    if (refreshBtn) refreshBtn.addEventListener("click", () => refresh());

    const pairSel = byId("topology-pair-filter");
    if (pairSel) {
      pairSel.addEventListener("change", () => refresh());
    }

    const warnEl = byId("topology-stale-warn-hours");
    const errEl = byId("topology-stale-error-hours");
    const neverEl = byId("topology-never-succeeded-error");
    const normalize = () => {
      if (!warnEl || !errEl) return;
      const w = parseInt((warnEl.value || "0").toString(), 10);
      const e = parseInt((errEl.value || "0").toString(), 10);
      const wv = Number.isFinite(w) ? Math.max(0, w) : 0;
      let ev = Number.isFinite(e) ? Math.max(0, e) : 0;
      if (ev < wv) ev = wv;
      warnEl.value = String(wv);
      errEl.value = String(ev);
    };
    if (warnEl) warnEl.addEventListener("change", () => { normalize(); refresh(); });
    if (errEl) errEl.addEventListener("change", () => { normalize(); refresh(); });
    if (neverEl) neverEl.addEventListener("change", () => refresh());

    ["topology-show-pull", "topology-show-push", "topology-show-disabled"].forEach((id) => {
      const el = byId(id);
      if (!el) return;
      el.addEventListener("change", () => {
        // re-render from cached data
        if (topology.raw) render();
      });
    });

    const canvas = byId("topology-canvas");
    if (canvas) {
      topology.resizeObserver = new ResizeObserver(() => {
        if (topology.raw) render();
      });
      topology.resizeObserver.observe(canvas);
    }
  }

  // Public hook called from app.js when the tab is activated
  window.initTopologyTab = async function initTopologyTab() {
    const isFileDemo = (window?.location?.protocol || "") === "file:";
    // Demo screenshots are opened via file://. In that case, allow rendering from
    // an injected demo payload so docs/screenshots/demo-topology.html can work.
    if (isFileDemo) {
      if (window.__TOPOLOGY_DEMO_DATA__) {
        bindUIOnce();
        topology.raw = window.__TOPOLOGY_DEMO_DATA__;
        render();
      }
      return;
    }

    // Populate pair dropdown (best-effort) for overlay filtering.
    try {
      const pairSel = byId("topology-pair-filter");
      if (pairSel && pairSel.options.length <= 1) {
        const [pairs, instances] = await Promise.all([
          apiRequest("/api/pairs").catch(() => []),
          apiRequest("/api/instances").catch(() => []),
        ]);
        const instById = new Map((instances || []).map((i) => [i.id, i]));
        const opts = [
          `<option value="">All pairs</option>`,
          ...(pairs || []).map((p) => {
            const src = instById.get(p.source_instance_id);
            const tgt = instById.get(p.target_instance_id);
            const srcName = src?.name || `#${p.source_instance_id}`;
            const tgtName = tgt?.name || `#${p.target_instance_id}`;
            const dir = (p.mirror_direction || "").toString().toLowerCase() || "n/a";
            return `<option value="${String(p.id)}">${esc(p.name)} — ${esc(srcName)} → ${esc(tgtName)} (${esc(dir)})</option>`;
          }),
        ].join("");
        pairSel.innerHTML = opts;
      }
    } catch (e) {
      // ignore (UI will still work without the filter list)
    }

    bindUIOnce();
    if (!topology.raw) {
      await refresh();
    } else {
      render();
    }
  };
})();

