(function(){
 'use strict';
 if(window.__LAB_TOKEN__)return;
 const key='blue-public-workspace-v1',C=window.LabCore;
 const read=()=>{const raw=localStorage.getItem(key);if(!raw)return {revision:0,data:C.empty()};const value=JSON.parse(raw);if(!value.data||value.data.schemaVersion!==2)throw Error('浏览器项目数据损坏，请从备份恢复');return value};
 let recognizing=false;
 async function recognize(image){
  if(recognizing)throw Error('正在识别另一张图片，请稍后重试');
  recognizing=true;let worker;
  const status=document.querySelector('#metricReadStatus');
  try{
   if(!window.Tesseract)await new Promise((resolve,reject)=>{const script=document.createElement('script');script.src='vendor/tesseract.min.js';script.onload=resolve;script.onerror=()=>{script.remove();reject(Error('图片识别组件加载失败，请检查网络或粘贴文字'))};document.head.appendChild(script)});
   worker=await Tesseract.createWorker('chi_sim+eng',1,{workerPath:new URL('vendor/worker.min.js',document.baseURI).href,corePath:new URL('vendor/core/',document.baseURI).href,langPath:new URL('vendor/lang',document.baseURI).href,gzip:false,logger:m=>{if(status)status.textContent='正在本机识别图片… '+Math.round((m.progress||0)*100)+'%（首次需下载识别组件）'}});
   const bitmap=await createImageBitmap(await(await fetch(image)).blob());const scale=Math.min(2,4000/Math.max(bitmap.width,bitmap.height));const canvas=document.createElement('canvas');canvas.width=Math.round(bitmap.width*scale);canvas.height=Math.round(bitmap.height*scale);canvas.getContext('2d').drawImage(bitmap,0,0,canvas.width,canvas.height);bitmap.close();await worker.setParameters({tessedit_pageseg_mode:'6'});const result=await worker.recognize(canvas);return {text:result.data.text};
  }catch(e){throw Error('图片识别未完成，请换清晰截图或粘贴文字。'+(e.message||''))}
  finally{recognizing=false;if(worker)await worker.terminate()}
 }
 window.LabBrowserAPI=async function(path,data,method){
  if(path==='/api/recognize-image')return recognize(data.image);
  if(path!=='/api/workspace')throw Error('公开网页版不支持此服务，请使用文字或字幕导入');
  if(method==='PUT'){
   const current=read();if(data.revision!==current.revision){const error=Error('另一标签页已修改项目，请导出备份后载入最新版本');error.status=409;throw error}
   if(data.data?.schemaVersion!==2||!Array.isArray(data.data.records))throw Error('项目备份格式不正确');
   const next={revision:current.revision+1,data:data.data};
   try{localStorage.setItem(key,JSON.stringify(next))}catch{throw Error('浏览器存储空间不足或被禁用，请导出项目备份')}
   return {revision:next.revision};
  }
  return read();
 };
 const hide=selector=>{const el=document.querySelector(selector);if(el)el.hidden=true};
 hide('#serviceSettings');hide('#parseVideo');hide('#recoverJobs');
 const check=document.querySelector('#withTranscription');if(check){check.checked=false;check.closest('label').hidden=true;}
 const notice=document.querySelector('#videoDialog .video-status');if(notice)notice.textContent='公开版支持保存视频链接和手动粘贴字幕；自动读取平台视频及语音转写需本地服务版。';
 refreshService=async function(){document.querySelector('#connectionStatus').textContent='公开网页版 · 数据保存在当前浏览器 · 无需 API 密钥'};
 runVideoParse=async function(){toast('公开版请补充字幕后拆解，暂不自动读取平台视频')};
 refreshService();
})();
