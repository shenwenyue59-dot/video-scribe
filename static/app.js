const $ = id => document.getElementById(id);
let inputMode = 'links', selected = null, current = null, view = 'plain', busy = false;
let list = [], toastTimer, lastRenderKey = '', initialized = false;
let previousStatuses = new Map();
const rowSelection = new Set();
function toast(text) { $('toast').textContent = text; $('toast').classList.add('show'); clearTimeout(toastTimer); toastTimer = setTimeout(() => $('toast').classList.remove('show'), 2800); }
async function api(path, options) {
  const response = await fetch(path, options);
  if (!response.ok) { let error; try { error = await response.json(); } catch {} throw new Error(typeof error?.detail === 'string' ? error.detail : `请求失败（${response.status}）`); }
  return response.json();
}
function switchInput(mode) {
  $('form-message').textContent = '';
  inputMode = mode; $('link-panel').hidden = mode !== 'links'; $('file-panel').hidden = mode !== 'files';
  for (const [id, active] of [['link-tab', mode === 'links'], ['file-tab', mode === 'files']]) { $(id).classList.toggle('active', active); $(id).setAttribute('aria-selected', String(active)); }
}
$('link-tab').onclick = () => switchInput('links'); $('file-tab').onclick = () => switchInput('files');
$('browser').onchange = () => { $('browser-help').hidden = $('browser').value === 'none'; $('browser-consent').checked = false; if ($('browser').value !== 'none') $('cookies').value = ''; };
$('cookies').onchange = () => { if ($('cookies').files.length) { $('browser').value = 'none'; $('browser').onchange(); } };
for (const id of ['link-tab', 'file-tab']) $(id).onkeydown = e => { if (['ArrowLeft','ArrowRight'].includes(e.key)) { e.preventDefault(); switchInput(inputMode === 'links' ? 'files' : 'links'); $(inputMode === 'links' ? 'link-tab' : 'file-tab').focus(); } };
$('links').oninput = () => { const count = new Set(($('links').value.match(/https?:\/\/.*?(?=https?:\/\/|[\s<>\[\]()"\u3000]|$)/g) || []).map(u => u.replace(/[.,;!，。；！]+$/, ''))).size; $('link-count').textContent = `${count} 个链接`; };
const updateFiles = () => { $('file-names').textContent = [...$('media').files].map(f => `${f.name} · ${(f.size/1048576).toFixed(1)} MB`).join('\n'); };
$('media').onchange = updateFiles;
for (const event of ['dragenter','dragover']) $('dropzone').addEventListener(event, e => { e.preventDefault(); $('dropzone').classList.add('drag'); });
for (const event of ['dragleave','drop']) $('dropzone').addEventListener(event, e => { e.preventDefault(); $('dropzone').classList.remove('drag'); });
$('dropzone').addEventListener('drop', e => { $('media').files = e.dataTransfer.files; updateFiles(); });
$('task-form').onsubmit = async e => {
  e.preventDefault(); if (busy) return;
  $('form-message').textContent = ''; busy = true; $('submit').disabled = true; $('submit').textContent = '正在提交…';
  const fields = () => { const data = new FormData(); data.append('language', $('language').value); data.append('model', $('model').value); return data; };
  let created = [];
  try {
    if (inputMode === 'links') {
      if (!$('links').value.trim()) throw new Error('请先粘贴视频链接。');
      const data = fields(); data.append('links', $('links').value); data.append('mode', $('mode').value);
      if ($('browser').value !== 'none' && !$('browser-consent').checked) throw new Error('请勾选本批任务的浏览器 Cookies 读取授权，或选择“不使用浏览器登录状态”。');
      data.append('browser', $('browser').value); data.append('browser_consent', String($('browser-consent').checked));
      if ($('cookies').files[0]) data.append('cookies', $('cookies').files[0]);
      created = (await api('/api/links', {method:'POST', body:data})).ids;
      $('cookies').value = '';
      $('browser-consent').checked = false;
    } else {
      const files = [...$('media').files];
      if (!files.length) throw new Error('请先选择视频或音频文件。');
      if (files.length > 10) throw new Error('一次最多上传 10 个文件。');
      if (files.some(f => f.size > 500*1024*1024)) throw new Error('有文件超过 500 MB，请先压缩或分段。');
      for (const [i, file] of files.entries()) { $('submit').textContent = `正在上传 ${i+1} / ${files.length}…`; const data = fields(); data.append('file', file); created.push(...(await api('/api/upload', {method:'POST', body:data})).ids); }
      $('media').value = ''; updateFiles();
    }
    toast(`已添加 ${created.length} 个任务`);
  } catch (error) { $('form-message').textContent = (created.length ? `已提交 ${created.length} 个任务；` : '') + error.message; }
  finally { if (created.length) selected = created[0]; busy = false; $('submit').disabled = false; $('submit').replaceChildren(document.createTextNode('生成逐字稿 ')); const arrow = document.createElement('span'); arrow.textContent='→'; $('submit').append(arrow); await refresh(); }
};
function element(tag, className, text) { const node = document.createElement(tag); if (className) node.className = className; if (text !== undefined) node.textContent = text; return node; }
const statuses = {queued:'排队中',running:'处理中',done:'已完成',error:'未完成',cancelled:'已取消'};
function renderJobs() {
  const completedIds = list.filter(j => j.status === 'done').map(j => j.id);
  const selectableIds = list.filter(j => ['done','error','cancelled'].includes(j.status)).map(j => j.id);
  for (const id of [...rowSelection]) if (!selectableIds.includes(id)) rowSelection.delete(id);
  const completedSelected = completedIds.filter(id => rowSelection.has(id));
  const renderKey = JSON.stringify([list, selected, [...rowSelection].sort()]);
  if (renderKey === lastRenderKey) return;
  lastRenderKey = renderKey;
  $('job-count').textContent = list.length; $('empty').hidden = list.length > 0;
  const pending = list.filter(j => ['queued','running'].includes(j.status)).length;
  $('queue-status').textContent = pending ? `${pending} 个任务待完成 · 按顺序在本机处理` : '你的内容会保存在这台电脑上';
  $('export-all').disabled = !completedIds.length;
  $('select-all').disabled = !selectableIds.length;
  $('select-all').textContent = selectableIds.length && selectableIds.every(id => rowSelection.has(id)) ? '取消全选' : '全选可清理';
  $('delete-selected').disabled = !rowSelection.size;
  $('delete-selected').textContent = `删除已选（${rowSelection.size}）`;
  $('export-selected').disabled = !completedSelected.length;
  $('export-selected').textContent = `导出已选表格（${completedSelected.length}）↓`;
  $('job-list').replaceChildren();
  for (const job of list) {
    const row = element('div', `job ${job.status}${selected === job.id ? ' selected' : ''}`);
    if (['done','error','cancelled'].includes(job.status)) {
      const check = document.createElement('input'); check.type = 'checkbox'; check.className = 'export-check'; check.checked = rowSelection.has(job.id);
      check.setAttribute('aria-label', `选择记录：${job.title}`);
      check.onchange = () => { if (check.checked) rowSelection.add(job.id); else rowSelection.delete(job.id); lastRenderKey=''; renderJobs(); };
      row.append(check);
    } else row.append(element('span','export-check-placeholder'));
    const icon = element('span','job-icon',job.status === 'done' ? '✓' : job.status === 'error' ? '!' : '≋');
    const main = element(job.status === 'done' ? 'button' : 'div','job-main');
    const oldAuthError = job.status === 'error' && /fresh cookies|cookies are needed/i.test(job.message);
    main.append(element('div','job-title',job.title),element('div','job-message',oldAuthError ? '视频尚未读取：抖音要求有效 Cookies。可选择本机浏览器登录状态后重试，或上传文件。' : job.message));
    main.title = job.title;
    if (job.status === 'done') main.onclick = async () => { selected = job.id; try { await loadReader(); renderJobs(); } catch(e) { toast(e.message); } };
    if (job.status === 'error' && job.url) {
      const recovery = element('div','job-recovery');
      const retry = element('button','','设置后重试');
      retry.onclick = () => { switchInput('links'); $('links').value = job.url; $('links').oninput(); $('language').value = job.language || 'auto'; $('model').value = job.model || 'base'; $('mode').value = job.mode || 'auto'; $('browser-consent').checked = false; $('advanced').open = true; $('form-message').textContent = '请先在常用浏览器中打开原视频，再选择同一个浏览器并提交。也可以选择 Cookies 文件。'; $('browser').scrollIntoView({block:'center',behavior:'smooth'}); $('browser').focus({preventScroll:true}); };
      const upload = element('button','','改用文件上传'); upload.onclick = () => { switchInput('files'); $('dropzone').scrollIntoView({block:'center',behavior:'smooth'}); $('media').focus({preventScroll:true}); };
      const original = element('a','','打开原视频 ↗'); original.href = job.url; original.target = '_blank'; original.rel = 'noopener noreferrer';
      recovery.append(retry, upload, original); main.append(recovery);
      const technical = job.technical_detail || (oldAuthError ? job.message : '');
      if (technical) { const details = element('details','error-detail'); details.append(element('summary','','查看技术详情'),element('pre','',technical)); main.append(details); }
    }
    const action = element('button','job-action', ['running','queued'].includes(job.status) ? '取消' : '删除');
    action.setAttribute('aria-label', `${action.textContent}：${job.title}`);
    action.onclick = async () => { try {
      if (['queued','running'].includes(job.status)) await api(`/api/jobs/${job.id}/cancel`,{method:'POST'});
      else { if (!confirm('删除这条任务和保存在本机的逐字稿？')) return; await api(`/api/jobs/${job.id}`,{method:'DELETE'}); if (selected===job.id) {selected=null;current=null;} }
      await refresh();
    } catch (error) { toast(error.message); } };
    row.append(icon, main, element('span','badge',statuses[job.status]), action); $('job-list').append(row);
  }
}
async function loadReader() {
  const job = list.find(j => j.id === selected);
  if (!job || job.status !== 'done') { $('reader').hidden = true; current = null; return; }
  const changed = current?.id !== job.id;
  if (changed) current = await api(`/api/jobs/${job.id}`);
  $('reader').hidden = false; $('reader-title').textContent = current.title; $('source').textContent = current.source;
  $('reader-meta').textContent = `${current.language} · ${current.segments.length} 段 · ${current.text.length} 字符${current.duration ? ` · ${Math.round(current.duration)} 秒` : ''}`;
  $('original').hidden = !current.url; if (current.url) $('original').href = current.url;
  if (changed) renderTranscript();
}
function clock(seconds) { const whole = Math.floor(seconds); return `${String(Math.floor(whole/3600)).padStart(2,'0')}:${String(Math.floor(whole%3600/60)).padStart(2,'0')}:${String(whole%60).padStart(2,'0')}`; }
function renderTranscript() {
  if (!current) return;
  $('plain-view').classList.toggle('active',view==='plain'); $('timed-view').classList.toggle('active',view==='timed');
  if (view==='plain') $('transcript').textContent=current.text;
  else { $('transcript').replaceChildren(); for(const segment of current.segments) { const row=element('div','segment'); row.append(element('time','',clock(segment.start)),element('p','',segment.text)); $('transcript').append(row); } }
}
$('plain-view').onclick=()=>{view='plain';renderTranscript();}; $('timed-view').onclick=()=>{view='timed';renderTranscript();};
$('copy').onclick=async()=>{if(!current)return;try{await navigator.clipboard.writeText(current.text);toast('逐字稿已复制');}catch{toast('无法访问剪贴板，请选择原文复制或导出 TXT。');}};
function download(url){const a=document.createElement('a');a.href=url;a.download='';document.body.append(a);a.click();a.remove();}
$('txt').onclick=()=>current&&download(`/api/jobs/${current.id}/export/txt`); $('srt').onclick=()=>current&&download(`/api/jobs/${current.id}/export/srt`); $('csv').onclick=()=>current&&download(`/api/jobs/${current.id}/export/csv`);
$('select-all').onclick=()=>{const ids=list.filter(j=>['done','error','cancelled'].includes(j.status)).map(j=>j.id);if(ids.every(id=>rowSelection.has(id)))ids.forEach(id=>rowSelection.delete(id));else ids.forEach(id=>rowSelection.add(id));lastRenderKey='';renderJobs();};
$('delete-selected').onclick=async()=>{const ids=[...rowSelection];if(!ids.length||!confirm(`确定删除选中的 ${ids.length} 条记录？删除后无法恢复。`))return;try{const result=await api(`/api/delete-selected?ids=${ids.join(',')}`,{method:'DELETE'});ids.forEach(id=>rowSelection.delete(id));if(ids.includes(selected)){selected=null;current=null;}toast(`已删除 ${result.deleted} 条记录`);await refresh();}catch(error){toast(error.message);}};
$('export-selected').onclick=()=>{const ids=list.filter(j=>j.status==='done'&&rowSelection.has(j.id)).map(j=>j.id);if(ids.length)download(`/api/export.xlsx?ids=${ids.join(',')}`);};
$('export-all').onclick=()=>download('/api/export.xlsx');
let polling=false;
async function refresh(){
  if(polling)return; polling=true;
  try{
    const nextList=await api('/api/jobs');
    const newlyCompleted=initialized ? nextList.filter(j=>j.status==='done'&&previousStatuses.get(j.id)!=='done') : [];
    list=nextList; $('connection').textContent='● 本机服务已连接';
    if(newlyCompleted.length){
      selected=newlyCompleted[0].id; current=null; view='plain';
      toast('逐字稿已生成，已在当前页面打开');
    }else if(!selected||!list.some(j=>j.id===selected&&j.status==='done')){
      selected=list.find(j=>j.status==='done')?.id||null; current=null;
    }
    previousStatuses=new Map(list.map(j=>[j.id,j.status])); initialized=true;
    renderJobs(); await loadReader();
  }catch(error){$('connection').textContent='服务连接中断，请重新运行 start.cmd';}
  finally{polling=false;}
}
refresh();setInterval(refresh,1800);
