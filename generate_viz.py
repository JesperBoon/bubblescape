"""
generate_viz.py — Build the self-contained Bubblescape HTML visualization.

Reads:  data/graph.json  (output of analyze.py --publish or --export)
Writes: ~/Desktop/Hertie/outside coursework/Bubblescape/bubblescape.html

Usage:
    python3 generate_viz.py
    python3 generate_viz.py --out /some/other/path.html

Run this after every `python3 analyze.py --publish` to update the visualization.
The HTML is fully self-contained — graph data is embedded, opens directly in any browser.
"""

import json
import sys
from pathlib import Path

# ── Paths ─────────────────────────────────────────────────────────────────────
GRAPH_JSON = Path(__file__).parent / "data" / "graph.json"
DEFAULT_OUT = Path.home() / "Desktop/Hertie/outside coursework/Bubblescape/bubblescape.html"

# ── Color palette (TikTok-inspired) ──────────────────────────────────────────
# Sub-cluster tonal families per macro cluster.
# Add more entries here as new sub-clusters appear.
SUB_COLORS_JS = """{
  "1a":"#FF2D55","1b":"#FF6B82","1c":"#B51C38","1d":"#FF9EAD",
  "2a":"#00EEFF","2b":"#00B8CC","2c":"#66F5FF","2d":"#008899","2e":"#004D55",
  "3a":"#2A8A9A","3b":"#1A6070","3c":"#0F3D4A","3d":"#6AAABB",
  "4a":"#E07A94","4b":"#EAA0B0","4c":"#CC4466",
  "5a":"#AA88FF","5b":"#8855DD","5c":"#CC99FF",
}"""

MACRO_COLORS_JS = """{1:"#FF2D55",2:"#00EEFF",3:"#2A6A7A",4:"#E07A94",5:"#AA88FF"}"""


def build_html(data: dict) -> str:
    data_js = json.dumps(data, ensure_ascii=False)

    css = """
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, sans-serif; background: #000; color: #e0e0e0; overflow: hidden; height: 100vh; }
#header { position: absolute; top: 0; left: 0; right: 0; padding: 18px 24px 0; display: flex; align-items: flex-start; justify-content: space-between; z-index: 10; pointer-events: none; }
#header-left h1 { font-size: 20px; font-weight: 800; letter-spacing: 0.02em; color: #fff; }
#header-left p  { font-size: 11px; color: #444; margin-top: 2px; letter-spacing: 0.04em; }
#meta-stats { display: flex; gap: 24px; padding-top: 2px; }
.stat-value { font-size: 20px; font-weight: 800; color: #fff; line-height: 1; }
.stat-label { font-size: 9px; color: #333; text-transform: uppercase; letter-spacing: 0.1em; margin-top: 2px; }
#breadcrumb { position: absolute; top: 62px; left: 24px; z-index: 10; display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
.crumb { font-size: 11px; color: #444; cursor: pointer; transition: color 0.15s; white-space: nowrap; }
.crumb:hover { color: #fff; }
.crumb.active { color: #fff; cursor: default; }
.crumb-sep { font-size: 11px; color: #1e1e1e; }
#canvas { width: 100vw; height: 100vh; }
.summary-circle { cursor: pointer; }
.summary-label { text-anchor: middle; pointer-events: none; paint-order: stroke; stroke: #000; stroke-linejoin: round; }
.summary-label.name  { font-size: 13px; font-weight: 700; fill: #fff; stroke-width: 5px; }
.summary-label.hint  { font-size: 10px; fill: #888; stroke-width: 4px; }
.summary-label.count { font-size: 10px; fill: #555; stroke-width: 4px; }
.node circle { cursor: pointer; }
.link { stroke: #ffffff06; stroke-linecap: round; }
#panel { position: absolute; top: 0; right: -380px; width: 360px; bottom: 0; background: #080808; border-left: 1px solid #111; padding: 52px 22px 22px; overflow-y: auto; z-index: 20; transition: right 0.28s cubic-bezier(.4,0,.2,1); }
#panel.open { right: 0; }
#panel-close { position: absolute; top: 14px; right: 16px; background: none; border: none; color: #333; font-size: 24px; cursor: pointer; }
#panel-close:hover { color: #fff; }
.p-badge { display: inline-flex; align-items: center; gap: 5px; font-size: 9px; font-weight: 800; padding: 3px 10px; border-radius: 99px; text-transform: uppercase; letter-spacing: 0.08em; margin-bottom: 12px; }
.p-dot { width: 6px; height: 6px; border-radius: 50%; flex-shrink: 0; }
.p-name { font-size: 20px; font-weight: 800; color: #fff; margin-bottom: 3px; word-break: break-all; line-height: 1.2; }
.p-bio { font-size: 12px; color: #444; line-height: 1.6; margin-bottom: 14px; white-space: pre-wrap; }
.p-section { margin-bottom: 18px; }
.p-section-title { font-size: 9px; font-weight: 800; color: #222; text-transform: uppercase; letter-spacing: 0.12em; margin-bottom: 7px; border-bottom: 1px solid #0f0f0f; padding-bottom: 5px; }
.p-row { display: flex; justify-content: space-between; font-size: 12px; margin-bottom: 5px; color: #444; }
.p-row span:last-child { color: #bbb; font-weight: 600; }
.tag-list { display: flex; flex-wrap: wrap; gap: 5px; }
.tag { background: #0a0a0a; border: 1px solid #141414; border-radius: 4px; padding: 3px 9px; font-size: 11px; }
.fit-bg { background: #0f0f0f; border-radius: 2px; height: 4px; margin-top: 6px; }
.fit-bar { height: 4px; border-radius: 2px; }
.badge-row { display: flex; flex-wrap: wrap; gap: 6px; margin: 8px 0; }
.badge-outlier { font-size: 10px; padding: 3px 9px; border-radius: 4px; background: #0e0505; border: 1px solid #2a0808; color: #bb2222; }
.badge-bridge  { font-size: 10px; padding: 3px 9px; border-radius: 4px; background: #050e07; border: 1px solid #082a10; color: #22bb44; }
.chip { display: inline-flex; align-items: center; background: #0a0a0a; border: 1px solid #141414; border-radius: 20px; padding: 4px 11px; font-size: 11px; color: #777; margin: 2px; cursor: pointer; transition: background 0.1s; }
.chip:hover { background: #141414; color: #fff; }
#tooltip { position: absolute; background: #080808; border: 1px solid #141414; border-radius: 6px; padding: 8px 12px; font-size: 12px; color: #bbb; pointer-events: none; z-index: 30; opacity: 0; transition: opacity 0.08s; }
#changelog { position: absolute; bottom: 20px; left: 20px; z-index: 10; }
.cl-chip { display: flex; align-items: center; gap: 8px; background: #080808; border: 1px solid #111; border-radius: 7px; padding: 7px 13px; cursor: pointer; }
.cl-chip:hover { border-color: #1a1a1a; }
.cl-dot { width: 5px; height: 5px; border-radius: 50%; background: #FF2D55; }
.cl-version { font-size: 11px; font-weight: 700; color: #fff; }
.cl-date { font-size: 10px; color: #333; }
#cl-dropdown { position: absolute; bottom: 44px; left: 0; background: #080808; border: 1px solid #111; border-radius: 7px; padding: 12px 16px; width: 290px; display: none; }
#cl-dropdown.open { display: block; }
.ver-entry { margin-bottom: 12px; }
.ver-entry:last-child { margin-bottom: 0; }
.ver-header { display: flex; gap: 8px; align-items: center; margin-bottom: 4px; }
.ver-badge { font-size: 9px; font-weight: 800; padding: 2px 7px; border-radius: 4px; background: #FF2D5515; border: 1px solid #FF2D5530; color: #FF2D55; }
.ver-date { font-size: 10px; color: #333; }
.ver-stats { font-size: 10px; color: #222; margin-bottom: 3px; }
.ver-change { font-size: 11px; color: #555; padding-left: 10px; position: relative; margin-top: 2px; }
.ver-change::before { content: "•"; position: absolute; left: 0; color: #2a2a2a; }
#controls { position: absolute; bottom: 20px; right: 20px; display: flex; gap: 8px; z-index: 10; }
.ctrl-btn { background: #080808; border: 1px solid #111; color: #444; width: 34px; height: 34px; border-radius: 6px; font-size: 14px; cursor: pointer; display: flex; align-items: center; justify-content: center; transition: color 0.1s, background 0.1s; }
.ctrl-btn:hover { background: #111; color: #fff; }
#panel::-webkit-scrollbar { width: 3px; }
#panel::-webkit-scrollbar-track { background: #000; }
#panel::-webkit-scrollbar-thumb { background: #141414; border-radius: 2px; }
"""

    js = f"""
const GRAPH_DATA = {data_js};

const SUB_COLORS  = {SUB_COLORS_JS};
const MACRO_COLORS = {MACRO_COLORS_JS};

function nodeColor(d) {{
  if (d.is_outlier) return "#1e1e1e";
  const s = d.sub_cluster_label;
  if (!s) return MACRO_COLORS[d.cluster_index] || "#444";
  return SUB_COLORS[s.split("·")[0]] || MACRO_COLORS[d.cluster_index] || "#444";
}}
function treeColor(tn) {{
  if (tn.type === "macro") return MACRO_COLORS[tn.cluster_index] || "#444";
  const d1 = tn.id.split("·")[0];
  return SUB_COLORS[d1] || MACRO_COLORS[tn.cluster_index] || "#444";
}}
function fmt(n) {{ return n>=1e6?(n/1e6).toFixed(1)+"M":n>=1e3?Math.round(n/1e3)+"K":""+n; }}
function esc(s) {{ return String(s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;"); }}

function buildTree(data) {{
  const {{ clusters, nodes }} = data;
  function membersOf(prefix, ci) {{
    if (prefix===null) return nodes.filter(n=>n.cluster_index===ci).map(n=>n.id);
    return nodes.filter(n=>{{ const s=n.sub_cluster_label; return s&&(s===prefix||s.startsWith(prefix+"·")); }}).map(n=>n.id);
  }}
  function buildSubs(subs, ci) {{
    if (!subs||!subs.length) return null;
    const r = subs.map(s=>{{
      const m = membersOf(s.sub_label, ci);
      if (!m.length) return null;
      return {{ id:s.sub_label, type:"sub", label:s.name||s.label_hint||s.sub_label,
               sub_label:s.sub_label, cluster_index:ci, size:m.length, members:m,
               children:buildSubs(s.subclusters,ci), depth:s.depth,
               label_hint:s.label_hint, name:s.name }};
    }}).filter(Boolean);
    return r.length ? r : null;
  }}
  return {{ id:"root", type:"root", children: clusters.map(c=>{{
    const m = membersOf(null, c.cluster_index);
    return {{ id:`c${{c.cluster_index}}`, type:"macro", label:c.name||`Bubble ${{c.cluster_index}}`,
             cluster_index:c.cluster_index, fingerprint_id:c.fingerprint_id,
             size:m.length, members:m, children:buildSubs(c.subclusters,c.cluster_index),
             total_followers:c.total_followers, top_hashtags:c.top_hashtags,
             top_accounts:c.top_accounts, top_bridges:c.top_bridges,
             confidence_label:c.confidence_label, internal_density:c.internal_density }};
  }}) }};
}}

(function build(data) {{
  const {{ meta, clusters, nodes, edges }} = data;
  const nodeMap = {{}};
  nodes.forEach(n => nodeMap[n.id] = n);
  const tree = buildTree(data);

  // ── State (declared before render()) ────────────────────────────────────
  let path = [];
  let summaryData = [];

  // ── Header ───────────────────────────────────────────────────────────────
  const statsEl = document.getElementById("meta-stats");
  [[fmt(nodes.length),"accounts"],[fmt(edges.length),"connections"],
   [meta.macro_clusters,"bubbles"],[fmt(nodes.reduce((s,n)=>s+(n.followers||0),0)),"reach"]]
  .forEach(([v,l]) => {{
    statsEl.innerHTML += `<div class="stat"><div class="stat-value">${{v}}</div><div class="stat-label">${{l}}</div></div>`;
  }});

  // ── Changelog ────────────────────────────────────────────────────────────
  const versions = meta.versions || [];
  const latest = versions[versions.length-1];
  if (latest) {{ document.getElementById("cl-v").textContent=`v${{latest.v}}`; document.getElementById("cl-d").textContent=latest.date; }}
  const clDrop = document.getElementById("cl-dropdown");
  versions.slice().reverse().forEach(v => {{
    const ch=(v.changes||[]).map(c=>`<div class="ver-change">${{esc(c)}}</div>`).join("");
    clDrop.innerHTML += `<div class="ver-entry"><div class="ver-header"><span class="ver-badge">v${{v.v}}</span><span class="ver-date">${{esc(v.date)}}</span></div><div class="ver-stats">${{v.accounts}} accounts · ${{v.macro_clusters}} bubbles</div>${{ch}}</div>`;
  }});
  document.getElementById("cl-toggle").onclick = e => {{ e.stopPropagation(); clDrop.classList.toggle("open"); }};
  document.addEventListener("click", () => clDrop.classList.remove("open"));

  // ── SVG / zoom ────────────────────────────────────────────────────────────
  const W = window.innerWidth, H = window.innerHeight;
  const svg = d3.select("#canvas").attr("width",W).attr("height",H);
  const g   = svg.append("g");
  const zoom = d3.zoom().scaleExtent([0.08,8]).on("zoom", e => g.attr("transform",e.transform));
  svg.call(zoom);
  const initT = d3.zoomIdentity.translate(W/2,H/2).scale(0.9).translate(-W/2,-H/2);
  svg.call(zoom.transform, initT);
  document.getElementById("btn-zoom-in").onclick  = () => svg.transition().call(zoom.scaleBy,1.4);
  document.getElementById("btn-zoom-out").onclick = () => svg.transition().call(zoom.scaleBy,0.7);
  document.getElementById("btn-reset").onclick    = () => svg.transition().duration(600).call(zoom.transform,initT);
  document.getElementById("btn-back").onclick     = drillOut;
  svg.on("click", drillOut);

  // ── Scales ────────────────────────────────────────────────────────────────
  const maxF = d3.max(nodes, n=>n.followers||0);
  const rAcc  = d3.scaleSqrt().domain([0,maxF]).range([3,22]);
  function rSum(tn) {{ return Math.max(30, Math.sqrt(tn.size) * 13); }}

  // ── Simulation (account-level only) ──────────────────────────────────────
  const sim = d3.forceSimulation(nodes)
    .force("link", d3.forceLink(edges).id(d=>d.id)
      .distance(d=>28/Math.sqrt(d.weight||1)).strength(d=>Math.min(0.85,(d.weight||1)/5)))
    .force("charge", d3.forceManyBody().strength(-90))
    .force("center", d3.forceCenter(W/2,H/2))
    .force("collision", d3.forceCollide().radius(d=>rAcc(d.followers||0)+4))
    .force("cluster", clusterForce(0.18))
    .stop();

  // Run sim to convergence upfront so account positions are ready
  for (let i=0; i<300; i++) sim.tick();

  // ── Layers ────────────────────────────────────────────────────────────────
  const linkLayer    = g.append("g").style("display","none");
  const nodeLayer    = g.append("g").style("opacity","0");
  const summaryLayer = g.append("g");

  // ── Links ─────────────────────────────────────────────────────────────────
  const link = linkLayer.selectAll("line").data(edges).join("line")
    .attr("class","link").attr("stroke-width",d=>Math.min(2,Math.sqrt(d.weight||1)*0.3))
    .attr("x1",d=>d.source.x).attr("y1",d=>d.source.y)
    .attr("x2",d=>d.target.x).attr("y2",d=>d.target.y);

  // ── Account nodes ─────────────────────────────────────────────────────────
  const node = nodeLayer.selectAll("g.node").data(nodes).join("g")
    .attr("class","node")
    .attr("transform",d=>`translate(${{d.x}},${{d.y}})`)
    .call(drag());
  node.append("circle")
    .attr("r",d=>rAcc(d.followers||0)).attr("fill",nodeColor)
    .attr("fill-opacity",d=>d.is_outlier?0.35:0.88)
    .attr("stroke",d=>d.is_bridge?"#fff":nodeColor(d))
    .attr("stroke-width",d=>d.is_bridge?1.5:0.5)
    .attr("stroke-opacity",d=>d.is_bridge?0.55:0.25);

  // ── Tooltip ───────────────────────────────────────────────────────────────
  const tooltip = document.getElementById("tooltip");
  function tp(ev){{ tooltip.style.left=(ev.clientX+14)+"px"; tooltip.style.top=(ev.clientY-8)+"px"; }}
  node.on("mouseenter",(ev,d)=>{{
    const sub=d.sub_cluster_label?` · ${{d.sub_cluster_label}}`:"";
    tooltip.innerHTML=`<b style="color:${{nodeColor(d)}};">@${{esc(d.id)}}</b>${{sub}}<br>${{fmt(d.followers||0)}} followers`;
    tooltip.style.opacity="1"; tp(ev);
  }}).on("mousemove",tp).on("mouseleave",()=>tooltip.style.opacity="0")
    .on("click",(ev,d)=>{{ ev.stopPropagation(); showNodePanel(d); }});

  // ── Initial render ────────────────────────────────────────────────────────
  render();

  // ── State machine ─────────────────────────────────────────────────────────
  function current() {{ return path.length===0 ? tree : path[path.length-1]; }}
  function isLeaf(tn) {{ return !tn.children || tn.children.length===0; }}
  function drillIn(tn) {{ path.push(tn); render(); closePanel(); }}
  function drillOut() {{ if(!path.length) return; path.pop(); render(); closePanel(); }}

  function render() {{
    updateBreadcrumb();
    const cn = current();
    if (isLeaf(cn)) {{ showAccounts(cn.members); hideSummary(); }}
    else            {{ hideAccounts(); showSummary(cn.children); }}
  }}

  // ── Static layout for summary circles ────────────────────────────────────
  // Runs a mini force layout synchronously to get non-overlapping positions.
  function computeLayout(treeNodes) {{
    const cW = window.innerWidth, cH = window.innerHeight;
    const layoutNodes = treeNodes.map(tn => ({{
      id: tn.id, r: rSum(tn), x: cW/2 + (Math.random()-0.5)*80, y: cH/2 + (Math.random()-0.5)*80
    }}));
    const mini = d3.forceSimulation(layoutNodes)
      .force("center", d3.forceCenter(cW/2, cH/2))
      .force("charge", d3.forceManyBody().strength(-60))
      .force("collision", d3.forceCollide().radius(d=>d.r + 60).strength(1))
      .stop();
    for (let i=0; i<400; i++) mini.tick();
    const map = {{}};
    layoutNodes.forEach(n => map[n.id] = {{x:n.x, y:n.y}});
    return map;
  }}

  // ── Overlap badges ────────────────────────────────────────────────────────
  function drawOverlapBadges(treeNodes, layout) {{
    summaryLayer.selectAll("g.overlap-badge").remove();
    for (let i=0; i<treeNodes.length; i++) {{
      for (let j=i+1; j<treeNodes.length; j++) {{
        const a=treeNodes[i], b=treeNodes[j];
        const setA=new Set(a.members), shared=b.members.filter(id=>setA.has(id));
        if (!shared.length) continue;
        const la=layout[a.id], lb=layout[b.id];
        const mx=(la.x+lb.x)/2, my=(la.y+lb.y)/2;
        const badge=summaryLayer.append("g").attr("class","overlap-badge")
          .attr("transform",`translate(${{mx}},${{my}})`);
        badge.append("circle").attr("r",13).attr("fill","#111").attr("stroke","#222").attr("stroke-width",1);
        badge.append("text").attr("text-anchor","middle").attr("dy","0.35em")
          .attr("font-size","10px").attr("fill","#666").text(`${{shared.length}}`);
      }}
    }}
  }}

  // ── Summary circles (static positions) ────────────────────────────────────
  function showSummary(treeNodes) {{
    summaryData = treeNodes;
    const layout = computeLayout(treeNodes);

    // Remove old overlap badges
    summaryLayer.selectAll("g.overlap-badge").remove();

    summaryLayer.selectAll("g.sc")
      .data(treeNodes, d=>d.id)
      .join(
        enter => {{
          const grp = enter.append("g").attr("class","sc summary-circle")
            .attr("transform", tn=>`translate(${{layout[tn.id].x}},${{layout[tn.id].y}})`)
            .style("opacity",0)
            .on("click",(ev,tn)=>{{ ev.stopPropagation(); drillIn(tn); }})
            .on("mouseenter",(ev,tn)=>{{
              tooltip.innerHTML=`<b style="color:${{treeColor(tn)}};">${{esc(tn.label)}}</b><br>${{tn.size}} accounts`;
              tooltip.style.opacity="1"; tp(ev);
            }})
            .on("mousemove",tp)
            .on("mouseleave",()=>tooltip.style.opacity="0");

          grp.append("circle").attr("class","outer")
            .attr("r",tn=>rSum(tn))
            .attr("fill",tn=>treeColor(tn)).attr("fill-opacity",0.08)
            .attr("stroke",tn=>treeColor(tn)).attr("stroke-width",1.5).attr("stroke-opacity",0.40)
            .attr("stroke-dasharray",tn=>tn.type==="sub"?"6 3":"none");

          // Top hashtags as small text lines inside the circle
          grp.append("text").attr("class","summary-label name")
            .attr("dy","-1.4em").text(tn=>tn.label);

          grp.append("text").attr("class","summary-label hint").attr("dy","-0.1em")
            .text(tn=>{{
              if (tn.top_hashtags) return tn.top_hashtags.slice(0,3).map(h=>`#${{h}}`).join(" / ");
              return tn.label_hint&&tn.label_hint!==tn.label?tn.label_hint:"";
            }});

          grp.append("text").attr("class","summary-label count").attr("dy","1.3em")
            .text(tn=>`${{tn.size}} accounts${{tn.children?" · click to explore":""}}`);

          grp.transition().duration(400).style("opacity",1);
          return grp;
        }},
        update => update.attr("transform", tn=>`translate(${{layout[tn.id].x}},${{layout[tn.id].y}})`),
        exit   => exit.transition().duration(250).style("opacity",0).remove()
      );

    drawOverlapBadges(treeNodes, layout);
  }}

  function hideSummary() {{
    summaryData=[];
    summaryLayer.selectAll("g.sc").transition().duration(250).style("opacity",0).remove();
    summaryLayer.selectAll("g.overlap-badge").remove();
  }}

  // ── Account layer ─────────────────────────────────────────────────────────
  function showAccounts(ids) {{
    const set=new Set(ids);
    node.style("display",d=>set.has(d.id)?null:"none");
    nodeLayer.transition().duration(350).style("opacity","1");
    linkLayer.style("display",null).transition().duration(350).style("opacity","1");
  }}
  function hideAccounts() {{
    nodeLayer.transition().duration(250).style("opacity","0");
    linkLayer.transition().duration(200).style("opacity","0")
      .on("end",function(){{ d3.select(this).style("display","none"); }});
  }}

  // ── Breadcrumb ────────────────────────────────────────────────────────────
  function updateBreadcrumb() {{
    const el=document.getElementById("breadcrumb");
    const crumbs=[{{label:"All bubbles",idx:-1}}];
    path.forEach((tn,i)=>crumbs.push({{label:tn.label,idx:i}}));
    el.innerHTML=crumbs.map((c,i)=>{{
      const active=i===crumbs.length-1;
      const sep=i>0?`<span class="crumb-sep">›</span>`:"";
      const col=c.idx>=0?treeColor(path[c.idx]):"#fff";
      const style=active?`style="color:${{col}};"`:"";
      return `${{sep}}<span class="crumb ${{active?"active":""}}" ${{style}} data-idx="${{c.idx}}">${{esc(c.label)}}</span>`;
    }}).join("");
    el.querySelectorAll(".crumb:not(.active)").forEach(el=>{{
      el.onclick=e=>{{
        e.stopPropagation();
        const idx=+el.dataset.idx;
        path=idx===-1?[]:path.slice(0,idx+1);
        render(); closePanel();
      }};
    }});
  }}

  // ── Cluster force ─────────────────────────────────────────────────────────
  function clusterForce(strength) {{
    return function(alpha) {{
      const sums={{}},cnts={{}};
      nodes.forEach(n=>{{
        const k=n.cluster_index; if(k==null)return;
        if(!sums[k]){{sums[k]=[0,0];cnts[k]=0;}}
        sums[k][0]+=n.x||0; sums[k][1]+=n.y||0; cnts[k]++;
      }});
      const cen={{}};
      Object.keys(sums).forEach(k=>cen[k]=[sums[k][0]/cnts[k],sums[k][1]/cnts[k]]);
      nodes.forEach(n=>{{
        const c=cen[n.cluster_index]; if(!c)return;
        n.vx=(n.vx||0)+(c[0]-n.x)*strength*alpha;
        n.vy=(n.vy||0)+(c[1]-n.y)*strength*alpha;
      }});
    }};
  }}

  // Drag for account nodes (manual repositioning only, no live sim)
  function drag(){{
    return d3.drag()
      .on("start",(ev,d)=>{{d.fx=d.x;d.fy=d.y;}})
      .on("drag",(ev,d)=>{{
        d.x=ev.x; d.y=ev.y; d.fx=ev.x; d.fy=ev.y;
        d3.select(ev.sourceEvent.target.closest("g.node")).attr("transform",`translate(${{ev.x}},${{ev.y}})`);
      }})
      .on("end",(ev,d)=>{{d.fx=null;d.fy=null;}});
  }}

  // ── Side panel ────────────────────────────────────────────────────────────
  document.getElementById("panel-close").onclick=closePanel;
  function closePanel(){{ document.getElementById("panel").classList.remove("open"); }}
  function openPanel(html){{
    document.getElementById("panel-content").innerHTML=html;
    document.getElementById("panel").classList.add("open");
    setTimeout(()=>{{
      document.querySelectorAll(".chip[data-id]").forEach(el=>{{
        el.onclick=()=>{{ const nd=nodeMap[el.dataset.id]; if(nd) showNodePanel(nd); }};
      }});
    }},30);
  }}

  function showNodePanel(d) {{
    const c=clusters.find(x=>x.cluster_index===d.cluster_index);
    const col=nodeColor(d);
    const fit=Math.round((d.fit_score||0)*100);
    const fitCol=fit>=50?"#00EEFF":fit>=25?"#FF9D00":"#FF2D55";
    const tags=(d.hashtags||[]).slice(0,12).map(h=>`<span class="tag" style="color:${{col}}88;">#${{esc(h)}}</span>`).join("");
    openPanel(`
      <span class="p-badge" style="background:${{col}}15;color:${{col}};border:1px solid ${{col}}30;">
        <span class="p-dot" style="background:${{col}};"></span>${{c?esc(c.name):"Unassigned"}}
      </span>
      <div class="p-name">@${{esc(d.id)}}</div>
      ${{d.bio?`<div class="p-bio">${{esc(d.bio.slice(0,140))}}</div>`:""}}
      <div class="badge-row">
        ${{d.is_outlier?'<span class="badge-outlier">⚠ Low cluster fit</span>':""}}
        ${{d.is_bridge?'<span class="badge-bridge">⬡ Bridge node</span>':""}}
      </div>
      <div class="p-section" style="margin-top:${{d.is_outlier||d.is_bridge?14:6}}px;">
        <div class="p-section-title">Profile</div>
        <div class="p-row"><span>Followers</span><span>${{fmt(d.followers||0)}}</span></div>
        ${{d.sub_cluster_label?`<div class="p-row"><span>Sub-cluster</span><span style="color:${{col}};">${{esc(d.sub_cluster_label)}}</span></div>`:""}}
        <div class="p-row"><span>Cluster fit</span><span>${{fit}}%</span></div>
        <div class="fit-bg"><div class="fit-bar" style="width:${{fit}}%;background:${{fitCol}};"></div></div>
      </div>
      ${{tags?`<div class="p-section"><div class="p-section-title">Top hashtags</div><div class="tag-list">${{tags}}</div></div>`:""}}
    `);
  }}

  window.addEventListener("resize",()=>{{
    svg.attr("width",window.innerWidth).attr("height",window.innerHeight);
    // Re-render summary layer with fresh layout on resize
    if (summaryData.length) showSummary(summaryData);
  }});

}})(GRAPH_DATA);
"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Bubblescape — Dutch TikTok Information Bubbles</title>
<script src="https://d3js.org/d3.v7.min.js"></script>
<style>{css}</style>
</head>
<body>
<div id="header">
  <div id="header-left"><h1>Bubblescape</h1><p>Dutch TikTok — algorithmic information bubbles</p></div>
  <div id="meta-stats"></div>
</div>
<div id="breadcrumb"></div>
<svg id="canvas"></svg>
<div id="panel"><button id="panel-close">×</button><div id="panel-content"></div></div>
<div id="tooltip"></div>
<div id="changelog">
  <div class="cl-chip" id="cl-toggle">
    <div class="cl-dot"></div><span class="cl-version" id="cl-v">v–</span><span class="cl-date" id="cl-d">–</span>
  </div>
  <div id="cl-dropdown"></div>
</div>
<div id="controls">
  <button class="ctrl-btn" id="btn-back" title="Go up one level">↑</button>
  <button class="ctrl-btn" id="btn-zoom-in">+</button>
  <button class="ctrl-btn" id="btn-zoom-out">−</button>
  <button class="ctrl-btn" id="btn-reset">⌂</button>
</div>
<script>{js}</script>
</body>
</html>"""


def main():
    # Allow custom output path via --out flag
    out_path = DEFAULT_OUT
    if "--out" in sys.argv:
        idx = sys.argv.index("--out")
        if idx + 1 < len(sys.argv):
            out_path = Path(sys.argv[idx + 1])

    if not GRAPH_JSON.exists():
        print(f"❌  {GRAPH_JSON} not found — run analyze.py --export first")
        sys.exit(1)

    with open(GRAPH_JSON, encoding="utf-8") as f:
        data = json.load(f)

    html = build_html(data)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)

    versions = data.get("meta", {}).get("versions", [])
    latest   = versions[-1] if versions else None
    v_label  = f"v{latest['v']}" if latest else "no version"
    nodes    = data["meta"].get("graph_nodes", "?")
    clusters = data["meta"].get("macro_clusters", "?")

    print(f"✅  bubblescape.html written ({len(html)//1024}KB)")
    print(f"   {nodes} accounts · {clusters} bubbles · {v_label}")
    print(f"   → {out_path}")


if __name__ == "__main__":
    main()
