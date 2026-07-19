const state = {
  calcId: null,
  strategy: 'operational_window',
  filters: { company: '', line: '', op: '', product: '', confidence: '' },
  auditor: false,
  page: 'summary',
  auditData: null,
};

const $ = (s) => document.querySelector(s);
const $$ = (s) => [...document.querySelectorAll(s)];
const api = async (url, options = {}) => {
  const response = await fetch(url, options);
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    throw new Error(payload.detail || `Falha HTTP ${response.status}`);
  }
  return response.json();
};
const esc = (value) => String(value ?? '').replace(/[&<>'"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));
const params = () => {
  const q = new URLSearchParams({ calc_id: state.calcId });
  Object.entries(state.filters).forEach(([k,v]) => { if (v) q.set(k, v); });
  return q.toString();
};
const format = (value, unit = '') => {
  if (value === null || value === undefined || value === '') return 'Sem dado';
  const n = Number(value);
  if (!Number.isFinite(n)) return String(value);
  if (unit === '%' || unit === 'PP') return `${(n * 100).toLocaleString('pt-BR',{minimumFractionDigits:2,maximumFractionDigits:2})}${unit === '%' ? '%' : ' p.p.'}`;
  if (['CX','BR','BD','UN','OP','LINHA'].includes(unit)) return `${n.toLocaleString('pt-BR',{maximumFractionDigits:3})}`;
  return n.toLocaleString('pt-BR',{minimumFractionDigits:2,maximumFractionDigits:2});
};
const statusLabel = (confidence) => {
  const t = String(confidence || '').toUpperCase();
  if (t.includes('PENDENTE')) return 'Pendente';
  if (t.includes('CONFLITO') || t.includes('DIVERG')) return 'Divergente';
  if (t.includes('ESTIMADO') || t.includes('RATEADO') || t.includes('PARAMETRIZADO')) return 'Estimado';
  return 'Confirmado';
};
const bucket = (confidence) => {
  const t = String(confidence || '').toUpperCase();
  if (t.includes('CONFLITO') || t.includes('DIVERG') || t.includes('CRIT')) return 'critical';
  if (t.includes('PENDENTE')) return 'pending';
  if (t.includes('ESTIMADO') || t.includes('RATEADO') || t.includes('PARAMETRIZADO')) return 'estimated';
  return 'confirmed';
};
const pill = (confidence) => `<span class="status-pill ${bucket(confidence)}"><i class="confidence-dot ${bucket(confidence)}"></i>${esc(statusLabel(confidence))}</span>`;
const toast = (message) => {
  const el = $('#toast'); el.textContent = message; el.classList.add('show');
  setTimeout(() => el.classList.remove('show'), 2800);
};

function showPage(page) {
  state.page = page;
  $$('.page').forEach(el => el.classList.toggle('active', el.id === `page-${page}`));
  $$('.nav-item').forEach(el => el.classList.toggle('active', el.dataset.page === page));
  const titles = {summary:'Resumo Executivo',lines:'Consumo por Linha',ops:'Consumo por OP',detail:'Detalhamento da OP',losses:'Perdas MPOP',repasse:'Repasse',audit:'Auditoria',import:'Importação'};
  $('#pageTitle').textContent = titles[page] || 'SAQR';
  $('#filterBar').style.display = ['import','audit','detail'].includes(page) ? 'none' : '';
  $('#sidebar').classList.remove('open');
  if (page === 'summary') loadSummary();
  if (page === 'lines') loadLines();
  if (page === 'ops') loadOps();
  if (page === 'losses') loadLosses();
  if (page === 'repasse') loadRepasse();
  if (page === 'audit') loadAudit();
  if (page === 'import') loadImports();
}

function option(select, value, label) {
  const el = document.createElement('option'); el.value = value; el.textContent = label; select.appendChild(el);
}

async function init() {
  bindEvents();
  try {
    const data = await api('/api/state');
    state.calcId = data.calculation_id;
    state.strategy = data.context.strategy;
    renderContext(data.context);
    renderStrategy(data.strategies);
    renderFilters(data.filters);
    renderImports(data.imports);
    await loadSummary();
  } catch (e) {
    toast(e.message);
    showPage('import');
  }
}

function renderContext(context) {
  const cfg = context.config || {};
  const day = cfg.operational_day || {};
  $('#operationalWindow').textContent = `${day.date || ''} · ${String(day.start || '').replace('T',' ')} até ${String(day.end || '').replace('T',' ')}`;
  $('#calculationInfo').textContent = `Execução #${context.calculation.id} · Motor ${context.calculation.engine_version}`;
}

function renderStrategy(strategies) {
  const select = $('#strategySelect'); select.innerHTML = '';
  strategies.forEach(x => option(select, x.key, x.label)); select.value = state.strategy;
}

function renderFilters(filters) {
  const day = $('#filterDay'); day.innerHTML = ''; filters.operational_days.forEach(x => option(day,x,x));
  const company = $('#filterCompany'); company.innerHTML = '<option value="">Todas</option>'; filters.companies.forEach(x => option(company,x,x));
  const line = $('#filterLine'); line.innerHTML = '<option value="">Todas</option>'; filters.lines.forEach(([k,v]) => option(line,k,v));
  const op = $('#filterOp'); op.innerHTML = '<option value="">Todas</option>'; filters.ops.forEach(x => option(op,x,x));
  const product = $('#filterProduct'); product.innerHTML = '<option value="">Todos</option>'; filters.products.forEach(([k,v]) => option(product,k,`${k} · ${v}`));
  const confidence = $('#filterConfidence'); confidence.innerHTML = '<option value="">Todos</option>'; filters.confidence_levels.forEach(x => option(confidence,x.value,x.label));
}

async function switchStrategy(strategy) {
  $('#strategySelect').disabled = true;
  try {
    const data = await api('/api/strategy',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({strategy})});
    state.calcId = data.calculation_id; state.strategy = strategy; renderContext(data.context);
    const filters = await api(`/api/filters?calc_id=${state.calcId}`); renderFilters(filters);
    toast(`Estratégia ativa: ${data.strategy_label}`);
    showPage(state.page === 'detail' ? 'ops' : state.page);
  } catch (e) { toast(e.message); $('#strategySelect').value = state.strategy; }
  finally { $('#strategySelect').disabled = false; }
}

async function loadSummary() {
  $('#kpiGrid').innerHTML = '<div class="skeleton tall"></div>';
  try {
    const data = await api(`/api/summary?${params()}`);
    $('#summaryScope').textContent = `${data.row_count} OPs no escopo atual. Estratégia: ${data.calculation_context.strategy.replaceAll('_',' ')}.`;
    $('#kpiGrid').innerHTML = data.metrics.map(metric => {
      let value;
      if (metric.value_by_unit) {
        value = `<div class="kpi-value multi">${Object.entries(metric.value_by_unit).map(([u,v])=>`<span>${format(v,u)}<small class="kpi-unit">${esc(u)}</small></span>`).join('')}</div>`;
      } else {
        value = `<div class="kpi-value">${format(metric.value,metric.unit)}${metric.unit && !['%','PP','OP','LINHA'].includes(metric.unit) ? `<small class="kpi-unit">${esc(metric.unit)}</small>`:''}</div>`;
      }
      return `<article class="kpi"><div class="kpi-label"><span>${esc(metric.name)}</span><button class="audit-link" data-explain-aggregate='${esc(JSON.stringify({metric_code:metric.code,indicator_ids:metric.source_indicator_ids,filters:state.filters}))}' title="Como este valor foi calculado?">◎</button></div>${value}<div style="margin-top:10px">${pill(metric.confidence)}</div></article>`;
    }).join('');
    const pending = data.metrics.filter(x => x.confidence_bucket !== 'confirmed');
    $('#summaryAlerts').innerHTML = pending.length ? pending.slice(0,6).map(x=>`<span class="alert-chip">${esc(x.name)} · ${statusLabel(x.confidence)}</span>`).join('') : '<span class="alert-chip">Todos os indicadores do escopo estão confirmados.</span>';
    bindExplainButtons();
  } catch (e) { $('#kpiGrid').innerHTML = `<div class="empty">${esc(e.message)}</div>`; }
}

async function loadLines() {
  $('#linesBody').innerHTML = '<tr><td colspan="13"><div class="skeleton"></div></td></tr>';
  try {
    const rows = await api(`/api/lines?${params()}`);
    $('#linesBody').innerHTML = rows.length ? rows.map(r => `<tr class="clickable" data-line="${esc(r.line_key)}">
      <td><strong>${esc(r.center_resource)}</strong></td><td>${esc(r.line_name)}</td><td class="number">${r.op_count}</td>
      <td>${Object.entries(r.production_by_unit||{}).map(([u,v])=>`${format(v,u)} ${esc(u)}`).join('<br>')}</td>
      <td class="number metric-cell">${format(r.consumption_active_kg,'KG')} kg ${auditCell(r.indicator_ids.consumption)}</td>
      <td class="number metric-cell">${format(r.lixo_mpop_kg,'KG')} kg ${auditCell(r.indicator_ids.lixo)}</td>
      <td class="number metric-cell">${format(r.repasse_kg,'KG')} kg ${auditCell(r.indicator_ids.repasse)}</td>
      <td class="number metric-cell">${format(r.mp_aproveitada_kg,'KG')} kg ${auditCell(r.indicator_ids.mp_aproveitada)}</td>
      <td class="number metric-cell">${format(r.rendimento_operacional,'%')} ${auditCell(r.indicator_ids.rendimento)}</td>
      <td class="number metric-cell">${format(r.loss_standard_percent,'%')} ${auditCell(r.indicator_ids.loss_standard)}</td>
      <td class="number metric-cell">${format(r.excesso_sobre_padrao_kg,'KG')} kg ${auditCell(r.indicator_ids.excesso)}</td>
      <td>${pill(r.overall_confidence)}</td><td class="number">${r.pending_count}</td></tr>`).join('') : '<tr><td colspan="13" class="empty">Nenhuma linha encontrada.</td></tr>';
    $$('#linesBody tr[data-line]').forEach(tr => tr.addEventListener('click', e => { if(e.target.closest('.audit-link')) return; state.filters.line=tr.dataset.line; $('#filterLine').value=tr.dataset.line; showPage('ops'); }));
    bindExplainButtons();
  } catch(e) { $('#linesBody').innerHTML=`<tr><td colspan="13" class="empty">${esc(e.message)}</td></tr>`; }
}

const auditCell = (id) => id ? `<button class="audit-link" data-indicator="${id}" title="Como este valor foi calculado?">◎</button>` : '';

async function loadOps() {
  $('#opsBody').innerHTML = '<tr><td colspan="17"><div class="skeleton"></div></td></tr>';
  try {
    const rows = await api(`/api/ops?${params()}`);
    $('#opsBody').innerHTML = rows.length ? rows.map(r => `<tr class="clickable" data-op="${esc(r.op)}">
      <td>${esc(r.company)}</td><td><strong>${esc(r.op)}</strong></td><td>${esc(r.product_code)}</td><td class="description-cell">${esc(r.product_description)}</td>
      <td>${esc(r.center_resource||'Sem centro')}</td><td>${esc(r.line_name)}</td><td class="number">${format(r.production,r.production_unit)} ${esc(r.production_unit)}</td>
      <td class="number metric-cell">${format(r.theoretical_consumption_kg,'KG')} kg ${auditCell(r.indicator_ids.theoretical)}</td>
      <td class="number metric-cell">${format(r.consumption_active_kg,'KG')} kg ${auditCell(r.indicator_ids.consumption_active)}</td>
      <td class="number metric-cell">${format(r.consumption_variance_kg,'KG')} kg ${auditCell(r.indicator_ids.variance)}</td>
      <td class="number metric-cell">${format(r.lixo_mpop_kg,'KG')} kg ${auditCell(r.indicator_ids.lixo)}</td>
      <td class="number metric-cell">${format(r.repasse_kg,'KG')} kg ${auditCell(r.indicator_ids.repasse)}</td>
      <td class="number metric-cell">${format(r.mp_aproveitada_kg,'KG')} kg ${auditCell(r.indicator_ids.mp_aproveitada)}</td>
      <td class="number metric-cell">${format(r.rendimento_operacional,'%')} ${auditCell(r.indicator_ids.rendimento)}</td>
      <td class="number metric-cell">${format(r.perda_padrao_percent,'%')} ${auditCell(r.indicator_ids.perda_padrao)}</td>
      <td class="number metric-cell">${format(r.excesso_sobre_padrao_kg,'KG')} kg ${auditCell(r.indicator_ids.excesso_kg)}</td>
      <td>${pill(r.overall_confidence)}</td></tr>`).join('') : '<tr><td colspan="17" class="empty">Nenhuma OP encontrada.</td></tr>';
    $$('#opsBody tr[data-op]').forEach(tr => tr.addEventListener('click', e => { if(e.target.closest('.audit-link')) return; openOp(tr.dataset.op, rows.find(r=>r.op===tr.dataset.op)); }));
    bindExplainButtons();
  } catch(e) { $('#opsBody').innerHTML=`<tr><td colspan="17" class="empty">${esc(e.message)}</td></tr>`; }
}

async function openOp(op, row) {
  showPage('detail');
  $('#detailTitle').textContent = `OP ${op}`;
  $('#detailSubtitle').textContent = row ? `${row.company} · ${row.product_code} · ${row.product_description}` : '';
  $('#detailSections').innerHTML = '<div class="skeleton tall"></div>';
  try {
    const data = await api(`/api/ops/${encodeURIComponent(op)}/components?calc_id=${state.calcId}`);
    const order = ['Produção','Matéria-prima','Salmoura','Aditivos','Azeites e óleos','Embalagens','Lixo','Repasse','Não classificado'];
    const sections = order.filter(s => Object.prototype.hasOwnProperty.call(data.sections, s)).concat(Object.keys(data.sections).filter(s=>!order.includes(s)));
    $('#detailSections').innerHTML = sections.length ? sections.map(section => {
      const rows = data.sections[section];
      const content = rows.length ? `<div class="table-wrap"><table><thead><tr><th>Código</th><th>Descrição</th><th>Grupo</th><th>Movimento</th><th>Qtd. original</th><th>Unidade</th><th>Qtd. convertida</th><th>Origem</th><th>Data e hora</th><th>Confiança</th><th>Fonte</th></tr></thead><tbody>${rows.map(r=>`<tr>
      <td>${esc(r.component_code)}</td><td class="description-cell">${esc(r.description)}</td><td>${esc(r.classification_group||'Sem classificação')}</td><td>${esc(r.movement_code)}</td>
      <td class="number">${format(r.quantity_original,r.unit_original)}</td><td>${esc(r.unit_original||'')}</td><td class="number">${r.quantity_converted==null?'Sem conversão':`${format(r.quantity_converted,r.unit_converted)} ${esc(r.unit_converted)}`}</td>
      <td>${esc(r.source_origin)}</td><td>${esc((r.occurred_at||'Sem horário').replace('T',' '))}</td><td>${pill(r.confidence)}</td><td><small>${esc(r.source_file)}<br>${esc(r.source_row)}<br>${esc((r.record_hash||'').slice(0,12))}…</small></td></tr>`).join('')}</tbody></table></div>` : `<div class="empty category-empty">Nenhum registro desta categoria para a OP.</div>`;
      return `<div class="panel detail-panel"><div class="panel-header"><div><h3>${esc(section)}</h3><p>${rows.length} registros originários do Motor Analítico</p></div><span>${rows.filter(r=>r.confidence_bucket!=='confirmed').length} pendências/estimativas</span></div>${content}</div>`;
    }).join('') : '<div class="panel empty">Nenhum componente encontrado.</div>';
  } catch(e) { $('#detailSections').innerHTML=`<div class="panel empty">${esc(e.message)}</div>`; }
}

async function loadLosses() {
  $('#lossesBody').innerHTML='<tr><td colspan="8"><div class="skeleton"></div></td></tr>';
  try { const rows=await api(`/api/losses?calc_id=${state.calcId}${state.filters.op?`&op=${encodeURIComponent(state.filters.op)}`:''}`);
    $('#lossesBody').innerHTML=rows.length?rows.map(r=>`<tr><td>${esc(r.op)}</td><td>${esc(r.component_code)}</td><td class="description-cell">${esc(r.description)}</td><td>${esc(r.classification_group||'Sem classificação')}</td><td class="number">${format(r.quantity_original,r.unit_original)} ${esc(r.unit_original||'')}</td><td class="number">${r.quantity_converted==null?'Sem conversão':`${format(r.quantity_converted,r.unit_converted)} ${esc(r.unit_converted)}`}</td><td>${pill(r.confidence)}</td><td><small>${esc(r.source_file)}<br>${esc(r.source_row)}</small></td></tr>`).join(''):'<tr><td colspan="8" class="empty">Nenhuma perda encontrada.</td></tr>';
  } catch(e){$('#lossesBody').innerHTML=`<tr><td colspan="8" class="empty">${esc(e.message)}</td></tr>`;}
}
async function loadRepasse() {
  $('#repasseBody').innerHTML='<tr><td colspan="9"><div class="skeleton"></div></td></tr>';
  try { const rows=await api(`/api/repasse?calc_id=${state.calcId}${state.filters.op?`&op=${encodeURIComponent(state.filters.op)}`:''}`);
    $('#repasseBody').innerHTML=rows.length?rows.map(r=>`<tr><td>${esc(r.op)}</td><td>${esc(r.component_code)}</td><td class="description-cell">${esc(r.description)}</td><td>${esc(r.movement_code)}</td><td class="number">${format(r.quantity_original,r.unit_original)} ${esc(r.unit_original)}</td><td class="number">${format(r.quantity_converted,r.unit_converted)} ${esc(r.unit_converted)}</td><td>${esc((r.occurred_at||'').replace('T',' '))}</td><td>${pill(r.confidence)}</td><td><small>${esc(r.source_file)}<br>${esc(r.source_row)}</small></td></tr>`).join(''):'<tr><td colspan="9" class="empty">Nenhum repasse encontrado.</td></tr>';
  } catch(e){$('#repasseBody').innerHTML=`<tr><td colspan="9" class="empty">${esc(e.message)}</td></tr>`;}
}

const auditLabels={componentes_conflitantes:'Componentes conflitantes',ops_sem_centro:'OPs sem Centro',ops_multicentro:'OPs multicentro',perdas_sem_classificacao:'Perdas sem classificação',movimentos_fora_janela:'Movimentos fora da janela',divergencias_referencia:'Divergências com referência',parametros_provisorios:'Parâmetros provisórios',itens_sem_peso:'Itens sem peso',possiveis_duplas_deducoes:'Possíveis duplas deduções'};
async function loadAudit(){
  $('#auditOverview').innerHTML='<div class="skeleton tall"></div>';
  try { state.auditData=await api(`/api/audit?calc_id=${state.calcId}`); const keys=Object.keys(auditLabels);
    $('#auditOverview').innerHTML=keys.map(k=>`<button class="audit-card" data-audit="${k}"><strong>${state.auditData[k]?.count||0}</strong><span>${auditLabels[k]}</span></button>`).join('');
    $$('.audit-card').forEach(b=>b.addEventListener('click',()=>renderAuditCategory(b.dataset.audit)));
    renderAuditCategory(keys[0]);
  } catch(e){$('#auditOverview').innerHTML=`<div class="panel empty">${esc(e.message)}</div>`;}
}
function renderAuditCategory(key){
  $$('.audit-card').forEach(b=>b.classList.toggle('active',b.dataset.audit===key));
  $('#auditTableTitle').textContent=auditLabels[key]; const rows=state.auditData[key]?.rows||[]; $('#auditTableDescription').textContent=`${rows.length} ocorrências preservadas para análise.`;
  $('#auditBody').innerHTML=rows.length?rows.map(r=>`<tr><td>${pill(r.severity==='ERROR'?'DIVERGENTE':r.severity==='WARNING'?'ESTIMADO_ATENCAO':'CONFIRMADO')}</td><td>${esc(r.audit_code||r.code)}</td><td>${esc(r.entity_type||r.entity)}</td><td>${esc(r.entity_key||'')}</td><td class="description-cell">${esc(r.message)}</td><td>${esc(r.official_value||'')}</td><td>${esc(r.reference_value||'')}</td><td>${esc(r.difference||'')}</td></tr>`).join(''):'<tr><td colspan="8" class="empty">Nenhuma ocorrência nesta categoria.</td></tr>';
}

async function loadImports(){ try{renderImports(await api('/api/imports'));}catch(e){toast(e.message);} }
function renderImports(rows){
  $('#sourceStatusList').innerHTML=rows.map(r=>`<div class="source-row"><div><strong>${esc(r.label)} ${r.optional?'<small>opcional</small>':''}</strong><small>${esc(r.filename||'Não importado')}<br>${r.imported_at?esc(new Date(r.imported_at).toLocaleString('pt-BR')):'Sem importação'}<br>${esc((r.sha256||'').slice(0,20))}${r.sha256?'…':''}</small></div><div class="source-stat"><span>Status</span><b>${esc(r.status)}</b></div><div class="source-stat"><span>Registros</span><b>${Number(r.rows_read||0).toLocaleString('pt-BR')}</b></div><div class="source-stat"><span>Alertas</span><b>${r.warnings||0} / ${r.errors||0}</b></div></div>`).join('');
}

function bindExplainButtons(){
  $$('[data-indicator]').forEach(b=>b.onclick=e=>{e.stopPropagation();openExplanation(Number(b.dataset.indicator));});
  $$('[data-explain-aggregate]').forEach(b=>b.onclick=e=>{e.stopPropagation();openAggregateExplanation(JSON.parse(b.dataset.explainAggregate));});
}
async function openExplanation(id){
  openModal(); $('#auditModalContent').innerHTML='<div class="skeleton tall"></div>';
  try{renderExplanation(await api(`/api/explain/${id}`));}catch(e){$('#auditModalContent').innerHTML=`<div class="empty">${esc(e.message)}</div>`;}
}
async function openAggregateExplanation(payload){
  openModal(); $('#auditModalContent').innerHTML='<div class="skeleton tall"></div>';
  try{renderAggregateExplanation(await api('/api/explain-aggregate',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)}));}catch(e){$('#auditModalContent').innerHTML=`<div class="empty">${esc(e.message)}</div>`;}
}
function openModal(){ $('#auditModal').classList.add('open'); $('#auditModal').setAttribute('aria-hidden','false'); }
function closeModal(){ $('#auditModal').classList.remove('open'); $('#auditModal').setAttribute('aria-hidden','true'); }
function renderExplanation(x){
  const i=x.indicator||{}, rule=x.formula_and_rule||{}, calc=x.calculation_execution||{};
  $('#auditModalContent').innerHTML=`
    <section class="audit-section"><div class="audit-grid"><div class="audit-field"><span>Indicador</span><strong>${esc(i.indicator_code)}</strong></div><div class="audit-field"><span>Resultado</span><strong>${format(i.value,i.unit)} ${esc(i.unit||'')}</strong></div><div class="audit-field"><span>Confiança</span><strong>${esc(i.confidence)}</strong></div><div class="audit-field"><span>Execução</span><strong>#${esc(i.calculation_run_id)} · ${esc(calc.engine_version||'')}</strong></div></div></section>
    <section class="audit-section"><h3>Regra e fórmula</h3><div class="audit-grid"><div class="audit-field"><span>Regra</span><strong>${esc(i.rule_code)} v${esc(i.rule_version)}</strong></div><div class="audit-field"><span>Fonte oficial</span><strong>${esc(x.indicator_catalog?.official_source||'')}</strong></div></div><p>${esc(rule.formula||x.indicator_catalog?.formula_description||'')}</p></section>
    <section class="audit-section"><h3>Parâmetros aplicados</h3><pre class="code-block">${esc(JSON.stringify(x.parameters_applied,null,2))}</pre></section>
    <section class="audit-section"><h3>Linhagem</h3><p>${x.lineage_summary?.source_records||0} registros em ${x.lineage_summary?.source_files||0} fontes.</p>${lineageHtml(x.source_records_and_events||[])}</section>
    <section class="audit-section"><h3>Divergências e auditoria</h3>${(x.related_audit||[]).length?`<div class="lineage-list">${x.related_audit.map(a=>`<div class="lineage-item"><strong>${esc(a.audit_code)} · ${esc(a.severity)}</strong><small>${esc(a.message)}<br>Oficial: ${esc(a.official_value||'')} · Referência: ${esc(a.reference_value||'')}</small></div>`).join('')}</div>`:'<p>Nenhuma divergência diretamente relacionada.</p>'}</section>`;
}
function lineageHtml(rows){ const shown=rows.slice(0,80); return `<div class="lineage-list">${shown.map(r=>`<div class="lineage-item"><strong>${esc(r.filename||'Fonte analítica')} · ${esc(r.source_row||'')}</strong><small>Hash: ${esc(r.record_hash||'')}<br>SHA-256: ${esc(r.sha256||'')}<br>Evento: ${esc(r.event_type||'')} ${esc(r.occurred_at||'')}</small></div>`).join('')}</div>${rows.length>shown.length?`<p>Exibindo ${shown.length} de ${rows.length} registros.</p>`:''}`; }
function renderAggregateExplanation(x){
  $('#auditModalContent').innerHTML=`<section class="audit-section"><div class="audit-grid"><div class="audit-field"><span>Indicador solicitado</span><strong>${esc(x.metric_code)}</strong></div><div class="audit-field"><span>Indicadores-fonte</span><strong>${x.source_indicators?.length||0}</strong></div><div class="audit-field"><span>Registros originais</span><strong>${x.source_record_count||0}</strong></div><div class="audit-field"><span>Camada</span><strong>Motor Analítico</strong></div></div><p>${esc(x.aggregation||x.message||'')}</p></section>
  <section class="audit-section"><h3>Indicadores utilizados</h3><div class="lineage-list">${(x.source_indicators||[]).map(i=>`<div class="lineage-item"><strong>${esc(i.indicator_code)} · ${esc(i.dimension_key)}</strong><small>${format(i.value,i.unit)} ${esc(i.unit)} · ${esc(i.rule_code)} v${esc(i.rule_version)} · ${esc(i.confidence)}</small></div>`).join('')}</div></section>
  <section class="audit-section"><h3>Registros e fontes</h3>${lineageHtml(x.source_records||[])}</section>
  <section class="audit-section"><h3>Divergências relacionadas</h3>${(x.related_audit||[]).length?`<div class="lineage-list">${x.related_audit.map(a=>`<div class="lineage-item"><strong>${esc(a.audit_code)} · ${esc(a.severity)}</strong><small>${esc(a.message)}</small></div>`).join('')}</div>`:'<p>Nenhuma divergência diretamente relacionada.</p>'}</section>`;
}

function bindEvents(){
  $$('.nav-item').forEach(b=>b.addEventListener('click',()=>showPage(b.dataset.page)));
  $$('[data-page-jump]').forEach(b=>b.addEventListener('click',()=>showPage(b.dataset.pageJump)));
  $('#strategySelect').addEventListener('change',e=>switchStrategy(e.target.value));
  $('#auditModeButton').addEventListener('click',()=>{state.auditor=!state.auditor;document.body.classList.toggle('auditor-on',state.auditor);$('#auditModeButton').classList.toggle('active',state.auditor);toast(state.auditor?'Modo Auditor ativado':'Modo Auditor desativado');});
  $('#menuButton').addEventListener('click',()=>$('#sidebar').classList.toggle('open'));
  $('#backToOps').addEventListener('click',()=>showPage('ops'));
  $$('[data-close-modal]').forEach(x=>x.addEventListener('click',closeModal));
  document.addEventListener('keydown',e=>{if(e.key==='Escape')closeModal();});
  const mapping={filterCompany:'company',filterLine:'line',filterOp:'op',filterProduct:'product',filterConfidence:'confidence'};
  Object.entries(mapping).forEach(([id,key])=>$('#'+id).addEventListener('change',e=>{state.filters[key]=e.target.value;showPage(state.page);}));
  $('#clearFilters').addEventListener('click',()=>{Object.keys(state.filters).forEach(k=>state.filters[k]='');['filterCompany','filterLine','filterOp','filterProduct','filterConfidence'].forEach(id=>$('#'+id).value='');showPage(state.page);});
  $('#importForm').addEventListener('submit',async e=>{
    e.preventDefault(); const button=$('#importButton'),progress=$('#importProgress'); button.disabled=true;progress.textContent='Validando estruturas, importando registros e executando o Motor Analítico...';
    try{const data=await api('/api/import',{method:'POST',body:new FormData(e.target)});state.calcId=data.calculation_id;state.strategy='operational_window';renderImports(data.imports);renderContext(data.context);const filters=await api(`/api/filters?calc_id=${state.calcId}`);renderFilters(filters);progress.textContent='Importação concluída. O novo cálculo está disponível.';toast('Fontes importadas e auditadas.');e.target.reset();}
    catch(err){progress.textContent=err.message;toast(err.message);}finally{button.disabled=false;}
  });
}

document.addEventListener('DOMContentLoaded',init);
