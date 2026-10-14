(async function integrateVideoScribe() {
  const actions = document.querySelector('header .actions');
  if (actions) {
    const back = document.createElement('a');
    back.href = '/';
    back.textContent = '← 逐字稿工具';
    back.style.cssText = 'display:inline-flex;align-items:center;padding:9px 12px;border:1px solid #d9dfec;border-radius:8px;color:#24304a;text-decoration:none;font-size:13px;background:white';
    actions.prepend(back);
  }
  ['serviceSettings', 'importVideo', 'recoverJobs'].forEach(id => {
    const node = document.getElementById(id);
    if (node) node.hidden = true;
  });
  const connection = document.getElementById('connectionStatus');
  if (connection) connection.textContent = '已连接逐字稿工具 · 拆解在当前浏览器本地运行';

  const jobId = new URLSearchParams(location.search).get('job');
  if (!jobId) return;
  try {
    const response = await fetch('/api/jobs/' + encodeURIComponent(jobId));
    if (!response.ok) throw new Error('没有找到这篇逐字稿');
    const job = await response.json();
    if (job.status !== 'done' || !job.text?.trim()) throw new Error('逐字稿尚未生成完成');
    let record = state.records.find(item =>
      (!item.sample && item.source?.url && item.source.url === job.url) ||
      (!item.sample && item.title === job.title && item.text === job.text));
    if (record) {
      if (record.text !== job.text) {
        record.text = job.text.slice(0, 50000);
        record.analysis = analyze(record.text);
        record.manual = Array(10).fill('');
        record.topics = [];
        record.approved = false;
      }
      record.title = job.title.slice(0, 200);
    } else {
      record = make({title: job.title, text: job.text, sample: false});
      if (job.url) {
        try {
          record.source = {...parseVideoURL(job.url), kind: 'video', notes: '', textOrigin: 'asr'};
        } catch (_) {}
      }
      state.records.push(record);
    }
    state.selected = record.id;
    state.view = 'work';
    save();
    render();
    toast('逐字稿已导入，并完成本地拆解');
    history.replaceState({}, '', '/lab/');
  } catch (error) {
    toast(error.message || '导入逐字稿失败');
  }
})();
