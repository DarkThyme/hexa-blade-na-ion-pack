// Builds docs/Hexa-Blade_Project_Report.docx from the project's result files.
// Run: cd docs/report_builder && npm install && node build.js   (then word_export.ps1 for the PDF)
const fs = require("fs");
const path = require("path");
const {
  Document, Packer, Paragraph, TextRun, HeadingLevel, AlignmentType, Table, TableRow, TableCell, WidthType,
  ShadingType, BorderStyle, ImageRun, ExternalHyperlink, Footer, Header, PageNumber, TableOfContents, PageBreak,
  LevelFormat, TableLayoutType, VerticalAlign,
} = require("docx");

const ROOT = path.resolve(__dirname, "..", "..");   // project root
const OUT = process.env.REPORT_OUT || path.join(ROOT, "docs", "Hexa-Blade_Project_Report.docx");
const KEEP = !process.env.REPORT_NO_KEEP;   // keep-with-next on headings (orphan control)
const SHOTS = path.join(__dirname, "img");
const matrix = JSON.parse(fs.readFileSync(path.join(ROOT, "analysis/matrix/results.json"), "utf8"));
const bench = JSON.parse(fs.readFileSync(path.join(ROOT, "analysis/realcars/benchmark.json"), "utf8"));
const ROWS = matrix.rows;

const PAGE_W = 11906, PAGE_H = 16838, MARGIN = 1134, CONTENT = PAGE_W - 2 * MARGIN; // A4, 2 cm margins
const FONT = "Calibri";
const ACCENT = "1F4E79", HEAD_FILL = "DCE6F2", ALT_FILL = "F3F6FA", RULE = "B7C4D3";

// ------------------------------------------------------------------ helpers
const t = (text, o = {}) => new TextRun({ text, font: FONT, ...o });
const b = (text, o = {}) => t(text, { bold: true, ...o });
const para = (runs, o = {}) => new Paragraph({ children: Array.isArray(runs) ? runs : [t(runs)], spacing: { after: 120, line: 276 }, ...o });
const p = (...runs) => para(runs.map(r => (typeof r === "string" ? t(r) : r)));
const h1 = (text) => new Paragraph({ heading: HeadingLevel.HEADING_1, children: [t(text)], pageBreakBefore: true });
const h2 = (text) => new Paragraph({ heading: HeadingLevel.HEADING_2, children: [t(text)], keepNext: KEEP, keepLines: KEEP });
const h3 = (text) => new Paragraph({ heading: HeadingLevel.HEADING_3, children: [t(text)], keepNext: KEEP, keepLines: KEEP });
const bullet = (...runs) => new Paragraph({ numbering: { reference: "bullets", level: 0 }, spacing: { after: 60, line: 264 },
  children: runs.map(r => (typeof r === "string" ? t(r) : r)) });
const link = (text, url) => new ExternalHyperlink({ link: url, children: [t(text, { style: "Hyperlink" })] });
const caption = (text) => new Paragraph({ children: [t(text, { italics: true, size: 18, color: "555555" })], spacing: { before: 60, after: 200 } });
const cite = (n) => t(` [${n}]`, { color: ACCENT });

function table(headers, rows, widths, o = {}) {
  const total = widths.reduce((a, c) => a + c, 0);
  const size = o.size || 18;
  const border = { style: BorderStyle.SINGLE, size: 4, color: RULE };
  const borders = { top: border, bottom: border, left: border, right: border };
  const cell = (text, w, head, alt, bold) => new TableCell({
    width: { size: w, type: WidthType.DXA }, borders, verticalAlign: VerticalAlign.CENTER,
    margins: { top: 50, bottom: 50, left: 80, right: 80 },
    shading: head ? { type: ShadingType.CLEAR, color: "auto", fill: HEAD_FILL } : alt ? { type: ShadingType.CLEAR, color: "auto", fill: ALT_FILL } : undefined,
    children: [new Paragraph({ spacing: { after: 0 }, children: [t(String(text), { size, bold: head || bold })] })],
  });
  return new Table({
    width: { size: total, type: WidthType.DXA }, columnWidths: widths, layout: TableLayoutType.FIXED,
    rows: [
      new TableRow({ tableHeader: true, children: headers.map((h, i) => cell(h, widths[i], true)) }),
      ...rows.map((r, k) => new TableRow({ children: r.map((v, i) => cell(v, widths[i], false, k % 2 === 1, o.boldFirst && i === 0)) })),
    ],
  });
}
const spacer = () => new Paragraph({ children: [], spacing: { after: 120 } });
function image(file, widthPx, type = "png") {
  const data = fs.readFileSync(file);
  let w, h;
  if (type === "png") { w = data.readUInt32BE(16); h = data.readUInt32BE(20); }
  else { // jpeg: find SOF0/SOF2
    let i = 2;
    while (i < data.length) {
      const marker = data.readUInt16BE(i);
      const len = data.readUInt16BE(i + 2);
      if (marker === 0xffc0 || marker === 0xffc2) { h = data.readUInt16BE(i + 5); w = data.readUInt16BE(i + 7); break; }
      i += 2 + len;
    }
  }
  return new Paragraph({ alignment: AlignmentType.CENTER, spacing: { before: 120, after: 40 },
    children: [new ImageRun({ type, data, transformation: { width: widthPx, height: Math.round(widthPx * h / w) } })] });
}

// ------------------------------------------------------------------ data helpers
const AMB = [-20, -10, 0, 10, 25, 35, 45];
const CHARGERS = ["7 kW AC", "15 kW AC", "50 kW DC", "125 kW DC", "250 kW DC", "350 kW DC"];
const get = (arch, ch, amb, pre, st) => ROWS.find(r => r.arch === arch && r.charger === ch && r.ambient_c === amb && r.precondition === pre && r.strategy === st);
const fm = (m) => (m == null ? "-" : m >= 100 ? `${(m / 60).toFixed(1)} h` : m.toFixed(1));
const ARCH = { blade: "Hex-Blade (cell-to-pack)", module: "Hex-Cell Modules" };
const STRAT = { standard: "Standard BMS", optimized: "Anode-aware BMS" };
function t90Table(arch, st) {
  const rows = CHARGERS.map(c => [c, ...AMB.map(a => `${fm(get(arch, c, a, true, st).t90_min)} / ${fm(get(arch, c, a, false, st).t90_min)}`)]);
  return table(["Charger", ...AMB.map(a => `${a} °C`)], rows, [1420, ...AMB.map(() => 1174)], { size: 16, boldFirst: true });
}
const nChecks = Object.keys(ROWS[0].checks).length;
const crossTested = Object.values(matrix.cross_tested).reduce((a, c) => a + c, 0);
const hottest = ROWS.reduce((a, r) => (r.peak_temp_c > a.peak_temp_c ? r : a));
const realCars = bench.filter(r => r.class === "800V" && r.t10_90 != null).sort((a, c) => a.t10_90 - c.t10_90);
const ours300 = bench.filter(r => r.class === "ours" && r.vehicle.includes("300 kW"));
const abl = JSON.parse(fs.readFileSync(path.join(ROOT, "analysis/ablation/attribution.json"), "utf8"));
const isHot = (c) => c.case[1] === "350 kW DC" && c.case[2] === 45;
function ablShare(g, hot) {
  const pct = (c) => { const x = c.shapley[g] / (c.standard - c.anode_aware); return Math.abs(x) < 0.005 ? 0 : 100 * x; };
  if (hot) return abl.filter(isHot).sort((a, b) => a.case[0].localeCompare(b.case[0])).map(c => `${pct(c).toFixed(0)} %`).join(" / ");
  const s = abl.filter(c => !isHot(c)).map(pct);
  const lo = Math.min(...s), hi = Math.max(...s);
  if (Math.max(...s.map(Math.abs)) < 0.5) return "0 %";
  return `${lo.toFixed(0)}–${hi.toFixed(0)} %`;
}

// ------------------------------------------------------------------ references
const REFS = [
  ["Sulzer, V., Marquis, S. G., Timms, R., Robinson, M., Chapman, S. J. (2021). Python Battery Mathematical Modelling (PyBaMM). Journal of Open Research Software, 9(1), 14.", "https://doi.org/10.5334/jors.309"],
  ["Chen, C.-H., Brosa Planella, F., O'Regan, K., Gastol, D., Widanage, W. D., Kendrick, E. (2020). Development of experimental techniques for parameterization of multi-scale lithium-ion battery models. Journal of The Electrochemical Society, 167, 080534. (LG M50 21700 parameter set.)", "https://doi.org/10.1149/1945-7111/ab9050"],
  ["Doyle, M., Fuller, T. F., Newman, J. (1993). Modeling of galvanostatic charge and discharge of the lithium/polymer/insertion cell. Journal of The Electrochemical Society, 140(6), 1526. (The DFN / pseudo-2D model.)", "https://doi.org/10.1149/1.2221597"],
  ["Chayambuka, K., Jiang, M., Mulder, G., Danilov, D. L., Notten, P. H. L. (2022). Physics-based modeling of sodium-ion batteries part I: Experimental parameter determination. Electrochimica Acta, 404, 139726.", "https://doi.org/10.1016/j.electacta.2021.139726"],
  ["Chayambuka, K., Mulder, G., Danilov, D. L., Notten, P. H. L. (2022). Physics-based modeling of sodium-ion batteries part II. Model and validation. Electrochimica Acta, 404, 139764.", "https://doi.org/10.1016/j.electacta.2021.139764"],
  ["Ionworks (2024). Sodium-ion battery model now available in PyBaMM (the Chayambuka 2022 NVPF / hard-carbon parameter set).", "https://www.ionworks.com/blog/sodium-ion-battery-model-now-available-in-pybamm"],
  ["Akiba, T., Sano, S., Yanase, T., Ohta, T., Koyama, M. (2019). Optuna: A next-generation hyperparameter optimization framework. Proceedings of the 25th ACM SIGKDD Conference, 2623-2631.", "https://doi.org/10.1145/3292500.3330701"],
  ["Waldmann, T., Hogg, B.-I., Wohlfahrt-Mehrens, M. (2018). Li plating as unwanted side reaction in commercial Li-ion cells - A review. Journal of Power Sources, 384, 107-124.", "https://doi.org/10.1016/j.jpowsour.2018.02.063"],
  ["He, H., Zhang, J., Wang, Y., Jiang, B., Huang, S., Wang, C., Zhang, Y., Xiong, G., Han, X., Guo, D., He, G., Ouyang, M. (2022, rev. 2023). EVBattery: A Large-Scale Electric Vehicle Dataset for Battery Health and Capacity Estimation. arXiv:2201.12358. Data licence CC BY-NC-SA (non-commercial).", "https://arxiv.org/abs/2201.12358"],
  ["Morin, H., Chatra, A., Whitacre, J. F., Michalek, J. (2025). Digitized Electric Vehicle Fast-Charging Profiles by Model (Power vs SOC/SOE, CSV Format), Version 1. Figshare. Licence CC BY 4.0. Curves digitized from Fastned and InsideEVs charts.", "https://doi.org/10.6084/m9.figshare.30570653.v1"],
  ["Battery Technology (2025). 5 key takeaways from CATL's Naxtra sodium-ion battery launch (175 Wh/kg, April 2025).", "https://www.batterytechonline.com/materials/5-key-takeaways-from-catl-s-naxtra-sodium-ion-battery-launch"],
  ["EV Database. Useable battery capacity of electric vehicles (gross vs usable capacity reference; read manually, not scraped, per the site's terms).", "https://ev-database.org/uk/cheatsheet/useable-battery-capacity-electric-car"],
  ["Autotijd. Porsche Taycan 4S Performance Battery Plus (2024-2026) specifications: 105 kWh gross, 97 kWh usable, 800 V. (Third-party spec listing.)", "https://autotijd.be/en/specs/porsche/taycan/4s-performance-battery-plus-awd-4820"],
  ["Royal Society of Chemistry, Energy & Environmental Science (2025), article D4EE06029F: hard-carbon sodium storage, describing the low-voltage plateau as the diffusion-limited region.", "https://pubs.rsc.org/lv/content/articlehtml/2025/ee/d4ee06029f"],
  ["OpenChargingCloud. open-ev-data (MIT licence; evaluated, not used: last updated 2020, 800 V entries use estimated curves).", "https://github.com/OpenChargingCloud/open-ev-data"],
  ["Bevy Engine (v0.16), an open-source Rust game engine, used for the 3D viewer compiled to WebAssembly.", "https://bevyengine.org"],
  ["Mikhailov, A. (2019). Turbo, an improved rainbow colormap for visualization. Google Research blog. (Used for the temperature view.)", "https://research.google/blog/turbo-an-improved-rainbow-colormap-for-visualization/"],
];
const refList = REFS.map(([text, url], i) => new Paragraph({
  spacing: { after: 80, line: 260 }, indent: { left: 567, hanging: 567 },
  children: [t(`[${i + 1}]\t${text} `, { size: 19 }), link(url, url)],
}));

// ------------------------------------------------------------------ content
const s350 = (arch, st) => get(arch, "350 kW DC", 25, true, st);
const body = [];
const add = (...x) => body.push(...x);

// title page
add(
  new Paragraph({ spacing: { before: 2400, after: 200 }, children: [t("HEXA-BLADE", { size: 64, bold: true, color: ACCENT })] }),
  new Paragraph({ spacing: { after: 300 }, children: [t("A fast-charging 110 kWh sodium-ion EV battery pack: design, physics simulation, real-world benchmarks and a 3D charging lab", { size: 32 })] }),
  new Paragraph({ border: { bottom: { style: BorderStyle.SINGLE, size: 8, color: ACCENT, space: 4 } }, children: [] }),
  new Paragraph({ spacing: { before: 300, after: 80 }, children: [t("Project report", { size: 24, bold: true })] }),
  new Paragraph({ spacing: { after: 80 }, children: [t("Prepared by Naman Dariya · 20 October 2026", { size: 22 })] }),
  new Paragraph({ spacing: { after: 80 }, children: [t("Status: simulation study. Nothing here has been built or tested in hardware yet.", { size: 22, italics: true })] }),
  new Paragraph({ spacing: { before: 1600 }, children: [t("What this document is for: to explain the project to someone who can review it and guide me. It covers the aim, what was built, every key number with its source, what is uncertain, and the specific questions I would like help with (Section 10).", { size: 21, color: "444444" })] }),
  new Paragraph({ children: [new PageBreak()] }),
  new Paragraph({ children: [b("Contents", { size: 28, color: ACCENT })], spacing: { after: 200 } }),
  new TableOfContents("Contents", { hyperlink: true, headingStyleRange: "1-2" }),
);

// 1 summary
add(h1("1. Summary"),
  p(b("Aim. "), "Design an electric-car battery pack that charges from 10 % to 90 % in about 15 minutes, using sodium-ion cells instead of lithium-ion, without ever crossing the safety limits that damage cells (sodium plating on the anode, overheating, over-voltage)."),
  p(b("What was built. "), "A software project, step by step: (1) a lithium-ion fast-charge optimiser to learn the physics; (2) a 110 kWh, 800 V-class sodium-ion pack in two layouts, simulated cell by cell with a physics-based cell model; (3) a battery-management (BMS) strategy that watches the anode potential directly; (4) calibration against 1.28 million real EV charging records and a benchmark against 14 real 800 V car variants; (5) a 3D web viewer; (6) a full test matrix of 336 charging sessions; (7) an ablation showing which BMS setting causes the speed-up; (8) a check of the reduced cell model against the full physics model; (9) a stress test of the BMS with imperfect information; (10) measurement-only anode-potential estimators tested in closed loop on the full physics model."),
  p(b("Headline results (350 kW charger, pack preconditioned, 10 → 90 %):")),
  table(["Pack", "BMS", "10→80 %", "10→90 %", "Hottest cell", "SEI exposure index"], [
    ["Hex-Blade", "Standard", fm(s350("blade", "standard").t80_min) + " min", fm(s350("blade", "standard").t90_min) + " min", s350("blade", "standard").peak_temp_c.toFixed(1) + " °C", s350("blade", "standard").aging_h25.toFixed(2)],
    ["Hex-Blade", "Anode-aware", fm(s350("blade", "optimized").t80_min) + " min", fm(s350("blade", "optimized").t90_min) + " min", s350("blade", "optimized").peak_temp_c.toFixed(1) + " °C", s350("blade", "optimized").aging_h25.toFixed(2)],
    ["Hex-Cell Modules", "Standard", fm(s350("module", "standard").t80_min) + " min", fm(s350("module", "standard").t90_min) + " min", s350("module", "standard").peak_temp_c.toFixed(1) + " °C", s350("module", "standard").aging_h25.toFixed(2)],
    ["Hex-Cell Modules", "Anode-aware", fm(s350("module", "optimized").t80_min) + " min", fm(s350("module", "optimized").t90_min) + " min", s350("module", "optimized").peak_temp_c.toFixed(1) + " °C", s350("module", "optimized").aging_h25.toFixed(2)],
  ], [2000, 1700, 1400, 1400, 1600, 1538], { boldFirst: true }),
  spacer(),
  bullet(b("Realistic baseline: "), "with a conventional BMS the pack charges like today's best 800 V cars (Kia EV6, Hyundai Ioniq 5/6, Porsche Taycan): 21.7 min for 10→90 % on a 300 kW charger, against 20.9 min for the best real car (Kia EV6) and 20.9–38.2 min across the real 800 V cars with complete curves", cite(10), "."),
  bullet(b("Ideal-information reference: "), "with direct access to the modelled anode potential and no estimation error or delay, the anode-aware BMS reaches 90 % in about 15 min on 350 kW. This is a simulation reference, not demonstrated production-BMS performance. On the same 300 kW charger as the real cars it takes 16.8 min, about 4 min faster than the best of them. An ablation of the four settings that differ between the two BMS strategies attributes 81–95 % of the gain to running a 15 mV safety margin to sodium plating instead of about 40 mV, and the rest to a warmer pack, in 18 of 20 tested conditions (in a 45 °C hot-soak at 350 kW, relaxed thermal limits also matter); whether a production BMS could hold 15 mV is the main open question."),
  bullet(b("With imperfect information: "), "a controller–plant–evaluator stress test with assumed estimation errors (Section 3.12) shows the answer depends on the operating condition. Warm and charger-limited, the tested errors matter little. Cold and plating-limited, they eat directly into the true margin. Single assumed errors need a 20–30 mV margin and most of the advantage survives; an assumed moderate combination needs about 41–45 mV, removing the gain from the smaller margin; at the same 40 mV setting and the same errors the anode-aware configuration stays 1–2 min faster, but both end slightly below the target, so that is a same-setting rather than a protection-matched comparison."),
  bullet(b("Tested everywhere: "), `${ROWS.length} sessions (every charger from 7 kW AC to 350 kW DC, −20 to 45 °C, with and without preconditioning) all pass ${nChecks} safety and sanity checks, and ${crossTested} cross-comparisons are consistent. The test caught and fixed one real bug.`),
  bullet(b("Biggest real-world risk: "), "arriving cold without preconditioning. At −20 °C the 350 kW session takes 29 min (anode-aware) to 59 min (standard) instead of 15 min."),
);

// 2 aim
add(h1("2. Aim and motivation"),
  h2("2.1 The problem"),
  p("Fast charging is limited by the anode, not the charger. During charging, ions are pushed into the anode; if the anode's potential drops to the metal's own potential (0 V vs Li or vs Na), metal plates onto the surface instead of entering the material. Plating permanently loses capacity and, in the worst case, grows dendrites that can short the cell", cite(8), ". Cold cells, high current and high state of charge all push the anode closer to that line. This is why real cars slow down (\"taper\") from about 50–60 % SOC, and why charging from 80 to 90 % often takes as long as 10 to 80 %."),
  h2("2.2 Why sodium-ion"),
  bullet("No lithium, cobalt or nickel: sodium is abundant and cheap, which reduces cost and supply-chain risk."),
  bullet("Better cold behaviour and safety than many lithium chemistries, and it can be transported at 0 V."),
  bullet("Energy density has become usable for cars: CATL's Naxtra sodium-ion cell, announced in April 2025, is quoted at 175 Wh/kg", cite(11), ". This project assumes a more conservative 150 Wh/kg power-optimised cell."),
  bullet("The weakness: the hard-carbon anode stores much of its sodium in a low-voltage \"plateau\" close to 0 V vs Na, and that region is diffusion-limited", cite(14), ". Charging fast without plating is therefore the central challenge."),
  h2("2.3 The idea being tested"),
  p("Most BMSs use fixed current tables with a generous safety margin. If the BMS instead estimates the anode potential of every cell in real time (ideal-information control: the simulation gives it the modelled anode potential directly) and preheats the pack, it can keep the current high until much closer to full. The project measures how much faster that is, what it costs in heat and aging, and how robust it is."),
);

// 3 journey
add(h1("3. What was built, phase by phase"),
  h2("3.1 Phase 1: lithium-ion baseline (learning the physics)"),
  p("Cell: LG M50 21700, 5 Ah NMC811/graphite, with the published Chen 2020 parameters", cite(2), ", simulated in PyBaMM", cite(1), " with the SPMe model and lumped thermal model. Plain constant-current / constant-voltage (CC-CV) charging, 25 °C:"),
  table(["CC rate", "10→80 %", "Temp at 80 %", "Peak temp", "Over 45 °C?"], [
    ["0.5C", "84.0 min", "29.0 °C", "29.2 °C", "no"], ["1.0C", "43.2 min", "36.3 °C", "37.6 °C", "no"],
    ["2.0C", "26.5 min", "47.3 °C", "60.3 °C", "yes"], ["2.5C", "25.6 min", "53.1 °C", "73.4 °C", "yes"], ["3.0C", "25.3 min", "53.6 °C", "73.9 °C", "yes"],
  ], [1600, 2000, 2000, 2000, 2038], { boldFirst: true }),
  caption("Past about 2C the cell hits 4.2 V almost at once, so constant-voltage mode limits the current anyway: more heat, almost no speed."),
  h2("3.2 Phase 2: optimised multi-step charging"),
  p("The 10→80 % window was split into 5 SOC bins with their own currents, searched with Optuna", cite(7), " under two limits: peak temperature ≤ 45 °C and anode potential ≥ 0 V vs Li (no plating)."),
  table(["", "Profile (C per SOC bin)", "10→80 %", "Peak temp", "Min anode potential"], [
    ["Best safe CC-CV", "0.80 flat", "52.7 min", "33.9 °C", "+2.2 mV"],
    ["Optimised", "1.43 / 1.41 / 1.39 / 1.03 / 0.92", "35.8 min", "44.0 °C", "+0.8 mV"],
  ], [1900, 2900, 1500, 1500, 1838], { boldFirst: true }),
  caption("32 % faster with both limits respected. Lesson carried forward: plating, not heat, is the binding limit."),
  h2("3.3 Phase 3: first sodium-ion pack (equivalent-circuit model)"),
  p("A first 110 kWh pack (200 blades of 46 mm, later redesigned) with an equivalent-circuit model per cell and sodium OCV data from Chayambuka 2022", cite(4), cite(5), ". It showed that a 350 kW charger could give about 15 min for 10→80 % at 25 °C, and 25.5 min at −10 °C without preconditioning. These numbers are superseded by the physics model below."),
  h2("3.4 Phase 4: physics-based sodium-ion cell and two pack layouts"),
  p(b("Cell model. "), "PyBaMM's sodium-ion DFN model", cite(3), cite(6), " (NVPF cathode / hard-carbon anode, Chayambuka 2022 thermodynamics", cite(4), cite(5), ") scaled from a 3 mAh lab cell to an EV cell. The lab cell reaches only about 30 % at 1C, so these documented changes make it a plausible power-optimised EV cell: reaction kinetics ×50, cathode diffusion ×10, hard-carbon diffusion ×40, 2.5 µm particles, thinner electrodes (48 / 51 µm), Arrhenius temperature dependence (absent in the base set), and a 4.3 V cut-off (NVPF's second plateau sits near 4.1–4.2 V)."),
  p("Plating onset with a 15 mV margin behaves like a commercial fast-charge cell:"),
  table(["Temperature", "2C", "3C", "4C"], [["25 °C", "68 % SOC", "62 %", "55 %"], ["45 °C", "none", "none", "72 %"], ["0 °C", "43 %", "35 %", "28 %"]], [2600, 2346, 2346, 2346], { boldFirst: true }),
  spacer(),
  p(b("Charge maps. "), "96 DFN runs (8 temperatures from −20 to 55 °C × 12 C-rates from 0.05C to 6C) give overpotential and anode potential as functions of temperature, current and SOC. 91 of 96 completed; 5 runs at −20 °C (0.05C, 3.5C, 4C, 5C and 6C) did not converge and are filled from neighbouring points. The pack simulator reads these maps for every cell at every 2-second step, which is how real BMS charge maps are built."),
  p(b("Two BMS strategies. "), "Standard: 40 mV plating margin, preconditioned to 30 °C, thermal derate from 45 °C, cutoff 55 °C, max 4C. Anode-aware (ideal-information control: it reads the modelled anode potential directly): 15 mV margin, preconditioned to 35 °C, derate from 50 °C, cutoff 58 °C, max 5C. The margin is enforced exactly on every cell at every step. A modelled SEI exposure index (equivalent hours at 25 °C, Arrhenius with 55 kJ/mol) makes the cost of heat visible."),
  h2("3.5 Phase 5: calibration against 1.28 million real charging snippets"),
  p("The EVBattery dataset", cite(9), " (three manufacturers, 1,278,277 charging snippets) was read with a restricted loader that allows only numpy data types. These are lithium-ion cars, so they set behavioural targets, not sodium parameters. Real-fleet facts: fast charging tapers at a median 77 % SOC; cell temperature spread is 3.3–3.6 °C at ≥ 0.5C; cars later flagged for plating or fire had about 3× the cell-voltage spread above 80 % SOC (p90 127 mV vs 45 mV)."),
  p("Three calibrations followed: tab, weld and busbar resistance (pack about 72 mΩ), ±25 % cell-to-cold-plate contact variation (cell spread now 1.5–4 °C) and 1 % initial SOC imbalance."),
  p(b("Important finding: "), "the sodium-ion voltage curve is very flat above 80 % SOC (about 1.6 mV per % SOC versus about 7 for NMC). A sodium BMS therefore cannot rely on cell-voltage spread to catch the imbalance that preceded failures in the fleet; it needs model-based or impedance-based detection."),
  h2("3.6 Phase 5b: benchmark against real 800 V cars"),
  p("Real charging curves for 14 800 V car variants from Morin et al. 2025 (digitized Fastned / InsideEVs curves, CC BY 4.0)", cite(10), ". Other sources were checked: open-ev-data", cite(15), " is out of date for 800 V cars, and EV Database's terms forbid automated collection, so it was only read by hand", cite(12), ". All times below are computed the same way from power-versus-SOC curves on a 300 kW charger."),
  table(["Vehicle (usable kWh)", "Peak kW", "10→80 %", "10→90 %"], [
    ...realCars.map(r => [r.vehicle.replace(/ \(\d+\.?\d* kWh\) \(/, " ("), r.peak_kw.toFixed(0), r.t10_80.toFixed(1), r.t10_90.toFixed(1)]),
    ...ours300.map(r => [r.vehicle.replace("Hexa-Blade", "THIS PROJECT:").replace(", 300 kW DC", ""), r.peak_kw.toFixed(0), r.t10_80.toFixed(1), r.t10_90.toFixed(1)]),
  ], [5038, 1500, 1500, 1600], { size: 17 }),
  caption("Times in minutes, 300 kW chargers; 12 of the 14 real car curves reach 90 %. With a standard BMS the pack sits among the best real cars; the anode-aware BMS is beyond all of them."),
  image(path.join(ROOT, "analysis/realcars/benchmark_800v.png"), 640),
  caption("Figure 1. Real 800 V cars versus this pack: power, power per kWh, and 10→90 % times."),
  p(b("Robustness check. "), "The literature describes slower sodium diffusion in the hard-carbon plateau than the base data has", cite(14), ", so the plateau diffusion was cut ×0.1 and ×0.03. The standard BMS slows from 21.7 to 25.4 min (consistent with real cars); the anode-aware result stays at 16.8–17.1 min. So the anode-aware advantage does not come from the diffusion data; Section 3.10 shows it comes mostly from the smaller plating margin."),
  h2("3.7 Phase 6: the 3D charging lab (web viewer)"),
  p("A web app written in Rust with the Bevy engine", cite(16), " and compiled to WebAssembly. It shows a glass car body, two drive units (one motor per axle, with gearbox, inverter, half-shafts, brakes and orange HV cables), and either pack layout with every cell animated from the simulation. In the default Charge view each cell is a hollow glass shell that fills with green liquid as its own SOC rises; the Temperature view colours cells by heat", cite(17), ". You can switch charger, conditions and BMS, see live readouts (including what is limiting the current), charts and a strategy comparison."),
  ...viewerShots(),
  h2("3.8 Phase 7: larger cells (October 2026)"),
  p("To make charging easier to see, and to test the effect of cell size, the cells were enlarged: blade 46 → 64 mm across flats and module cells 46 → 58 mm. The blade count cannot drop much below 200 because each blade is its own series cell in an 800 V pack (204 × 4.3 V = 877 V, under the 920 V charger limit), so blades became fatter and shorter (204 blades). The module pack went from 1,400 to 800 cells. Result: the anode-aware times were unchanged, cells run about 1 °C hotter, and the standard-BMS module pack got much faster (33.6 → 26.5 min to 90 %) because 4 parallel cells share current more evenly than 7."),
  h2("3.9 Phase 8: the full test matrix"),
  p(`Every combination was simulated: 2 packs × 6 chargers × 7 ambient temperatures (−20, −10, 0, 10, 25, 35, 45 °C) × with/without preconditioning × 2 BMS strategies = ${ROWS.length} sessions, each 10 → 90 %. Results are in Section 6.`),
  h2("3.10 Phase 9: ablation — which setting makes the anode-aware BMS faster?"),
  p("The two BMS strategies differ in four setting groups, so comparing them cannot say which setting causes the speed-up. A full 2⁴ factorial ran all 16 combinations (from the standard BMS with nothing switched to the anode-aware BMS with all four switched) on both packs, 250 and 350 kW, at 25 °C preconditioned, 0 °C / −20 °C cold-soaked and 35 °C / 45 °C hot-soaked: 20 cases, 320 runs. Shapley attribution splits each case's time saving fairly between the settings, including their interactions."),
  table(["Setting group", "Standard → anode-aware", "18 of 20 cases", "350 kW, 45 °C hot-soak (blade / module)"], [
    ["Plating margin", "40 → 15 mV", ablShare("margin", false), ablShare("margin", true)],
    ["Preconditioning / coolant setpoint", "30 / 30 → 35 / 33 °C", ablShare("precond", false), ablShare("precond", true)],
    ["Thermal derate start / cutoff", "45 / 55 → 50 / 58 °C", ablShare("thermal", false), ablShare("thermal", true)],
    ["Max C-rate", "4C → 5C", ablShare("crate", false), ablShare("crate", true)],
  ], [2900, 2200, 1900, 2638], { boldFirst: true }),
  spacer(),
  p("Why the last two usually contribute nothing: the 4C cap never binds (peak 3.4C), and crossing the 45 °C derate start is not the same as being limited by it. At 25 °C preconditioned the pack only passes 45 °C after about 64 % SOC, when the charger's power limit already holds the current near 2.7C, below what the standard derate still allows (about 3.0C at 47 °C); in cold-soaked cases the plating guard holds the current instead. In soaked cases no preconditioning happens, so the second group acts only through the coolant setpoint."),
  p(b("The hot exception. "), "Arriving hot-soaked at 45 °C on a 350 kW charger, the standard BMS spends 59 % (blade) and 42 % (module) of the session derating. There the relaxed thermal limits are worth about 2.9 minutes, and the 5C cap also helps, because in the simulator the derated current limit is the derate fraction times the C-rate cap. The price is a hotter pack: 53 °C instead of 50 °C (the modelled SEI exposure index is still lower because the session is shorter). A production version could keep the standard thermal limits and 4C cap and give up those minutes on hot days. At 250 kW, even at 45 °C, the thermal limits again contribute nothing. The shares are allocations for deterministic, seeded runs, not confidence intervals."),
  image(path.join(ROOT, "analysis/ablation/fig_attribution.png"), 620),
  caption("Figure 3. Minutes saved by each setting (Shapley attribution), 20 cases. Only the 350 kW / 45 °C hot-soak rows show thermal-limit (green) and C-rate (purple) contributions."),
  h2("3.11 Phase 10: does the reduced cell model match the full physics model?"),
  p("The pack simulator never solves the DFN per cell. It reads constant-current DFN lookup maps, lags the overpotential by 15 s, and reads the anode potential instantly. One cell was driven with identical current profiles in both models (isothermal, 0–45 °C): real BMS session traces, steps with rests, and 4C/1C pulses. This is model-to-model verification, not experimental validation of the cell; the full model card is in analysis/validation/MODEL_CARD.md."),
  table(["Current profile (25–45 °C)", "Worst error in the dangerous direction (map says the anode is safer than the DFN)"], [
    ["Anode-aware BMS sessions", "≤ 1.5 mV (session minimum agrees within 0.4 mV)"],
    ["Standard BMS sessions", "≤ 5.5 mV, during the current taper"],
    ["Steps with rests (3C → 0.5C cut)", "21–50 mV, just after the cut"],
    ["4C / 1C pulses", "29–60 mV"],
  ], [4300, 5338], { boldFirst: true }),
  spacer(),
  p("So the reduced model is adequate for the smooth tapering currents of the two BMS strategies: a cell held at the 15 mV margin sits at about 13.5–15 mV by the DFN. It is not adequate for controllers that cut current sharply, pause or pulse; those must be checked with full DFN runs. Two more limits: beyond the state of charge each constant-current map run reached, the map holds the anode value flat (optimistic; the pack BMS cannot go there because its voltage limit forbids it), and cold-weather dynamics are poorly represented."),
  image(path.join(ROOT, "analysis/validation/fig_dynamic_check.png"), 640),
  caption("Figure 4. Same current, two cell models: lookup maps (dashed) vs full DFN (solid)."),
  h2("3.12 Phase 11: stress test with imperfect information"),
  p("Question: how much of the ideal-information advantage survives when the BMS cannot see the true anode potential, with the protection envelope fixed? The simulator was split into three roles. The controller sees only declared measurements and estimates (temperature readings, an SOC estimate, an anode-potential estimate from its own model), with assumed bias, noise, resistance mismatch and delay; the true-anode clamp is disabled. The plant evolves on its true state. The evaluator measures violations on the true state and never corrects the controller. The errors are injected with assumed sizes: no estimator has been built. 664 runs: both packs, 250 and 350 kW, 25 °C preconditioned and 0 °C cold-soaked, 14 information scenarios, controller margins 15–40 mV."),
  table(["Information error (assumed)", "Margin needed to keep the true anode ≥ 15 mV", "Result at a 15 mV margin"], [
    ["Anode estimate +5 / +10 / +15 mV optimistic", "20 / 25 / 30 mV", "passes straight through (true 10 / 5 / 0 mV)"],
    ["Temperature sensor +3 °C", "25 mV", "true anode 5 mV (about 3.2 mV per °C)"],
    ["SOC estimate −3 % (thinks emptier)", "30 mV", "true anode 0 mV (about 5 mV per %)"],
    ["Cells 20 % more resistive than the model", "25 mV", "true anode 4 mV"],
    ["Anode noise 3–6 mV, 4 s delay", "20 mV", "small (the BMS acts on the lowest of hundreds of noisy estimates)"],
    ["Combined, assumed moderate (+5 mV, 3 mV noise, +2 °C, −2 % SOC, +10 % R, 1 step)", "about 41–45 mV", "crosses 0 mV below a 30 mV margin"],
    ["Combined, assumed harsh (+10 mV, 5 mV, +3 °C, −3 %, +20 %, 2 steps)", "66 mV (fallback rule)", "crosses 0 mV even at 40 mV; at 66 mV the target holds but the charge stalls before 80 %"],
  ], [3900, 2500, 3238], { size: 17, boldFirst: true }),
  spacer(),
  p(b("Fallback rule. "), "Each single error's effect was converted to an equivalent anode error; the rule margin = 15 mV + the sum of the declared errors' equivalents was derived on single errors and tested on the two held-out combinations. It was empirically successful on the tested combinations (true anode at or above 15 mV within 0.1 mV in every scenario), which is not a general safety guarantee. Under the assumed harsh set it holds the target but the charge stalls before 80 %: constraint-respecting, but task-incomplete."),
  p(b("Equal information. "), "Configured margins are not equivalent protections, so the standard BMS was also run facing the same errors (112 runs) and both were compared on what the evaluator measures. With the assumed moderate errors and the same 40 mV margin, the anode-aware configuration is 1.1–2.3 min faster in every case. This is a same-setting comparison, not a protection-matched one: both end slightly below the 15 mV target (worst 14.4 vs 14.3 mV), so it is not evidence of faster charging while meeting the target. The difference is plausibly from the warmer temperature settings, but that attribution comes from the perfect-information ablation and was not re-tested under injected errors. The standard BMS is not automatically protected either: under the assumed harsh set both controllers cross 0 mV briefly (worst −5.1 mV standard, −4.4 mV anode-aware). Under single errors the anode-aware controller with its fallback margin stays several minutes faster than the standard BMS facing the same error."),
  image(path.join(ROOT, "analysis/robustness/fig_time_cost.png"), 620),
  caption("Figure 5. 10→90 % time with equal information: the standard and anode-aware BMS both face the assumed moderate errors at a 40 mV margin; the perfect-information anode-aware time is shown for reference."),
  p(b("Warm vs cold. "), "The penalty depends on whether the plating guard is binding. Warm and charger-limited (blade, 350 kW, 25 °C preconditioned), the guard rarely binds: even the assumed moderate errors leave the true anode at 15–21 mV at every margin, and the harsh set dips only to about 13 mV. Cold-soaked (module, 350 kW, 0 °C), the guard binds for most of the session and every error turns into lost true margin. The worst-case margins above are set by the cold cases."),
  image(path.join(ROOT, "analysis/robustness/fig_tradeoff.png"), 620),
  caption("Figure 6. Charging time vs lowest true anode potential; each point is a controller margin (mV). Dashed: standard BMS time."),
  p(b("The reduced model's own error. "), "The plant here is the same lookup-map cell model. Its error relative to the full DFN is reported two ways. Pointwise optimistic error: 0.5–1.5 mV for smooth anode-aware tapers, 4–10 mV around current jumps from noisy estimates, 50–60 mV after sharp cuts. Difference between session minima: at most 1.4 mV in the replays. Brief errors can occur away from the session minimum, so the allowance is profile-dependent: about 1.5 mV for the existing smooth tapers (giving 22–32 mV for single errors and 43–47 mV for the moderate set), more or a current slew limit for jumpy controllers, and full DFN evaluation for controllers that cut, pause or pulse."),
  p(b("DFN re-check. "), "Noisy estimates make the current jump (up to 1.2C between 2 s steps), the profile where the reduced model is optimistic. Replaying the jumpiest runs through the full DFN moved the session minimum by at most 1.4 mV, with 4–10 mV momentary optimism around the jumps, so the margin conclusions hold within a few mV; a noisy estimator should add that much margin or limit how fast the current may change."),
  p(b("Thresholds. "), "Below 15 mV is a violation of the selected safety buffer; below 0 mV crosses the model's plating-risk criterion. Neither is evidence that sodium actually deposited, which would need a deposition model or an experiment. The combinations are assumed, so the results show sensitivity to those assumptions, not the error distribution of a real BMS."),
  p(b("What it establishes. "), "A controller–plant–evaluator benchmark that quantifies how estimation uncertainty changes charging performance across charger-limited and plating-limited conditions. The part of the advantage that comes from the smaller plating margin depends on estimator quality; a 1–2 min same-setting difference remains, plausibly from the temperature settings. The next bounded experiment: can a measurement-only estimator (a model-based observer vs a small history-based ML estimator, using only voltage, current, temperature and declared initialisation) recover charging performance in cold, plating-limited conditions while holding the declared modelled protection target, judged on held-out sessions? Experimental validation of internal potential is a separate hurdle that needs instrumented cells with micro-reference electrodes."),
  h2("3.13 Phase 12: measurement-only estimators in closed loop"),
  p("Question: can an estimator that sees only voltage, current, temperature and the declared initial SOC recover charging performance in cold, plating-limited conditions while holding the modelled 15 mV target? Ground truth is the full DFN (a higher-fidelity simulation reference, not experiment) on cells that differ from the nominal design (hard-carbon diffusivity, anode reaction rate and electrolyte conductivity varied): 20 training cases, 7 development-holdout cases, and a final family of 8 unseen interior cases at 5 °C and 0 °C. Three estimators: the nominal lookup map (what the pack BMS does), a model-based observer (lagged anode polarisation plus an online gain fitted to the voltage error), and a small neural network on history features. Margins were calibrated on training cases only."),
  p(b("Open loop is not closed loop. "), "The network was about 10 mV accurate (p99) predicting along recorded sessions, but in closed loop on the development holdout its optimistic error reached 163 mV p99 and the modelled anode stayed below the plating-risk criterion for a maximum estimated cumulative 24 minutes. Two causes were isolated: a decision-horizon mismatch in the training data (fix A) and training coverage (fix B, DAgger-inspired closed-loop data aggregation on training cells only). The development holdout guided these fixes, so the result was confirmed on the final family, simulated only after every model, calibration and script was hashed in a freeze manifest."),
  table(["Final family (16 runs each)", "Completed: N/M; median among completed runs", "Runs below 0 mV", "Worst modelled anode"], [
    ["Oracle feedback (true anode; reference)", "15/16; 65 min", "0", "10.6 mV"],
    ["Network v1 (open-loop trained)", "16/16; 70 min", "16", "−120 mV"],
    ["Network B (aggregated), calibrated 20 mV", "16/16; 78 min", "8", "−8 mV (max cumulative 0.7 min below 0 mV)"],
    ["Observer, calibrated 40 mV", "12/16; 139 min", "0 (and never below 15 mV)", "21.9 mV"],
    ["Standard map, 40 mV", "8/16; 127 min", "0", "8.9 mV"],
    ["Map, 15 mV (pack BMS method)", "16/16; 70 min", "8", "−16 mV"],
    ["Map, calibrated 56 mV", "0/16 (all stalled); —", "0", "25.1 mV"],
  ], [3500, 2300, 1900, 1938], { size: 17, boldFirst: true }),
  spacer(),
  p(b("Verdict: not yet. "), "No measurement-only estimator recovered charging performance while holding the declared target on the frozen family. Aggregation removed most of the severity (worst crossing −120 → −8 mV, maximum cumulative time below 0 mV 24 → 0.7 min) and completed every run, but 8 of 16 runs still crossed 0 mV briefly: the margin calibrated on training-cell rollouts (p99 4.9 mV) underestimated the tail on unseen cells (maximum across per-run p99 optimistic errors, 23 mV). The calibrated observer held both thresholds but charged slowly; a conservative map margin is safe but stalls. On cells that differ from the nominal design, the nominal-map controllers are not a safe baseline either (the standard 40 mV controller crossed 0 mV in 8 of 14 development-holdout runs). None of this is evidence of sodium deposition: the DFN does not model it."),
  image(path.join(ROOT, "analysis/estimator/fig_final.png"), 620),
  caption("Figure 7. Final confirmation family (frozen): runs that reached 90 % vs runs whose modelled anode crossed 0 mV, per controller."),
);

function viewerShots() {
  const shots = [
    ["viewer_module_fill.jpg", "Module pack, Charge view: green level = each cell's SOC (78 %)"],
    ["viewer_drive_unit.jpg", "Front drive unit: finned motor, gearbox, inverter, HV cables, brakes"],
  ].filter(([f]) => fs.existsSync(path.join(SHOTS, f)));
  if (!shots.length) return [];
  const cellW = Math.floor(CONTENT / shots.length);
  const none = { style: BorderStyle.NONE, size: 0, color: "FFFFFF" };
  const borders = { top: none, bottom: none, left: none, right: none };
  return [new Table({
    width: { size: cellW * shots.length, type: WidthType.DXA }, columnWidths: shots.map(() => cellW), layout: TableLayoutType.FIXED,
    rows: [new TableRow({ children: shots.map(([f, cap]) => new TableCell({
      width: { size: cellW, type: WidthType.DXA }, borders,
      children: [image(path.join(SHOTS, f), 290, "jpg"), new Paragraph({ alignment: AlignmentType.CENTER, children: [t(cap, { italics: true, size: 17, color: "555555" })] })],
    })) })],
  }), caption("Figure 2. Screenshots of the 3D charging lab.")];
}

// 4 pack spec
const spec = (a) => JSON.parse(fs.readFileSync(path.join(ROOT, `web_data/${a}/pack.json`), "utf8")).spec;
const sb = spec("blade"), sm = spec("module");
add(h1("4. Pack specification"),
  p("Both layouts hold 110 kWh gross / 102.3 kWh usable (3 % bottom and 4 % top buffer, similar to the Porsche Taycan's 105 kWh gross / 97 kWh usable pack", cite(13), "), are 800 V class, and put every cell on a cold plate."),
  table(["Item", "Hex-Blade (cell-to-pack)", "Hex-Cell Modules"], Object.keys(sb).map(k => [k, sb[k], sm[k]]), [2000, 3819, 3819], { size: 17, boldFirst: true }),
  spacer(),
  bullet(b("Hex-Blade: "), "2 layers × 34 lanes × 3 blades end to end, blades lying across the car. Cold plates at the bottom, between the layers and on top, so every blade touches two plates. Copper links join blades in a zig-zag series string."),
  bullet(b("Hex-Cell Modules: "), "20 modules of 10s4p upright hexagonal cells on one full-width bottom cold plate. Parallel cells share current by conductance, so warmer cells take more current."),
  bullet(b("Chargers modelled: "), "7 kW AC (93 % efficient), 15 kW AC (94 %), 50 kW DC legacy 400 V station with on-board boost (95 %, 125 A), 125 kW DC (200 A), 250 kW DC (375 A), 350 kW DC (500 A); all DC chargers 920 V max."),
);

// 5 model
add(h1("5. Simulation model and key assumptions"),
  p("Each charging session is simulated in 2-second steps (20 s for AC). For every cell and every step:"),
  bullet("Overpotential and anode potential come from the DFN charge maps for that cell's temperature, current and SOC, with a 15 s lag standing in for diffusion relaxation."),
  bullet("Heat = current × overpotential + tab resistance losses; heat flows to touching cells and to the cell's cold plate; coolant warms from front to rear."),
  bullet("The BMS sets the string current to the lowest of: charger power, charger current, cell C-rate cap, sodium-plating guard (anode ≥ margin at every cell), voltage limit, thermal derate."),
  table(["Parameter", "Value", "Basis"], [
    ["Usable window", "3 % – 96 % of electrode SOC", "Typical OEM buffers [12][13]"],
    ["Cell specific energy", "150 Wh/kg", "Below CATL Naxtra's 175 Wh/kg [11]"],
    ["Cell cut-off voltage", "4.3 V", "NVPF second plateau ~4.1–4.2 V [4]"],
    ["Plating margin", "40 mV standard / 15 mV anode-aware", "Engineering choice; the key open question"],
    ["Preconditioning target", "30 °C standard / 35 °C anode-aware", "Phase 3 trade-off study"],
    ["Thermal derate → cutoff", "45 → 55 °C / 50 → 58 °C", "Engineering estimate"],
    ["Tab + weld resistance", "0.12 mΩ for a 150 Ah cell (scales 1/capacity)", "Calibrated to fleet data [9]"],
    ["Busbars, fuse, contactors", "8 mΩ", "Calibrated to fleet data [9]"],
    ["Cold-plate contact spread", "±25 % (log-normal σ 0.25)", "Calibrated to fleet ΔT [9]"],
    ["Initial SOC imbalance", "1 % (1σ)", "Calibrated to fleet data [9]"],
    ["Coolant / chiller / heater", "0.5 kg/s (30 L/min) / 25 kW / 7 kW", "Engineering estimate"],
    ["Blade cell-to-plate conductance", "3.0 W/K per 1.40 m of blade per plate", "Lumped estimate (hexagon face + TIM)"],
    ["Module cell-to-plate conductance", "1.02 W/K (0.64 W/K at 46 mm, scaled by face area)", "Lumped estimate"],
  ], [2700, 3600, 3338], { size: 17, boldFirst: true }),
  caption("Thermal conductances and activation energies are engineering estimates, documented in the code."),
);

// 6 matrix
const lim = {}; ROWS.forEach(r => { lim[r.main_limiter] = (lim[r.main_limiter] || 0) + 1; });
add(h1("6. Full test matrix results"),
  p(`${ROWS.length} sessions, each 10 → 90 % SOC. Every session was checked against ${nChecks} rules, and sessions were compared with each other in ${crossTested} pairs.`),
  h2("6.1 Verdict"),
  table(["Check on every session", "Passed"], [
    ["Simulation completes and reaches 90 %", `${ROWS.length} / ${ROWS.length}`],
    ["Anode never below the plating margin (40 / 15 mV)", `${ROWS.length} / ${ROWS.length}`],
    ["Hottest cell below the BMS cutoff (55 / 58 °C)", `${ROWS.length} / ${ROWS.length}`],
    ["Power within the charger rating (+0.5 %)", `${ROWS.length} / ${ROWS.length}`],
    ["Current and voltage within charger limits (920 V)", `${ROWS.length} / ${ROWS.length}`],
    ["Energy delivered between stored energy and stored + 15 % losses", `${ROWS.length} / ${ROWS.length}`],
    ["No NaN values; SOC never goes backwards", `${ROWS.length} / ${ROWS.length}`],
  ], [7438, 2200]),
  spacer(),
  table(["Cross-run comparison", "Pairs", "Violations"], [
    ["A more powerful charger is never slower", matrix.cross_tested.faster_charger_never_slower, 0],
    ["At ≤ 10 °C, preconditioning is never slower than arriving cold", matrix.cross_tested.preconditioning_helps_in_cold, 0],
    ["The anode-aware BMS is never slower than the standard BMS", matrix.cross_tested.anode_aware_never_slower, 0],
    ["Without preconditioning, a warmer pack (−20 → 25 °C) is never slower", matrix.cross_tested.warmer_cold_soak_never_slower, 0],
    ["Preconditioned sessions vary < 5 % across ambients (true by construction, see 6.4)", matrix.cross_tested.preconditioned_insensitive_to_ambient, 0],
  ], [6638, 1500, 1500]),
  spacer(),
  p(b("Bug found and fixed. "), "On its first run the matrix showed that a cold-soaked pack briefly drew 3–7 % more power than the charger's rating (30 sessions). The power limit used the previous step's pack voltage, which climbs quickly in a cold pack. The simulator now re-solves the current until power is within the rating; a regression test was added."),
  p(`What limits charging (main limiter per session): charger power ${lim["Charger power"] || 0}, sodium-plating guard ${lim["Na-plating guard"] || 0}, thermal derate ${lim["Thermal derate"] || 0}. Hottest cell in any session: ${hottest.peak_temp_c.toFixed(1)} °C (${hottest.arch}, ${hottest.charger}, ${hottest.ambient_c} °C, no preconditioning). Highest pack voltage: ${Math.max(...ROWS.map(r => r.max_pack_v)).toFixed(0)} V.`),
  h2("6.2 Key findings"),
  bullet(b("Preconditioned, ambient temperature doesn't matter: "), "15.3 min (blade, anode-aware) at every ambient from −20 to 45 °C on 350 kW."),
  bullet(b("Skipping preconditioning in the cold is the biggest penalty: "), `blade at −20 °C, 350 kW: ${fm(get("blade", "350 kW DC", -20, false, "optimized").t90_min)} min anode-aware, ${fm(get("blade", "350 kW DC", -20, false, "standard").t90_min)} min standard, against 15.3 preconditioned. The plating guard limits current until the 7 kW heater warms the cells.`),
  bullet(b("The anode-aware BMS roughly halves the cold penalty: "), `blade at 0 °C, 350 kW: ${fm(get("blade", "350 kW DC", 0, false, "optimized").t90_min)} vs ${fm(get("blade", "350 kW DC", 0, false, "standard").t90_min)} min.`),
  bullet(b("Faster chargers pay off more with the anode-aware BMS: "), "250 → 350 kW cuts the blade time by 28 % (anode-aware) but 20 % (standard), because the standard BMS tapers early."),
  bullet(b("AC and 50 kW charging are charger-limited everywhere: "), "about 12.8 h (7 kW), 5.9 h (15 kW) and 1.8 h (50 kW); pack, BMS and temperature change these by under 3 %."),
  bullet(b("Hot-soaked packs charge slightly faster but run hottest: "), `up to ${hottest.peak_temp_c.toFixed(1)} °C at 45 °C ambient. With the standard BMS at 350 kW and 45 °C the thermal derate is active for part of the session.`),
  image(path.join(ROOT, "analysis/matrix/fig_t90_heatmaps.png"), 640),
  caption("Figure 8. Minutes from 10 to 90 % for every DC charger and condition. Left half of each panel: preconditioned; right half: arriving without preconditioning."),
  image(path.join(ROOT, "analysis/matrix/fig_cold_soak.png"), 620),
  caption("Figure 9. The cost of arriving without preconditioning (solid: anode-aware BMS, dashed: standard)."),
  image(path.join(ROOT, "analysis/matrix/fig_peak_temp.png"), 620),
  caption("Figure 10. Hottest cell during a 350 kW session."),
  h2("6.3 Every 10 → 90 % time"),
  p("Each cell shows preconditioned / cold-soaked (no preconditioning). Minutes, or hours where marked. Peak power, temperatures, anode margin, energy, aging and limiter for every session are in analysis/matrix/matrix.csv and REPORT.md."),
  ...["blade", "module"].flatMap(a => ["standard", "optimized"].flatMap(st => [h3(`${ARCH[a]} · ${STRAT[st]}`), t90Table(a, st), spacer()])),
  h2("6.4 Limits of this test"),
  bullet("Preconditioning is modelled as already done (the pack starts at 30 / 35 °C); the energy and time to precondition are not counted, and ambient only reaches the pack through a weak heat leak. The \"preconditioned is insensitive to ambient\" check therefore passes by construction."),
  bullet("−20 °C is at the edge of the cell model: 5 of the 96 DFN map runs at −20 °C (0.05C, 3.5C, 4C, 5C and 6C) did not converge, so cold-soaked −20 °C times are lower-confidence."),
  bullet("Heater, chiller and thermal conductances are estimates, calibrated to lithium-ion fleet behaviour, not a validated vehicle thermal system."),
);

// 7 limitations
add(h1("7. Limitations and honest uncertainties"),
  bullet(b("No hardware yet. "), "Every number comes from simulation. The cell model is a published lab-cell parameter set scaled to an EV cell; the scaling factors (kinetics ×50, diffusion ×10/×40) are assumptions that make it behave like a commercial fast-charge cell, not measured values."),
  bullet(b("The 15 mV margin. "), "The ablation (Section 3.10) attributes 81–95 % of the anode-aware advantage to it in 18 of 20 tested conditions. A production margin must also cover cell aging, observer error (a few mV), cell-to-cell spread and warranty conservatism (8 years / 160,000 km). Treat about 15 min as an ideal-information simulation reference, and about 21–22 min (standard BMS) as the conservative, real-world-validated figure. The stress test (Section 3.12) shows that with an assumed moderate combination of estimation errors a safe margin is about 41–45 mV, which removes the gain from the smaller margin in cold, plating-limited sessions; warm, charger-limited sessions are barely affected."),
  bullet(b("Reduced cell model. "), "The lookup maps match the full DFN for the existing BMS's smooth current tapers but are up to 50–60 mV optimistic after sharp current cuts or pulses (Section 3.11); new controllers must be checked with the full DFN."),
  bullet(b("Constant-current maps. "), "The charge maps come from constant-current DFN runs; the effect of changing current is approximated with a 15 s lag."),
  bullet(b("Aging. "), "Only a modelled SEI exposure index is computed; there is no full cycle-life or plating-damage model."),
  bullet(b("Hex cell format. "), "Hexagonal prism cells (64 mm × 0.46 m blades, 58 mm × 175 mm cans) are a design concept; manufacturability has not been studied."),
  bullet(b("Data licences. "), "EVBattery is CC BY-NC-SA (non-commercial use only)", cite(9), "; the real-car curves are CC BY 4.0 and require attribution", cite(10), "."),
);

// 8 reproduce + tools
add(h1("8. Tools, code and how to reproduce"),
  p(b("Software: "), "Python 3.12 with PyBaMM, NumPy, SciPy, Pandas, Matplotlib, Optuna and NiceGUI; Rust with Bevy 0.16, bevy_egui and egui_plot, built to WebAssembly with trunk. 36 automated tests (pytest) cover the physics maps, plating and voltage limits, energy balance, the export format and the charger-power regression."),
  table(["Step", "Command", "Time"], [
    ["Install", ".venv/Scripts/python -m pip install -r requirements.txt", "-"],
    ["Rebuild DFN charge maps", ".venv/Scripts/python -m ev_pack.charge_maps", "~4 min"],
    ["Export 72 viewer scenarios", ".venv/Scripts/python -m ev_pack.export", "~10 min"],
    ["Full 336-session test matrix", ".venv/Scripts/python analysis/matrix/run_matrix.py --workers 8", "~4 min"],
    ["BMS-setting ablation (320 runs)", ".venv/Scripts/python analysis/ablation/run_ablation.py --workers 8", "~6 min"],
    ["Model check vs full DFN", ".venv/Scripts/python analysis/validation/dynamic_check.py", "~1 min"],
    ["Imperfect-information stress test (664 runs)", ".venv/Scripts/python analysis/robustness/run_robustness.py --workers 8", "~7 min"],
    ["Estimator study (see analysis/estimator/REPORT.md)", "generate_step.py, train_eval.py, closed_loop.py, freeze.py, report.py", "~2 h"],
    ["Matrix report and figures", ".venv/Scripts/python analysis/matrix/report.py", "seconds"],
    ["Real-car benchmark", ".venv/Scripts/python analysis/realcars/benchmark.py", "~1 min"],
    ["Tests", ".venv/Scripts/python -m pytest -q", "~30 s"],
    ["3D viewer (web)", "cd viewer && trunk build --release", "~9 min"],
  ], [2500, 5638, 1500], { size: 17, boldFirst: true }),
  spacer(),
  table(["Folder / file", "What it contains"], [
    ["ev_pack/na_cell.py", "Sodium-ion DFN cell design (all scaling knobs)"],
    ["ev_pack/charge_maps.py", "Builds the 96-run charge maps"],
    ["ev_pack/architectures.py", "The two pack layouts, geometry and thermal links"],
    ["ev_pack/pack_sim.py", "Cell-by-cell pack charging simulation and BMS strategies"],
    ["ev_pack/export.py", "Writes web_data/ for the viewer"],
    ["analysis/evbattery/", "Real-fleet calibration (REPORT.md)"],
    ["analysis/realcars/", "Benchmark vs real 800 V cars (REPORT.md)"],
    ["analysis/matrix/", "Full test matrix, results and figures (REPORT.md, matrix.csv)"],
    ["analysis/ablation/", "Factorial ablation of the BMS settings (REPORT.md, attribution.json)"],
    ["analysis/validation/", "Model card: reduced model vs full DFN (MODEL_CARD.md)"],
    ["analysis/robustness/", "Imperfect-information stress test (REPORT.md)"],
    ["analysis/estimator/", "Measurement-only estimators, open vs closed loop, frozen final test (REPORT.md, freeze_manifest.json)"],
    ["viewer/", "Rust / Bevy 3D charging lab"],
    ["phase1_baseline.py, phase2_optimize.py", "Lithium-ion learning phases"],
  ], [3600, 6038], { size: 17, boldFirst: true }),
);

// 9 next steps
add(h1("9. Next steps (roadmap)"),
  bullet("Estimator next steps (Section 3.13): calibrate margins on closed-loop errors from held-out-style cells (e.g. nested cross-validation over parameter cases) so the error tail transfers; combine the observer's physics structure with the network's accuracy; add a current slew limit; test across seeds."),
  bullet("Then: joint optimisation of preconditioning and charging with honest energy and time accounting, evaluated with full DFN runs where the current changes sharply."),
  bullet("Model predictive control or reinforcement learning that adjusts the current live from predicted temperature and anode potential."),
  bullet("An aging model beyond the SEI index (plating-free cycle-life validation) and model-based imbalance detection for the flat sodium-ion voltage curve."),
  bullet("Model preconditioning itself: energy, time and route-based start, so cold-weather results include it."),
  bullet("A safe single-cell hardware rig (ESP32 microcontroller, INA226 current/voltage sensor, NTC thermistors) on a protected commercial sodium-ion cell, to check the model against reality."),
  bullet("Publish the 3D viewer as a static website."),
);

// 10 guidance
add(h1("10. Where I would value guidance"),
  p("These are the questions where an experienced battery or BMS engineer could help me most:"),
  bullet(b("Plating margin: "), "the ablation shows 81–95 % of the speed-up depends on it in 18 of 20 tested conditions, and the stress test shows an assumed moderate combination of estimation errors needs about 41–45 mV to hold the 15 mV target in cold sessions. What anode-potential margin do OEMs actually run in production fast-charge maps, and how is the observer error budgeted? Is 15 mV ever defensible, and with which sensing or estimation?"),
  bullet(b("Hard-carbon plateau kinetics: "), "is there published GITT or EIS data for a commercial fast-charge hard carbon that I could use instead of scaled lab parameters?"),
  bullet(b("Cell scaling: "), "are my scaling factors (kinetics ×50, diffusion ×10/×40, 48/51 µm electrodes) reasonable for a power-optimised EV cell? How would you validate them without a lab?"),
  bullet(b("Thermal design: "), "is 3.0 W/K per metre of blade per plate realistic for a 64 mm hexagonal blade on a corrugated plate? Is a 7 kW heater a sensible size for cold-soak charging?"),
  bullet(b("Cell format: "), "is a hexagonal prism cell (blade or can) manufacturable? What are the main obstacles compared with BYD-style blades or cylindrical cells?"),
  bullet(b("System voltage: "), "is 204s (877 V max) too close to the 920 V limit of 800 V chargers once tolerances and temperature are included?"),
  bullet(b("Validation path: "), "what is the safest and most useful first hardware experiment for a student project, and which test protocol should it follow?"),
  bullet(b("Career / next step: "), "which parts of this project are most worth developing further or presenting, and what is missing to make it credible to an industry reviewer?"),
);

// glossary
add(h1("11. Glossary"),
  table(["Term", "Meaning"], [
    ["SOC", "State of charge: how full the battery is, 0–100 %."],
    ["C-rate", "Current relative to capacity: 1C charges the cell in about one hour, 3C in about 20 minutes."],
    ["CC-CV", "Constant current until the voltage limit, then constant voltage while the current tapers."],
    ["DFN / P2D", "Doyle-Fuller-Newman physics-based cell model (electrochemistry inside the electrodes) [3]."],
    ["Plating", "Metal (lithium or sodium) depositing on the anode surface instead of entering it; caused by anode potential reaching 0 V vs the metal [8]."],
    ["Anode potential vs Na", "Voltage of the anode against sodium metal; the BMS keeps it above a safety margin (15 or 40 mV)."],
    ["NVPF", "Na3V2(PO4)2F3, the sodium-ion cathode material used [4]."],
    ["Hard carbon", "The sodium-ion anode material; stores much of its sodium in a low-voltage plateau."],
    ["BMS", "Battery management system: sets the charging current and protects the cells."],
    ["Preconditioning", "Heating or cooling the pack to its ideal temperature before charging."],
    ["Cold-soaked", "A pack that has cooled to ambient temperature (no preconditioning)."],
    ["Modelled SEI exposure index", "Equivalent hours at 25 °C of the main aging reaction; lower is better."],
    ["Cell-to-pack", "Cells placed directly in the pack without modules (like BYD's Blade)."],
    ["204s1p / 200s4p", "204 cells in series, 1 in parallel / 200 groups in series, each of 4 parallel cells."],
    ["800 V class", "Pack voltage around 700–880 V, allowing high charging power at lower current."],
  ], [2400, 7238], { size: 18, boldFirst: true }),
);

// references
add(h1("12. References"), ...refList);

// ------------------------------------------------------------------ document
const doc = new Document({
  creator: "Naman Dariya", title: "Hexa-Blade project report", description: "Fast-charging sodium-ion EV pack: design, simulation and results",
  styles: {
    default: { document: { run: { font: FONT, size: 21 } } },
    paragraphStyles: [
      { id: "Heading1", name: "Heading 1", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { font: FONT, size: 32, bold: true, color: ACCENT }, paragraph: { spacing: { before: 120, after: 200 }, outlineLevel: 0 } },
      { id: "Heading2", name: "Heading 2", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { font: FONT, size: 26, bold: true, color: ACCENT }, paragraph: { spacing: { before: 280, after: 120 }, outlineLevel: 1 } },
      { id: "Heading3", name: "Heading 3", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { font: FONT, size: 22, bold: true, color: "333333" }, paragraph: { spacing: { before: 200, after: 80 }, outlineLevel: 2 } },
    ],
  },
  numbering: { config: [{ reference: "bullets", levels: [{ level: 0, format: LevelFormat.BULLET, text: "•", alignment: AlignmentType.LEFT,
    style: { paragraph: { indent: { left: 567, hanging: 283 } } } }] }] },
  sections: [{
    properties: { page: { size: { width: PAGE_W, height: PAGE_H }, margin: { top: MARGIN, bottom: MARGIN, left: MARGIN, right: MARGIN } } },
    headers: { default: new Header({ children: [new Paragraph({ alignment: AlignmentType.RIGHT, children: [t("Hexa-Blade · sodium-ion fast-charging pack", { size: 16, color: "888888" })] })] }) },
    footers: { default: new Footer({ children: [new Paragraph({ alignment: AlignmentType.CENTER,
      children: [t("Page ", { size: 16, color: "888888" }), new TextRun({ children: [PageNumber.CURRENT], size: 16, color: "888888", font: FONT }),
        t(" of ", { size: 16, color: "888888" }), new TextRun({ children: [PageNumber.TOTAL_PAGES], size: 16, color: "888888", font: FONT })] })] }) },
    children: body,
  }],
});

Packer.toBuffer(doc).then(buf => { fs.writeFileSync(OUT, buf); console.log("wrote", OUT, buf.length, "bytes"); });
