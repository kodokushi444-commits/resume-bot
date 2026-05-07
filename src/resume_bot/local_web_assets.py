from __future__ import annotations


INDEX_HTML = """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Resume Bot Local</title>
  <link rel="icon" href="data:," />
  <link rel="stylesheet" href="/static/vendor/bootstrap/css/bootstrap.min.css" />
  <link rel="stylesheet" href="/static/vendor/bootstrap-icons/font/bootstrap-icons.min.css" />
  <style>
    :root {
      --bg: #f7f3ee;
      --panel: #fffaf4;
      --ink: #231f1b;
      --muted: #72665c;
      --line: rgba(35, 31, 27, 0.12);
      --accent: #cc5a1b;
      --accent-soft: #ffe1cf;
      --accent-2: #0f766e;
      --accent-2-soft: #d6f4ef;
      --danger: #9f1239;
      --radius: 22px;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: "Segoe UI", "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", sans-serif;
      color: var(--ink);
      background: var(--bg);
      min-height: 100vh;
      overflow-x: hidden;
    }
    .shell {
      width: min(1240px, calc(100vw - 28px));
      margin: 20px auto 32px;
      min-width: 0;
    }
    .hero {
      display: grid;
      grid-template-columns: 1.2fr 0.8fr;
      gap: 18px;
      margin-bottom: 18px;
      min-width: 0;
    }
    .card {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: var(--radius);
      min-width: 0;
    }
    .hero-main {
      padding: 26px 28px;
    }
    .hero-main h1 {
      margin: 0;
      font-size: clamp(32px, 5vw, 48px);
      line-height: 1;
      letter-spacing: -0.03em;
    }
    .hero-main p {
      margin: 12px 0 0;
      max-width: 700px;
      color: var(--muted);
      line-height: 1.65;
      font-size: 15px;
    }
    .hero-meta {
      padding: 22px;
      display: grid;
      gap: 12px;
      align-content: start;
      min-width: 0;
    }
    .meta-grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 10px;
    }
    .mini {
      border: 1px solid var(--line);
      border-radius: 16px;
      background: rgba(255,255,255,0.72);
      padding: 12px 14px;
      min-width: 0;
    }
    .mini label {
      display: block;
      font-size: 12px;
      color: var(--muted);
      margin-bottom: 6px;
    }
    .mini strong {
      font-size: 15px;
      word-break: break-all;
    }
    .layout {
      display: grid;
      grid-template-columns: 380px 1fr;
      gap: 18px;
      align-items: start;
      min-width: 0;
    }
    .stack {
      display: grid;
      gap: 18px;
      align-content: start;
      min-width: 0;
    }
    .panel {
      padding: 20px;
      min-width: 0;
    }
    .panel h2 {
      margin: 0 0 14px;
      font-size: 20px;
    }
    .panel h3 {
      margin: 0 0 12px;
      font-size: 16px;
    }
    .hint {
      color: var(--muted);
      font-size: 13px;
      line-height: 1.55;
    }
    .row {
      display: flex;
      gap: 10px;
      flex-wrap: wrap;
      align-items: center;
    }
    .grow {
      flex: 1 1 220px;
      min-width: 0;
    }
    .spacer { height: 8px; }
    .field {
      display: grid;
      gap: 8px;
      margin-bottom: 14px;
    }
    .field label {
      font-size: 13px;
      color: var(--muted);
    }
    .source-picker {
      display: grid;
      gap: 10px;
    }
    .source-option {
      display: flex;
      align-items: flex-start;
      gap: 10px;
      padding: 12px 14px;
      border: 1px solid var(--line);
      border-radius: 16px;
      background: rgba(255,255,255,0.78);
      cursor: pointer;
    }
    .source-option.disabled {
      opacity: 0.58;
      cursor: not-allowed;
      background: rgba(245, 240, 234, 0.92);
    }
    .source-option input[type="checkbox"] {
      margin-top: 3px;
      accent-color: var(--accent-2);
    }
    .source-option strong {
      display: block;
      font-size: 14px;
      margin-bottom: 4px;
    }
    .source-option span {
      display: block;
      color: var(--muted);
      font-size: 12px;
      line-height: 1.5;
    }
    input[type="text"], input[type="file"], textarea, select {
      width: 100%;
      border: 1px solid rgba(35, 31, 27, 0.14);
      background: rgba(255,255,255,0.82);
      color: var(--ink);
      border-radius: 14px;
      padding: 12px 14px;
      font-size: 14px;
      outline: none;
    }
    textarea {
      min-height: 120px;
      resize: vertical;
      line-height: 1.55;
    }
    button {
      border: 0;
      border-radius: 999px;
      padding: 11px 16px;
      font-size: 14px;
      font-weight: 600;
      cursor: pointer;
      transition: opacity 0.18s ease, background 0.18s ease;
    }
    button:disabled { opacity: 0.5; cursor: not-allowed; transform: none; }
    .primary {
      background: var(--accent);
      color: #fff9f6;
    }
    .secondary {
      background: var(--accent-soft);
      color: var(--accent);
    }
    .teal {
      background: var(--accent-2-soft);
      color: var(--accent-2);
    }
    .ghost {
      background: rgba(255,255,255,0.72);
      color: var(--ink);
      border: 1px solid var(--line);
    }
    .status-list, .timeline, .history, .source-runs {
      display: grid;
      gap: 10px;
    }
    .badge {
      display: inline-flex;
      align-items: center;
      border-radius: 999px;
      padding: 4px 9px;
      font-size: 12px;
      font-weight: 700;
      gap: 6px;
      margin: 0 8px 8px 0;
    }
    .ok { background: #d9f7eb; color: #116149; }
    .warn { background: #fff2c7; color: #8b5e00; }
    .bad { background: #ffd6dd; color: #8f1737; }
    .job-list {
      display: grid;
      gap: 14px;
      min-width: 0;
    }
    .job-card {
      border: 1px solid var(--line);
      border-radius: 18px;
      background: rgba(255,255,255,0.72);
      padding: 18px;
      display: grid;
      gap: 10px;
      min-width: 0;
    }
    .job-card h3 {
      margin: 0;
      font-size: 18px;
    }
    .job-meta {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      color: var(--muted);
      font-size: 13px;
    }
    .job-reasons {
      margin: 0;
      padding-left: 18px;
      color: var(--ink);
      line-height: 1.5;
    }
    .job-reasons li + li { margin-top: 4px; }
    .job-actions {
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
    }
    .review-workspace {
      display: grid;
      gap: 14px;
    }
    .review-toolbar {
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
      padding: 8px;
      border: 1px solid var(--line);
      border-radius: 16px;
      background: rgba(255,255,255,0.62);
    }
    .filter-chip {
      border: 1px solid var(--line);
      background: #fff;
      color: var(--ink);
      border-radius: 999px;
      padding: 8px 12px;
      font-weight: 800;
      cursor: pointer;
    }
    .filter-chip.is-active {
      background: var(--accent-2-soft);
      border-color: rgba(15, 118, 110, 0.28);
      color: var(--accent-2);
    }
    .review-list {
      display: grid;
      grid-template-columns: repeat(2, minmax(320px, 1fr));
      gap: 12px;
      align-items: start;
    }
    .review-list .job-card {
      height: 100%;
    }
    .badge.muted {
      background: rgba(35,31,27,0.08);
      color: var(--muted);
    }
    .badge.risk {
      background: #fff2df;
      color: #9a3412;
    }
    .ai-settings-grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(320px, 1fr));
      gap: 14px;
      align-items: start;
    }
    .secret-status {
      font-size: 12px;
      color: var(--muted);
      margin-top: 6px;
    }
    .summary-grid {
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 10px;
      margin-bottom: 14px;
    }
    .summary-box {
      border: 1px solid var(--line);
      border-radius: 16px;
      padding: 14px;
      background: rgba(255,255,255,0.68);
      min-width: 0;
    }
    .summary-box label {
      display: block;
      font-size: 12px;
      color: var(--muted);
      margin-bottom: 8px;
    }
    .summary-box strong {
      font-size: 22px;
    }
    .mono {
      font-family: "Cascadia Code", "JetBrains Mono", Consolas, monospace;
      font-size: 12px;
      color: var(--muted);
      word-break: break-all;
    }
    pre {
      margin: 0;
      padding: 14px;
      border-radius: 16px;
      border: 1px solid var(--line);
      background: rgba(30, 41, 59, 0.96);
      color: #dbe4ef;
      overflow: auto;
      max-width: 100%;
      min-width: 0;
      font-size: 12px;
      line-height: 1.55;
      white-space: pre-wrap;
      word-break: break-word;
      overflow-wrap: anywhere;
    }
    .empty {
      padding: 22px;
      border: 1px dashed var(--line);
      border-radius: 18px;
      text-align: center;
      color: var(--muted);
      background: rgba(255,255,255,0.46);
    }
    .note {
      padding: 12px 14px;
      border-radius: 16px;
      background: #fff7ec;
      border: 1px solid #ffd9ba;
      color: #8f3e08;
      font-size: 13px;
      line-height: 1.6;
      margin-bottom: 12px;
    }
    .status-strip {
      position: fixed;
      top: 14px;
      left: 50%;
      transform: translate(-50%, -14px);
      z-index: 60;
      width: min(760px, calc(100vw - 28px));
      padding: 14px 16px;
      border-radius: 18px;
      border: 1px solid #ffd9ba;
      background: #fff7ec;
      color: #8f3e08;
      line-height: 1.6;
      opacity: 0;
      pointer-events: none;
      transition: opacity 0.18s ease, transform 0.18s ease;
    }
    .status-strip.show {
      opacity: 1;
      transform: translate(-50%, 0);
    }
    .status-strip.ok {
      border-color: #bde7d6;
      background: #e8faf2;
      color: #116149;
    }
    .status-strip.bad {
      border-color: #f0b0bf;
      background: #fff0f4;
      color: #8f1737;
    }
    .status-strip.busy {
      border-color: #b8d4ff;
      background: #eef5ff;
      color: #184a8b;
    }
    .toast {
      position: fixed;
      top: 16px;
      right: 16px;
      z-index: 50;
      max-width: 320px;
      padding: 12px 14px;
      border-radius: 14px;
      border: 1px solid #bde7d6;
      background: #ecfbf4;
      color: #116149;
      font-size: 14px;
      line-height: 1.5;
      display: none;
    }
    .toast.show {
      display: block;
    }
    .detail-card {
      border: 1px solid var(--line);
      border-radius: 16px;
      padding: 16px;
      background: rgba(255,255,255,0.82);
      display: grid;
      gap: 12px;
      min-width: 0;
    }
    .detail-grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 10px;
    }
    .kv {
      border: 1px solid var(--line);
      border-radius: 14px;
      padding: 10px 12px;
      background: #fff;
    }
    .kv label {
      display: block;
      margin-bottom: 6px;
      font-size: 12px;
      color: var(--muted);
    }
    .kv strong {
      font-size: 14px;
      word-break: break-word;
    }
    .tag-list, .bullet-list {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
    }
    .tag {
      display: inline-flex;
      align-items: center;
      padding: 6px 10px;
      border-radius: 999px;
      border: 1px solid var(--line);
      background: #fff;
      font-size: 13px;
      color: var(--ink);
    }
    .tag-button {
      display: inline-flex;
      align-items: center;
      gap: 8px;
      padding-right: 8px;
    }
    .tag-button button {
      width: 18px;
      height: 18px;
      padding: 0;
      border-radius: 999px;
      border: 1px solid rgba(35, 31, 27, 0.15);
      background: #fff5ef;
      color: var(--accent);
      font-size: 12px;
      line-height: 1;
      font-weight: 700;
      cursor: pointer;
    }
    .tag-button button:hover {
      background: #ffe5d6;
    }
    .bullet-list {
      display: grid;
    }
    .bullet-item {
      border: 1px solid var(--line);
      border-radius: 14px;
      padding: 10px 12px;
      background: #fff;
      line-height: 1.6;
      font-size: 13px;
      min-width: 0;
      word-break: break-word;
      overflow-wrap: anywhere;
    }
    .subtle {
      font-size: 12px;
      color: var(--muted);
    }
    .gate-title {
      margin: 0;
      font-size: 18px;
    }
    .gate-message {
      line-height: 1.65;
      font-size: 14px;
    }
    .gate-details {
      border: 1px solid var(--line);
      border-radius: 14px;
      background: rgba(255,255,255,0.9);
      padding: 10px 12px;
    }
    .gate-details summary {
      cursor: pointer;
      color: var(--muted);
      font-size: 13px;
      font-weight: 600;
    }
    .gate-details[open] summary {
      margin-bottom: 10px;
    }
    .jd-fold {
      border: 1px solid var(--line);
      border-radius: 14px;
      background: rgba(255,255,255,0.72);
      padding: 10px 12px;
    }
    .jd-fold summary {
      cursor: pointer;
      color: var(--accent-2);
      font-size: 13px;
      font-weight: 700;
    }
    .jd-fold[open] summary {
      margin-bottom: 10px;
    }
    .jd-body {
      color: var(--ink);
      font-size: 13px;
      line-height: 1.7;
      white-space: pre-wrap;
      word-break: break-word;
      overflow-wrap: anywhere;
    }
    .legacy-panel {
      border-style: dashed;
      border-color: rgba(35, 31, 27, 0.18);
      background: rgba(255, 250, 244, 0.72);
    }
    .legacy-panel summary {
      cursor: pointer;
      color: var(--muted);
      font-weight: 700;
      list-style: none;
    }
    .legacy-panel summary::-webkit-details-marker {
      display: none;
    }
    .legacy-panel[open] summary {
      margin-bottom: 14px;
    }
    .workbench-lead {
      border-left: 4px solid var(--accent-2);
      background: var(--accent-2-soft);
      padding: 12px 14px;
      border-radius: 14px;
      color: #0d4e49;
      line-height: 1.65;
    }
    .session-strip {
      position: sticky;
      top: 14px;
      z-index: 20;
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 10px;
      padding: 12px;
      border: 1px solid rgba(35, 31, 27, 0.12);
      border-radius: 18px;
      background: rgba(255, 250, 244, 0.94);
      box-shadow: 0 18px 42px rgba(76, 56, 41, 0.08);
      backdrop-filter: blur(12px);
    }
    .flow-kv {
      min-width: 0;
      padding: 10px 12px;
      border-radius: 14px;
      background: rgba(255,255,255,0.7);
      border: 1px solid rgba(35, 31, 27, 0.08);
    }
    .flow-kv label {
      display: block;
      color: var(--muted);
      font-size: 11px;
      margin-bottom: 5px;
    }
    .flow-kv strong {
      display: block;
      font-size: 14px;
      line-height: 1.35;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }
    .thin-actions {
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
      align-items: center;
    }
    .version-chip {
      display: inline-flex;
      align-items: center;
      padding: 6px 10px;
      border-radius: 999px;
      border: 1px solid var(--line);
      background: #fff;
      color: var(--muted);
      font-size: 12px;
      margin-top: 10px;
    }
    body {
      background:
        radial-gradient(circle at top left, rgba(255, 218, 191, 0.72), transparent 28%),
        radial-gradient(circle at top right, rgba(214, 244, 239, 0.9), transparent 30%),
        linear-gradient(180deg, #f5efe7 0%, #f7f3ee 42%, #f3ede4 100%);
      font-family: "Segoe UI Variable", "PingFang SC", "Microsoft YaHei UI", sans-serif;
    }
    .shell {
      width: min(1380px, calc(100vw - 24px));
      margin: 0 auto;
      padding: 82px 0 28px;
    }
    .app-shell {
      display: grid;
      grid-template-columns: 280px minmax(0, 1fr);
      gap: 20px;
      align-items: start;
      min-width: 0;
    }
    .sidebar {
      position: sticky;
      top: 82px;
      padding: 18px;
      display: grid;
      gap: 16px;
      background:
        linear-gradient(180deg, rgba(255, 251, 246, 0.98), rgba(252, 245, 237, 0.94));
      box-shadow: 0 24px 60px rgba(73, 52, 36, 0.08);
    }
    .sidebar-brand {
      display: grid;
      grid-template-columns: 48px minmax(0, 1fr);
      gap: 12px;
      align-items: center;
    }
    .brand-mark {
      width: 48px;
      height: 48px;
      border-radius: 16px;
      background: linear-gradient(135deg, #d46a2d 0%, #b74e16 100%);
      color: #fff8f2;
      display: grid;
      place-items: center;
      font-weight: 800;
      letter-spacing: 0.04em;
      box-shadow: inset 0 1px 0 rgba(255,255,255,0.35);
    }
    .brand-copy strong {
      display: block;
      font-size: 18px;
    }
    .brand-copy span {
      display: block;
      margin-top: 4px;
      color: var(--muted);
      font-size: 13px;
      line-height: 1.5;
    }
    .sidebar-badges {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
    }
    .sidebar-nav {
      display: grid;
      gap: 8px;
    }
    .nav-button {
      width: 100%;
      border-radius: 16px;
      border: 1px solid transparent;
      background: rgba(255,255,255,0.7);
      color: var(--ink);
      padding: 12px 14px;
      text-align: left;
      font-weight: 700;
      box-shadow: inset 0 1px 0 rgba(255,255,255,0.28);
    }
    .nav-button:hover {
      background: rgba(255, 245, 236, 0.92);
      border-color: rgba(204, 90, 27, 0.16);
    }
    .nav-button.is-active {
      background: linear-gradient(180deg, rgba(255, 233, 217, 0.98), rgba(255, 244, 235, 0.98));
      color: var(--accent);
      border-color: rgba(204, 90, 27, 0.22);
      box-shadow: 0 14px 30px rgba(204, 90, 27, 0.12);
    }
    .sidebar-foot {
      padding-top: 6px;
      border-top: 1px solid var(--line);
    }
    .main-stage {
      display: grid;
      gap: 18px;
      min-width: 0;
    }
    .stage-head {
      padding: 24px 26px;
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      gap: 18px;
      align-items: start;
      background:
        linear-gradient(135deg, rgba(255, 251, 246, 0.98), rgba(246, 255, 252, 0.95));
      box-shadow: 0 26px 60px rgba(76, 56, 41, 0.08);
    }
    .stage-head h1 {
      margin: 6px 0 0;
      font-size: clamp(30px, 4.2vw, 42px);
      line-height: 1;
      letter-spacing: -0.04em;
    }
    .stage-head p {
      margin: 12px 0 0;
      max-width: 760px;
      color: var(--muted);
      line-height: 1.7;
      font-size: 14px;
    }
    .eyebrow {
      display: inline-flex;
      align-items: center;
      gap: 8px;
      padding: 5px 10px;
      border-radius: 999px;
      background: var(--accent-2-soft);
      color: var(--accent-2);
      font-size: 12px;
      font-weight: 700;
    }
    .stage-actions {
      display: grid;
      gap: 10px;
      justify-items: end;
      align-content: start;
    }
    .page {
      display: none;
      gap: 18px;
      min-width: 0;
    }
    .page.is-active {
      display: grid;
    }
    .page-grid {
      display: grid;
      grid-template-columns: minmax(0, 1.15fr) minmax(300px, 0.85fr);
      gap: 18px;
      align-items: start;
      min-width: 0;
    }
    .page-main, .page-side, .stack-tight {
      display: grid;
      gap: 18px;
      min-width: 0;
    }
    .review-grid {
      grid-template-columns: minmax(0, 1fr) minmax(0, 0.92fr);
    }
    .workbench-grid {
      display: grid;
      grid-template-columns: minmax(280px, 0.34fr) minmax(0, 1fr);
      gap: 18px;
      align-items: start;
      min-width: 0;
    }
    .boss-workbench-panel {
      min-width: 0;
    }
    .boss-control-grid {
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 10px 12px;
      align-items: end;
    }
    .boss-control-grid .field {
      margin-bottom: 0;
    }
    .boss-results-shell {
      display: grid;
      gap: 14px;
      min-width: 0;
    }
    .boss-results-grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(320px, 1fr));
      gap: 14px;
      align-items: start;
      min-width: 0;
    }
    .boss-results-grid .job-card {
      align-self: start;
      height: 100%;
    }
    .boss-context-grid {
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 10px;
      min-width: 0;
    }
    .boss-compact-list {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 10px;
    }
    .compact-grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 12px;
    }
    .section-head {
      display: flex;
      justify-content: space-between;
      gap: 12px;
      align-items: flex-start;
      flex-wrap: wrap;
      margin-bottom: 12px;
    }
    .section-head h2,
    .section-head h3 {
      margin-bottom: 0;
    }
    .plain-link {
      padding: 0;
      border: 0;
      background: transparent;
      color: var(--accent);
      font-weight: 700;
      cursor: pointer;
    }
    .overview-strip {
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 10px;
    }
    .summary-box strong {
      display: block;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }
    .helper-card {
      border: 1px solid var(--line);
      border-radius: 18px;
      padding: 14px 16px;
      background: rgba(255,255,255,0.7);
      color: var(--muted);
      line-height: 1.65;
    }
    .helper-card strong {
      display: block;
      margin-bottom: 6px;
      color: var(--ink);
      font-size: 15px;
    }
    .page-divider {
      height: 1px;
      background: var(--line);
      margin: 4px 0 0;
    }
    .status-grid-card {
      display: grid;
      gap: 12px;
    }
    @media (max-width: 1180px) {
      .app-shell,
      .page-grid,
      .review-grid,
      .workbench-grid,
      .overview-strip {
        grid-template-columns: 1fr;
      }
      .sidebar {
        position: static;
      }
      .stage-head {
        grid-template-columns: 1fr;
      }
      .stage-actions {
        justify-items: start;
      }
      .session-strip {
        position: static;
        grid-template-columns: repeat(2, minmax(0, 1fr));
      }
    }
    @media (max-width: 860px) {
      .shell {
        width: min(100vw - 16px, 100%);
        padding-top: 72px;
      }
      .sidebar {
        padding: 14px;
      }
      .sidebar-brand {
        grid-template-columns: 40px minmax(0, 1fr);
      }
      .brand-mark {
        width: 40px;
        height: 40px;
        border-radius: 14px;
      }
      .compact-grid,
      .detail-grid,
      .boss-control-grid,
      .boss-context-grid,
      .boss-compact-list,
      .boss-results-grid,
      .review-list,
      .ai-settings-grid,
      .summary-grid,
      .meta-grid {
        grid-template-columns: 1fr;
      }
      .status-strip {
        width: calc(100vw - 16px);
        top: 8px;
      }
      .session-strip {
        grid-template-columns: 1fr;
      }
      .shell {
        padding-left: 0;
        padding-right: 0;
      }
    }
    body[data-theme="sea"], body:not([data-theme]) {
      --bg: #f6f7f8;
      --panel: rgba(255, 255, 255, 0.92);
      --ink: #1f2933;
      --muted: #677b8f;
      --line: rgba(64, 78, 91, 0.14);
      --accent: #404e5b;
      --accent-contrast: #fff;
      --accent-soft: rgba(143, 160, 184, 0.24);
      --accent-2: #677b8f;
      --accent-2-contrast: #fff;
      --accent-2-soft: rgba(204, 201, 198, 0.42);
      --danger: #b45353;
      --theme-warn: #948f8c;
      --theme-shadow: 0 18px 48px rgba(64, 78, 91, 0.10);
    }
    body[data-theme="tuning"] {
      --bg: #f4f7f6;
      --panel: rgba(255, 255, 255, 0.93);
      --ink: #1f2d28;
      --muted: #5e6b49;
      --line: rgba(57, 107, 141, 0.15);
      --accent: #396b8d;
      --accent-contrast: #fff;
      --accent-soft: rgba(89, 157, 181, 0.20);
      --accent-2: #5e6b49;
      --accent-2-contrast: #fff;
      --accent-2-soft: rgba(176, 181, 127, 0.30);
      --danger: #a45c54;
      --theme-warn: #b0b57f;
      --theme-shadow: 0 18px 48px rgba(57, 107, 141, 0.11);
    }
    body[data-theme="forest"] {
      --bg: #fef9de;
      --panel: rgba(255, 255, 255, 0.92);
      --ink: #242f1a;
      --muted: #556136;
      --line: rgba(36, 47, 26, 0.16);
      --accent: #556136;
      --accent-contrast: #fff;
      --accent-soft: rgba(85, 97, 54, 0.18);
      --accent-2: #b2622c;
      --accent-2-contrast: #fff;
      --accent-2-soft: rgba(214, 151, 91, 0.22);
      --danger: #9d3b24;
      --theme-warn: #d6975b;
      --theme-shadow: 0 18px 48px rgba(36, 47, 26, 0.12);
    }
    body[data-theme="sorbet"] {
      --bg: #fbf7ff;
      --panel: rgba(255, 255, 255, 0.94);
      --ink: #394158;
      --muted: #667f97;
      --line: rgba(154, 203, 251, 0.34);
      --accent: #c5abd3;
      --accent-contrast: #2f2938;
      --accent-soft: rgba(197, 171, 211, 0.28);
      --accent-2: #fca7c4;
      --accent-2-contrast: #3b2b34;
      --accent-2-soft: rgba(182, 219, 251, 0.34);
      --danger: #c95678;
      --theme-warn: #fdc2d7;
      --theme-shadow: 0 18px 48px rgba(80, 92, 140, 0.11);
    }
    body[data-theme="sunrise"] {
      --bg: #9fe7f5;
      --panel: rgba(255, 255, 255, 0.94);
      --ink: #053f5c;
      --muted: #428ebd;
      --line: rgba(5, 63, 92, 0.18);
      --accent: #053f5c;
      --accent-contrast: #fff;
      --accent-soft: rgba(66, 142, 189, 0.18);
      --accent-2: #f27f0c;
      --accent-2-contrast: #053f5c;
      --accent-2-soft: rgba(247, 173, 25, 0.24);
      --danger: #b74f12;
      --theme-warn: #f7ad19;
      --theme-shadow: 0 18px 48px rgba(5, 63, 92, 0.13);
    }
    :root {
      --bs-body-bg: var(--bg);
      --bs-body-color: var(--ink);
      --bs-primary: var(--accent);
      --bs-border-color: var(--line);
      --bs-border-radius: 16px;
      --bs-border-radius-lg: 24px;
      --bs-link-color: var(--accent);
      --bs-link-hover-color: var(--accent-2);
    }
    body {
      background:
        linear-gradient(180deg, rgba(255, 255, 255, 0.88), rgba(255, 255, 255, 0.18) 42%, transparent),
        var(--bg);
      letter-spacing: 0;
    }
    .shell {
      width: min(1440px, calc(100vw - 40px));
      margin-top: 28px;
    }
    .app-shell {
      gap: 20px;
    }
    .card,
    .panel,
    .detail-card,
    .job-card,
    .summary-box,
    .mini,
    .source-option,
    .note,
    .helper-card {
      border-color: var(--line);
      border-radius: 22px;
      box-shadow: var(--theme-shadow);
    }
    .card,
    .panel {
      background: var(--panel);
      backdrop-filter: blur(14px);
    }
    .sidebar {
      border-radius: 28px;
      padding: 22px;
    }
    .brand-mark {
      background: var(--accent);
      box-shadow: 0 12px 28px rgba(0, 0, 0, 0.12);
    }
    .brand-copy strong,
    .stage-head h1,
    .panel h2 {
      letter-spacing: 0;
    }
    .brand-copy span,
    .hint,
    .mini label,
    .summary-box label,
    .flow-kv label {
      color: var(--muted);
    }
    .sidebar-nav {
      gap: 8px;
    }
    .nav-button,
    .filter-chip,
    button.primary,
    button.secondary,
    button.teal,
    button.ghost,
    .plain-link,
    .btn {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      gap: 8px;
      border-radius: 999px;
      min-height: 42px;
      border: 1px solid transparent;
      box-shadow: none;
      transition: transform 0.18s ease, box-shadow 0.18s ease, border-color 0.18s ease, background 0.18s ease, color 0.18s ease;
    }
    .nav-button:hover,
    .filter-chip:hover,
    button.primary:hover,
    button.secondary:hover,
    button.teal:hover,
    button.ghost:hover,
    .plain-link:hover,
    .btn:hover {
      transform: translateY(-1px);
      box-shadow: 0 12px 24px rgba(0, 0, 0, 0.09);
    }
    .nav-button {
      justify-content: flex-start;
      color: var(--ink);
      background: rgba(255, 255, 255, 0.64);
      border-color: transparent;
    }
    .nav-button.is-active {
      background: var(--accent);
      color: var(--accent-contrast);
      border-color: var(--accent);
    }
    .primary,
    .btn-primary {
      background: var(--accent);
      border-color: var(--accent);
      color: var(--accent-contrast);
    }
    .secondary,
    .btn-outline-secondary {
      background: var(--accent-soft);
      border-color: rgba(0, 0, 0, 0.03);
      color: var(--accent);
    }
    .teal,
    .btn-success {
      background: var(--accent-2);
      border-color: var(--accent-2);
      color: var(--accent-2-contrast);
    }
    .ghost,
    .btn-light {
      background: rgba(255, 255, 255, 0.70);
      border-color: var(--line);
      color: var(--ink);
    }
    .plain-link {
      min-height: 34px;
      padding: 6px 12px;
      background: transparent;
      color: var(--accent);
    }
    .row {
      --bs-gutter-x: 0;
      --bs-gutter-y: 0;
      margin-left: 0;
      margin-right: 0;
    }
    .row > * {
      width: auto;
      max-width: 100%;
      padding-left: 0;
      padding-right: 0;
    }
    .row > .grow {
      flex: 1 1 220px;
    }
    .main-stage button:not(.nav-button),
    .main-stage .btn,
    .main-stage .filter-chip {
      width: auto;
      min-width: max-content;
      max-width: 100%;
      padding: 10px 22px;
      line-height: 1.25;
      white-space: normal;
      overflow: visible;
      overflow-wrap: anywhere;
      text-align: center;
    }
    .main-stage button:not(.nav-button) i,
    .main-stage .btn i,
    .main-stage .filter-chip i {
      flex: 0 0 auto;
    }
    .main-stage button:not(.nav-button) {
      flex: 0 0 auto;
    }
    .main-stage .tag-button button {
      width: 22px;
      height: 22px;
      min-width: 22px;
      max-width: 22px;
      flex: 0 0 22px;
      padding: 0;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      border-radius: 50%;
      line-height: 1;
      font-size: 14px;
      overflow: hidden;
      white-space: nowrap;
      text-align: center;
    }
    input[type="text"],
    input[type="number"],
    input[type="file"],
    textarea,
    select,
    .form-control,
    .form-select {
      min-height: 44px;
      border-radius: 16px;
      border-color: var(--line);
      background-color: rgba(255, 255, 255, 0.82);
      color: var(--ink);
      transition: border-color 0.18s ease, box-shadow 0.18s ease, background 0.18s ease;
    }
    input:focus,
    textarea:focus,
    select:focus,
    .form-control:focus,
    .form-select:focus {
      border-color: var(--accent);
      box-shadow: 0 0 0 0.2rem rgba(64, 78, 91, 0.10);
      background: #fff;
    }
    .stage-head {
      border-radius: 30px;
      padding: 30px;
      align-items: center;
    }
    .stage-head h1 {
      font-size: clamp(34px, 5vw, 56px);
      line-height: 1.02;
      margin-top: 2px;
    }
    .eyebrow,
    .version-chip,
    .badge {
      border-radius: 999px;
      letter-spacing: 0;
    }
    .version-chip,
    .badge.warn,
    .status-strip.busy {
      background: var(--accent-2-soft);
      color: var(--accent-2);
    }
    .badge.ok,
    .status-strip.ok,
    .pill.ok {
      background: rgba(72, 139, 107, 0.16);
      color: #23734e;
    }
    .status-strip.bad,
    .badge.bad,
    .pill.bad {
      background: rgba(219, 136, 139, 0.18);
      color: var(--danger);
    }
    .status-strip,
    .toast {
      border: 1px solid var(--line);
      box-shadow: 0 16px 44px rgba(0, 0, 0, 0.11);
      border-radius: 22px;
      color: var(--ink);
    }
    .status-strip {
      background: #fff;
    }
    .status-strip.ok {
      background: #edf8f2;
    }
    .status-strip.bad {
      background: #fff0f4;
    }
    .status-strip.busy {
      background: #eef5ff;
    }
    .toast {
      background: rgba(255, 255, 255, 0.88);
    }
    .session-strip {
      border: 1px solid var(--line);
      border-radius: 26px;
      box-shadow: var(--theme-shadow);
      background: rgba(255, 255, 255, 0.78);
      backdrop-filter: blur(12px);
    }
    .flow-kv,
    .summary-box,
    .mini,
    .detail-card {
      background: rgba(255, 255, 255, 0.70);
    }
    .user-flow {
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 12px;
      margin-bottom: 16px;
    }
    .flow-step {
      display: grid;
      grid-template-columns: 40px minmax(0, 1fr);
      gap: 12px;
      align-items: center;
      padding: 16px;
      border: 1px solid var(--line);
      border-radius: 22px;
      background: rgba(255, 255, 255, 0.72);
      box-shadow: var(--theme-shadow);
    }
    .flow-step i {
      width: 40px;
      height: 40px;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      border-radius: 14px;
      background: var(--accent-soft);
      color: var(--accent);
      font-size: 19px;
    }
    .flow-step strong {
      display: block;
      font-size: 15px;
    }
    .flow-step span {
      display: block;
      margin-top: 2px;
      color: var(--muted);
      font-size: 12px;
      line-height: 1.4;
    }
    .review-toolbar {
      gap: 8px;
    }
    .filter-chip {
      border-color: var(--line);
      background: rgba(255, 255, 255, 0.72);
      color: var(--ink);
    }
    .filter-chip.is-active {
      border-color: var(--accent);
      background: var(--accent);
      color: var(--accent-contrast);
    }
    .boss-results-grid,
    .review-list {
      grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
    }
    .job-card {
      border-radius: 22px;
      padding: 18px;
    }
    .job-card h3 {
      font-size: 18px;
      line-height: 1.35;
    }
    .theme-fab {
      position: fixed;
      right: 22px;
      bottom: 22px;
      z-index: 80;
      width: 58px;
      height: 58px;
      padding: 0;
      border-radius: 50%;
      background: var(--accent);
      color: var(--accent-contrast);
      border: 1px solid rgba(255, 255, 255, 0.55);
      box-shadow: 0 16px 36px rgba(0, 0, 0, 0.18);
    }
    .theme-fab span {
      font-size: 12px;
      font-weight: 800;
    }
    .theme-fab i {
      font-size: 18px;
      line-height: 1;
    }
    .troubleshooting-fold {
      border: 1px solid var(--line);
      border-radius: 22px;
      background: rgba(255, 255, 255, 0.70);
      box-shadow: var(--theme-shadow);
      padding: 16px;
    }
    .troubleshooting-fold + .troubleshooting-fold {
      margin-top: 14px;
    }
    .troubleshooting-fold summary {
      cursor: pointer;
      font-weight: 800;
      color: var(--ink);
    }
    @media (max-width: 1180px) {
      .user-flow {
        grid-template-columns: repeat(2, minmax(0, 1fr));
      }
    }
    @media (max-width: 860px) {
      .shell {
        width: min(100vw - 18px, 100%);
        margin-top: 10px;
      }
      .user-flow {
        grid-template-columns: 1fr;
      }
      .stage-head,
      .sidebar,
      .card,
      .panel {
        border-radius: 22px;
      }
      .theme-fab {
        right: 14px;
        bottom: 14px;
      }
    }
  </style>
</head>
<body>
  <main class="shell">
    <div class="status-strip" id="global-status">页面已加载。先准备简历和 BOSS 环境，再开始采集。</div>
    <div class="toast" id="toast"></div>
    <div class="app-shell">
      <aside class="card sidebar">
        <div class="sidebar-brand">
          <div class="brand-mark">RB</div>
          <div class="brand-copy">
            <strong>Resume Bot</strong>
            <span>帮你从 BOSS 岗位里筛出更值得看的机会。</span>
          </div>
        </div>
        <div class="sidebar-badges">
          <span class="badge ok" id="resume-badge">简历：未上传</span>
          <span class="badge warn" id="boss-badge">BOSS：未准备</span>
        </div>
        <div class="sidebar-summary">
          <div class="mini">
            <label>本地页面</label>
            <strong><span id="runtime-port">unknown</span></strong>
          </div>
          <div class="mini">
            <label>本轮岗位</label>
            <strong id="sidebar-session">未建立</strong>
          </div>
          <div class="mini">
            <label>正在做</label>
            <strong id="sidebar-focus">等待采集</strong>
          </div>
        </div>
        <nav class="sidebar-nav">
          <button class="nav-button is-active" type="button" data-nav-page="workbench"><i class="bi bi-house"></i>首页</button>
          <button class="nav-button" type="button" data-nav-page="review"><i class="bi bi-card-checklist"></i>岗位审阅</button>
          <button class="nav-button" type="button" data-nav-page="profile"><i class="bi bi-person-vcard"></i>简历画像</button>
          <button class="nav-button" type="button" data-nav-page="settings"><i class="bi bi-sliders"></i>求职偏好</button>
          <button class="nav-button" type="button" data-nav-page="ai-settings"><i class="bi bi-stars"></i>AI 设置</button>
          <button class="nav-button" type="button" data-nav-page="system"><i class="bi bi-tools"></i>故障排查</button>
        </nav>
        <div class="sidebar-foot hint">
          日常只需要按首页流程走；出问题时再打开“故障排查”。
        </div>
      </aside>

      <section class="main-stage">
        <section class="card stage-head">
          <div>
            <div class="eyebrow">求职助手</div>
            <h1>让岗位筛选变轻松</h1>
            <p>上传简历，设好目标，然后把 BOSS 列表交给它整理。你只需要看推荐理由，再决定想投、不合适或稍后看。</p>
          </div>
          <div class="stage-actions">
            <div class="version-chip"><i class="bi bi-shield-check"></i> 本机运行</div>
            <button class="btn btn-outline-secondary secondary" id="llm-check-btn-top" type="button"><i class="bi bi-lightning-charge"></i>检查 AI</button>
          </div>
        </section>

        <section class="user-flow" aria-label="使用流程">
          <div class="flow-step"><i class="bi bi-file-earmark-person"></i><div><strong>准备资料</strong><span>上传简历，让系统先认识你</span></div></div>
          <div class="flow-step"><i class="bi bi-bullseye"></i><div><strong>设置目标</strong><span>城市、方向、学历要求一次保存</span></div></div>
          <div class="flow-step"><i class="bi bi-search"></i><div><strong>采集岗位</strong><span>从当前 BOSS 列表导入岗位</span></div></div>
          <div class="flow-step"><i class="bi bi-check2-circle"></i><div><strong>查看推荐</strong><span>按理由审阅并标记去向</span></div></div>
        </section>

        <section class="session-strip" id="boss-session-strip">
          <div class="flow-kv"><label>当前 session</label><strong id="flow-session">未建立</strong></div>
          <div class="flow-kv"><label>本轮条件</label><strong id="flow-condition">等待采集</strong></div>
          <div class="flow-kv"><label>当前阶段</label><strong id="flow-stage">未采集</strong></div>
          <div class="flow-kv"><label>当前进度</label><strong id="flow-progress">等待列表采集</strong></div>
        </section>

        <section class="page is-active" data-page="workbench">
          <section class="card panel">
            <div class="section-head">
              <div>
                <h2>今天的求职进度</h2>
                <div class="hint">首页只放下一步要用的信息：资料、目标、岗位采集和推荐结果。</div>
              </div>
            </div>
            <div class="overview-strip">
              <div class="summary-box"><label>岗位总数</label><strong id="job-count">0</strong></div>
              <div class="summary-box"><label>推荐机会</label><strong id="rec-count">0</strong></div>
              <div class="summary-box"><label>已做操作</label><strong id="history-count">0</strong></div>
              <div class="summary-box"><label>本轮编号</label><strong id="overview-session">未建立</strong></div>
            </div>
          </section>

          <div class="workbench-grid">
            <div class="stack-tight">
              <section class="card panel">
                <h2>准备资料</h2>
                <div class="field">
                  <label>支持 PDF / DOCX / JPG / PNG / TXT</label>
                  <input class="form-control" id="resume-file" type="file" />
                </div>
                <div class="row">
                  <button class="btn btn-primary primary" id="upload-btn" type="button"><i class="bi bi-upload"></i>上传并解析</button>
                  <button class="btn btn-light ghost" id="refresh-btn" type="button"><i class="bi bi-arrow-clockwise"></i>刷新</button>
                </div>
                <div class="spacer"></div>
                <div class="hint" id="resume-hint">还没导入简历。</div>
              </section>

              <section class="card panel">
                <h2>BOSS 准备状态</h2>
                <div class="hint">开始前确认登录状态正常；如果需要打开浏览器，这里会提示你。</div>
                <div class="spacer"></div>
                <div id="boss-gate-view" class="detail-card">
                  <div class="empty">正在检查 BOSS 状态...</div>
                </div>
                <div class="spacer"></div>
                <div class="row">
                  <button class="btn btn-light ghost" id="boss-check-btn" type="button"><i class="bi bi-arrow-clockwise"></i>重新检查</button>
                  <button class="btn btn-outline-secondary secondary" id="boss-action-btn" type="button" hidden><i class="bi bi-box-arrow-up-right"></i>打开登录浏览器</button>
                </div>
              </section>

              <section class="card panel">
                <div class="section-head">
                  <div>
                    <h2>目标摘要</h2>
                    <div class="hint">这里显示长期筛选目标；需要修改时去“求职偏好”。</div>
                  </div>
                  <button class="plain-link" type="button" data-open-page="settings"><i class="bi bi-pencil-square"></i>去设置</button>
                </div>
                <div id="settings-brief" class="detail-card">
                  <div class="empty">还没有设置。</div>
                </div>
              </section>
            </div>

            <div class="boss-results-shell">
              <section class="card panel boss-workbench-panel">
                <h2>采集岗位</h2>
                <div class="workbench-lead">在 BOSS 打开目标列表后，先导入列表，再补全职位详情并生成推荐。</div>
                <div class="spacer"></div>
                <div class="boss-control-grid">
                  <div class="field">
                    <label>BOSS 城市</label>
                    <input class="form-control" id="boss-workbench-city" type="text" placeholder="例如：深圳" />
                  </div>
                  <div class="field">
                    <label>BOSS 关键词</label>
                    <input class="form-control" id="boss-workbench-keyword" type="text" placeholder="例如：运营" />
                  </div>
                  <div class="field">
                    <label>学历快筛</label>
                    <select class="form-select" id="boss-workbench-degree-filter">
                      <option value="">不限学历</option>
                      <option value="大专">大专及以下</option>
                      <option value="本科">本科及以下</option>
                      <option value="硕士">硕士及以下</option>
                      <option value="博士">博士及以下</option>
                    </select>
                  </div>
                  <div class="field">
                    <label>正职 / 实习</label>
                    <select class="form-select" id="boss-workbench-employment-filter">
                      <option value="">正职和实习都看</option>
                      <option value="full_time">只看正职</option>
                      <option value="intern">只看实习</option>
                    </select>
                  </div>
                  <div class="field">
                    <label>列表上限</label>
                    <input class="form-control" id="boss-workbench-limit" type="number" min="1" max="120" value="45" />
                  </div>
                  <div class="field">
                    <label>推荐展示</label>
                    <input class="form-control" id="boss-workbench-review-limit" type="number" min="1" max="120" value="5" />
                  </div>
                </div>
                <div class="hint">快筛会随本轮保存。列表上限控制采集多少条，推荐展示只控制首页先露出多少条。</div>
                <div class="spacer"></div>
                <div class="row">
                  <button class="btn btn-primary primary" id="boss-workbench-capture-btn" type="button"><i class="bi bi-cloud-download"></i>采集岗位</button>
                  <button class="btn btn-outline-secondary secondary" id="boss-workbench-supplement-btn" type="button"><i class="bi bi-stars"></i>补全并推荐</button>
                </div>
                <div class="spacer"></div>
                <div id="boss-workbench-overview" class="detail-card">
                  <div class="empty">还没有可用的 BOSS 队列。先完成一次列表采集。</div>
                </div>
                <div class="spacer"></div>
                <div class="thin-actions">
                  <button class="btn btn-light ghost" id="boss-workbench-refresh-btn" type="button">刷新状态</button>
                  <button class="btn btn-outline-secondary secondary" id="boss-workbench-default-btn" type="button">查看推荐</button>
                  <button class="btn btn-success teal" id="boss-workbench-social-btn" type="button">只看社招</button>
                  <button class="btn btn-light ghost" type="button" data-open-page="settings">查看偏好</button>
                </div>
                <div class="spacer"></div>
                <div class="row" id="boss-workbench-profile-actions"></div>
                <div class="spacer"></div>
                <div id="boss-workbench-view" class="detail-card">
                  <div class="empty">还没有可用的 BOSS 队列。先完成一次列表采集。</div>
                </div>
              </section>
            </div>
          </div>
        </section>

        <section class="page" data-page="review">
          <section class="card panel review-workspace">
            <div class="section-head">
              <div>
                <h2>岗位审阅</h2>
                <div class="hint">把推荐理由、未推荐原因和可投状态放在一起看，标记后刷新也会保留。</div>
              </div>
              <button class="btn btn-light ghost" type="button" data-open-page="workbench"><i class="bi bi-arrow-left"></i>回到首页</button>
            </div>
            <div class="review-toolbar" id="review-filter-bar">
              <button class="filter-chip is-active" type="button" data-review-filter="all">全部</button>
              <button class="filter-chip" type="button" data-review-filter="hit">推荐</button>
              <button class="filter-chip" type="button" data-review-filter="miss">未推荐</button>
              <button class="filter-chip" type="button" data-review-filter="pending_detail">待补全</button>
              <button class="filter-chip" type="button" data-review-filter="unknown_status">可投状态未知</button>
              <button class="filter-chip" type="button" data-review-filter="unmarked">未标记</button>
              <button class="filter-chip" type="button" data-review-filter="saved">想投</button>
              <button class="filter-chip" type="button" data-review-filter="disliked">不合适</button>
              <button class="filter-chip" type="button" data-review-filter="deferred">稍后看</button>
            </div>
            <div class="review-list" id="review-workspace-list">
              <div class="empty">先在工作台加载一轮 BOSS 审阅结果。</div>
            </div>
          </section>
        </section>

        <section class="page" data-page="profile">
          <div class="page-grid">
            <section class="card panel">
              <h2>简历画像</h2>
              <div id="profile-view" class="empty">还没有解析结果。</div>
            </section>
            <section class="card panel">
              <h2>识别链路</h2>
              <pre id="extraction-view">还没有识别记录。</pre>
            </section>
          </div>
        </section>

        <section class="page" data-page="settings">
          <section class="card panel">
            <h2>求职偏好</h2>
            <div class="hint">这里保存长期生效的筛选标准，不直接触发采集或补抓。</div>
            <div class="spacer"></div>
            <div id="settings-view" class="empty">还没有设置。</div>
          </section>
        </section>

        <section class="page" data-page="ai-settings">
          <section class="card panel">
            <div class="section-head">
              <div>
                <h2>AI 设置</h2>
                <div class="hint">保存在本机私有配置文件里。API Key 不回显完整内容，留空表示继续沿用当前值。</div>
              </div>
              <button class="btn btn-outline-secondary secondary" id="ai-settings-refresh-btn" type="button"><i class="bi bi-arrow-clockwise"></i>刷新配置</button>
            </div>
            <div id="ai-settings-view" class="ai-settings-grid">
              <div class="empty">正在加载 AI 设置...</div>
            </div>
          </section>
        </section>

        <section class="page" data-page="system">
          <div class="page-grid">
            <div class="page-main">
              <section class="card panel">
                <div class="section-head">
                  <div>
                    <h2>故障排查</h2>
                    <div class="hint">平时不用看。只有 AI、采集或页面状态异常时，再到这里检查。</div>
                  </div>
                </div>
                <div class="meta-grid" id="status-grid"></div>
                <div class="spacer"></div>
                <div class="row">
                  <button class="btn btn-outline-secondary secondary" id="llm-check-btn" type="button"><i class="bi bi-lightning-charge"></i>检查 AI</button>
                </div>
              </section>

              <section class="card panel">
                <details class="troubleshooting-fold">
                  <summary>最近采集来源</summary>
                  <div class="spacer"></div>
                  <div class="source-runs" id="source-runs"></div>
                </details>
              </section>

              <section class="card panel">
                <details class="troubleshooting-fold">
                  <summary>最近页面记录</summary>
                  <div class="spacer"></div>
                  <div class="source-runs" id="frontend-logs"></div>
                </details>
              </section>
            </div>

            <div class="page-side">
              <section class="card panel">
                <details class="troubleshooting-fold">
                  <summary>最近操作记录</summary>
                  <div class="spacer"></div>
                  <div class="timeline" id="interaction-list"></div>
                </details>
              </section>

              <section class="card panel legacy-panel">
                <h2>旧入口</h2>
                <div class="hint">只在排查问题时使用；日常入口已经收在首页。</div>
                <div class="spacer"></div>
                <details class="troubleshooting-fold" data-legacy-controls="1">
                  <summary>展开旧抓取 / 推荐入口</summary>
                  <div class="spacer"></div>
                  <div class="field">
                    <label>本轮抓取数量</label>
                    <input id="fetch-limit" type="number" min="1" max="200" value="40" />
                    <div class="hint">每点一次“导入/抓取并推荐”就算一轮新的 session。对 BOSS 来说，先手动打开结果页，再导入当前页。</div>
                  </div>
                  <div class="field">
                    <label>抓取来源</label>
                    <div class="source-picker" id="fetch-source-options">
                      <div class="empty">正在加载来源选项...</div>
                    </div>
                  </div>
                  <div class="row">
                    <button class="teal" id="fetch-btn">导入/抓取并推荐</button>
                    <button class="ghost" id="dryrun-btn">只重算推荐</button>
                    <button class="secondary" id="export-btn">下载 Excel</button>
                  </div>
                  <div class="spacer"></div>
                  <div class="note">本地版默认是“想抓取就抓取”。没有自动定时。BOSS 抓取走本机浏览器环境，更适合真人登录态。</div>
                  <div class="note" id="fetch-summary">还没有执行过抓取。</div>
                  <div class="summary-grid">
                    <div class="summary-box"><label>发现企业</label><strong id="funnel-enterprise-count">0</strong></div>
                    <div class="summary-box"><label>发现岗位</label><strong id="funnel-discovered-count">0</strong></div>
                    <div class="summary-box"><label>规则通过</label><strong id="funnel-rules-pass-count">0</strong></div>
                    <div class="summary-box"><label>最终推荐</label><strong id="funnel-final-count">0</strong></div>
                  </div>
                  <pre id="fetch-report">还没有抓取报告。</pre>
                </details>
              </section>
            </div>
          </div>
        </section>
      </section>
    </div>
  </main>

  <button class="theme-fab" id="theme-toggle-btn" type="button" aria-label="切换配色">
    <i class="bi bi-palette"></i>
    <span id="theme-toggle-label">海</span>
  </button>
  <script src="/static/vendor/bootstrap/js/bootstrap.bundle.min.js"></script>
  <script>
    const userId = "me";
    let jobsOperationPoller = null;
    let expectedJobsOperation = null;
    let settingsEditorState = null;
    let lastDashboard = null;
    let bossWorkbenchState = { summary: null, review: null, supplement: null };
    let bossWorkbenchBusy = false;
    let reviewFilter = "all";
    let aiSettingsState = null;
    let aiModelLists = {};
    let aiFormDrafts = {};
    let activePageId = "workbench";
    const themeCycle = [
      { id: "sea", label: "海", name: "海的守望" },
      { id: "tuning", label: "潮", name: "潮风调音" },
      { id: "forest", label: "森", name: "森林暖金" },
      { id: "sorbet", label: "莓", name: "云莓晴空" },
      { id: "sunrise", label: "橙", name: "橙海晴蓝" },
    ];

    function applyTheme(themeId, options = {}) {
      const chosen = themeCycle.find((item) => item.id === themeId) || themeCycle[0];
      document.body.dataset.theme = chosen.id;
      const label = document.getElementById("theme-toggle-label");
      if (label) label.textContent = chosen.label;
      const button = document.getElementById("theme-toggle-btn");
      if (button) button.title = `当前配色：${chosen.name}`;
      if (options.persist !== false) {
        window.localStorage.setItem("resumeBotTheme", chosen.id);
      }
      return chosen;
    }

    function cycleTheme() {
      const current = document.body.dataset.theme || "sea";
      const index = themeCycle.findIndex((item) => item.id === current);
      const next = themeCycle[(index + 1 + themeCycle.length) % themeCycle.length];
      const chosen = applyTheme(next.id);
      showToast(`已切换配色：${chosen.name}`);
      logFrontend("theme_switched", { theme: chosen.id });
    }

    applyTheme(window.localStorage.getItem("resumeBotTheme") || "sea", { persist: false });

    function escapeHtml(value) {
      return String(value ?? "")
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;");
    }

    function prettyJson(value) {
      return JSON.stringify(value ?? {}, null, 2);
    }

    function dedupeValues(values) {
      const result = [];
      const seen = new Set();
      for (const raw of (values || [])) {
        const value = String(raw ?? "").trim();
        if (!value || seen.has(value)) continue;
        seen.add(value);
        result.push(value);
      }
      return result;
    }

    function splitTagInput(value) {
      return dedupeValues(String(value || "").split(/[\\n，,、;；|/]+/g));
    }

    function jobScopeFromSettings(settings) {
      const jobTypes = settings.job_types || [];
      const hasCampus = jobTypes.includes("校招");
      const hasSocial = jobTypes.includes("社招");
      if (hasCampus && !hasSocial) return "campus_only";
      if (!hasCampus && hasSocial) return "social_only";
      return "campus_social";
    }

    function jobTypesFromScope(scope) {
      if (scope === "campus_only") return ["校招"];
      if (scope === "social_only") return ["社招"];
      return ["校招", "社招"];
    }

    function normalizeSettingsForEditor(settings = {}) {
      const jobScope = ["campus_social", "campus_only", "social_only"].includes(settings.job_scope)
        ? settings.job_scope
        : jobScopeFromSettings(settings);
      return {
        preferred_roles: dedupeValues(settings.preferred_roles || []),
        preferred_cities: dedupeValues(settings.preferred_cities || []),
        preferred_keywords: dedupeValues(settings.preferred_keywords || []),
        excluded_keywords: dedupeValues([...(settings.excluded_keywords || []), ...(settings.avoided_roles || [])]),
        salary_min: Number(settings.salary_min || 0),
        salary_max: Number(settings.salary_max || 0),
        max_degree_requirement: settings.max_degree_requirement || "",
        campus_role_mode: settings.campus_role_mode || "full_time",
        job_scope: jobScope,
      };
    }

    async function api(path, options = {}) {
      const response = await fetch(path, {
        headers: {
          "content-type": options.body instanceof FormData ? undefined : "application/json",
          ...(options.headers || {}),
        },
        ...options,
      });
      if (!response.ok) {
        const text = await response.text();
        try {
          const payload = JSON.parse(text);
          throw new Error(payload.detail || text || ("HTTP " + response.status));
        } catch (error) {
          if (error instanceof SyntaxError) {
            throw new Error(text || ("HTTP " + response.status));
          }
          throw error;
        }
      }
      return response.json();
    }

    function setStatus(message, tone = "info", autoHideMs = 0) {
      const root = document.getElementById("global-status");
      root.textContent = message;
      root.className = "status-strip show";
      if (tone === "ok") root.classList.add("ok");
      if (tone === "bad") root.classList.add("bad");
      if (tone === "busy") root.classList.add("busy");
      if (setStatus._timer) {
        window.clearTimeout(setStatus._timer);
        setStatus._timer = null;
      }
      if (autoHideMs > 0) {
        setStatus._timer = window.setTimeout(() => {
          root.className = "status-strip";
        }, autoHideMs);
      }
    }

    function setBusy(message = "") {
      if (!message) {
        const root = document.getElementById("global-status");
        root.className = "status-strip";
        root.textContent = "正在处理中，请稍等...";
        return;
      }
      setStatus(message, "busy");
    }

    function showPage(pageId) {
      const pages = Array.from(document.querySelectorAll("[data-page]"));
      const availablePages = new Set(pages.map((node) => node.getAttribute("data-page")));
      const normalized = availablePages.has(pageId) ? pageId : "workbench";
      activePageId = normalized;
      pages.forEach((node) => {
        node.classList.toggle("is-active", node.getAttribute("data-page") === normalized);
      });
      document.querySelectorAll("[data-nav-page]").forEach((button) => {
        button.classList.toggle("is-active", button.getAttribute("data-nav-page") === normalized);
      });
    }

    function bindPageNavigation() {
      document.querySelectorAll("[data-nav-page]").forEach((button) => {
        button.addEventListener("click", () => {
          showPage(button.getAttribute("data-nav-page") || "workbench");
        });
      });
      document.querySelectorAll("[data-open-page]").forEach((button) => {
        button.addEventListener("click", () => {
          showPage(button.getAttribute("data-open-page") || "workbench");
        });
      });
      document.querySelectorAll("[data-review-filter]").forEach((button) => {
        button.addEventListener("click", () => {
          reviewFilter = button.getAttribute("data-review-filter") || "all";
          renderReviewWorkspace();
        });
      });
    }

    function showToast(message) {
      const toast = document.getElementById("toast");
      toast.textContent = message;
      toast.className = "toast show";
      window.clearTimeout(showToast._timer);
      showToast._timer = window.setTimeout(() => {
        toast.className = "toast";
      }, 2400);
    }

    function formatDurationMs(value) {
      const totalSeconds = Math.max(0, Math.round(Number(value || 0) / 1000));
      if (totalSeconds < 60) {
        return `${totalSeconds} 秒`;
      }
      const minutes = Math.floor(totalSeconds / 60);
      const seconds = totalSeconds % 60;
      return `${minutes} 分 ${seconds} 秒`;
    }

    function formatApplicationStatus(status) {
      const normalized = String(status || "").toLowerCase();
      if (normalized === "open") return "可投递";
      if (normalized === "closed") return "已结束";
      if (normalized === "pending") return "待上线";
      return "状态未识别";
    }

    function resolveJobLink(job) {
      const safeJob = job || {};
      const raw = safeJob.raw_payload || {};
      return String(
        safeJob.apply_url ||
        safeJob.url ||
        raw.job_url ||
        raw.detail_url ||
        "#"
      ).trim() || "#";
    }

    function formatDegreeRequirement(job) {
      const safeJob = job || {};
      const raw = safeJob.raw_payload || {};
      return String(
        safeJob.degree_requirement ||
        safeJob.degree_preference ||
        raw.degree_requirement ||
        raw.degreeName ||
        raw.jobDegree ||
        ""
      ).trim() || "瀛﹀巻鏈啓";
    }

    async function logFrontend(event, detail = {}) {
      try {
        await api("/api/frontend-log", {
          method: "POST",
          body: JSON.stringify({ user_id: userId, event, detail }),
        });
      } catch (error) {
        console.warn("frontend log failed", error);
      }
    }

    function humanBossBadge(value, fallback = "") {
      const text = String(value || fallback || "").trim();
      if (text === "Logged In") return "已登录";
      if (text === "Logged Out") return "未登录";
      return text;
    }

    function renderStatusBadges(status) {
      const items = [
        ["文本模型", status.llm_ready && !status.llm_warning, status.llm_warning || status.llm_summary],
        ["视觉/OCR", status.vision_ready, status.vision_summary],
        ["Tavily", status.tavily_ready, status.tavily_ready ? "已配置" : "未配置"],
        ["BOSS 接入", status.boss_ready, status.boss_summary],
        ["数据库", true, status.db_path],
        ["数据目录", true, status.data_dir],
      ];
      document.getElementById("status-grid").innerHTML = items.map(([label, ok, value]) => `
        <div class="mini">
          <label>${escapeHtml(label)}</label>
          <strong>${escapeHtml(value)}</strong>
          <div style="margin-top:8px"><span class="badge ${ok ? "ok" : "warn"}">${ok ? "已准备" : "待配置"}</span></div>
        </div>
      `).join("");
      document.getElementById("boss-badge").className = "badge " + (status.boss_ready ? "ok" : "warn");
      document.getElementById("boss-badge").textContent = "BOSS：" + humanBossBadge(status.boss_badge, status.boss_ready ? "已准备" : "未准备");
      if (status.llm_warning) {
        setStatus(status.llm_warning, "bad");
      }
    }

    function bossGateTone(gate) {
      if (gate?.can_start) return "ok";
      if (gate?.status === "security_verify") return "bad";
      return "warn";
    }

    function bossGateStatusTone(gate) {
      if (gate?.can_start) return "ok";
      if (gate?.status === "security_verify") return "bad";
      return "bad";
    }

    function selectedFetchSourceIds() {
      return Array.from(
        document.querySelectorAll('input[name="fetch-source"]:checked')
      ).map((node) => node.value);
    }

    function bossGateSourceGroups() {
      return lastDashboard?.status?.boss_gate_source_groups || ["boss"];
    }

    function selectedSourcesNeedBossGate() {
      const required = new Set(bossGateSourceGroups());
      return selectedFetchSourceIds().some((value) => required.has(value));
    }

    function updateFetchGateState() {
      const fetchButton = document.getElementById("fetch-btn");
      if (!fetchButton) return;
      const gate = lastDashboard?.status?.boss_gate || {};
      const needsBossGate = selectedSourcesNeedBossGate();
      const blocked = needsBossGate && !gate.can_start;
      fetchButton.disabled = !!blocked;
      fetchButton.title = blocked ? (gate.message || "当前 BOSS 状态还不能开始抓取。") : "";
    }

    function bindFetchSourceChangeHandlers() {
      document.querySelectorAll('input[name="fetch-source"]').forEach((node) => {
        node.addEventListener("change", () => {
          updateFetchGateState();
        });
      });
    }

    function syncBossGateActionButton(gate) {
      const button = document.getElementById("boss-action-btn");
      if (!button) return;
      const action = gate?.action_kind || "";
      const label = gate?.action_label || "";
      button.dataset.action = action;
      button.disabled = false;
      if (!action || !label) {
        button.hidden = true;
        button.title = "";
        button.textContent = "打开登录浏览器";
        return;
      }
      button.hidden = false;
      button.title = gate.action_hint || "";
      button.textContent = label;
    }

    function renderBossGate(status) {
      const root = document.getElementById("boss-gate-view");
      const gate = status?.boss_gate || {};
      if (!gate || !gate.status) {
        root.innerHTML = '<div class="empty">BOSS 检查尚未完成。</div>';
        syncBossGateActionButton(null);
        return;
      }
      const tone = bossGateTone(gate);
      const details = gate.details || {};
      const checkedAt = gate.checked_at ? `<div class="mono">最近检查：${escapeHtml(gate.checked_at)}</div>` : "";
      const actionHint = gate.action_hint ? `<div class="note">${escapeHtml(gate.action_hint)}</div>` : "";
      root.innerHTML = `
        <div class="row" style="justify-content:space-between;align-items:flex-start">
          <div>
            <h3 class="gate-title">${escapeHtml(gate.title || "BOSS 检查")}</h3>
            <div class="gate-message">${escapeHtml(gate.message || "当前还没有可用状态。")}</div>
          </div>
          <span class="badge ${tone}">${escapeHtml(humanBossBadge(gate.badge, "先别抓"))}</span>
        </div>
        ${actionHint}
        ${checkedAt}
        <details class="gate-details">
          <summary>查看详情</summary>
          <pre>${escapeHtml(prettyJson(details))}</pre>
        </details>
      `;
      syncBossGateActionButton(gate);
    }

    function renderTimeline(items) {
      const root = document.getElementById("interaction-list");
      if (!items.length) {
        root.innerHTML = '<div class="empty">你还没有标记过岗位。</div>';
        return;
      }
      root.innerHTML = items.map((item) => `
        <div class="job-card">
          <strong>${escapeHtml(item.action)}</strong>
          <div class="mono">${escapeHtml(item.created_at)}</div>
          <div>${escapeHtml(item.notes || "无备注")}</div>
          <div class="mono">${escapeHtml(item.fingerprint)}</div>
        </div>
      `).join("");
    }

    function renderSourceRuns(items) {
      const root = document.getElementById("source-runs");
      if (!items.length) {
        root.innerHTML = '<div class="empty">还没有抓取记录。</div>';
        return;
      }
      root.innerHTML = items.map((item) => {
        let detail = {};
        try { detail = JSON.parse(item.detail_json || "{}"); } catch (error) { detail = {}; }
        return `
          <div class="job-card">
            <div class="row">
              <strong>${escapeHtml(item.source_name)}</strong>
              <span class="badge ${item.status === "ok" ? "ok" : "bad"}">${escapeHtml(item.status)}</span>
            </div>
            <div class="mono">${escapeHtml(item.started_at)} -> ${escapeHtml(item.finished_at)}</div>
            <pre>${escapeHtml(prettyJson(detail))}</pre>
          </div>
        `;
      }).join("");
    }

    function renderFetchSummary(run) {
      const root = document.getElementById("fetch-summary");
      if (!run || !Object.keys(run).length) {
        root.textContent = "还没有执行过抓取。";
        return;
      }
      root.textContent = run.summary || "最近一次抓取已经完成。";
      document.getElementById("fetch-report").textContent = prettyJson(run);
    }

    function renderFetchFunnel(funnel) {
      const safe = funnel || {};
      document.getElementById("funnel-enterprise-count").textContent = String(safe.enterprise_count || 0);
      document.getElementById("funnel-discovered-count").textContent = String(safe.discovered_job_count || 0);
      document.getElementById("funnel-rules-pass-count").textContent = String(safe.rules_passed_count || 0);
      document.getElementById("funnel-final-count").textContent = String(safe.final_recommendation_count || 0);
    }

    function renderFetchSources(items, selectedIds) {
      const root = document.getElementById("fetch-source-options");
      if (!items.length) {
        root.innerHTML = '<div class="empty">当前没有可选来源。</div>';
        return;
      }
      const selectedSet = new Set((selectedIds || []).filter(Boolean));
      root.innerHTML = items.map((item) => {
        const disabled = !!item.disabled;
        const checked = selectedSet.size
          ? selectedSet.has(item.id) && !disabled
          : !!item.default_checked;
        const helperText = disabled && item.disabled_reason
          ? `${item.description || ""} ${item.disabled_reason}`.trim()
          : (item.description || "");
        return `
          <label class="source-option ${disabled ? "disabled" : ""}" title="${escapeHtml(item.disabled_reason || "")}">
            <input type="checkbox" name="fetch-source" value="${escapeHtml(item.id)}" ${checked ? "checked" : ""} ${disabled ? "disabled" : ""} />
            <div>
              <strong>${escapeHtml(item.label || item.id)}</strong>
              <span>${escapeHtml(helperText)}</span>
            </div>
          </label>
        `;
      }).join("");
    }

    function renderFrontendLogs(items) {
      const root = document.getElementById("frontend-logs");
      if (!items.length) {
        root.innerHTML = '<div class="empty">还没有网页动作日志。</div>';
        return;
      }
      root.innerHTML = items.slice().reverse().map((item) => `
        <div class="job-card">
          <strong>${escapeHtml(item.event || "unknown")}</strong>
          <div class="mono">${escapeHtml(item.timestamp || "")}</div>
          <pre>${escapeHtml(prettyJson(item.detail || item))}</pre>
        </div>
      `).join("");
    }

    function syncSettingsFormToState() {
      if (!settingsEditorState) {
        settingsEditorState = normalizeSettingsForEditor({});
      }
      const salaryMin = document.getElementById("settings-salary-min");
      const salaryMax = document.getElementById("settings-salary-max");
      const degree = document.getElementById("settings-degree");
      const roleMode = document.getElementById("settings-role-mode");
      const jobScope = document.getElementById("settings-job-scope");
      if (salaryMin) settingsEditorState.salary_min = Number(salaryMin.value || 0);
      if (salaryMax) settingsEditorState.salary_max = Number(salaryMax.value || 0);
      if (degree) settingsEditorState.max_degree_requirement = degree.value || "";
      if (roleMode) settingsEditorState.campus_role_mode = roleMode.value || "full_time";
      if (jobScope) settingsEditorState.job_scope = jobScope.value || "campus_social";
    }

    function renderEditorTags(field, label, placeholder) {
      const values = settingsEditorState?.[field] || [];
      const tags = values.length
        ? values.map((value) => `
            <span class="tag tag-button">
              ${escapeHtml(value)}
              <button type="button" onclick='removeEditorTag(${JSON.stringify(field)}, ${JSON.stringify(value)})' title="删除">×</button>
            </span>
          `).join("")
        : `<span class="subtle">未设置</span>`;
      return `
        <div>
          <div class="subtle">${escapeHtml(label)}</div>
          <div class="tag-list">${tags}</div>
          <div class="row" style="margin-top:8px">
            <input class="grow" id="settings-input-${escapeHtml(field)}" type="text" placeholder="${escapeHtml(placeholder)}" />
            <button class="ghost" type="button" onclick='addEditorTag(${JSON.stringify(field)})'>添加</button>
          </div>
        </div>
      `;
    }

    function renderTagList(values, emptyText = "未设置", options = {}) {
      const deduped = dedupeValues(values);
      if (!deduped.length) {
        return `<span class="subtle">${escapeHtml(emptyText)}</span>`;
      }
      const field = options.field || "";
      const removable = !!options.removable && !!field;
      return deduped.map((value) => {
        const disableRemove = !!options.keepAtLeastOne && deduped.length <= 1;
        if (!removable) {
          return `<span class="tag">${escapeHtml(value)}</span>`;
        }
        return `<span class="tag tag-button">${escapeHtml(value)}<button type="button" onclick="removeSettingItem('${escapeHtml(field)}','${escapeHtml(value)}')" title="删除" ${disableRemove ? "disabled" : ""}>×</button></span>`;
      }).join("");
    }

    function renderBulletList(values, emptyText = "暂无") {
      if (!values || !values.length) {
        return `<div class="subtle">${escapeHtml(emptyText)}</div>`;
      }
      return values.map((value) => `<div class="bullet-item">${escapeHtml(value)}</div>`).join("");
    }

    function renderProfileCard(profile, extractionInfo) {
      if (!profile || !Object.keys(profile).length) {
        return '<div class="empty">还没有解析结果。</div>';
      }
      const rawSections = profile.raw_sections || {};
      const parseMethod = rawSections._parse_method || extractionInfo?.parse_method || "unknown";
      const internshipPreview = rawSections["实习经历"] || rawSections["工作经历"] || "";
      const internshipShort = internshipPreview ? internshipPreview.slice(0, 420) + (internshipPreview.length > 420 ? "..." : "") : "";
      return `
        <div class="detail-card">
          <div class="detail-grid">
            <div class="kv"><label>姓名</label><strong>${escapeHtml(profile.name || "未识别")}</strong></div>
            <div class="kv"><label>毕业年份</label><strong>${escapeHtml(profile.graduation_year || "未识别")}</strong></div>
            <div class="kv"><label>学校</label><strong>${escapeHtml(profile.school || "未识别")}</strong></div>
            <div class="kv"><label>专业</label><strong>${escapeHtml(profile.major || "未识别")}</strong></div>
            <div class="kv"><label>学历</label><strong>${escapeHtml(profile.degree || "未识别")}</strong></div>
            <div class="kv"><label>解析方式</label><strong>${escapeHtml(parseMethod)}</strong></div>
          </div>
          <div>
            <div class="subtle">目标岗位</div>
            <div class="tag-list">${renderTagList(profile.target_roles, "未识别")}</div>
          </div>
          <div>
            <div class="subtle">技能标签</div>
            <div class="tag-list">${renderTagList((profile.skills || []).slice(0, 10), "未识别")}</div>
          </div>
          <div>
            <div class="subtle">经历摘要</div>
            <div class="bullet-list">${renderBulletList((profile.experiences || []).slice(0, 3), "未识别")}</div>
          </div>
          <div>
            <div class="subtle">实习/工作原文预览</div>
            <div class="bullet-item">${escapeHtml(internshipShort || "未识别")}</div>
          </div>
        </div>
      `;
    }

    function renderSettingsBrief(settings) {
      const root = document.getElementById("settings-brief");
      if (!root) return;
      const editor = normalizeSettingsForEditor(settings || {});
      const scopeMap = {
        campus_social: "校招 + 社招",
        campus_only: "只看校招",
        social_only: "只看社招",
      };
      const roleModeMap = {
        full_time: "只看正职",
        intern: "只看实习",
        both: "正职 + 实习",
      };
      const salaryMin = Number(editor.salary_min || 0);
      const salaryMax = Number(editor.salary_max || 0);
      const salaryText = salaryMin || salaryMax
        ? `${salaryMin || 0} - ${salaryMax || "不限"}`
        : "不限";
      root.innerHTML = `
        <div class="detail-grid">
          <div class="kv"><label>招聘范围</label><strong>${escapeHtml(scopeMap[editor.job_scope] || "校招 + 社招")}</strong></div>
          <div class="kv"><label>学历上限</label><strong>${escapeHtml(editor.max_degree_requirement || "不限")}</strong></div>
          <div class="kv"><label>薪资范围</label><strong>${escapeHtml(salaryText)}</strong></div>
          <div class="kv"><label>岗位性质</label><strong>${escapeHtml(roleModeMap[editor.campus_role_mode] || "只看正职")}</strong></div>
        </div>
        <div>
          <div class="subtle">城市偏好</div>
          <div class="tag-list">${renderTagList(editor.preferred_cities, "未设置")}</div>
        </div>
        <div>
          <div class="subtle">岗位偏好</div>
          <div class="tag-list">${renderTagList(editor.preferred_roles, "未设置")}</div>
        </div>
        <div>
          <div class="subtle">关键词摘要</div>
          <div class="tag-list">${renderTagList(editor.preferred_keywords, "未设置")}</div>
        </div>
      `;
    }

    function renderSettingsCard(settings) {
      settingsEditorState = normalizeSettingsForEditor(settings || {});
      const editor = settingsEditorState;
      const degreeOptions = [
        ["", "不限"],
        ["大专", "大专"],
        ["本科", "本科"],
        ["硕士", "硕士"],
        ["博士", "博士"],
      ].map(([value, label]) => `<option value="${escapeHtml(value)}" ${editor.max_degree_requirement === value ? "selected" : ""}>${escapeHtml(label)}</option>`).join("");
      const roleModeOptions = [
        ["full_time", "只看正职"],
        ["intern", "只看实习"],
        ["both", "正职和实习都看"],
      ].map(([value, label]) => `<option value="${escapeHtml(value)}" ${editor.campus_role_mode === value ? "selected" : ""}>${escapeHtml(label)}</option>`).join("");
      const jobScopeOptions = [
        ["campus_social", "校招 + 社招"],
        ["campus_only", "只看校招"],
        ["social_only", "只看社招"],
      ].map(([value, label]) => `<option value="${escapeHtml(value)}" ${editor.job_scope === value ? "selected" : ""}>${escapeHtml(label)}</option>`).join("");
      return `
        <div class="detail-card">
          <div class="note">“黑名单词”现在同时承担原来“不想看岗位”的过滤作用，设置页里只保留一份，避免重复配置。</div>
          <div class="detail-grid">
            <div class="field">
              <label>最低薪资（月）</label>
              <input id="settings-salary-min" type="number" min="0" value="${escapeHtml(editor.salary_min || "")}" placeholder="0 表示不限" />
            </div>
            <div class="field">
              <label>最高薪资（月）</label>
              <input id="settings-salary-max" type="number" min="0" value="${escapeHtml(editor.salary_max || "")}" placeholder="0 表示不限" />
            </div>
            <div class="field">
              <label>最高可接受学历要求</label>
              <select id="settings-degree">${degreeOptions}</select>
              <div class="hint">这里表示岗位要求不要高于你选的学历；例如选“本科”时，大专 / 高中 / 本科都会保留。</div>
            </div>
            <div class="field">
              <label>岗位性质</label>
              <select id="settings-role-mode">${roleModeOptions}</select>
            </div>
            <div class="field">
              <label>招聘范围</label>
              <select id="settings-job-scope">${jobScopeOptions}</select>
            </div>
          </div>
          ${renderEditorTags("preferred_cities", "想看城市", "例如：成都, 贵阳")}
          ${renderEditorTags("preferred_roles", "想看岗位", "例如：运营, 内容运营")}
          ${renderEditorTags("preferred_keywords", "加分词", "例如：AIGC, 用户增长")}
          ${renderEditorTags("excluded_keywords", "黑名单词", "例如：销售, 客服")}
          <div class="row">
            <button class="secondary" type="button" onclick="saveSettings()">保存当前设置</button>
          </div>
        </div>
      `;
    }

    function addEditorTag(field) {
      syncSettingsFormToState();
      const input = document.getElementById(`settings-input-${field}`);
      if (!input) return;
      const values = splitTagInput(input.value);
      if (!values.length) return;
      settingsEditorState[field] = dedupeValues([...(settingsEditorState[field] || []), ...values]);
      document.getElementById("settings-view").innerHTML = renderSettingsCard(settingsEditorState);
    }

    function removeEditorTag(field, value) {
      syncSettingsFormToState();
      settingsEditorState[field] = (settingsEditorState[field] || []).filter((item) => item !== value);
      document.getElementById("settings-view").innerHTML = renderSettingsCard(settingsEditorState);
    }

    async function saveSettings() {
      syncSettingsFormToState();
      setStatus("正在保存设置，请稍等...", "busy");
      const payload = {
        user_id: userId,
        preferred_roles: settingsEditorState.preferred_roles || [],
        preferred_cities: settingsEditorState.preferred_cities || [],
        preferred_keywords: settingsEditorState.preferred_keywords || [],
        excluded_keywords: settingsEditorState.excluded_keywords || [],
        job_scope: settingsEditorState.job_scope || "campus_social",
        campus_role_mode: settingsEditorState.campus_role_mode || "full_time",
        salary_min: Number(settingsEditorState.salary_min || 0),
        salary_max: Number(settingsEditorState.salary_max || 0),
        max_degree_requirement: settingsEditorState.max_degree_requirement || "",
      };
      try {
        const result = await api("/api/settings/save", {
          method: "POST",
          body: JSON.stringify(payload),
        });
        setStatus("设置保存完成。", "ok", 2200);
        showToast("设置已保存。");
        await logFrontend("settings_save_ok", {
          preferred_roles: payload.preferred_roles.length,
          preferred_cities: payload.preferred_cities.length,
          preferred_keywords: payload.preferred_keywords.length,
          excluded_keywords: payload.excluded_keywords.length,
        });
        renderDashboard(result.dashboard || {});
      } catch (error) {
        setStatus(error.message || "设置保存失败。", "bad", 3200);
        showToast(error.message || "设置保存失败。");
        await logFrontend("settings_save_error", { error: error.message || String(error) });
      }
    }

    function renderFoldedJd(description, label = "查看 JD 摘要") {
      const text = String(description || "").trim();
      const body = text
        ? escapeHtml(text.length > 560 ? `${text.slice(0, 560)}...` : text)
        : "暂无岗位摘要";
      return `
        <details class="jd-fold">
          <summary>${escapeHtml(label)}</summary>
          <div class="jd-body">${body}</div>
        </details>
      `;
    }

    function renderReasonList(reasons, fallback = "规则匹配通过") {
      const items = (reasons || [])
        .map((reason) => String(reason || "").trim())
        .filter(Boolean);
      const html = items.length
        ? items.map((reason) => `<li>${escapeHtml(reason)}</li>`).join("")
        : `<li>${escapeHtml(fallback)}</li>`;
      return `<ul class="job-reasons">${html}</ul>`;
    }

    function renderJobs(items, options = {}) {
      const root = document.getElementById("job-list");
      if (!root) return;
      if (!items.length) {
        const recentJobs = options.recentJobs || [];
        const run = options.lastJobRefresh || {};
        let message = "当前没有可展示的推荐岗位。先上传简历，再导入岗位。";
        if (recentJobs.length) {
          message = `这次抓取后暂时没有推荐岗位，但最近入库岗位已显示在下方“最近入库岗位”，共 ${recentJobs.length} 条。`;
        }
        if (run.summary && recentJobs.length) {
          message = `${run.summary} 最近入库岗位已显示在下方。`;
        }
        if (run.summary && !recentJobs.length) {
          message = run.summary;
        }
        root.innerHTML = `<div class="empty">${escapeHtml(message)}</div>`;
        return;
      }
      root.innerHTML = items.map((item) => {
        const job = item.job;
        return `
          <article class="job-card">
            <div class="row" style="justify-content:space-between;align-items:flex-start">
              <div>
                <h3>${escapeHtml(job.title || "岗位未识别")}</h3>
                <div class="job-meta">
                  <span>${escapeHtml(job.company_name || "公司未识别")}</span>
                  <span>${escapeHtml((job.city_list || []).join("/") || job.city || "城市未识别")}</span>
                <span>${escapeHtml(job.job_type || "岗位")}</span>
                <span>${escapeHtml(job.employment_mode || "unknown")}</span>
                <span>${escapeHtml(job.salary_text || "薪资未写")}</span>
                <span>${escapeHtml(formatApplicationStatus(job.application_status))}</span>
                <span>${escapeHtml(formatDegreeRequirement(job))}</span>
              </div>
            </div>
              <span class="badge ok">推荐分 ${Number(item.score || 0).toFixed(1)}</span>
            </div>
            <div class="hint">来源：${escapeHtml(job.source)} | 投递：<a href="${escapeHtml(resolveJobLink(job))}" target="_blank" rel="noreferrer">打开链接</a></div>
            ${renderFoldedJd(job.description, "查看岗位摘要")}
            ${renderReasonList(item.reasons)}
            <div class="job-actions"></div>
          </article>
        `;
      }).join("");
    }

    function renderRecentJobs(items) {
      const root = document.getElementById("recent-job-list");
      if (!root) return;
      if (!items.length) {
        root.innerHTML = '<div class="empty">最近还没有入库岗位。</div>';
        return;
      }
      root.innerHTML = items.map((job) => {
        const decisionHtml = job.recommended
          ? `<div class="hint">命中理由</div>${renderReasonList(job.recommendation_reasons, "已进入推荐结果")}`
          : `<div class="hint">未推荐原因：${escapeHtml(job.skip_reason || "未记录")}</div>`;
        return `
          <article class="job-card">
            <div class="row" style="justify-content:space-between;align-items:flex-start">
              <div>
                <h3>${escapeHtml(job.title || "岗位未识别")}</h3>
                <div class="job-meta">
                  <span>${escapeHtml(job.company_name || "公司未识别")}</span>
                  <span>${escapeHtml((job.city_list || []).join("/") || job.city || "城市未识别")}</span>
                  <span>${escapeHtml(job.job_type || "岗位")}</span>
                  <span>${escapeHtml(job.employment_mode || "unknown")}</span>
                  <span>${escapeHtml(job.salary_text || "薪资未写")}</span>
                  <span>${escapeHtml(formatDegreeRequirement(job))}</span>
                  <span>${escapeHtml(formatApplicationStatus(job.application_status))}</span>
                </div>
              </div>
              <span class="badge ${(job.recommended ? "ok" : "warn")}">${job.recommended ? "已推荐" : "未推荐"}</span>
            </div>
            <div class="hint">来源：${escapeHtml(job.source)} | 链接：<a href="${escapeHtml(resolveJobLink(job))}" target="_blank" rel="noreferrer">打开岗位</a></div>
            ${decisionHtml}
            ${renderFoldedJd(job.description, "查看岗位摘要")}
          </article>
        `;
      }).join("");
    }

    function actionLabel(action) {
      if (action === "saved") return "想投";
      if (action === "disliked") return "不想投";
      if (action === "deferred") return "暂缓";
      if (action === "applied") return "已投";
      return "未标记";
    }

    function decisionLabel(status) {
      if (status === "hit") return "已命中";
      if (status === "pending_detail") return "未补 JD";
      return "未命中";
    }

    function decisionBadgeClass(status) {
      if (status === "hit") return "ok";
      if (status === "pending_detail") return "risk";
      return "muted";
    }

    function reviewItemMatchesFilter(item, filter) {
      const job = item.job || {};
      const action = item.last_action || "";
      if (filter === "all") return true;
      if (filter === "hit") return item.decision_status === "hit";
      if (filter === "miss") return item.decision_status === "miss";
      if (filter === "pending_detail") return item.decision_status === "pending_detail";
      if (filter === "unknown_status") return Boolean(item.is_application_status_inferred) || job.application_status === "unknown";
      if (filter === "unmarked") return !action;
      return action === filter;
    }

    function renderReviewWorkspace() {
      const root = document.getElementById("review-workspace-list");
      if (!root) return;
      const review = bossWorkbenchState.review || {};
      const items = Array.isArray(review.review_items) ? review.review_items : [];
      document.querySelectorAll("[data-review-filter]").forEach((button) => {
        button.classList.toggle("is-active", button.getAttribute("data-review-filter") === reviewFilter);
      });
      if (!items.length) {
        root.innerHTML = '<div class="empty">先在工作台加载一轮 BOSS 审阅结果。</div>';
        return;
      }
      const visibleItems = items.filter((item) => reviewItemMatchesFilter(item, reviewFilter));
      if (!visibleItems.length) {
        root.innerHTML = '<div class="empty">当前筛选下没有岗位。</div>';
        return;
      }
      root.innerHTML = visibleItems.map((item) => {
        const job = item.job || {};
        const reasons = item.decision_status === "hit"
          ? (item.reasons || item.recommendation_reasons || [])
          : [item.skip_reason || "未记录原因"];
        const action = item.last_action || "";
        const statusLabel = item.boss_status_label || formatApplicationStatus(job.application_status);
        const statusClass = item.is_application_status_inferred || job.application_status === "unknown" ? "risk" : "muted";
        return `
          <article class="job-card">
            <div class="row" style="justify-content:space-between;align-items:flex-start">
              <div>
                <h3>${escapeHtml(job.title || "岗位未识别")}</h3>
                <div class="job-meta">
                  <span>${escapeHtml(job.company_name || "公司未识别")}</span>
                  <span>${escapeHtml((job.city_list || []).join("/") || job.city || "城市未识别")}</span>
                  <span>${escapeHtml(job.salary_text || "薪资未写")}</span>
                  <span>${escapeHtml(formatDegreeRequirement(job))}</span>
                  <span>${escapeHtml(job.job_type || "岗位")}</span>
                </div>
              </div>
              <div class="row" style="justify-content:flex-end">
                <span class="badge ${decisionBadgeClass(item.decision_status)}">${decisionLabel(item.decision_status)}</span>
                <span class="badge ${statusClass}">${escapeHtml(statusLabel)}</span>
                ${action ? `<span class="badge ok">${escapeHtml(actionLabel(action))}</span>` : '<span class="badge muted">未标记</span>'}
                ${Number(item.score || 0) ? `<span class="badge ok">分数 ${Number(item.score || 0).toFixed(1)}</span>` : ""}
              </div>
            </div>
            <div class="hint">链接：<a href="${escapeHtml(resolveJobLink(job))}" target="_blank" rel="noreferrer">打开岗位</a></div>
            ${renderFoldedJd(job.description, job.detail_fetched ? "查看 JD 摘要" : "查看列表摘要")}
            <div class="hint">${item.decision_status === "hit" ? "命中理由" : "未命中原因"}</div>
            ${renderReasonList(reasons, item.decision_status === "hit" ? "规则匹配通过" : "未记录原因")}
            <div class="job-actions">
              <button class="secondary" type="button" data-job-action="saved" data-job-fingerprint="${escapeHtml(job.fingerprint || "")}">想投</button>
              <button class="ghost" type="button" data-job-action="deferred" data-job-fingerprint="${escapeHtml(job.fingerprint || "")}">暂缓</button>
              <button class="ghost" type="button" data-job-action="disliked" data-job-fingerprint="${escapeHtml(job.fingerprint || "")}">不想投</button>
            </div>
          </article>
        `;
      }).join("");
      root.querySelectorAll("[data-job-action]").forEach((button) => {
        button.addEventListener("click", () => {
          const fingerprint = button.getAttribute("data-job-fingerprint") || "";
          const action = button.getAttribute("data-job-action") || "";
          if (fingerprint && action) {
            markJob(fingerprint, action).catch(() => {});
          }
        });
      });
    }

    function renderBossSkipDiagnostics(review) {
      const skipReasons = review?.skip_reasons || {};
      const entries = Object.entries(skipReasons)
        .map(([reason, count]) => [String(reason || "未记录原因"), Number(count || 0)])
        .filter(([, count]) => count > 0)
        .sort((a, b) => b[1] - a[1]);
      if (!entries.length) return "";
      const examples = review?.skip_examples || {};
      const rows = entries.map(([reason, count]) => {
        const sampleItems = Array.isArray(examples[reason]) ? examples[reason] : [];
        const sampleHtml = sampleItems.length
          ? sampleItems.map((job) => `
              <div class="bullet-item">
                <strong>${escapeHtml(job.title || "岗位未识别")}</strong>
                <div class="subtle">${escapeHtml(job.company_name || "公司未识别")} · ${escapeHtml(job.city || "城市未识别")}</div>
              </div>
            `).join("")
          : '<div class="bullet-item">暂无样例。</div>';
        return `
          <details class="jd-fold">
            <summary>${escapeHtml(reason)} · ${count} 条</summary>
            <div class="bullet-list">${sampleHtml}</div>
          </details>
        `;
      }).join("");
      return `
        <div class="spacer"></div>
        <div class="section-head">
          <div>
            <h2>未命中原因</h2>
            <div class="hint">这里列出本轮没有进入推荐的岗位原因和样例，方便判断是规则太严还是岗位确实不合适。</div>
          </div>
        </div>
        ${rows}
      `;
    }

    function bossDegreeFilterLabel(value) {
      const labels = {
        "大专": "学历大专及以下",
        "本科": "学历本科及以下",
        "硕士": "学历硕士及以下",
        "博士": "学历博士及以下",
      };
      return labels[String(value || "").trim()] || "";
    }

    function bossEmploymentFilterLabel(value) {
      const labels = {
        full_time: "只看正职",
        intern: "只看实习",
      };
      return labels[String(value || "").trim()] || "";
    }

    function bossQuickFilterText(filters) {
      const safeFilters = filters || {};
      const parts = [
        bossDegreeFilterLabel(safeFilters.degree_filter),
        bossEmploymentFilterLabel(safeFilters.employment_mode_filter),
      ].filter(Boolean);
      return parts.length ? parts.join(" / ") : "不限快筛";
    }

    function bossWorkbenchActiveRun(summary) {
      const safeSummary = summary || {};
      const sessionId = safeSummary.latest_fetch_session_id || "";
      const runs = safeSummary.recent_source_runs || [];
      return runs.find((item) => (item.fetch_session_id || "") === sessionId) || runs[0] || {};
    }

    function renderWorkbenchSnapshot(summary) {
      const sessionRoot = document.getElementById("sidebar-session");
      const focusRoot = document.getElementById("sidebar-focus");
      const overviewRoot = document.getElementById("overview-session");
      const safeSummary = summary || {};
      const latestRun = bossWorkbenchActiveRun(safeSummary);
      const sessionText = safeSummary.latest_fetch_session_id || "未建立";
      const city = latestRun.city || safeSummary.capture_defaults?.city || "";
      const keyword = latestRun.keyword || safeSummary.capture_defaults?.keyword || "";
      const filterText = bossQuickFilterText(latestRun.quick_filters || safeSummary.capture_defaults || {});
      const focusText = city || keyword ? [city || "未填城市", keyword || "未填关键词", filterText].join(" / ") : "等待采集";
      if (sessionRoot) sessionRoot.textContent = sessionText;
      if (focusRoot) focusRoot.textContent = focusText;
      if (overviewRoot) overviewRoot.textContent = sessionText;
    }

    function renderBossSessionStrip(summary) {
      const safeSummary = summary || {};
      const review = bossWorkbenchState.review || {};
      const supplement = bossWorkbenchState.supplement || {};
      const latestRun = bossWorkbenchActiveRun(safeSummary);
      const sessionId = safeSummary.latest_fetch_session_id || "未建立";
      const baseConditionText = latestRun.city || latestRun.keyword
        ? `${latestRun.city || "未填城市"} / ${latestRun.keyword || "未填关键词"}`
        : `${safeSummary.capture_defaults?.city || "未填城市"} / ${safeSummary.capture_defaults?.keyword || "未填关键词"}`;
      const conditionText = `${baseConditionText} · ${bossQuickFilterText(latestRun.quick_filters || safeSummary.capture_defaults || {})}`;
      let stageText = "未采集";
      let progressText = "等待列表采集";
      if (safeSummary.latest_fetch_session_id) {
        const sessionJobCount = Number(review.session_job_count || supplement.session_job_count || latestRun.job_count || 0);
        const baseCount = Number(review.recommendation_base_count || review.detail_fetched_count || supplement.detail_fetched_count || 0);
        const pendingCount = Number(supplement.pending_job_count || review.pending_job_count || 0);
        if (review.stage === "recommendation_done" || review.ok) {
          stageText = "推荐完成";
          progressText = `完整 JD ${baseCount}/${sessionJobCount || baseCount}，命中 ${Number(review.matched_count || 0)} 条`;
        } else if (review.stage === "recommendation_pending") {
          stageText = "待补抓推荐";
          progressText = review.message || `列表已入库 ${sessionJobCount || latestRun.job_count || 0} 条`;
        } else if (supplement.fetch_session_id === safeSummary.latest_fetch_session_id) {
          stageText = "补抓后待推荐";
          progressText = `完整 JD ${baseCount}/${sessionJobCount || baseCount}，待补 ${pendingCount}`;
        } else {
          stageText = "列表已入库";
          progressText = `列表 ${sessionJobCount || latestRun.job_count || 0} 条，等待补抓并推荐`;
        }
      }
      const pairs = [
        ["flow-session", sessionId],
        ["flow-condition", conditionText],
        ["flow-stage", stageText],
        ["flow-progress", progressText],
      ];
      pairs.forEach(([id, value]) => {
        const node = document.getElementById(id);
        if (node) node.textContent = value;
      });
    }

    function renderBossWorkbenchOverview(summary) {
      const root = document.getElementById("boss-workbench-overview");
      if (!root) return;
      const safeSummary = summary || {};
      if (!safeSummary.latest_fetch_session_id) {
        root.innerHTML = '<div class="empty">还没有可用的 BOSS 队列。先完成一次列表采集。</div>';
        return;
      }
      const review = bossWorkbenchState.review || {};
      const supplement = bossWorkbenchState.supplement || {};
      const latestRun = bossWorkbenchActiveRun(safeSummary);
      const matchedCount = Number(review.matched_count || 0);
      const displayedCount = Number((review.items || []).length || 0);
      const updatedCount = Number(supplement.updated_count || 0);
      const hasPendingCount = supplement && Object.prototype.hasOwnProperty.call(supplement, "pending_job_count");
      const pendingCount = hasPendingCount ? Number(supplement.pending_job_count || 0) : null;
      const quickFilterText = bossQuickFilterText(latestRun.quick_filters || safeSummary.capture_defaults || {});
      root.innerHTML = `
        <div class="detail-grid">
          <div class="kv"><label>当前 session</label><strong>${escapeHtml(safeSummary.latest_fetch_session_id || "")}</strong></div>
          <div class="kv"><label>最近查询</label><strong>${escapeHtml(`${latestRun.city || safeSummary.capture_defaults?.city || "未填城市"} / ${latestRun.keyword || safeSummary.capture_defaults?.keyword || "未填关键词"}`)}</strong></div>
          <div class="kv"><label>快筛条件</label><strong>${escapeHtml(quickFilterText)}</strong></div>
          <div class="kv"><label>最近命中</label><strong>${matchedCount}</strong></div>
          <div class="kv"><label>当前展示</label><strong>${displayedCount}</strong></div>
          <div class="kv"><label>最近补抓成功</label><strong>${updatedCount}</strong></div>
          <div class="kv"><label>当前待补</label><strong>${pendingCount == null ? "未统计" : pendingCount}</strong></div>
        </div>
      `;
    }

    function bossWorkbenchProfileTone(profileId) {
      if (profileId === "default") return "secondary";
      if (profileId === "boss_social") return "teal";
      return "ghost";
    }

    function bossWorkbenchProfileLabel(profileId, summary) {
      const profiles = summary?.available_review_profiles || [];
      const matched = profiles.find((item) => item.id === profileId);
      if (matched?.label) return matched.label;
      if (!profileId || profileId === "default") return "默认审阅";
      return profileId;
    }

    function syncBossWorkbenchCaptureForm(summary) {
      const defaults = summary?.capture_defaults || {};
      const cityInput = document.getElementById("boss-workbench-city");
      const keywordInput = document.getElementById("boss-workbench-keyword");
      const degreeFilterInput = document.getElementById("boss-workbench-degree-filter");
      const employmentFilterInput = document.getElementById("boss-workbench-employment-filter");
      const limitInput = document.getElementById("boss-workbench-limit");
      const reviewLimitInput = document.getElementById("boss-workbench-review-limit");
      if (cityInput && !cityInput.value) cityInput.value = defaults.city || "深圳";
      if (keywordInput && !keywordInput.value) keywordInput.value = defaults.keyword || "运营";
      if (degreeFilterInput && !degreeFilterInput.value && defaults.degree_filter) degreeFilterInput.value = defaults.degree_filter;
      if (employmentFilterInput && !employmentFilterInput.value && defaults.employment_mode_filter) employmentFilterInput.value = defaults.employment_mode_filter;
      if (limitInput && !limitInput.value) limitInput.value = String(defaults.limit || 45);
      if (reviewLimitInput && !reviewLimitInput.value) reviewLimitInput.value = "5";
    }

    function readBossWorkbenchCaptureForm() {
      return {
        city: String(document.getElementById("boss-workbench-city")?.value || "").trim(),
        keyword: String(document.getElementById("boss-workbench-keyword")?.value || "").trim(),
        degree_filter: String(document.getElementById("boss-workbench-degree-filter")?.value || "").trim(),
        employment_mode_filter: String(document.getElementById("boss-workbench-employment-filter")?.value || "").trim(),
        limit: Number(document.getElementById("boss-workbench-limit")?.value || 45),
        review_limit: Number(document.getElementById("boss-workbench-review-limit")?.value || 5),
      };
    }

    function ensureBossWorkbenchUi() {
      return;
    }

    function syncBossWorkbenchButtons(summary) {
      const hasSession = !!(summary && summary.latest_fetch_session_id);
      const profiles = summary?.available_review_profiles || [];
      const defaultButton = document.getElementById("boss-workbench-default-btn");
      const socialButton = document.getElementById("boss-workbench-social-btn");
      const root = document.getElementById("boss-workbench-profile-actions");
      if (defaultButton) defaultButton.hidden = true;
      if (socialButton) socialButton.hidden = true;
      if (!root) return;
      if (!hasSession) {
        root.innerHTML = '<button class="ghost" disabled>先导入一轮 BOSS 队列</button>';
        return;
      }
      const activeProfileId = bossWorkbenchState.review?.review_profile?.name || "default";
      root.innerHTML = profiles.map((item) => {
        const count = Number(item.job_count || 0);
        const active = activeProfileId === item.id;
        return `
          <button
            class="${bossWorkbenchProfileTone(item.id)}"
            id="boss-workbench-profile-${escapeHtml(item.id || "default")}"
            type="button"
            data-boss-review-profile="${escapeHtml(item.id || "")}"
            title="${escapeHtml(item.description || "")}"
            ${count <= 0 ? "disabled" : ""}
          >${escapeHtml(item.label || item.id)}${active ? " · 当前" : ""}</button>
        `;
      }).join("");
      root.querySelectorAll("[data-boss-review-profile]").forEach((button) => {
        button.addEventListener("click", () => {
          const reviewProfile = button.getAttribute("data-boss-review-profile") || "";
          const label = bossWorkbenchProfileLabel(reviewProfile, summary);
          withBossWorkbenchBusy(button.id, "加载中...", async () => {
            setStatus(`正在加载 ${label}...`, "busy");
            await logFrontend("boss_workbench_review_click", { review_profile: reviewProfile || "default" });
            await loadBossWorkbenchReview(reviewProfile === "default" ? "" : reviewProfile);
          }).catch(() => {});
        });
      });
    }

    function renderBossWorkbench(summary) {
      const root = document.getElementById("boss-workbench-view");
      const safeSummary = summary || {};
      const review = bossWorkbenchState.review;
      syncBossWorkbenchCaptureForm(safeSummary);
      syncBossWorkbenchButtons(safeSummary);
      renderWorkbenchSnapshot(safeSummary);
      renderBossSessionStrip(safeSummary);
      renderBossWorkbenchOverview(safeSummary);
      if (!safeSummary.latest_fetch_session_id) {
        root.innerHTML = '<div class="empty">还没有可用的 BOSS 队列。先完成一次列表队列导入。</div>';
        return;
      }
      const profiles = safeSummary.available_review_profiles || [];
      const profilesHtml = profiles.length
        ? profiles.map((item) => `<span class="badge ok">${escapeHtml(item.label || item.id)} · ${Number(item.job_count || 0)} 条</span>`).join("")
        : '<span class="badge warn">当前没有可用场景</span>';
      const recentRuns = (safeSummary.recent_source_runs || []).slice(0, 3);
      const recentRunsHtml = recentRuns.length
        ? recentRuns.map((item) => `
            <div class="bullet-item">
              <strong>${escapeHtml(item.fetch_session_id || "未记录 session")}</strong>
              <div class="subtle">${escapeHtml(item.city || "城市未写")} / ${escapeHtml(item.keyword || "关键词未写")} / ${escapeHtml(item.status || "unknown")}</div>
            </div>
          `).join("")
        : '<div class="bullet-item">最近还没有 BOSS 队列记录。</div>';
      const supplement = bossWorkbenchState.supplement;
      const supplementUpdatedJobs = supplement && Array.isArray(supplement.updated_jobs)
        ? supplement.updated_jobs
        : [];
        const supplementUpdatedHtml = supplementUpdatedJobs.length
        ? `
          <div class="spacer"></div>
          <div class="note">本次补抓结果：下面这 ${supplementUpdatedJobs.length} 条就是刚刚真正补到 JD 的岗位。</div>
          <div class="boss-results-grid">
          ${supplementUpdatedJobs.map((job) => {
            return `
              <article class="job-card">
                <div class="row" style="justify-content:space-between;align-items:flex-start">
                  <div>
                    <h3>${escapeHtml(job.title || "岗位未识别")}</h3>
                    <div class="job-meta">
                      <span>${escapeHtml(job.company_name || "公司未识别")}</span>
                      <span>${escapeHtml((job.city_list || []).join("/") || job.city || "城市未识别")}</span>
                      <span>${escapeHtml(job.salary_text || "薪资未写")}</span>
                      <span>${escapeHtml(formatDegreeRequirement(job))}</span>
                    </div>
                  </div>
                  <div class="row" style="justify-content:flex-end">
                    <span class="badge ok">已补 JD</span>
                  </div>
                </div>
                <div class="hint">链接：<a href="${escapeHtml(resolveJobLink(job))}" target="_blank" rel="noreferrer">打开岗位</a></div>
                ${renderFoldedJd(job.description, "查看 JD 摘要")}
              </article>
            `;
          }).join("")}
          </div>
        `
        : "";
      const supplementHtml = supplement && supplement.fetch_session_id === safeSummary.latest_fetch_session_id
        ? `
          <div class="note">
            最近一次补抓 JD：成功 ${Number(supplement.updated_count || 0)} / 尝试 ${Number(supplement.attempted_count || 0)}，
            当前待补 ${Number(supplement.pending_job_count || 0)} 条。
          </div>
          ${supplementUpdatedHtml}
        `
        : "";
      let reviewHtml = '<div class="note">这轮列表已经入库。下一步点“补抓并推荐”，推荐只会基于已补到完整 JD 的岗位生成。</div>';
      if (review && review.fetch_session_id === safeSummary.latest_fetch_session_id) {
        const activeProfile = review.review_profile?.label || "当前全局设置";
        const activeProfileId = review.review_profile?.name || "default";
        const suggestedLabel = review.suggested_review_profile_detail?.label || review.suggested_review_profile || "";
        const suggested = suggestedLabel
          ? `<div class="note">默认审阅当前没有命中，系统建议切到：${escapeHtml(suggestedLabel)}</div>`
          : "";
        const items = review.items || [];
        const matchedCount = Number(review.matched_count || 0);
        const displayedCount = Number(items.length || 0);
        const baseCount = Number(review.recommendation_base_count || review.detail_fetched_count || 0);
        const sessionCount = Number(review.session_job_count || baseCount || 0);
        const previewNote = matchedCount > displayedCount
          ? `<div class="note">当前命中 ${matchedCount} 条，默认先展示 ${displayedCount} 条。</div>`
          : "";
        const skipDiagnosticsHtml = renderBossSkipDiagnostics(review);
        const showAllButton = matchedCount > displayedCount
          ? `<button class="ghost" type="button" data-boss-show-all="${escapeHtml(activeProfileId)}">查看全部过线岗位</button>`
          : "";
        const itemsHtml = items.length
          ? items.map((item) => {
              const job = item.job || {};
              const detailFetched = Boolean(job.detail_fetched);
              const jdBadge = detailFetched
                ? '<span class="badge ok">已补 JD</span>'
                : '<span class="badge warn">列表摘要</span>';
              const reasonHtml = renderReasonList(item.reasons || item.recommendation_reasons, "规则匹配通过");
              return `
                <article class="job-card">
                  <div class="row" style="justify-content:space-between;align-items:flex-start">
                    <div>
                      <h3>${escapeHtml(job.title || "岗位未识别")}</h3>
                      <div class="job-meta">
                        <span>${escapeHtml(job.company_name || "公司未识别")}</span>
                        <span>${escapeHtml((job.city_list || []).join("/") || job.city || "城市未识别")}</span>
                        <span>${escapeHtml(job.salary_text || "薪资未写")}</span>
                        <span>${escapeHtml(formatDegreeRequirement(job))}</span>
                        <span>${escapeHtml(job.job_type || "岗位")}</span>
                      </div>
                    </div>
                    <div class="row" style="justify-content:flex-end">
                      <span class="badge ok">已命中</span>
                      ${jdBadge}
                      <span class="badge ok">分数 ${Number(item.score || 0).toFixed(1)}</span>
                    </div>
                  </div>
                  <div class="hint">链接：<a href="${escapeHtml(resolveJobLink(job))}" target="_blank" rel="noreferrer">打开岗位</a></div>
                  ${renderFoldedJd(job.description, detailFetched ? "查看 JD 摘要" : "查看列表摘要")}
                  <div class="hint">命中理由</div>
                  ${reasonHtml}
                </article>
              `;
            }).join("")
          : '<div class="empty">当前这个审阅场景还没有可展示岗位。</div>';
        const resultsGridHtml = items.length
          ? `<div class="boss-results-grid">${itemsHtml}</div>`
          : itemsHtml;
        reviewHtml = `
          <div class="section-head">
            <div>
              <h2>本轮推荐结果</h2>
              <div class="hint">默认只展示 Top 5，JD 摘要保持折叠。</div>
            </div>
            ${showAllButton}
          </div>
          <div class="summary-grid">
            <div class="summary-box"><label>当前 session</label><strong>${escapeHtml(review.fetch_session_id || "")}</strong></div>
            <div class="summary-box"><label>当前场景</label><strong>${escapeHtml(activeProfile)}</strong></div>
            <div class="summary-box"><label>命中岗位</label><strong>${matchedCount}</strong></div>
            <div class="summary-box"><label>推荐依据</label><strong>${baseCount}/${sessionCount || baseCount}</strong></div>
          </div>
          ${suggested}
          ${previewNote}
          ${resultsGridHtml}
          ${skipDiagnosticsHtml}
        `;
      }
      root.innerHTML = `
        <div class="boss-context-grid">
          <div class="kv">
            <label>当前 BOSS session</label>
            <strong>${escapeHtml(safeSummary.latest_fetch_session_id || "")}</strong>
          </div>
          <div class="kv">
            <label>可用审阅口径</label>
            <strong>${profiles.length}</strong>
          </div>
          <div class="kv">
            <label>最近记录</label>
            <strong>${recentRuns.length}</strong>
          </div>
        </div>
        <div class="spacer"></div>
        <div>${profilesHtml}</div>
        <div class="spacer"></div>
        <details class="jd-fold">
          <summary>查看最近 BOSS 记录</summary>
          <div class="boss-compact-list">${recentRunsHtml}</div>
        </details>
        <div class="spacer"></div>
        ${supplementHtml}
        ${reviewHtml}
      `;
      root.querySelectorAll("[data-boss-show-all]").forEach((button) => {
        button.addEventListener("click", () => {
          const profileName = button.getAttribute("data-boss-show-all") || "";
          withBossWorkbenchBusy("boss-workbench-refresh-btn", "加载中...", async () => {
            await loadBossWorkbenchReview(profileName === "default" ? "" : profileName, { limit: 120 });
          }).catch(() => {});
        });
      });
    }

    async function loadBossWorkbenchReview(reviewProfile, options = {}) {
      const summary = bossWorkbenchState.summary || lastDashboard?.boss_workbench || {};
      const fetchSessionId = summary.latest_fetch_session_id || "";
      if (!fetchSessionId) {
        setStatus("还没有可审阅的 BOSS 队列。先完成一次列表队列导入。", "bad", 2800);
        return;
      }
      const reviewLimit = Math.max(1, Math.min(Number(options.limit || document.getElementById("boss-workbench-review-limit")?.value || 5), 120));
      const params = new URLSearchParams({
        user_id: userId,
        fetch_session_id: fetchSessionId,
        limit: String(reviewLimit),
      });
      if (reviewProfile) {
        params.set("review_profile", reviewProfile);
      }
      const payload = await api(`/api/boss/workbench/review?${params.toString()}`);
      bossWorkbenchState.summary = payload.workbench || summary;
      bossWorkbenchState.review = payload.review || null;
      renderBossWorkbench(bossWorkbenchState.summary);
      renderReviewWorkspace();
      if (payload.review?.stage === "recommendation_pending") {
        if (!options || !options.suppressStatus) {
          setStatus(payload.review?.message || "这轮还没有推荐结果，先补抓并推荐。", "warn", 3200);
        }
        return payload;
      }
      const matchedCount = Number(payload.review?.matched_count || 0);
      const suggested = payload.review?.suggested_review_profile_detail?.label || payload.review?.suggested_review_profile || "";
      const activeProfile = payload.review?.review_profile?.label || bossWorkbenchProfileLabel(reviewProfile || "default", bossWorkbenchState.summary);
      const displayedCount = Number(payload.review?.items?.length || 0);
      const message = suggested && !matchedCount
        ? `默认审阅当前没命中，建议切到 ${suggested}。`
        : `BOSS 工作台已加载：${activeProfile}，当前命中 ${matchedCount} 条，展示 ${displayedCount} 条。`;
      if (!options || !options.suppressStatus) {
        setStatus(message, "ok", 2800);
      }
      return payload;
    }

    async function captureBossWorkbenchQueue() {
      const form = readBossWorkbenchCaptureForm();
      if (!form.city) {
        throw new Error("请先填写 BOSS 城市。");
      }
      if (!form.keyword) {
        throw new Error("请先填写 BOSS 关键词。");
      }
      const payload = await api("/api/boss/workbench/capture", {
        method: "POST",
        body: JSON.stringify({
          user_id: userId,
          city: form.city,
          keyword: form.keyword,
          degree_filter: form.degree_filter,
          employment_mode_filter: form.employment_mode_filter,
          limit: form.limit,
          review_limit: form.review_limit,
        }),
      });
      bossWorkbenchState.summary = payload.workbench || bossWorkbenchState.summary || lastDashboard?.boss_workbench || {};
      bossWorkbenchState.review = payload.review || null;
      renderBossWorkbench(bossWorkbenchState.summary);
      renderReviewWorkspace();
      const importedCount = Number(payload.import_result?.job_count || 0);
      const droppedCount = Number(payload.import_result?.local_filter?.dropped_count || 0);
      const filterNote = droppedCount ? `，本地快筛拦下 ${droppedCount} 条` : "";
      const sessionId = payload.import_result?.fetch_session_id || payload.capture?.fetch_session_id || "";
      setStatus(`BOSS 列表采集完成：导入 ${importedCount} 条${filterNote}。下一步点“补抓并推荐”，session ${sessionId}`, "ok", 3600);
      return payload;
    }

    async function supplementBossWorkbenchDetails() {
      const form = readBossWorkbenchCaptureForm();
      const summary = bossWorkbenchState.summary || lastDashboard?.boss_workbench || {};
      const fetchSessionId = summary.latest_fetch_session_id || "";
      if (!fetchSessionId) {
        throw new Error("还没有可补抓 JD 的 BOSS session。先完成一次列表采集。");
      }
      const activeReviewProfile = bossWorkbenchState.review?.review_profile?.name || "";
      const payload = await api("/api/boss/workbench/supplement", {
        method: "POST",
        body: JSON.stringify({
          user_id: userId,
          fetch_session_id: fetchSessionId,
          review_limit: form.review_limit,
          review_profile: activeReviewProfile === "default" ? "" : activeReviewProfile,
        }),
      });
      bossWorkbenchState.summary = payload.workbench || bossWorkbenchState.summary || lastDashboard?.boss_workbench || {};
      bossWorkbenchState.review = payload.review || bossWorkbenchState.review || null;
      bossWorkbenchState.supplement = payload.supplement || null;
      renderBossWorkbench(bossWorkbenchState.summary);
      renderReviewWorkspace();
      const updatedCount = Number(payload.supplement?.updated_count || 0);
      const attemptedCount = Number(payload.supplement?.attempted_count || 0);
      const updatedPreviewCount = Number(payload.supplement?.updated_jobs?.length || 0);
      const displayedCount = Number(payload.review?.items?.length || 0);
      if (!payload.ok && updatedCount <= 0) {
        throw new Error(payload.supplement?.error || "这次没有成功补到 JD。");
      }
      const baseCount = Number(payload.recommendation?.recommendation_base_count || 0);
      const totalCount = Number(payload.recommendation?.session_job_count || 0);
      setStatus(`补抓并推荐完成：补到 ${updatedCount} / 尝试 ${attemptedCount}，推荐基于 ${baseCount}/${totalCount} 条完整 JD，当前展示 ${displayedCount} 条。`, "ok", 4600);
      return payload;
    }

    function renderOperationStatus(op) {
      if (!op || op.kind !== "jobs_refresh") {
        return;
      }
      const parts = [];
      if (op.message) parts.push(op.message);
      if (op.selected_source_labels && op.selected_source_labels.length) {
        parts.push(`来源 ${op.selected_source_labels.join("/")}`);
      }
      if (op.fetch_limit) {
        parts.push(`目标 ${op.fetch_limit} 条`);
      }
      if (op.progress_total) {
        parts.push(`进度 ${op.progress_current || 0}/${op.progress_total}`);
      }
      if (op.current_source) {
        parts.push(`当前来源 ${op.current_source}`);
      }
      if (op.elapsed_ms) {
        parts.push(`耗时 ${formatDurationMs(op.elapsed_ms)}`);
      }
      const summaryText = parts.join(" | ") || op.message || "正在处理...";
      const tone = op.status === "error" ? "bad" : (op.active ? "busy" : "ok");
      setStatus(summaryText, tone, op.active ? 0 : 3200);
      document.getElementById("fetch-summary").textContent = summaryText;
      document.getElementById("fetch-report").textContent = prettyJson({
        active: !!op.active,
        status: op.status || "",
        stage: op.stage || "",
        message: op.message || "",
        selected_sources: op.selected_sources || [],
        selected_source_labels: op.selected_source_labels || [],
        fetch_limit: op.fetch_limit || 0,
        fetch_session_id: op.fetch_session_id || "",
        progress_current: op.progress_current || 0,
        progress_total: op.progress_total || 0,
        current_source: op.current_source || "",
        total_jobs: op.total_jobs || 0,
        matched: op.matched || 0,
        upsert: op.upsert || {},
        recommendation_count: op.recommendation_count || 0,
        source_reports: op.source_reports || [],
        error: op.error || "",
      });
    }

    function buildFetchSessionId() {
      const now = new Date();
      const pad = (value, size = 2) => String(value).padStart(size, "0");
      return [
        now.getFullYear(),
        pad(now.getMonth() + 1),
        pad(now.getDate()),
        "-",
        pad(now.getHours()),
        pad(now.getMinutes()),
        pad(now.getSeconds()),
        pad(now.getMilliseconds(), 3),
      ].join("");
    }

    function stopJobsOperationPolling() {
      if (jobsOperationPoller) {
        window.clearInterval(jobsOperationPoller);
        jobsOperationPoller = null;
      }
    }

    function startJobsOperationPolling(expectedOp) {
      stopJobsOperationPolling();
      expectedJobsOperation = expectedOp || null;
      if (expectedJobsOperation) {
        renderOperationStatus({
          kind: "jobs_refresh",
          active: true,
          status: "running",
          stage: "queued",
          message: "已提交本轮抓取，等待服务端开始处理。",
          selected_sources: expectedJobsOperation.selected_sources || [],
          selected_source_labels: expectedJobsOperation.selected_source_labels || [],
          fetch_limit: expectedJobsOperation.fetch_limit || 0,
          fetch_session_id: expectedJobsOperation.fetch_session_id || "",
          progress_current: 0,
          progress_total: 0,
          source_reports: [],
          recommendation_count: 0,
          error: "",
        });
      }
      const tick = async () => {
        try {
          const op = await api(`/api/ops/status?user_id=${encodeURIComponent(userId)}`);
          if (op.kind === "jobs_refresh") {
            if (
              expectedJobsOperation &&
              expectedJobsOperation.fetch_session_id &&
              op.fetch_session_id &&
              op.fetch_session_id !== expectedJobsOperation.fetch_session_id
            ) {
              return;
            }
            renderOperationStatus(op);
            if (!op.active) {
              expectedJobsOperation = null;
              stopJobsOperationPolling();
            }
          }
        } catch (error) {
          console.warn("poll operation failed", error);
        }
      };
      tick();
      jobsOperationPoller = window.setInterval(tick, 900);
      return stopJobsOperationPolling;
    }

    function renderDashboard(data) {
      lastDashboard = data || {};
      ensureBossWorkbenchUi();
      const nextWorkbench = data.boss_workbench || {};
      const activeWorkbenchSession = bossWorkbenchState.review?.fetch_session_id || "";
      const nextWorkbenchSession = nextWorkbench.latest_fetch_session_id || "";
      bossWorkbenchState.summary = nextWorkbench;
      if (!activeWorkbenchSession || activeWorkbenchSession !== nextWorkbenchSession) {
        bossWorkbenchState.review = null;
        bossWorkbenchState.supplement = null;
      }
      renderStatusBadges(data.status);
      renderBossGate(data.status);
      renderTimeline(data.interactions || []);
      renderSourceRuns(data.source_runs || []);
      renderFrontendLogs(data.frontend_logs || []);
      renderFetchSummary(data.last_job_refresh || {});
      renderFetchFunnel(data.fetch_funnel || {});
      renderFetchSources(data.available_fetch_sources || [], data.selected_fetch_sources || []);
      bindFetchSourceChangeHandlers();
      updateFetchGateState();
      renderJobs(data.recommendations || [], {
        recentJobs: data.recent_jobs || [],
        lastJobRefresh: data.last_job_refresh || {},
      });
      renderRecentJobs(data.recent_jobs || []);
      document.getElementById("fetch-limit").value = String(data.current_fetch_limit || 40);
      document.getElementById("profile-view").innerHTML = renderProfileCard(data.profile || {}, data.extraction_info || {});
      document.getElementById("extraction-view").textContent = prettyJson(data.extraction_info || {});
      document.getElementById("settings-view").innerHTML = renderSettingsCard(data.settings || {});
      renderSettingsBrief(data.settings || {});
      document.getElementById("job-count").textContent = String(data.job_count || 0);
      document.getElementById("rec-count").textContent = String((data.recommendations || []).length);
      document.getElementById("history-count").textContent = String((data.delivery_history || []).length);
      document.getElementById("resume-badge").className = "badge " + (data.has_resume ? "ok" : "warn");
      document.getElementById("resume-badge").textContent = "简历：" + (data.resume_file_name || "未上传");
      renderWorkbenchSnapshot(nextWorkbench);
      renderBossWorkbench(nextWorkbench);
      renderReviewWorkspace();
      document.getElementById("resume-hint").textContent = data.resume_file_name
        ? `当前简历：${data.resume_file_name}`
        : "还没导入简历。";
    }

    async function loadDashboard() {
      const data = await api(`/api/dashboard?user_id=${encodeURIComponent(userId)}`);
      renderDashboard(data);
    }

    async function launchBossBrowser() {
      return api("/api/boss/launch-browser", {
        method: "POST",
      });
    }

    async function uploadResume() {
      const input = document.getElementById("resume-file");
      if (!input.files.length) {
        alert("先选一个简历文件。");
        return;
      }
      const form = new FormData();
      form.append("user_id", userId);
      form.append("file", input.files[0]);
      const result = await fetch("/api/resume/upload", { method: "POST", body: form });
      if (!result.ok) {
        const text = await result.text();
        setStatus("上传失败：" + text, "bad");
        await logFrontend("resume_upload_error", { error: text });
        return;
      }
      const payload = await result.json();
      document.getElementById("resume-hint").textContent = "简历导入完成，右侧已刷新。";
      setStatus(
        "简历导入完成。当前链路：" +
        (payload.extraction?.route_summary || payload.extraction?.extraction_method || "unknown") +
        "；解析方式：" +
        (payload.parse_method || "unknown"),
        "ok",
        2400
      );
      showToast("简历解析成功，右侧画像已刷新。");
      await logFrontend("resume_upload_ok", {
        route_summary: payload.extraction?.route_summary || "",
        provider_used: payload.extraction?.provider_used || "",
        extraction_method: payload.extraction?.extraction_method || "",
        quality_score: payload.extraction?.quality_score || 0,
      });
      await loadDashboard();
    }

    async function fetchRecommendations(fetchJobs) {
      const selectedSources = selectedFetchSourceIds();
      const selectedSourceLabels = Array.from(
        document.querySelectorAll('input[name="fetch-source"]:checked')
      ).map((node) => node.closest("label")?.querySelector("strong")?.textContent || node.value);
      const fetchLimitInput = document.getElementById("fetch-limit");
      const fetchLimit = Math.max(1, Math.min(Number(fetchLimitInput?.value || 40), 200));
      const fetchSessionId = buildFetchSessionId();
      if (fetchLimitInput) {
        fetchLimitInput.value = String(fetchLimit);
      }
      if (!selectedSources.length) {
        setStatus("至少勾选一个抓取来源。", "bad", 2400);
        showToast("至少勾选一个抓取来源。");
        return;
      }
      if (fetchJobs && selectedSourcesNeedBossGate()) {
        const gate = lastDashboard?.status?.boss_gate || {};
        if (!gate.can_start) {
          const message = gate.message || "当前 BOSS 状态还不能开始抓取。";
          setStatus(message, "bad", 3200);
          showToast(message);
          return;
        }
      }
      const stopPolling = startJobsOperationPolling({
        fetch_session_id: fetchSessionId,
        fetch_limit: fetchLimit,
        selected_sources: selectedSources,
        selected_source_labels: selectedSourceLabels,
      });
      try {
        const payload = await api("/api/jobs/refresh", {
          method: "POST",
          body: JSON.stringify({
            user_id: userId,
            fetch_jobs: fetchJobs,
            selected_sources: selectedSources,
            fetch_limit: fetchLimit,
            fetch_session_id: fetchSessionId,
          }),
        });
        renderDashboard(payload.dashboard || {});
        const summary = payload.summary || `抓取完成。当前推荐 ${payload.recommendations?.length || 0} 条。`;
        setStatus(summary, "ok", 3600);
        showToast(summary);
        await logFrontend("jobs_refresh_ok", {
          fetch_jobs: fetchJobs,
          selected_sources: selectedSources,
          fetch_limit: fetchLimit,
          fetch_session_id: fetchSessionId,
          recommendation_count: payload.recommendations?.length || 0,
          summary,
        });
      } finally {
        expectedJobsOperation = null;
        stopPolling();
      }
    }

    async function checkLlm() {
      const payload = await api("/api/llm-check", { method: "POST" });
      setStatus(`AI 接口正常，返回：${payload.reply}（${payload.duration_ms}ms）`, "ok", 2200);
      showToast(`AI 接口正常：${payload.reply}`);
      await logFrontend("llm_check_ok", payload);
    }

    function aiProviderSignature(data = {}) {
      const provider = String(data.provider || "").trim().toLowerCase();
      let baseUrl = String(data.base_url || "").trim().replace(/\\/+$/, "");
      if (provider === "openai-compatible" && baseUrl.endsWith("/chat/completions")) {
        baseUrl = baseUrl.slice(0, -"/chat/completions".length);
      }
      if ((provider === "anthropic-compatible" || provider === "minimax-anthropic") && baseUrl.endsWith("/v1/messages")) {
        baseUrl = baseUrl.slice(0, -"/v1/messages".length);
      }
      return `${provider}|${baseUrl}`;
    }

    function renderAiProviderCard(kind, title, data = {}) {
      const draft = aiFormDrafts[kind] || {};
      const providerValue = String(draft.provider || data.provider || "").trim();
      const baseUrlValue = String(draft.base_url || data.base_url || "").trim();
      const draftApiKey = String(draft.api_key || "").trim();
      const currentModel = String(draft.model || data.model || "").trim();
      const configured = data.api_key_configured ? `已配置，尾号 ${escapeHtml(data.api_key_tail || "")}` : "未配置";
      const keyPlaceholder = data.api_key_configured
        ? `已配置：••••••${data.api_key_tail || ""}，输入新 Key 可替换`
        : "未配置，请输入 API Key";
      const draftKeyStatus = draftApiKey
        ? `<div class="secret-status">本次输入：尾号 ${escapeHtml(draftApiKey.slice(-4))}，保存、测试和列模型都会优先使用它</div>`
        : "";
      const modelList = aiModelLists[kind]?.form_signature === aiProviderSignature({ provider: providerValue, base_url: baseUrlValue }) ? aiModelLists[kind] : null;
      const models = Array.isArray(modelList?.models) ? modelList.models : [];
      const modelControl = models.length ? `
        <select class="form-select" id="ai-${kind}-model">
          ${models.map((item) => {
            const id = String(item.id || "").trim();
            const labelBits = [id, item.input ? `输入：${item.input}` : "", item.name || ""].filter(Boolean);
            return `<option value="${escapeHtml(id)}" ${id === currentModel ? "selected" : ""}>${escapeHtml(labelBits.join(" · "))}</option>`;
          }).join("")}
        </select>
        <div class="hint">已读取到模型列表，可直接从下拉选择。</div>
      ` : `
        <select class="form-select" id="ai-${kind}-model" disabled>
          <option value="">先列出可用模型</option>
        </select>
        <div class="hint">先点击“列出可用模型”，再从下拉选择；这里不再支持手填模型名。</div>
      `;
      return `
        <section class="detail-card ai-provider-card">
          <h3>${escapeHtml(title)}</h3>
          <div class="field">
            <label>Provider</label>
            <select class="form-select" id="ai-${kind}-provider">
              <option value="openai-compatible" ${providerValue === "openai-compatible" ? "selected" : ""}>openai-compatible</option>
              <option value="anthropic-compatible" ${providerValue === "anthropic-compatible" ? "selected" : ""}>anthropic-compatible</option>
              ${kind === "text" ? `<option value="minimax-anthropic" ${providerValue === "minimax-anthropic" ? "selected" : ""}>minimax-anthropic</option>` : ""}
            </select>
          </div>
          <div class="field">
            <label>Base URL</label>
            <input class="form-control" id="ai-${kind}-base-url" type="text" value="${escapeHtml(baseUrlValue)}" placeholder="https://..." />
          </div>
          <div class="field">
            <label>Model</label>
            ${modelControl}
          </div>
          <div class="field">
            <label>API Key</label>
            <input class="form-control" id="ai-${kind}-api-key" type="text" value="${escapeHtml(draftApiKey)}" placeholder="${escapeHtml(keyPlaceholder)}" autocomplete="off" />
            <div class="secret-status">当前保存：${configured}</div>
            ${draftKeyStatus}
            <label class="check-row"><input id="ai-${kind}-clear-key" type="checkbox" /> 清除本地保存的 Key</label>
          </div>
          <div class="row">
            <button class="btn btn-outline-secondary secondary" type="button" data-ai-test="${kind}">测试${kind === "text" ? "文本" : "视觉"}模型</button>
            <button class="btn btn-outline-secondary secondary" type="button" data-ai-list-models="${kind}">列出可用模型</button>
          </div>
        </section>
      `;
    }

    function renderAiSettings(payload) {
      aiSettingsState = payload || {};
      const root = document.getElementById("ai-settings-view");
      if (!root) return;
      root.innerHTML = `
        ${renderAiProviderCard("text", "文本模型", aiSettingsState.text || {})}
        ${renderAiProviderCard("vision", "视觉 / OCR 模型", aiSettingsState.vision || {})}
        <section class="detail-card" style="grid-column:1/-1">
          <div class="hint">保存位置：${escapeHtml(aiSettingsState.settings_path || "")}</div>
          <div class="spacer"></div>
          <div class="row">
            <button class="btn btn-primary primary" id="ai-settings-save-btn" type="button"><i class="bi bi-check2"></i>保存 AI 设置</button>
          </div>
        </section>
      `;
      document.getElementById("ai-settings-save-btn").addEventListener("click", () => withBusy("ai-settings-save-btn", "保存中...", saveAiSettings).catch(() => {}));
      root.querySelectorAll("[data-ai-test]").forEach((button) => {
        button.addEventListener("click", () => {
          const target = button.getAttribute("data-ai-test") || "text";
          withBusy(button.id || "ai-settings-refresh-btn", "测试中...", async () => {
            await testAiSettings(target);
          }).catch(() => {});
        });
      });
      root.querySelectorAll("[data-ai-list-models]").forEach((button) => {
        button.addEventListener("click", () => {
          const target = button.getAttribute("data-ai-list-models") || "text";
          withBusy(button.id || "ai-settings-refresh-btn", "读取中...", async () => {
            await listAiModels(target);
          }).catch(() => {});
        });
      });
      ["text", "vision"].forEach((kind) => {
        ["provider", "base-url"].forEach((field) => {
          const node = document.getElementById(`ai-${kind}-${field}`);
          if (!node) return;
          node.addEventListener("change", () => {
            delete aiModelLists[kind];
          });
        });
      });
    }

    function readAiProviderForm(kind) {
      const providerNode = document.getElementById(`ai-${kind}-provider`);
      if (!providerNode) {
        return aiFormDrafts[kind] || {};
      }
      const draft = {
        provider: String(document.getElementById(`ai-${kind}-provider`)?.value || "").trim(),
        base_url: String(document.getElementById(`ai-${kind}-base-url`)?.value || "").trim(),
        model: String(document.getElementById(`ai-${kind}-model`)?.value || "").trim(),
        api_key: String(document.getElementById(`ai-${kind}-api-key`)?.value || "").trim(),
        clear_api_key: Boolean(document.getElementById(`ai-${kind}-clear-key`)?.checked),
      };
      aiFormDrafts[kind] = draft;
      return draft;
    }

    async function loadAiSettings() {
      const payload = await api("/api/ai-settings");
      renderAiSettings(payload);
      return payload;
    }

    async function saveAiSettings() {
      const textDraft = readAiProviderForm("text");
      const visionDraft = readAiProviderForm("vision");
      const payload = await api("/api/ai-settings/save", {
        method: "POST",
        body: JSON.stringify({
          user_id: userId,
          text: textDraft,
          vision: visionDraft,
        }),
      });
      aiFormDrafts = {};
      renderAiSettings(payload);
      setStatus("AI 设置已保存并立即生效。", "ok", 2400);
      await logFrontend("ai_settings_save_ok", {
        text_provider: payload.text?.provider || "",
        text_model: payload.text?.model || "",
        vision_provider: payload.vision?.provider || "",
        vision_model: payload.vision?.model || "",
      });
    }

    async function testAiSettings(target) {
      const textDraft = readAiProviderForm("text");
      const visionDraft = readAiProviderForm("vision");
      const payload = await api("/api/ai-settings/test", {
        method: "POST",
        body: JSON.stringify({
          user_id: userId,
          target,
          text: textDraft,
          vision: visionDraft,
        }),
      });
      setStatus(`${target === "vision" ? "视觉" : "文本"}模型正常，返回：${payload.reply}`, "ok", 2600);
      await logFrontend("ai_settings_test_ok", { target, duration_ms: payload.duration_ms });
    }

    async function listAiModels(target) {
      const textDraft = readAiProviderForm("text");
      const visionDraft = readAiProviderForm("vision");
      const draft = target === "vision" ? visionDraft : textDraft;
      const payload = await api("/api/ai-settings/models", {
        method: "POST",
        body: JSON.stringify({
          user_id: userId,
          target,
          text: textDraft,
          vision: visionDraft,
        }),
      });
      payload.form_signature = aiProviderSignature(draft);
      aiModelLists[target] = payload;
      const models = Array.isArray(payload.models) ? payload.models : [];
      const modelIds = models.map((item) => String(item.id || "").trim()).filter(Boolean);
      const selectedModel = modelIds.includes(draft.model) ? draft.model : (modelIds[0] || draft.model || "");
      aiSettingsState = aiSettingsState || {};
      aiSettingsState[target] = {
        ...(aiSettingsState[target] || {}),
        provider: draft.provider,
        base_url: draft.base_url,
        model: selectedModel,
      };
      aiFormDrafts[target] = {
        ...draft,
        model: selectedModel,
      };
      renderAiSettings(aiSettingsState);
      setStatus(`${target === "vision" ? "视觉" : "文本"}模型列表已读取：${payload.model_count || 0} 个`, "ok", 2600);
      await logFrontend("ai_settings_models_ok", { target, model_count: payload.model_count || 0, duration_ms: payload.duration_ms });
    }

    async function downloadExcel() {
      const selectedSources = Array.from(
        document.querySelectorAll('input[name="fetch-source"]:checked')
      ).map((node) => node.value);
      if (!selectedSources.length) {
        setStatus("至少勾选一个抓取来源。", "bad", 2400);
        showToast("至少勾选一个抓取来源。");
        return;
      }
      const params = new URLSearchParams({ user_id: userId });
      selectedSources.forEach((value) => params.append("selected_source", value));
      const response = await fetch(`/api/jobs/export?${params.toString()}`);
      if (!response.ok) {
        const text = await response.text();
        try {
          const payload = JSON.parse(text);
          throw new Error(payload.detail || text || ("HTTP " + response.status));
        } catch (error) {
          if (error instanceof SyntaxError) {
            throw new Error(text || ("HTTP " + response.status));
          }
          throw error;
        }
      }
      const blob = await response.blob();
      const disposition = response.headers.get("content-disposition") || "";
      const match = disposition.match(/filename="?([^\";]+)"?/i);
      const fileName = match ? match[1] : `resume-bot-jobs-${Date.now()}.xlsx`;
      const objectUrl = window.URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = objectUrl;
      link.download = fileName;
      document.body.appendChild(link);
      link.click();
      link.remove();
      window.URL.revokeObjectURL(objectUrl);
      setStatus("Excel 已生成并开始下载。", "ok", 2600);
      showToast("Excel 已开始下载。");
      await logFrontend("jobs_export_ok", {
        selected_sources: selectedSources,
        file_name: fileName,
      });
    }

    async function removeSettingItem(field, value) {
      await api("/api/preferences/remove-item", {
        method: "POST",
        body: JSON.stringify({ user_id: userId, field, value }),
      });
      setStatus(`已删除：${value}`, "ok", 1800);
      await logFrontend("preferences_remove_ok", { field, value });
      await loadDashboard();
    }

    async function markJob(fingerprint, action) {
      await api(`/api/jobs/${encodeURIComponent(fingerprint)}/action`, {
        method: "POST",
        body: JSON.stringify({ user_id: userId, action }),
      });
      const reviewItems = bossWorkbenchState.review?.review_items || [];
      reviewItems.forEach((item) => {
        if ((item.job || {}).fingerprint === fingerprint) {
          item.last_action = action;
        }
      });
      renderReviewWorkspace();
      setStatus(`已记录动作：${action}`, "ok", 1800);
      await logFrontend("job_action_ok", { fingerprint, action });
      await loadDashboard();
    }

    async function withBusy(buttonId, busyText, fn) {
      const button = document.getElementById(buttonId);
      const original = button.textContent;
      button.disabled = true;
      button.textContent = busyText;
      setBusy(busyText);
      try {
        await fn();
      } catch (error) {
        setStatus(error.message, "bad", 4200);
        showToast(error.message);
        await logFrontend("action_error", { buttonId, error: error.message });
        throw error;
      } finally {
        button.disabled = false;
        button.textContent = original;
        const root = document.getElementById("global-status");
        if (root.classList.contains("busy")) {
          root.className = "status-strip";
          root.textContent = "正在处理中，请稍等...";
        }
      }
    }

    async function withBossWorkbenchBusy(buttonId, busyText, fn) {
      if (bossWorkbenchBusy) {
        throw new Error("BOSS 工作台上一项操作还没完成，请先等它结束。");
      }
      bossWorkbenchBusy = true;
      try {
        return await withBusy(buttonId, busyText, fn);
      } finally {
        bossWorkbenchBusy = false;
      }
    }

    document.getElementById("upload-btn").addEventListener("click", () => withBusy("upload-btn", "解析中...", async () => {
      setStatus("正在上传并解析简历，请稍等...");
      await logFrontend("resume_upload_click");
      await uploadResume();
    }).catch(() => {}));
    document.getElementById("refresh-btn").addEventListener("click", () => withBusy("refresh-btn", "刷新中...", async () => {
      setStatus("正在刷新面板...");
      await logFrontend("dashboard_refresh_click");
      await loadDashboard();
    }).catch(() => {}));
    document.getElementById("boss-check-btn").addEventListener("click", () => withBusy("boss-check-btn", "检查中...", async () => {
      setStatus("正在检查 BOSS 状态...", "busy");
      await logFrontend("boss_gate_check_click");
      await loadDashboard();
      const gate = lastDashboard?.status?.boss_gate || {};
      setStatus(gate.message || "BOSS 检查完成。", bossGateStatusTone(gate), 3200);
    }).catch(() => {}));
    document.getElementById("boss-action-btn").addEventListener("click", () => {
      const action = document.getElementById("boss-action-btn").dataset.action || "";
      if (action !== "launch_browser") {
        return;
      }
      withBusy("boss-action-btn", "打开中...", async () => {
        setStatus("正在打开登录浏览器...", "busy");
        await logFrontend("boss_gate_action_click", { action });
        const result = await launchBossBrowser();
        await loadDashboard();
        const gate = lastDashboard?.status?.boss_gate || {};
        const message = result.message || gate.message || "登录浏览器已尝试打开。";
        setStatus(message, bossGateStatusTone(gate), 4200);
      }).catch(() => {});
    });
    document.getElementById("fetch-btn").addEventListener("click", () => withBusy("fetch-btn", "导入中...", async () => {
      setStatus("正在导入岗位并计算推荐，请稍等...");
      await logFrontend("jobs_refresh_click", { fetch_jobs: true });
      await fetchRecommendations(true);
    }).catch(() => {}));
    document.getElementById("dryrun-btn").addEventListener("click", () => withBusy("dryrun-btn", "计算中...", async () => {
      setStatus("正在重算推荐，请稍等...");
      await logFrontend("jobs_refresh_click", { fetch_jobs: false });
      await fetchRecommendations(false);
    }).catch(() => {}));
    document.getElementById("export-btn").addEventListener("click", () => withBusy("export-btn", "导出中...", async () => {
      setStatus("正在生成 Excel，请稍等...");
      await logFrontend("jobs_export_click");
      await downloadExcel();
    }).catch(() => {}));
    document.getElementById("boss-workbench-capture-btn").addEventListener("click", () => withBossWorkbenchBusy("boss-workbench-capture-btn", "采集中...", async () => {
      const form = readBossWorkbenchCaptureForm();
      setStatus(`正在采集 BOSS 列表：${form.city || "未填城市"} / ${form.keyword || "未填关键词"} · ${bossQuickFilterText(form)}`, "busy");
      await logFrontend("boss_workbench_capture_click", form);
      await captureBossWorkbenchQueue();
    }).catch(() => {}));
    document.getElementById("boss-workbench-supplement-btn").addEventListener("click", () => withBossWorkbenchBusy("boss-workbench-supplement-btn", "补抓中...", async () => {
      const form = readBossWorkbenchCaptureForm();
      setStatus("正在补抓并推荐：默认处理当前 session 的全部待补 JD。", "busy");
      await logFrontend("boss_workbench_supplement_click", form);
      await supplementBossWorkbenchDetails();
    }).catch(() => {}));
    const llmCheckButtons = ["llm-check-btn", "llm-check-btn-top"];
    llmCheckButtons.forEach((buttonId) => {
      document.getElementById(buttonId).addEventListener("click", () => withBusy(buttonId, "测试中...", async () => {
        setStatus("正在测试 AI 接口，请稍等...");
        await logFrontend("llm_check_click", { source: buttonId });
        await checkLlm();
      }).catch(() => {}));
    });
    document.getElementById("ai-settings-refresh-btn").addEventListener("click", () => withBusy("ai-settings-refresh-btn", "刷新中...", async () => {
      await loadAiSettings();
      setStatus("AI 设置已刷新。", "ok", 1800);
    }).catch(() => {}));

    document.getElementById("boss-workbench-refresh-btn").addEventListener("click", () => withBossWorkbenchBusy("boss-workbench-refresh-btn", "刷新中...", async () => {
      setStatus("正在刷新 BOSS 工作台...", "busy");
      await loadDashboard();
      await logFrontend("boss_workbench_refresh_click");
      const fetchSessionId = bossWorkbenchState.summary?.latest_fetch_session_id || "";
      const message = fetchSessionId
        ? `已刷新 BOSS 工作台，当前 session：${fetchSessionId}`
        : "已刷新 BOSS 工作台，但还没有可审阅的 BOSS 队列。";
      setStatus(message, fetchSessionId ? "ok" : "bad", 2800);
    }).catch(() => {}));
    document.getElementById("boss-workbench-default-btn").addEventListener("click", () => withBossWorkbenchBusy("boss-workbench-default-btn", "加载中...", async () => {
      setStatus("正在加载默认审阅...", "busy");
      await logFrontend("boss_workbench_review_click", { review_profile: "default" });
      await loadBossWorkbenchReview("");
    }).catch(() => {}));
    document.getElementById("boss-workbench-social-btn").addEventListener("click", () => withBossWorkbenchBusy("boss-workbench-social-btn", "加载中...", async () => {
      setStatus("正在加载 BOSS 社招预览...", "busy");
      await logFrontend("boss_workbench_review_click", { review_profile: "boss_social" });
      await loadBossWorkbenchReview("boss_social");
    }).catch(() => {}));
    document.getElementById("theme-toggle-btn").addEventListener("click", cycleTheme);
    bindPageNavigation();
    showPage(activePageId);
    document.getElementById("runtime-port").textContent = window.location.port || "unknown";
    loadDashboard().catch((error) => {
      console.error(error);
      setStatus(error.message, "bad", 4200);
      showToast(error.message);
    });
    loadAiSettings().catch((error) => {
      console.warn("AI settings load failed", error);
    });
    ensureBossWorkbenchUi();
    setStatus("页面已准备好。按首页步骤先准备资料、确认目标，再采集岗位。", "info", 2400);
    logFrontend("page_loaded");
  </script>
</body>
</html>
"""
