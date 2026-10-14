(function(){
 const base=new URL('.',document.currentScript.src);let mammothPromise,pdfPromise;
 function script(file){return new Promise((resolve,reject)=>{const s=document.createElement('script');s.src=new URL(file,base);s.onload=resolve;s.onerror=()=>{s.remove();reject(Error('文档读取组件加载失败，请刷新后重试'))};document.head.appendChild(s)})}
 async function word(){if(!mammothPromise)mammothPromise=script('vendor/documents/mammoth.browser.min.js').then(()=>window.mammoth).catch(e=>{mammothPromise=null;throw e});return mammothPromise}
 async function pdf(){if(!pdfPromise)pdfPromise=import(new URL('vendor/documents/pdf.mjs',base).href).then(lib=>{lib.GlobalWorkerOptions.workerSrc=new URL('vendor/documents/pdf.worker.mjs',base).href;return lib}).catch(e=>{pdfPromise=null;throw e});return pdfPromise}
 function clean(text){return String(text).replace(/^\uFEFF/,'').replace(/\r\n?/g,'\n').trim()}
 function record(title,text){text=clean(text);if(!text)throw Error('未读取到正文，请检查文件是否为空或仅包含图片');if(text.length>50000)throw Error('正文超过 50,000 字，请拆分后导入，避免内容被截断');return {title:clean(title).slice(0,200)||'未命名文案',text}}
 function textRows(text,name){return clean(text).split(/^\s*---\s*$/m).filter(s=>s.trim()).map(part=>{const lines=clean(part).split('\n'),title=lines[0].replace(/^(?:标题[:：]\s*|#{1,6}\s*)/,'');return lines.slice(1).join('').trim()?record(title,lines.slice(1).join('\n')):record(name,part)})}
 async function read(file,progress=()=>{}){
  if(file.size>20*1024*1024)throw Error('单个文件请小于 20 MB');
  const ext=file.name.split('.').pop().toLowerCase(),title=file.name.replace(/\.[^.]+$/,'');progress('正在读取 '+file.name+'…');let rows;
  if(ext==='docx'){const m=await word();const result=await m.extractRawText({arrayBuffer:await file.arrayBuffer()});rows=[record(title,result.value)];}
  else if(ext==='pdf'){
   const lib=await pdf(),task=lib.getDocument({data:new Uint8Array(await file.arrayBuffer()),isEvalSupported:false,cMapUrl:new URL('vendor/documents/cmaps/',base).href,cMapPacked:true,standardFontDataUrl:new URL('vendor/documents/standard_fonts/',base).href});
   try{const doc=await task.promise;if(doc.numPages>100)throw Error('PDF 超过 100 页，请拆分后导入');const parts=[];
    for(let i=1;i<=doc.numPages;i++){progress('正在读取 '+file.name+' · 第 '+i+'/'+doc.numPages+' 页');const page=await doc.getPage(i),content=await page.getTextContent();const text=content.items.map(x=>x.str+(x.hasEOL?'\n':' ')).join('').trim();if(!text)throw Error('PDF 第 '+i+' 页没有可提取文字，可能是扫描页或空白页。请先转换为可复制文字的 PDF / DOCX，或复制正文导入');parts.push(text);page.cleanup()}
    rows=[record(title,parts.join('\n\n'))];
   }catch(e){if(e.name==='PasswordException')throw Error('PDF 已加密，请解密后重新导入');throw e}finally{await task.destroy()}
  }
  else if(ext==='json'){const value=JSON.parse(await file.text());rows=Array.isArray(value)?value:value.records;if(!Array.isArray(rows))throw Error('JSON 需要 records 数组；完整项目备份请用“导入项目备份”');rows=rows.map(r=>{if(typeof r.title!=='string'||typeof r.text!=='string')throw Error('JSON 每篇需含 title 和 text');return {...r,...record(r.title,r.text)}})}
  else if(['csv','tsv'].includes(ext)){rows=LabCore.csv(await file.text()).map((r,i)=>{const title=r.title??r['标题']??r['内容标题']??(file.name+' 第'+(i+1)+'篇');const text=r.text??r.content??r['正文']??r['文案']??r['内容'];if(typeof text!=='string')throw Error('表格需要“正文 / 文案 / 内容 / text / content”列，可另有“标题 / title”列');return record(title,text)})}
  else if(['srt','vtt'].includes(ext)){let text=clean(await file.text()).replace(/^WEBVTT[^\n]*\n?/,'').split(/\n\s*\n/).filter(b=>!/^\s*(NOTE|STYLE|REGION)\b/.test(b)).map(b=>b.split('\n').filter(l=>!/^\s*\d+\s*$/.test(l)&&!l.includes('-->')).join('\n')).join('\n').replace(/<[^>]+>/g,'');rows=[record(title,text)]}
  else if(['txt','md','markdown'].includes(ext))rows=textRows(await file.text(),title);
  else if(ext==='doc')throw Error('旧版 .doc 暂不支持，请在 Word / WPS 中另存为 .docx 后导入');
  else throw Error('支持 DOCX、PDF、TXT、Markdown、CSV、TSV、JSON、SRT、VTT');
  if(!rows.length)throw Error('文件没有可导入的内容');if(rows.length>20)throw Error('每次最多导入 20 篇，请分批导入');return rows;
 }
 window.LabImport={read,textRows};
})();
